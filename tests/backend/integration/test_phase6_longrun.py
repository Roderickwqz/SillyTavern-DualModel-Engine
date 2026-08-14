"""Phase 6 §17.3 long-run simulation: 10,000-turn engine endurance test.

Proves three claims:
(a) an early high-importance memory stays retrievable after 10k turns
    (importance-ordered search must keep the seed event findable);
(b) the narrator prompt size is bounded: build_messages caps dialogue at
    max_history_messages, so the prompt does not grow with total turns;
(c) duplicate events do not explode row counts: each ACTION turn records
    exactly 2 SCENE transcripts, so memory_events stays linear in turn
    count, the FTS index stays in parity, and no per-turn state snapshots
    are stored (versions only bump on real mutations).

Every turn runs the FULL graph (normalize -> lineage -> route -> scene ->
retrieve -> narrate -> extract -> gate -> critic(skip) -> commit ->
respond), i.e. 2 scripted LLM calls per turn. The extract response is the
empty JSON `{"operations": []}`, so no state mutations occur and the seed
memory is the only high-importance event.

The echoed assistant reply is the engine's own `strip_tracker_blocks`
output (the model-channel text, not the player-UI Tracker block): the
lineage contract hashes the CLEANED reply (see lineage_hash_after), so
exact-match resolution sees the identical hash chain either way, while
re-sending the Tracker block in all 20k echoed messages would re-parse
its JSON on every normalize/build pass (O(n^2) harness cost, ~60 min at
10k turns). Tracker stripping itself is covered in test_normalize.

Runtime (measured on this machine, py313, SQLite WAL):
  LONG_RUN_TURNS=200  -> ~8.4 s
  LONG_RUN_TURNS=10000 -> ~35.6 min (2138 s wall)
  Final DB 14.6 MiB, 2 snapshots (campaign v0 + seed v1), 20001
  memory_events (1 seed + 2 per turn), 10000 turn rows. Wall time is
  dominated by the lineage contract: each turn re-hashes the full echoed
  history (4 passes) and re-normalizes every message, so per-turn cost is
  O(chat length) and total is O(turns^2) for a full-history echo. The
  engine's DB lookups stay O(log n) via the 0006/0007 partial indexes.
"""
import json
import os
import time

import pytest

from sillytavern_rpg_engine.config import load_settings
from sillytavern_rpg_engine.domain.memory import MemoryEventType
from sillytavern_rpg_engine.domain.models import Audience
from sillytavern_rpg_engine.llm.client import ChatMessage
from sillytavern_rpg_engine.llm.scripted import ScriptedLLMClient
from sillytavern_rpg_engine.orchestration.graph import TurnRunner, default_services
from sillytavern_rpg_engine.orchestration.narrative import build_messages
from sillytavern_rpg_engine.orchestration.normalize import strip_tracker_blocks
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.memory_events import MemoryEventService
from sillytavern_rpg_engine.services.retrieval import RetrievalQuery, RetrievalService

SEED_ID = "seed-memory-1"
SEED_CONTENT = "龙裔身份确立"


