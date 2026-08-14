import pytest

from sillytavern_rpg_engine.domain.errors import BranchResolutionError
from sillytavern_rpg_engine.llm.client import ChatMessage
from sillytavern_rpg_engine.orchestration.normalize import (
    lineage_hash_after,
    lineage_hash_before,
    response_hash,
)
from sillytavern_rpg_engine.persistence.repositories import SnapshotRepository
from sillytavern_rpg_engine.services.branches import BranchService
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.lineage import LineageResolver
from sillytavern_rpg_engine.services.snapshots import SnapshotBuilder
from sillytavern_rpg_engine.services.turns import TurnService

_ids = iter(range(1000))


def _boot(database):
    CampaignService(
        database, id_factory=lambda: f"id-{next(_ids)}",
        clock=lambda: "2026-08-14T00:00:00Z",
    ).create_campaign("c1", "T")


def _record_turn(database, *, branch="main", player="a", response="A", prefix=()):
    msgs = prefix + (ChatMessage("user", player),)
    turns = TurnService(database, id_factory=lambda: f"turn-{next(_ids)}")
    turn = turns.record(
        "c1", branch, intent="action", player_text=player,
        response_text=response, history_hash="audit",
        lineage_hash_before=lineage_hash_before(msgs),
        lineage_hash_after=lineage_hash_after(msgs, response),
        response_hash=response_hash(response),
        state_before_version=0, state_after_version=1,
    )
    with database.connect() as connection:
        connection.execute(
            "DELETE FROM state_snapshots WHERE campaign_id = ? AND branch_id = ?"
            " AND state_version = ?",
            ("c1", branch, turn.state_after_version),
        )
        snapshot = SnapshotBuilder().build(connection, "c1", branch)
        SnapshotRepository().insert(
            connection, "c1", branch, turn.state_after_version, snapshot,
            "2026-08-14T00:00:00Z",
        )
    return turn


def test_fresh_campaign_first_message_resolves_to_main(database):
    _boot(database)
    messages = (ChatMessage("user", "a"),)
    ctx = LineageResolver(database).resolve("c1", messages)
    assert ctx.branch_id == "main"
    assert ctx.parent_turn_id is None
    assert ctx.restored is False


def test_resolve_continues_same_branch(database):
    _boot(database)
    turn = _record_turn(database)
    messages = (
        ChatMessage("user", "a"),
        ChatMessage("assistant", "A"),
        ChatMessage("user", "b"),
    )
    ctx = LineageResolver(database).resolve("c1", messages)
    assert ctx.branch_id == "main"
    assert ctx.parent_turn_id == turn.id
    assert ctx.restored is False


