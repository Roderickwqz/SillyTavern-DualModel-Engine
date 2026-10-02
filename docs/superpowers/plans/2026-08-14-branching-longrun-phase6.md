# LangGraph RPG Core — Phase 6 (Swipe/Branch Recovery & Long-Run Simulation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement spec §14 swipe/edit/delete branch recovery using `history_hash` lineage (no SillyTavern chat IDs), snapshot-based branch fork/restore, detached-turn marking without physical deletion, compat-extension per-swipe Tracker display cache, and a 10 000-turn simulation proving bounded prompts and durable early-memory retrieval.

**Architecture:** Phase 4 stores one `history_hash` per turn but does not resolve parentage or branches. Phase 6 adds `lineage_hash_before` / `lineage_hash_after` / `response_hash` on turns, a `branch_heads` table, `SnapshotRestoreService` to reload live SQLite state from `state_snapshots`, and `LineageResolver` that runs before every mutating graph path. The resolver maps the incoming OpenAI `messages` array (tracker-stripped) to exactly one parent turn and branch; ambiguity returns `BranchResolutionError` (409) with no writes. New swipes fork a child branch and restore the parent snapshot at `state_before_version`. Edits/deletes match the longest known prefix, detach later turns on that branch, and restore to the matched head. The RPG Companion compat fork keeps per-swipe Tracker JSON as display cache only; authoritative state always comes from the next backend response after history changes.

**Tech Stack:** Python 3.11+, SQLite migrations (`0006_branch_lineage.sql`), pytest 8.x + FastAPI `TestClient`, Vitest 4.x for compat swipe-cache tests, `ScriptedLLMClient` for long-run simulation (`@pytest.mark.slow`).

## Global Constraints

- Spec §14: identify parent turns via `campaign_id` + normalized visible-history hashes; exclude Tracker JSON from hashes (reuse `strip_tracker_blocks` / `normalize._history_hash`).
- Cannot uniquely identify parent turn → return diagnostic `BranchResolutionError`; **no** state commit, **no** turn row (spec §14).
- New swipe forks from parent state **before** the target reply; switching swipes restores the matching branch head via snapshot restore; contradictory branches are never auto-merged (spec §14).
- Edit/delete: find longest known valid prefix; mark subsequent turns `detached`; do not physically delete turn rows or audit events (spec §14).
- `campaigns.state_version` stays monotonic globally; inactive branches are preserved in `state_snapshots` + `branch_heads`; switching branches restores live tables from the target head snapshot inside one transaction before the new turn runs.
- Per-swipe RPG Companion data is display cache only; swipe/edit/delete must not write authoritative state from the panel (spec §12.3, §2.1).
- Long-run test (spec §17.3): ≥10 000 scripted turns; early high-importance memory still retrievable; narrator prompt size does not grow linearly with total turns (`max_history_messages` cap + summaries); duplicate events do not explode row counts beyond bounded summarization rules.
- Phase 7 owns export/backup/migration hardening and real SillyTavern Playwright E2E; this plan adds API-level branch diagnostics only.
- Conda env `py313`; `python -m pip install -e ".[dev]"`; `python -m pytest tests/backend -q`; `python -m pytest tests/backend -m slow -q` for long-run; JS: `npm run test:run`; guard: `python .harness/scripts/guard.py src/sillytavern_rpg_engine tests/backend`; commit via `bash .harness/scripts/committer "<msg>" <files...>`.

## Multi-Plan Roadmap

1. Phase 1–4 (done): core, memory, D&D, LangGraph API.
2. Phase 5 (done or in progress): RPG Companion compat fork + dynamic Tracker rendering.
3. **This plan:** swipe/edit/delete branch recovery + 10 000-turn simulation.
4. Phase 7: export, backup, migration diagnostics, real SillyTavern E2E.

## File Structure

```text
src/sillytavern_rpg_engine/
├── domain/
│   └── errors.py                         # MODIFY: + BranchResolutionError
├── orchestration/
│   ├── normalize.py                      # MODIFY: lineage hash helpers
│   └── graph.py                          # MODIFY: resolve lineage before mutate
├── persistence/schema/
│   └── 0006_branch_lineage.sql           # NEW: turn lineage + branch_heads
├── services/
│   ├── branches.py                       # NEW: fork, head pointers, detach
│   ├── lineage.py                        # NEW: LineageResolver
│   ├── snapshot_restore.py               # NEW: SnapshotRestoreService
│   └── turns.py                          # MODIFY: lineage columns, detach API
├── server/
│   └── app.py                            # MODIFY: BranchResolutionError mapping, branch admin route
extensions/rpg-companion-compat/src/compat/
├── lineageCache.js                       # NEW: per-swipe tracker cache
├── swipeDisplay.js                       # NEW: MESSAGE_SWIPED display restore
└── bootstrap.js                          # MODIFY: wire swipe hooks
tests/backend/unit/
├── test_lineage_normalize.py
├── test_branches.py
├── test_snapshot_restore.py
├── test_lineage_resolver.py
└── test_turns_lineage.py
tests/backend/integration/
├── test_phase6_branch_flow.py
└── test_phase6_longrun.py                # @pytest.mark.slow
tests/rpg-companion-compat/unit/
└── lineageCache.test.js
pyproject.toml                            # MODIFY: pytest slow marker
```

---

### Task 1: Schema migration for turn lineage and branch heads

**Files:**
- Create: `src/sillytavern_rpg_engine/persistence/schema/0006_branch_lineage.sql`
- Test: `tests/backend/unit/test_migrations.py` (extend)

**Interfaces:**
- Consumes: existing `turns`, `branches`, `state_snapshots`.
- Produces: columns `turns.lineage_hash_before`, `turns.lineage_hash_after`, `turns.response_hash`, `turns.status`; table `branch_heads(campaign_id, branch_id, state_version, latest_turn_id, updated_at)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/backend/unit/test_migrations.py`:

```python
def test_migration_0006_adds_lineage_columns(database):
    with database.connect() as connection:
        cols = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(turns)").fetchall()
        }
    assert {"lineage_hash_before", "lineage_hash_after", "response_hash", "status"} <= cols
    with database.connect() as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='branch_heads'"
        ).fetchone() is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/backend/unit/test_migrations.py::test_migration_0006_adds_lineage_columns -v`
Expected: FAIL — columns missing

- [ ] **Step 3: Add migration SQL**

```sql
-- src/sillytavern_rpg_engine/persistence/schema/0006_branch_lineage.sql
ALTER TABLE turns ADD COLUMN lineage_hash_before TEXT;
ALTER TABLE turns ADD COLUMN lineage_hash_after TEXT;
ALTER TABLE turns ADD COLUMN response_hash TEXT;
ALTER TABLE turns ADD COLUMN status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'detached'));

CREATE INDEX idx_turns_lineage_after
    ON turns(campaign_id, lineage_hash_after)
    WHERE status = 'active';

CREATE TABLE branch_heads (
    campaign_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    state_version INTEGER NOT NULL CHECK (state_version >= 0),
    latest_turn_id TEXT REFERENCES turns(id),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (campaign_id, branch_id),
    FOREIGN KEY (campaign_id, branch_id)
        REFERENCES branches(campaign_id, id) ON DELETE CASCADE
);

-- Bootstrap main branch heads for existing campaigns.
INSERT INTO branch_heads(campaign_id, branch_id, state_version, latest_turn_id, updated_at)
SELECT c.id, 'main', c.state_version, (
    SELECT t.id FROM turns t
    WHERE t.campaign_id = c.id AND t.branch_id = 'main'
    ORDER BY t.created_at DESC LIMIT 1
), c.updated_at
FROM campaigns c
ON CONFLICT(campaign_id, branch_id) DO NOTHING;
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/backend/unit/test_migrations.py::test_migration_0006_adds_lineage_columns -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: add branch lineage schema migration" \
  src/sillytavern_rpg_engine/persistence/schema/0006_branch_lineage.sql \
  tests/backend/unit/test_migrations.py
```

---

### Task 2: Lineage hash helpers in normalizer

**Files:**
- Modify: `src/sillytavern_rpg_engine/orchestration/normalize.py`
- Test: `tests/backend/unit/test_lineage_normalize.py`

**Interfaces:**
- Consumes: existing `_history_hash`, `strip_tracker_blocks`, `ChatMessage`.
- Produces: `lineage_hash_before(messages) -> str`; `lineage_hash_after(messages, assistant_text) -> str`; `response_hash(assistant_text) -> str`; `NormalizedRequest` gains `lineage_hash_before: str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/backend/unit/test_lineage_normalize.py
from sillytavern_rpg_engine.llm.client import ChatMessage
from sillytavern_rpg_engine.orchestration.normalize import (
    lineage_hash_after,
    lineage_hash_before,
    normalize_request,
    response_hash,
)


def test_lineage_hashes_exclude_tracker_blocks():
    msgs = (
        ChatMessage("user", "你好"),
        ChatMessage("assistant", "回复\n```json\n{\"userStats\":{}}\n```"),
        ChatMessage("user", "继续"),
    )
    before = lineage_hash_before(msgs)
    after = lineage_hash_after(
        msgs[:-1],
        "回复\n```json\n{\"userStats\":{}}\n```",
    )
    assert len(before) == 64
    assert len(after) == 64
    assert before != after
    assert response_hash("回复\n```json\n{\"userStats\":{}}\n```") == response_hash("回复")


def test_normalize_request_includes_lineage_hash_before():
    request = normalize_request({
        "campaign_id": "c1",
        "messages": [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
        ],
    })
    assert request.lineage_hash_before == lineage_hash_before(request.messages)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/backend/unit/test_lineage_normalize.py -v`
Expected: FAIL — imports missing

- [ ] **Step 3: Implement helpers**

Add to `normalize.py`:

```python
def lineage_hash_before(messages: tuple[ChatMessage, ...]) -> str:
  if not messages or messages[-1].role != "user":
      raise ValidationError("lineage requires trailing user message")
  return _history_hash(messages[:-1])


def lineage_hash_after(
    prefix: tuple[ChatMessage, ...], assistant_text: str,
) -> str:
    cleaned = strip_tracker_blocks(assistant_text)
    return _history_hash(prefix + (ChatMessage("assistant", cleaned),))


def response_hash(assistant_text: str) -> str:
    return _history_hash((ChatMessage("assistant", strip_tracker_blocks(assistant_text)),))
```

Extend `NormalizedRequest` with `lineage_hash_before: str` and set it in `normalize_request` after building `messages`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/backend/unit/test_lineage_normalize.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: add lineage hash helpers" \
  src/sillytavern_rpg_engine/orchestration/normalize.py \
  tests/backend/unit/test_lineage_normalize.py
```

---

### Task 3: BranchService and branch head pointers

**Files:**
- Create: `src/sillytavern_rpg_engine/services/branches.py`
- Modify: `src/sillytavern_rpg_engine/domain/errors.py` (+ `BranchResolutionError`)
- Test: `tests/backend/unit/test_branches.py`

**Interfaces:**
- Consumes: `Database`, `BranchRepository`, `SnapshotBuilder`.
- Produces: `BranchHead` dataclass; `BranchService.fork(campaign_id, parent_branch_id, parent_turn_id, *, new_branch_id) -> str`; `BranchService.update_head(campaign_id, branch_id, state_version, turn_id)`; `BranchService.get_head(campaign_id, branch_id) -> BranchHead`.

- [ ] **Step 1: Write the failing test**