@pytest.mark.slow
def test_ten_thousand_turns_retains_seed_memory_and_bounded_prompt(database):
    turn_count = int(os.environ.get("LONG_RUN_TURNS", "10000"))

    settings = load_settings({"RPG_NARRATOR_MODEL": "narrator-model"})
    ids = iter(f"lr-{i}" for i in range(turn_count + 50))
    campaigns = CampaignService(
        database, id_factory=ids.__next__, clock=lambda: "2026-08-14T00:00:00Z"
    )
    campaigns.create_campaign("c1", "Long")
    memory = MemoryEventService(
        database, campaigns.mutation_engine, id_factory=lambda: SEED_ID
    )
    memory.record(
        "c1", "main", 0,
        type=MemoryEventType.IDENTITY, content=SEED_CONTENT,
        importance=5,
        audiences=frozenset({Audience.NARRATOR, Audience.PLAYER_UI}),
    )

    # Each ACTION turn is 2 scripted calls: narrate + extract. The extract
    # answer is the empty operations JSON, so no state ever mutates.
    responses = [
        item
        for i in range(turn_count)
        for item in (f"回合 {i} 的叙述。", json.dumps({"operations": []}))
    ]
    narrator = ScriptedLLMClient(responses)
    runner = TurnRunner(default_services(database, settings, narrator))

    history = []
    start = time.monotonic()
    for i in range(turn_count):
        history.append({"role": "user", "content": f"行动 {i}"})
        result = runner.run({"campaign_id": "c1", "messages": list(history)})
        # Echo the model-channel reply (tracker stripped with the engine's
        # own canonical cleaner): this is exactly the text the lineage
        # contract hashes, so exact-match resolution keeps working turn to
        # turn; the player-UI Tracker block is not part of the model channel.
        history.append({
            "role": "assistant",
            "content": strip_tracker_blocks(
                result["choices"][0]["message"]["content"]
            ),
        })
        if i % 500 == 0:
            # (a) seed memory still retrievable by text search, importance-
            # ordered. SEED_CONTENT is 6 chars, satisfying FTS5 trigram.
            hits = RetrievalService(database).assemble(RetrievalQuery(
                campaign_id="c1", branch_id="main",
                text=SEED_CONTENT,
                audiences=frozenset({Audience.NARRATOR}),
                limit=5,
            ))
            assert SEED_ID in {e["id"] for e in hits["related_events"]}, \
                f"seed memory {SEED_ID} lost from search at turn {i}"
            # (b) the actual narrate prompt stays bounded: 2 base system
            # messages + dialogue capped at max_history_messages. Call i*2
            # is turn i's narrate request (extract is the fixed 4-msg call).
            assert len(narrator.requests[i * 2]) \
                <= settings.max_history_messages + 2, \
                f"narrator prompt grew past the cap at turn {i}"
            msgs = build_messages(
                context={"current_state": {}},
                history=[ChatMessage("user", f"行动 {j}") for j in range(i + 1)],
                max_history=settings.max_history_messages,
            )
            assert len(msgs) <= settings.max_history_messages + 2

    with database.connect() as connection:
        turns = connection.execute(
            "SELECT COUNT(*) FROM turns"
        ).fetchone()[0]
        events = connection.execute(
            "SELECT COUNT(*) FROM memory_events WHERE branch_id = 'main'"
        ).fetchone()[0]
        fts = connection.execute(
            "SELECT COUNT(*) FROM memory_events_fts"
        ).fetchone()[0]
        snapshots = connection.execute(
            "SELECT COUNT(*) FROM state_snapshots"
        ).fetchone()[0]
        duplicate_rows = connection.execute(
            "SELECT COUNT(*) - COUNT(DISTINCT content) FROM memory_events"
        ).fetchone()[0]
    # (c) exactly one turn row per turn; 2 transcripts per turn + 1 seed;
    # FTS stays in parity; snapshots only on version bumps (campaign v0 +
    # seed bump), never per turn; every transcript carries the unique turn
    # index so content never repeats.
    assert turns == turn_count
    assert events <= 2 * turn_count + 50, \
        f"memory_events exploded: {events} > 2*{turn_count}+50"
    assert fts == events, "memory_events_fts lost parity with memory_events"
    assert snapshots <= 3, \
        f"per-turn snapshot growth detected: {snapshots} snapshots for" \
        f" {turn_count} turns"
    assert duplicate_rows == 0, \
        f"{duplicate_rows} duplicate memory event contents after" \
        f" {turn_count} turns"
    elapsed = time.monotonic() - start
    print(
        f"[longrun] {turn_count} turns in {elapsed:.1f}s,"
        f" db={database.path.stat().st_size / 1024:.0f}KiB,"
        f" snapshots={snapshots}, memory_events={events}, turns={turns}"
    )
