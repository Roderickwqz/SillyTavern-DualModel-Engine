"""Phase 6 integration tests: swipe forks, edit detaches, branch isolation."""

import json

from fastapi.testclient import TestClient

from sillytavern_rpg_engine.config import ModelConfig, Settings
from sillytavern_rpg_engine.domain.models import (
    AttributeDefinition,
    AttributeType,
    Audience,
    DisplayType,
    EntityKind,
)
from sillytavern_rpg_engine.llm.scripted import ScriptedLLMClient
from sillytavern_rpg_engine.server.app import create_app
from sillytavern_rpg_engine.services.attributes import DefineAttributeOperation
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation, EntityAttributeService,
)

NARRATOR_CFG = ModelConfig("http://x/v1", "", "narrator-model", 0.8, 5.0, 512)

_SET_OP = (
    '{"kind": "set_attribute", "entity_id": "erin", "attribute_key": "alchemy",'
    ' "value": %s, "turn_id": null}'
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


def _client(database, narrator):
    settings = Settings(
        database_path=":memory:", host="127.0.0.1", port=8000,
        max_history_messages=40, narrator=NARRATOR_CFG, critic=None,
    )
    return TestClient(create_app(settings, database, narrator))


def _chat(client, history, text):
    """Post one turn with the full echoed history; return the content."""
    history.append({"role": "user", "content": text})
    response = client.post("/v1/chat/completions", json={
        "campaign_id": "c1",
        "messages": history,
    })
    assert response.status_code == 200, response.text
    content = response.json()["choices"][0]["message"]["content"]
    history.append({"role": "assistant", "content": content})
    return content


def test_swipe_fork_isolates_attribute_changes(database):
    _world(database)
    narrator = ScriptedLLMClient([
        json.dumps({"operations": [json.loads(_SET_OP % 40)]}),  # turn 1 extract
        json.dumps({"operations": [json.loads(_SET_OP % 35)]}),  # turn 2 extract
        "叙述三",                                               # swipe narrate
        json.dumps({"operations": [json.loads(_SET_OP % 60)]}),  # swipe extract
    ])
    client = _client(database, narrator)

    history: list[dict] = []
    content = _chat(client, history, "把艾琳的炼金术调整为40")
    assert "已应用 1 项变更" in content
    content = _chat(client, history, "把艾琳的炼金术调整为35")
    assert "已应用 1 项变更" in content

    # Swipe turn 1: replace its reply in the visible history, then continue.
    history = [
        {"role": "user", "content": "把艾琳的炼金术调整为40"},
        {"role": "assistant", "content": "艾琳没有抬头。"},
        {"role": "user", "content": "把艾琳的炼金术调整为35"},
        {"role": "assistant", "content": "艾琳把瓶子放回架上。"},
    ]
    content = _chat(client, history, "我继续深入炼金铺。")
    assert "叙述三" in content

    with database.connect() as connection:
        child = connection.execute(
            "SELECT id, parent_branch_id FROM branches"
            " WHERE id LIKE 'branch-swipe-%'"
        ).fetchone()
        assert child is not None
        assert child["parent_branch_id"] == "main"
        assert connection.execute(
            "SELECT COUNT(DISTINCT branch_id) FROM turns"
        ).fetchone()[0] >= 2

        turn1 = connection.execute(
            "SELECT id, state_before_version FROM turns"
            " WHERE player_text = '把艾琳的炼金术调整为40'"
        ).fetchone()
        child_head = connection.execute(
            "SELECT state_version FROM branch_heads WHERE branch_id = ?",
            (child["id"],),
        ).fetchone()["state_version"]
        main_head = connection.execute(
            "SELECT state_version FROM branch_heads WHERE branch_id = 'main'"
        ).fetchone()["state_version"]
        assert child_head == turn1["state_before_version"]
        assert main_head == 4

        main_snap = json.loads(connection.execute(
            "SELECT snapshot_json FROM state_snapshots"
            " WHERE branch_id = 'main' AND state_version = 4"
        ).fetchone()["snapshot_json"])
        child_snap = json.loads(connection.execute(
            "SELECT snapshot_json FROM state_snapshots"
            " WHERE branch_id = ? AND state_version = ?",
            (child["id"], child_head),
        ).fetchone()["snapshot_json"])
        main_attrs = {
            a["key"]: a["value"]
            for a in next(e for e in main_snap["entities"]
                          if e["id"] == "erin")["attributes"]
        }
        child_attrs = {
            a["key"]: a["value"]
            for a in next(e for e in child_snap["entities"]
                          if e["id"] == "erin")["attributes"]
        }
        assert main_attrs["alchemy"] == 35
        assert "alchemy" not in child_attrs

        pending = connection.execute(
            "SELECT branch_id FROM pending_proposals WHERE status = 'pending'"
        ).fetchall()
        assert [row["branch_id"] for row in pending] == [child["id"]]


def test_edit_shortens_history_and_detaches_tail(database):
    _world(database)
    narrator = ScriptedLLMClient([
        "叙述一", json.dumps({"operations": []}),
        "叙述二", json.dumps({"operations": []}),
        "叙述三", json.dumps({"operations": []}),
        "叙述四", json.dumps({"operations": []}),
    ])
    client = _client(database, narrator)

    history: list[dict] = []
    _chat(client, history, "我走进炼金铺。")
    _chat(client, history, "我拿起一瓶药剂。")

    # Edit: assistant replies dropped, a new user turn appended.
    history = [
        {"role": "user", "content": "我走进炼金铺。"},
        {"role": "user", "content": "我拿起一瓶药剂。"},
    ]
    content = _chat(client, history, "我放下药剂。")
    assert "叙述三" in content

    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM turns WHERE status = 'detached'"
        ).fetchone()[0] >= 1
        turn1 = connection.execute(
            "SELECT id FROM turns WHERE player_text = '我走进炼金铺。'"
        ).fetchone()
        turn3 = connection.execute(
            "SELECT id, status, parent_turn_id, lineage_hash_after"
            " FROM turns WHERE player_text = '我放下药剂。'"
        ).fetchone()
        assert turn3["status"] == "active"
        assert turn3["parent_turn_id"] == turn1["id"]

    # Continuation with the new history must match turn 三 exactly.
    content = _chat(client, history, "我看向窗外。")
    assert "叙述四" in content

    with database.connect() as connection:
        detached = connection.execute(
            "SELECT COUNT(*) FROM turns WHERE status = 'detached'"
        ).fetchone()[0]
        assert detached == 1
        turn4 = connection.execute(
            "SELECT branch_id, parent_turn_id, lineage_hash_before"
            " FROM turns WHERE player_text = '我看向窗外。'"
        ).fetchone()
        assert turn4["branch_id"] == "main"
        assert turn4["parent_turn_id"] == turn3["id"]
        assert turn4["lineage_hash_before"] == turn3["lineage_hash_after"]