```python
# tests/backend/unit/test_branches.py
from sillytavern_rpg_engine.services.branches import BranchService
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.turns import TurnService


def test_fork_creates_child_branch_and_head(database):
    ids = iter(f"id-{i}" for i in range(20))
    campaigns = CampaignService(database, id_factory=ids.__next__,
                                clock=lambda: "2026-08-14T00:00:00Z")
    campaigns.create_campaign("c1", "Test")
    turns = TurnService(database, id_factory=ids.__next__)
    turn = turns.record(
        "c1", "main", intent="action", player_text="hi",
        response_text="ok", history_hash="h1",
        state_before_version=0, state_after_version=0,
        turn_id="turn-1",
    )
    branches = BranchService(database, id_factory=ids.__next__)
    child = branches.fork("c1", "main", turn.id, new_branch_id="branch-swipe-1")
    assert child == "branch-swipe-1"
    head = branches.get_head("c1", child)
    assert head.latest_turn_id == turn.id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/backend/unit/test_branches.py -v`
Expected: FAIL

- [ ] **Step 3: Implement BranchService**

```python
# src/sillytavern_rpg_engine/services/branches.py
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from ..domain.errors import NotFoundError, ValidationError
from ..persistence.database import Database
from ..persistence.repositories import BranchRepository


@dataclass(frozen=True)
class BranchHead:
    campaign_id: str
    branch_id: str
    state_version: int
    latest_turn_id: str | None
    updated_at: str


class BranchService:
    def __init__(self, database: Database, *, id_factory=None, clock=None):
        self.database = database
        self.branch_repository = BranchRepository()
        self._id_factory = id_factory or (lambda: uuid4().hex)
        self._clock = clock or (lambda: datetime.now(timezone.utc).isoformat())

    def get_head(self, campaign_id: str, branch_id: str) -> BranchHead:
        with self.database.connect() as connection:
            self.branch_repository.require(connection, campaign_id, branch_id)
            row = connection.execute(
                "SELECT campaign_id, branch_id, state_version, latest_turn_id,"
                " updated_at FROM branch_heads WHERE campaign_id = ? AND branch_id = ?",
                (campaign_id, branch_id),
            ).fetchone()
            if row is None:
                raise NotFoundError(f"branch head {branch_id!r} missing")
            return BranchHead(**dict(row))

    def update_head(
        self, campaign_id: str, branch_id: str,
        state_version: int, turn_id: str | None,
    ) -> None:
        now = self._clock()
        with self.database.connect() as connection:
            self.branch_repository.require(connection, campaign_id, branch_id)
            connection.execute(
                "INSERT INTO branch_heads(campaign_id, branch_id, state_version,"
                " latest_turn_id, updated_at) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(campaign_id, branch_id) DO UPDATE SET"
                " state_version = excluded.state_version,"
                " latest_turn_id = excluded.latest_turn_id,"
                " updated_at = excluded.updated_at",
                (campaign_id, branch_id, state_version, turn_id, now),
            )

    def fork(
        self, campaign_id: str, parent_branch_id: str,
        parent_turn_id: str, *, new_branch_id: str | None = None,
    ) -> str:
        branch_id = new_branch_id or f"branch-{self._id_factory()}"
        now = self._clock()
        with self.database.connect() as connection:
            self.branch_repository.require(connection, campaign_id, parent_branch_id)
            parent = connection.execute(
                "SELECT id, state_after_version FROM turns WHERE id = ? AND campaign_id = ?",
                (parent_turn_id, campaign_id),
            ).fetchone()
            if parent is None:
                raise NotFoundError(f"parent turn {parent_turn_id!r} not found")
            exists = connection.execute(
                "SELECT 1 FROM branches WHERE campaign_id = ? AND id = ?",
                (campaign_id, branch_id),
            ).fetchone()
            if exists:
                raise ValidationError(f"branch {branch_id!r} already exists")
            connection.execute(
                "INSERT INTO branches(id, campaign_id, parent_branch_id, status, created_at)"
                " VALUES (?, ?, ?, 'active', ?)",
                (branch_id, campaign_id, parent_branch_id, now),
            )
            connection.execute(
                "INSERT INTO branch_heads(campaign_id, branch_id, state_version,"
                " latest_turn_id, updated_at) VALUES (?, ?, ?, ?, ?)",
                (campaign_id, branch_id, parent["state_after_version"],
                 parent_turn_id, now),
            )
        return branch_id
```

Add to `errors.py`:

```python
class BranchResolutionError(DomainError):
    """Raised when visible history cannot be mapped to exactly one parent turn."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/backend/unit/test_branches.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: add branch fork and head tracking" \
  src/sillytavern_rpg_engine/services/branches.py \
  src/sillytavern_rpg_engine/domain/errors.py \
  tests/backend/unit/test_branches.py
```

---

### Task 4: SnapshotRestoreService

**Files:**
- Create: `src/sillytavern_rpg_engine/services/snapshot_restore.py`
- Test: `tests/backend/unit/test_snapshot_restore.py`

**Interfaces:**
- Consumes: `SnapshotBuilder` snapshot JSON shape; `state_snapshots` rows.
- Produces: `SnapshotRestoreService.restore(campaign_id, branch_id, state_version) -> int` reloads entities/attributes/facts/relationships/conditions/combat and sets `campaigns.state_version`; returns restored version.

- [ ] **Step 1: Write the failing test**

