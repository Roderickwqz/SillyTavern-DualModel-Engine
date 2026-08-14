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