def test_ambiguous_parent_raises(database):
    ids = iter(range(1000))
    campaigns = CampaignService(
        database, id_factory=lambda: f"id-{next(ids)}",
        clock=lambda: "2026-08-14T00:00:00Z",
    )
    campaigns.create_campaign("c1", "T")
    msgs = (ChatMessage("user", "a"),)
    shared_after = lineage_hash_after(msgs, "A")
    turns = TurnService(database, id_factory=lambda: f"turn-{next(ids)}")
    first = turns.record(
        "c1", "main", intent="action", player_text="a",
        response_text="A", history_hash="audit",
        lineage_hash_before=lineage_hash_before(msgs),
        lineage_hash_after=shared_after,
        response_hash=response_hash("A"),
        state_before_version=0, state_after_version=1,
    )
    BranchService(database).fork(
        "c1", "main", first.id, new_branch_id="branch-dup"
    )
    turns.record(
        "c1", "branch-dup", intent="action", player_text="a",
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


def test_ambiguous_root_prefix_raises(database):
    _boot(database)
    first = _record_turn(database, player="a", response="A")
    BranchService(database).fork(
        "c1", "main", first.id, new_branch_id="branch-dup"
    )
    _record_turn(database, branch="branch-dup", player="a", response="A")
    messages = (
        ChatMessage("user", "a"),
        ChatMessage("user", "b"),
        ChatMessage("user", "c"),
    )
    with pytest.raises(BranchResolutionError, match="ambiguous"):
        LineageResolver(database).resolve("c1", messages)


def test_edit_drops_assistant_and_restores_to_prefix(database):
    _boot(database)
    first = _record_turn(database, player="a", response="A")
    second = _record_turn(
        database, player="b", response="B",
        prefix=(ChatMessage("user", "a"), ChatMessage("assistant", "A")),
    )
    messages = (
        ChatMessage("user", "a"),
        ChatMessage("user", "b"),
        ChatMessage("user", "c"),
    )
    ctx = LineageResolver(database).resolve("c1", messages)
    assert ctx.branch_id == "main"
    assert ctx.parent_turn_id == first.id
    assert ctx.restored is True
    with database.connect() as connection:
        statuses = {
            row["id"]: row["status"]
            for row in connection.execute(
                "SELECT id, status FROM turns WHERE campaign_id = 'c1'"
            ).fetchall()
        }
        state_version = connection.execute(
            "SELECT state_version FROM campaigns WHERE id = 'c1'"
        ).fetchone()["state_version"]
    assert statuses == {first.id: "active", second.id: "detached"}
    assert state_version == first.state_after_version


def test_swipe_forks_child_branch(database):
    _boot(database)
    turn = _record_turn(database)
    messages = (
        ChatMessage("user", "a"),
        ChatMessage("assistant", "A-改"),
        ChatMessage("user", "b"),
    )
    ctx = LineageResolver(database).resolve("c1", messages)
    assert ctx.branch_id.startswith("branch-swipe-")
    assert ctx.parent_turn_id is None
    assert ctx.restored is True
    with database.connect() as connection:
        parent = connection.execute(
            "SELECT parent_branch_id FROM branches WHERE campaign_id = 'c1'"
            " AND id = ?",
            (ctx.branch_id,),
        ).fetchone()["parent_branch_id"]
        head = connection.execute(
            "SELECT state_version FROM branch_heads WHERE campaign_id = 'c1'"
            " AND branch_id = ?",
            (ctx.branch_id,),
        ).fetchone()
        snapshot = connection.execute(
            "SELECT 1 FROM state_snapshots WHERE campaign_id = 'c1'"
            " AND branch_id = ? AND state_version = ?",
            (ctx.branch_id, turn.state_before_version),
        ).fetchone()
    assert parent == "main"
    assert head["state_version"] == turn.state_before_version
    assert snapshot is not None


def test_swipe_forks_from_mid_history_parent(database):
    _boot(database)
    first = _record_turn(database, player="a", response="A")
    second = _record_turn(
        database, player="b", response="B",
        prefix=(ChatMessage("user", "a"), ChatMessage("assistant", "A")),
    )
    _record_turn(
        database, player="c", response="C",
        prefix=(
            ChatMessage("user", "a"), ChatMessage("assistant", "A"),
            ChatMessage("user", "b"), ChatMessage("assistant", "B"),
        ),
    )
    messages = (
        ChatMessage("user", "a"),
        ChatMessage("assistant", "A"),
        ChatMessage("user", "b"),
        ChatMessage("assistant", "B-改"),
        ChatMessage("user", "c"),
    )
    ctx = LineageResolver(database).resolve("c1", messages)
    assert ctx.branch_id.startswith("branch-swipe-")
    assert ctx.parent_turn_id is None
    assert ctx.restored is True
    with database.connect() as connection:
        row = connection.execute(
            "SELECT parent_branch_id FROM branches WHERE campaign_id = 'c1'"
            " AND id = ?",
            (ctx.branch_id,),
        ).fetchone()
        head = connection.execute(
            "SELECT latest_turn_id FROM branch_heads WHERE campaign_id = 'c1'"
            " AND branch_id = ?",
            (ctx.branch_id,),
        ).fetchone()
    assert row["parent_branch_id"] == "main"
    assert head["latest_turn_id"] == second.id
    assert first.status == "active"


def test_unmappable_history_raises(database):
    campaigns = CampaignService(
        database, id_factory=lambda: f"id-{next(_ids)}",
        clock=lambda: "2026-08-14T00:00:00Z",
    )
    campaigns.create_campaign("c1", "T")
    messages = (
        ChatMessage("user", "a"),
        ChatMessage("assistant", "X"),
        ChatMessage("user", "b"),
    )
    with pytest.raises(BranchResolutionError, match="cannot map"):
        LineageResolver(database).resolve("c1", messages)