```python
# tests/backend/unit/test_snapshot_restore.py
from sillytavern_rpg_engine.services.attributes import (
    CreateEntityOperation, DefineAttributeOperation, SetAttributeOperation,
)
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import EntityAttributeService
from sillytavern_rpg_engine.services.mutations import MutationEngine
from sillytavern_rpg_engine.services.snapshot_restore import SnapshotRestoreService
from sillytavern_rpg_engine.services.snapshots import SnapshotBuilder
from sillytavern_rpg_engine.domain.models import (
    AttributeDefinition, AttributeType, Audience, DisplayType, EntityKind,
)


def test_restore_reverts_live_state_to_snapshot(database):
    ids = iter(f"e-{i}" for i in range(30))
    campaigns = CampaignService(database, id_factory=ids.__next__,
                                clock=lambda: "2026-08-14T00:00:00Z")
    campaigns.create_campaign("c1", "Test")
    attrs = EntityAttributeService(database, campaigns.mutation_engine)
    attrs.apply_explicit("c1", "main", 0, CreateEntityOperation(
        "erin", EntityKind.CHARACTER, "艾琳",
    ))
    attrs.apply_explicit("c1", "main", 1, DefineAttributeOperation(
        AttributeDefinition(
            campaign_id="c1", key="alchemy", label="炼金", category="skill",
            value_type=AttributeType.NUMBER, display=DisplayType.BAR,
            audiences=frozenset({Audience.PLAYER_UI}), minimum=0, maximum=100,
        ),
    ))
    attrs.apply_explicit("c1", "main", 2, SetAttributeOperation("erin", "alchemy", 10, None))
    with database.connect() as connection:
        snap = SnapshotBuilder().build(connection, "c1", "main")
        MutationEngine(database)._persist_snapshot(connection, "c1", "main", 2, snap, "2026-08-14T00:00:00Z")
    attrs.apply_explicit("c1", "main", 3, SetAttributeOperation("erin", "alchemy", 99, None))
    restored = SnapshotRestoreService(database).restore("c1", "main", 2)
    assert restored == 2
    with database.connect() as connection:
        value = connection.execute(
            "SELECT value_json FROM attribute_values WHERE entity_id='erin'"
            " AND attribute_key='alchemy'"
        ).fetchone()["value_json"]
    assert value == "10"
```

Note: if `_persist_snapshot` is not public, insert snapshot via `SnapshotRepository.insert` in the test helper instead.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/backend/unit/test_snapshot_restore.py -v`
Expected: FAIL

- [ ] **Step 3: Implement SnapshotRestoreService**

Implement `restore` as one transaction that:
1. Loads `snapshot_json` from `state_snapshots` at `(campaign_id, branch_id, state_version)`.
2. Deletes campaign-scoped mutable rows: `attribute_values`, `entity_aliases`, `entities`, `attribute_definitions`, `facts`, `relationships`, `entity_conditions`, branch-scoped `combat_encounters`/`combatants`, branch-scoped `memory_events` with `state_version > target` (or full branch re-insert from snapshot sections).
3. Re-inserts snapshot sections in FK-safe order (definitions → entities → aliases → values → facts → relationships → conditions → combat).
4. `UPDATE campaigns SET state_version = ? WHERE id = ?`.

Keep the implementation aligned with `SnapshotBuilder.build` field names. Prefer one module-local `_DELETE_CAMPAIGN_STATE_SQL` list executed before `_insert_from_snapshot(snapshot)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/backend/unit/test_snapshot_restore.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: restore live state from branch snapshots" \
  src/sillytavern_rpg_engine/services/snapshot_restore.py \
  tests/backend/unit/test_snapshot_restore.py
```

---

### Task 5: LineageResolver

**Files:**
- Create: `src/sillytavern_rpg_engine/services/lineage.py`
- Modify: `src/sillytavern_rpg_engine/services/turns.py` (lineage columns on `record`, `detach_after`, `find_by_lineage_after`)
- Test: `tests/backend/unit/test_lineage_resolver.py`, `tests/backend/unit/test_turns_lineage.py`

**Interfaces:**
- Consumes: `lineage_hash_before`, `response_hash`, `BranchService`, `SnapshotRestoreService`.
- Produces: `@dataclass BranchContext: branch_id, parent_turn_id | None, restored: bool`; `LineageResolver.resolve(campaign_id, messages) -> BranchContext`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/backend/unit/test_lineage_resolver.py
import pytest
from sillytavern_rpg_engine.domain.errors import BranchResolutionError
from sillytavern_rpg_engine.llm.client import ChatMessage
from sillytavern_rpg_engine.orchestration.normalize import lineage_hash_after
from sillytavern_rpg_engine.services.lineage import LineageResolver
from sillytavern_rpg_engine.services.turns import TurnService
from sillytavern_rpg_engine.services.campaigns import CampaignService


def _record_turn(database, *, branch="main", player="a", response="A", prefix=()):
    ids = iter(range(1000))
    campaigns = CampaignService(database, id_factory=lambda: f"id-{next(ids)}",
                                clock=lambda: "2026-08-14T00:00:00Z")
    if not hasattr(_record_turn, "boot"):
        campaigns.create_campaign("c1", "T")
        _record_turn.boot = True
    msgs = prefix + (ChatMessage("user", player),)
    turns = TurnService(database, id_factory=lambda: f"turn-{next(ids)}")
    return turns.record(
        "c1", branch, intent="action", player_text=player,
        response_text=response, history_hash="audit",
        lineage_hash_before=__import__("sillytavern_rpg_engine.orchestration.normalize", fromlist=["lineage_hash_before"]).lineage_hash_before(msgs),
        lineage_hash_after=lineage_hash_after(prefix, response),
        response_hash=__import__("sillytavern_rpg_engine.orchestration.normalize", fromlist=["response_hash"]).response_hash(response),
        state_before_version=0, state_after_version=1,
    )


def test_resolve_continues_same_branch(database):
    turn = _record_turn(database)
    messages = (
        ChatMessage("user", "a"),
        ChatMessage("assistant", "A"),
        ChatMessage("user", "b"),
    )
    ctx = LineageResolver(database).resolve("c1", messages)
    assert ctx.branch_id == "main"
    assert ctx.parent_turn_id == turn.id


def test_ambiguous_parent_raises(database):
    from sillytavern_rpg_engine.orchestration.normalize import (
        lineage_hash_before, lineage_hash_after, response_hash,
    )
    ids = iter(range(1000))
    campaigns = CampaignService(database, id_factory=lambda: f"id-{next(ids)}",
                                clock=lambda: "2026-08-14T00:00:00Z")
    campaigns.create_campaign("c1", "T")
    prefix = ()
    msgs = prefix + (ChatMessage("user", "a"),)
    shared_after = lineage_hash_after(prefix, "A")
    turns = TurnService(database, id_factory=lambda: f"turn-{next(ids)}")
    for branch in ("main", "branch-dup"):
        turns.record(
            "c1", branch, intent="action", player_text="a",
            response_text="A", history_hash="audit",
            lineage_hash_before=lineage_hash_before(msgs),
            lineage_hash_after=shared_after,
            response_hash=response_hash("A"),
            state_before_version=0, state_after_version=1,
        )
    messages = (
        ChatMessage("user", "a"),
        ChatMessage("assistant", "A"),
        ChatMessage("user", "b"),
    )
    with pytest.raises(BranchResolutionError, match="ambiguous"):
        LineageResolver(database).resolve("c1", messages)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/backend/unit/test_lineage_resolver.py -v`