def test_parallel_branches_keep_independent_pending_proposals(database):
    _world(database)
    narrator = ScriptedLLMClient([
        "叙述一", json.dumps({"operations": [json.loads(_SET_OP % 40)]}),
        "叙述二", json.dumps({"operations": [json.loads(_SET_OP % 60)]}),
    ])
    client = _client(database, narrator)

    history: list[dict] = []
    content = _chat(client, history, "我让艾琳教我炼金术。")
    assert "叙述一" in content
    with database.connect() as connection:
        main_pending = connection.execute(
            "SELECT operation_json FROM pending_proposals"
            " WHERE branch_id = 'main' AND status = 'pending'"
        ).fetchall()
        assert len(main_pending) == 1
        assert json.loads(main_pending[0]["operation_json"])["value"] == 40

    # Swipe turn 1 with an alternate reply; the child must not inherit
    # main's pending proposal.
    history = [
        {"role": "user", "content": "我让艾琳教我炼金术。"},
        {"role": "assistant", "content": "艾琳没有抬头。"},
    ]
    content = _chat(client, history, "我继续观察。")
    assert "叙述二" in content

    with database.connect() as connection:
        child = connection.execute(
            "SELECT id FROM branches WHERE id LIKE 'branch-swipe-%'"
        ).fetchone()["id"]
        child_pending = connection.execute(
            "SELECT operation_json FROM pending_proposals"
            " WHERE branch_id = ? AND status = 'pending'",
            (child,),
        ).fetchall()
        assert len(child_pending) == 1
        assert json.loads(child_pending[0]["operation_json"])["value"] == 60
        assert connection.execute(
            "SELECT COUNT(*) FROM pending_proposals"
            " WHERE branch_id = 'main' AND status = 'pending'"
        ).fetchone()[0] == 1


