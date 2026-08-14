import pytest

from sillytavern_rpg_engine.domain.errors import NotFoundError, ValidationError
from sillytavern_rpg_engine.services.branches import BranchService
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.turns import TurnService


def _campaign(database):
    ids = iter(f"id-{i}" for i in range(20))
    campaigns = CampaignService(database, id_factory=ids.__next__,
                                clock=lambda: "2026-08-14T00:00:00Z")
    campaigns.create_campaign("c1", "Test")
    return campaigns


def _turn(database, turn_id="turn-1"):
    turns = TurnService(database, id_factory=lambda: turn_id)
    return turns.record(
        "c1", "main", intent="action", player_text="hi",
        response_text="ok", history_hash="h1",
        state_before_version=0, state_after_version=0,
    )


def test_fork_creates_child_branch_and_head(database):
    _campaign(database)
    turn = _turn(database)
    branches = BranchService(database, id_factory=lambda: "b")
    child = branches.fork("c1", "main", turn.id, new_branch_id="branch-swipe-1")
    assert child == "branch-swipe-1"
    head = branches.get_head("c1", child)
    assert head.latest_turn_id == turn.id


def test_fork_missing_parent_turn_raises_not_found(database):
    _campaign(database)
    with pytest.raises(NotFoundError, match="parent turn"):
        BranchService(database).fork(
            "c1", "main", "no-such-turn", new_branch_id="b1"
        )


def test_fork_existing_branch_id_raises_validation_error(database):
    _campaign(database)
    turn = _turn(database)
    with pytest.raises(ValidationError, match="already exists"):
        BranchService(database).fork(
            "c1", "main", turn.id, new_branch_id="main"
        )


def test_get_head_missing_branch_raises_not_found(database):
    _campaign(database)
    with pytest.raises(NotFoundError, match="branch"):
        BranchService(database).get_head("c1", "no-such-branch")