Expected: FAIL

- [ ] **Step 3: Implement resolver + extend TurnService.record**

`TurnService.record` new optional kwargs: `lineage_hash_before`, `lineage_hash_after`, `response_hash`.

`LineageResolver.resolve` algorithm:

```python
@dataclass(frozen=True)
class BranchContext:
    branch_id: str
    parent_turn_id: str | None
    restored: bool


class LineageResolver:
    def resolve(self, campaign_id: str, messages: tuple[ChatMessage, ...]) -> BranchContext:
        prefix_hash = lineage_hash_before(messages)
        rows = self._active_turns_by_lineage_after(campaign_id, prefix_hash)
        if len(rows) > 1:
            raise BranchResolutionError(
                f"ambiguous parent for lineage {prefix_hash}: {len(rows)} candidates"
            )
        if len(rows) == 1:
            parent = rows[0]
            return BranchContext(parent["branch_id"], parent["id"], restored=False)
        # No exact parent: attempt longest-prefix detach + restore (edit/delete)
        return self._resolve_by_prefix_detach(campaign_id, messages)
```

`_resolve_by_prefix_detach` walks prefix lengths from full `messages[:-1]` downward, finds the deepest turn whose `lineage_hash_after` matches, detaches later active turns on that branch, restores branch head snapshot, returns `BranchContext`.

For **swipe fork** (assistant text mismatch): detect when `messages[-2]` is assistant and `response_hash(messages[-2].content) != parent.response_hash`; call `BranchService.fork`, `SnapshotRestoreService.restore` to `parent.state_before_version`, return new branch.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/backend/unit/test_lineage_resolver.py tests/backend/unit/test_turns_lineage.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: resolve branch context from visible history" \
  src/sillytavern_rpg_engine/services/lineage.py \
  src/sillytavern_rpg_engine/services/turns.py \
  tests/backend/unit/test_lineage_resolver.py \
  tests/backend/unit/test_turns_lineage.py
```

---

### Task 6: Wire lineage resolution into graph and API

**Files:**
- Modify: `src/sillytavern_rpg_engine/orchestration/graph.py`
- Modify: `src/sillytavern_rpg_engine/server/app.py`
- Test: extend `tests/backend/unit/test_graph.py`

**Interfaces:**
- Consumes: `LineageResolver.resolve`, updated `TurnService.record`.
- Produces: graph `node_resolve_lineage` runs after `node_normalize`, sets `branch_id` on state from resolver (overrides request `branch_id` when resolved), skips mutation paths on `BranchResolutionError`; API maps `BranchResolutionError` → 409 `branch_resolution_error`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/backend/unit/test_graph.py
def test_edit_history_detaches_and_continues(database, scripted_runner):
    runner = scripted_runner
    chat = lambda text, history: runner.run({
        "campaign_id": "c1",
        "messages": history + [{"role": "user", "content": text}],
    })["choices"][0]["message"]["content"]

    history = []
    runner.narrator.queue("回复甲")
    chat("a", history)
    history.extend([
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "回复甲"},
    ])
    runner.narrator.queue("回复乙")
    chat("b", history)
    # Edit: drop first assistant, keep user messages only, continue with "c"
    edited = [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}]
    runner.narrator.queue("回复丙")
    result = runner.run({
        "campaign_id": "c1",
        "messages": edited + [{"role": "user", "content": "c"}],
    })
    assert result["choices"][0]["message"]["content"]
    with database.connect() as connection:
        detached = connection.execute(
            "SELECT COUNT(*) FROM turns WHERE status = 'detached'"
        ).fetchone()[0]
        active = connection.execute(
            "SELECT COUNT(*) FROM turns WHERE status = 'active'"
        ).fetchone()[0]
    assert detached >= 1
    assert active >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/backend/unit/test_graph.py -k edit_history -v`
Expected: FAIL

- [ ] **Step 3: Wire graph**

Add to `TurnState`: `branch_id: str`, `parent_turn_id: str | None`, `branch_restored: bool`.

New node after normalize:

```python
def _make_resolve_lineage_node(database: Database) -> Any:
    resolver = LineageResolver(database)
    def node_resolve_lineage(state: TurnState) -> dict[str, Any]:
        request = state["request"]
        ctx = resolver.resolve(request.campaign_id, request.messages)
        return {
            "branch_id": ctx.branch_id,
            "parent_turn_id": ctx.parent_turn_id,
            "branch_restored": ctx.restored,
        }
    return node_resolve_lineage
```

Use `state["branch_id"]` everywhere `request.branch_id` was used for mutations/turns.