def test_delete_tail_detaches_and_stales_its_proposal(database):
    """Deleting the tail (turn 2's applied change + turn 3's proposal) must
    detach both turns, roll state back to turn 1, and mark the deleted
    turn's Pending proposal Stale so the resumed state stays clean."""
    _world(database)
    narrator = ScriptedLLMClient([
        "叙述一", json.dumps({"operations": []}),                      # turn 1: no-op action
        json.dumps({"operations": [json.loads(_SET_OP % 40)]}),       # turn 2: applied change
        "叙述三", json.dumps({"operations": [json.loads(_SET_OP % 50)]}),  # turn 3: proposal
        "叙述四", json.dumps({"operations": []}),                      # turn 4: continuation
        "叙述五", json.dumps({"operations": []}),                      # turn 5: exact-match
    ])
    client = _client(database, narrator)

    history: list[dict] = []
    content = _chat(client, history, "我走进炼金铺。")
    assert "叙述一" in content
    content = _chat(client, history, "把艾琳的炼金术调整为40")
    assert "已应用 1 项变更" in content
    content = _chat(client, history, "我让艾琳教我炼金术。")
    assert "记录 1 项待确认提案" in content

    with database.connect() as connection:
        turn1 = connection.execute(
            "SELECT id FROM turns WHERE player_text = '我走进炼金铺。'"
        ).fetchone()
        pending = connection.execute(
            "SELECT status, base_state_version FROM pending_proposals"
            " WHERE branch_id = 'main' AND status = 'pending'"
        ).fetchall()
        assert len(pending) == 1
        assert pending[0]["base_state_version"] == 3

    # Delete turns 2 and 3: visible history ends at turn 1.
    history = [{"role": "user", "content": "我走进炼金铺。"}]
    content = _chat(client, history, "我环顾四周。")
    assert "叙述四" in content

    with database.connect() as connection:
        detached = connection.execute(
            "SELECT COUNT(*) FROM turns WHERE status = 'detached'"
        ).fetchone()[0]
        assert detached == 2
        turn4 = connection.execute(
            "SELECT id, status, parent_turn_id FROM turns"
            " WHERE player_text = '我环顾四周。'"
        ).fetchone()
        assert turn4["status"] == "active"
        assert turn4["parent_turn_id"] == turn1["id"]
        stale = connection.execute(
            "SELECT status, resolved_at FROM pending_proposals"
            " WHERE branch_id = 'main' AND base_state_version = 3"
        ).fetchone()
        assert stale["status"] == "stale"
        assert stale["resolved_at"] is not None
        assert connection.execute(
            "SELECT COUNT(*) FROM pending_proposals"
            " WHERE branch_id = 'main' AND status = 'pending'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM attribute_values"
            " WHERE entity_id = 'erin' AND attribute_key = 'alchemy'"
        ).fetchone()[0] == 0

    # Continuation with the new full history matches exactly; no new detaches.
    history.append({"role": "assistant", "content": content})
    content = _chat(client, history, "我看向窗外。")
    assert "叙述五" in content

    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM turns WHERE status = 'detached'"
        ).fetchone()[0] == 2
        turn5 = connection.execute(
            "SELECT branch_id, parent_turn_id FROM turns"
            " WHERE player_text = '我看向窗外。'"
        ).fetchone()
        assert turn5["branch_id"] == "main"
        assert turn5["parent_turn_id"] == turn4["id"]