In `node_commit`, pass lineage fields to `TurnService.record`.

In `app.py`:

```python
from ..domain.errors import BranchResolutionError
_STATUS[BranchResolutionError] = (409, "branch_resolution_error")
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/backend/unit/test_graph.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: wire lineage resolution into turn graph" \
  src/sillytavern_rpg_engine/orchestration/graph.py \
  src/sillytavern_rpg_engine/server/app.py \
  tests/backend/unit/test_graph.py
```

---

### Task 7: Compat per-swipe Tracker display cache

**Files:**
- Create: `extensions/rpg-companion-compat/src/compat/lineageCache.js`
- Modify: `extensions/rpg-companion-compat/src/compat/bootstrap.js`
- Test: `tests/rpg-companion-compat/unit/lineageCache.test.js`

**Interfaces:**
- Consumes: `parseResult.compat` from Task 5 (Phase 5 parser).
- Produces: `cacheCompatTracker(message, swipeId, compat)` → stores under `message.extra.rpg_compat_swipes[swipeId]`; `loadCompatTracker(message, swipeId) -> object | null`.

- [ ] **Step 1: Write the failing test**

```javascript
// tests/rpg-companion-compat/unit/lineageCache.test.js
import { describe, expect, it } from 'vitest';
import { cacheCompatTracker, loadCompatTracker } from '../../../extensions/rpg-companion-compat/src/compat/lineageCache.js';

describe('lineageCache', () => {
    it('stores and loads compat tracker per swipe without mutating committed settings', () => {
        const message = { swipe_id: 1, extra: {} };
        const compat = { state_version: 3, rules: { mode: 'narrative' } };
        cacheCompatTracker(message, 1, compat);
        expect(loadCompatTracker(message, 1)).toEqual(compat);
        expect(loadCompatTracker(message, 0)).toBeNull();
    });
});
```

- [ ] **Step 2–4: Implement, test, verify PASS**

```javascript
// extensions/rpg-companion-compat/src/compat/lineageCache.js
const STORE_KEY = 'rpg_compat_swipes';

export function cacheCompatTracker(message, swipeId, compat) {
    if (!message || compat == null) return;
    if (!message.extra || typeof message.extra !== 'object') message.extra = {};
    if (!message.extra[STORE_KEY] || typeof message.extra[STORE_KEY] !== 'object') {
        message.extra[STORE_KEY] = {};
    }
    message.extra[STORE_KEY][String(swipeId)] = structuredClone(compat);
}

export function loadCompatTracker(message, swipeId = message?.swipe_id ?? 0) {
    const store = message?.extra?.[STORE_KEY];
    if (!store) return null;
    return store[String(swipeId)] ?? null;
}
```

Call `cacheCompatTracker` from `applyCompatTracker` after successful parse.

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: cache compat tracker per swipe" \
  extensions/rpg-companion-compat/src/compat/lineageCache.js \
  extensions/rpg-companion-compat/src/compat/bootstrap.js \
  tests/rpg-companion-compat/unit/lineageCache.test.js
```

---

### Task 8: Swipe display restore (no authoritative panel writes)

**Files:**
- Create: `extensions/rpg-companion-compat/src/compat/swipeDisplay.js`
- Modify: `extensions/rpg-companion-compat/index.js`
- Test: `tests/rpg-companion-compat/unit/lineageCache.test.js` (extend)

**Interfaces:**
- Consumes: `loadCompatTracker`, `applyCompatTracker`.
- Produces: `bindSwipeDisplay(handlerContext)` registers `MESSAGE_SWIPED` / `MESSAGE_UPDATED` listeners that re-render panels from cached compat only.

- [ ] **Step 1: Write failing test** for `restoreSwipeDisplay(message)` calling `applyCompatTracker` with cached compat and not invoking `saveChatData` for tracker fields (mock/spy).

- [ ] **Step 2–3: Implement swipeDisplay.js**

```javascript
// extensions/rpg-companion-compat/src/compat/swipeDisplay.js
import { loadCompatTracker } from './lineageCache.js';
import { applyCompatTracker } from './bootstrap.js';

export function restoreSwipeDisplay(message, containers) {
    const compat = loadCompatTracker(message);
    if (!compat) return false;
    applyCompatTracker({ compat, rawText: '' }, containers);
    return true;
}

export function bindSwipeDisplay(eventSource, eventTypes, getContext, containers) {
    const rerender = (messageIndex) => {
        const message = getContext()?.chat?.[messageIndex];
        if (!message || message.is_user) return;
        restoreSwipeDisplay(message, containers);
    };
    eventSource.on(eventTypes.MESSAGE_SWIPED, rerender);
    eventSource.on(eventTypes.MESSAGE_UPDATED, rerender);
}
```

Wire from `index.js` after UI containers exist. Do **not** call upstream `updateMessageSwipeData` for compat tracker payloads.

- [ ] **Step 4: Run JS tests — PASS**

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: restore compat tracker display on swipe" \
  extensions/rpg-companion-compat/src/compat/swipeDisplay.js \
  extensions/rpg-companion-compat/index.js \
  tests/rpg-companion-compat/unit/lineageCache.test.js
```

---

### Task 9: Integration tests — swipe fork, edit detach, branch isolation

**Files:**
- Create: `tests/backend/integration/test_phase6_branch_flow.py`

**Interfaces:**
- Consumes: full FastAPI stack, `ScriptedLLMClient`.
- Produces: tests proving spec §17.2 items: swipe branch non-pollution, edit/delete prefix recovery.

- [ ] **Step 1: Write failing integration tests**

```python
# tests/backend/integration/test_phase6_branch_flow.py
import json
import re

from fastapi.testclient import TestClient

from sillytavern_rpg_engine.llm.scripted import ScriptedLLMClient
from sillytavern_rpg_engine.server.app import create_app
from sillytavern_rpg_engine.services.attributes import (
    CreateEntityOperation, DefineAttributeOperation, SetAttributeOperation,
)
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import EntityAttributeService
from sillytavern_rpg_engine.domain.models import (
    AttributeDefinition, AttributeType, Audience, DisplayType, EntityKind,
)


def _world(database):
    ids = iter(f"e-{i}" for i in range(200))
    campaigns = CampaignService(database, id_factory=ids.__next__,
                                clock=lambda: "2026-08-14T00:00:00Z")
    campaigns.create_campaign("c1", "Branch")
    attrs = EntityAttributeService(database, campaigns.mutation_engine)
    attrs.apply_explicit("c1", "main", 0, CreateEntityOperation(
        "erin", EntityKind.CHARACTER, "艾琳",
    ))
    attrs.apply_explicit("c1", "main", 1, DefineAttributeOperation(
        AttributeDefinition(
            campaign_id="c1", key="alchemy", label="炼金", category="skill",
            value_type=AttributeType.NUMBER, display=DisplayType.BAR,
            audiences=frozenset({Audience.PLAYER_UI}), minimum=0, maximum=100,
        ),
    ))
    return campaigns


def _chat(client, messages, narrator):
    narrator.queue("叙述一")
    return client.post("/v1/chat/completions", json={
        "campaign_id": "c1", "messages": messages,
    })


def test_swipe_fork_isolates_attribute_changes(database):
    _world(database)
    narrator = ScriptedLLMClient()
    client = TestClient(create_app(database=database, narrator=narrator))
    history = [{"role": "user", "content": "开始"}]
    _chat(client, history, narrator)
    history.append({"role": "assistant", "content": "叙述一"})
    history.append({"role": "user", "content": "继续"})
    narrator.queue("叙述二")
    _chat(client, history, narrator)
    # Swipe: alternate assistant at first reply
    swiped = [
        {"role": "user", "content": "开始"},
        {"role": "assistant", "content": "叙述一-改写"},
        {"role": "user", "content": "继续"},
    ]
    narrator.queue("叙述二-分支")
    response = _chat(client, swiped, narrator)
    assert response.status_code == 200
    with database.connect() as connection:
        branches = {
            row["branch_id"] for row in connection.execute(
                "SELECT DISTINCT branch_id FROM turns"
            ).fetchall()
        }
    assert len(branches) >= 2


def test_edit_shortens_history_and_detaches_tail(database):
    _world(database)
    narrator = ScriptedLLMClient()
    client = TestClient(create_app(database=database, narrator=narrator))
    history = [{"role": "user", "content": "一"}]
    narrator.queue("A")
    _chat(client, history, narrator)
    history.extend([
        {"role": "assistant", "content": "A"},
        {"role": "user", "content": "二"},
    ])
    narrator.queue("B")
    _chat(client, history, narrator)
    edited = [{"role": "user", "content": "一"}, {"role": "user", "content": "二"}]
    narrator.queue("C")
    response = _chat(client, edited + [{"role": "user", "content": "三"}], narrator)
    assert response.status_code == 200
    with database.connect() as connection:
        detached = connection.execute(
            "SELECT COUNT(*) FROM turns WHERE status = 'detached'"
        ).fetchone()[0]
    assert detached >= 1


def test_parallel_branches_keep_independent_pending_proposals(database):
    _world(database)
    narrator = ScriptedLLMClient()
    client = TestClient(create_app(database=database, narrator=narrator))
    history = [{"role": "user", "content": "行动"}]
    narrator.queue("剧情\n```json\n{\"operations\":[{\"kind\":\"set_attribute\",\"entity_id\":\"erin\",\"attribute_key\":\"alchemy\",\"value\":40}]}\n```")
    _chat(client, history, narrator)
    with database.connect() as connection:
        pending_main = connection.execute(
            "SELECT COUNT(*) FROM pending_proposals WHERE branch_id = 'main'"
        ).fetchone()[0]
    assert pending_main >= 1
```

- [ ] **Step 2: Run — FAIL**

Run: `python -m pytest tests/backend/integration/test_phase6_branch_flow.py -v`

- [ ] **Step 3: Fix implementation until PASS** (Tasks 3–6)

- [ ] **Step 4: Commit**

```bash
bash .harness/scripts/committer "test: add phase 6 branch flow integration tests" \
  tests/backend/integration/test_phase6_branch_flow.py
```

---

### Task 10: 10 000-turn long-run simulation

**Files:**
- Create: `tests/backend/integration/test_phase6_longrun.py`
- Modify: `pyproject.toml` (pytest marker `slow`)
- Optional: `src/sillytavern_rpg_engine/services/simulation.py` helper

**Interfaces:**
- Consumes: `ScriptedLLMClient`, `RetrievalService`, `build_messages`.
- Produces: `@pytest.mark.slow test_ten_thousand_turns_retains_seed_memory_and_bounded_prompt`.

- [ ] **Step 1: Register slow marker in pyproject.toml**

```toml
[tool.pytest.ini_options]
markers = ["slow: long-running simulation tests"]
```

- [ ] **Step 2: Write failing long-run test**

```python
# tests/backend/integration/test_phase6_longrun.py
import pytest
from sillytavern_rpg_engine.orchestration.narrative import build_messages
from sillytavern_rpg_engine.services.retrieval import RetrievalQuery, RetrievalService
from sillytavern_rpg_engine.domain.models import Audience

@pytest.mark.slow
def test_ten_thousand_turns_retains_seed_memory_and_bounded_prompt(database, settings):
    import os
    from sillytavern_rpg_engine.config import load_settings
    from sillytavern_rpg_engine.llm.scripted import ScriptedLLMClient
    from sillytavern_rpg_engine.llm.client import ChatMessage
    from sillytavern_rpg_engine.orchestration.narrative import build_messages
    from sillytavern_rpg_engine.orchestration.graph import TurnRunner, default_services
    from sillytavern_rpg_engine.services.campaigns import CampaignService
    from sillytavern_rpg_engine.services.memory_events import MemoryEventService
    from sillytavern_rpg_engine.domain.memory import MemoryEventType
    from sillytavern_rpg_engine.domain.models import Audience
    from sillytavern_rpg_engine.services.retrieval import RetrievalQuery, RetrievalService

    turn_count = int(os.environ.get("LONG_RUN_TURNS", "10000"))
    ids = iter(f"lr-{i}" for i in range(turn_count + 50))
    campaigns = CampaignService(database, id_factory=ids.__next__,
                                clock=lambda: "2026-08-14T00:00:00Z")
    campaigns.create_campaign("c1", "Long")
    memory = MemoryEventService(database, campaigns.mutation_engine)
    seed_id = "seed-memory-1"
    memory.record(
        "c1", "main", MemoryEventType.IDENTITY, "龙裔身份确立",
        importance=10, audiences=frozenset({Audience.NARRATOR, Audience.PLAYER_UI}),
        event_id=seed_id,
    )
    settings = load_settings()
    narrator = ScriptedLLMClient()
    runner = TurnRunner(default_services(database, settings, narrator))
    history = []
    for i in range(turn_count):
        narrator.queue(f"回合 {i} 的叙述。")
        history.append({"role": "user", "content": f"行动 {i}"})
        payload = {"campaign_id": "c1", "messages": list(history)}
        result = runner.run(payload)
        history.append({
            "role": "assistant",
            "content": result["choices"][0]["message"]["content"],
        })
        if i % 500 == 0:
            hits = RetrievalService(database).search(
                RetrievalQuery(
                    campaign_id="c1", branch_id="main",
                    text="龙裔", audiences=frozenset({Audience.NARRATOR}),
                    limit=5,
                ),
            )
            assert any(h["id"] == seed_id for h in hits["events"])
            msgs = build_messages(
                system="sys",
                dialogue=[ChatMessage("user", f"行动 {i}"),
                          ChatMessage("assistant", f"回合 {i}")],
                max_history=settings.max_history_messages,
            )
            assert len(msgs) <= settings.max_history_messages + 2
```

- [ ] **Step 3: Run fast subset**

Run: `LONG_RUN_TURNS=200 python -m pytest tests/backend/integration/test_phase6_longrun.py -m slow -v`
Expected: PASS after implementation

- [ ] **Step 4: Run full 10k locally**

Run: `LONG_RUN_TURNS=10000 python -m pytest tests/backend/integration/test_phase6_longrun.py -m slow -v --timeout=600`
Expected: PASS; document runtime in test module docstring.

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "test: add 10k-turn long-run simulation" \
  tests/backend/integration/test_phase6_longrun.py \
  pyproject.toml
```

---

### Task 11: Branch diagnostics admin route

**Files:**
- Modify: `src/sillytavern_rpg_engine/server/app.py`
- Test: `tests/backend/unit/test_api.py` (extend)

**Interfaces:**
- Produces: `GET /admin/campaigns/{campaign_id}/branches` → `{ branches: [{ id, parent_branch_id, status, head: { state_version, latest_turn_id } }], detached_turns: int }`.

- [ ] **Step 1: Write failing API test**

```python
def test_admin_lists_branches(client, seeded_campaign):
    response = client.get("/admin/campaigns/c1/branches")
    assert response.status_code == 200
    assert response.json()["branches"][0]["id"] == "main"
```

- [ ] **Step 2–4: Implement route, verify PASS**

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: expose branch diagnostics admin endpoint" \
  src/sillytavern_rpg_engine/server/app.py \
  tests/backend/unit/test_api.py
```

---

### Task 12: Phase 6 verification gate and README update

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add README section "Branching (Phase 6)"**

Document:
- History-hash lineage (no ST chat IDs).
- Swipe/regenerate creates isolated branches.
- Edit/delete shortens history; backend detaches tail turns.
- RPG Companion shows per-swipe cached Tracker; authoritative state follows backend after next generation.
- `GET /admin/campaigns/{id}/branches` for debugging.

- [ ] **Step 2: Run full verification**

```bash
python -m pytest tests/backend -q
LONG_RUN_TURNS=200 python -m pytest tests/backend/integration/test_phase6_longrun.py -m slow -q
npm run test:run -- tests/rpg-companion-compat
python .harness/scripts/guard.py src/sillytavern_rpg_engine tests/backend
```

Expected: all PASS

- [ ] **Step 3: Commit**

```bash
bash .harness/scripts/committer "docs: document phase 6 branching behavior" README.md
```

---

## Self-Review Checklist

| Spec requirement | Task |
|---|---|
| §14 history hash excludes Tracker JSON | 2, 5, 6 |
| §14 unique parent identification or diagnostic error | 3, 5, 6 |
| §14 new swipe → new branch from parent state | 3, 4, 5, 6, 9 |
| §14 swipe switch restores branch | 4, 5, 6, 8 |
| §14 edit/delete longest prefix + detach (no physical delete) | 5, 6, 9 |
| §12.3 per-swipe display cache, no authoritative panel write | 7, 8 |
| §17.2 swipe branch non-pollution integration | 9 |
| §17.3 10 000-turn simulation | 10 |
| §17.4 acceptance #6 swipe branches isolated | 9 |
| §17.4 acceptance #12 early memory retrievable | 10 |

**Type consistency:** `NormalizedRequest.lineage_hash_before` matches `Turn.lineage_hash_before` and resolver lookup keys. `BranchContext.branch_id` flows through `TurnState` to all services instead of stale `request.branch_id`.

**Deferred to Phase 7:** export/backup rotation, migration failure playbook expansion, Playwright SillyTavern E2E, inventory/quests legacy panel population.
