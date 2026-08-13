import pytest

from sillytavern_rpg_engine.domain.errors import NotFoundError
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.turns import TurnService


def _turns(database):
    campaigns = CampaignService(database, id_factory=lambda: "cid",
                                clock=lambda: "2026-08-13T00:00:00Z")
    campaigns.create_campaign("c1", "测试")
    ids = iter(f"turn-{n}" for n in range(1, 100))
    return TurnService(
        database,
        id_factory=lambda: next(ids),
        clock=lambda: "2026-08-13T01:00:00Z",
    )


def test_record_links_parent_and_reads_back(database):
    turns = _turns(database)
    first = turns.record(
        "c1", "main", intent="action", player_text="开始",
        response_text="世界展开。", history_hash="a" * 64,
        state_before_version=0, state_after_version=3,
    )
    second = turns.record(
        "c1", "main", intent="query", player_text="查询状态",
        response_text="……", history_hash="b" * 64,
        state_before_version=3, state_after_version=3,
    )
    assert first.parent_turn_id is None
    assert second.parent_turn_id == "turn-1"
    assert turns.latest("c1", "main").id == "turn-2"
    assert turns.get("turn-1").player_text == "开始"
    with pytest.raises(NotFoundError):
        turns.get("nope")


def test_record_validates_version_order_and_text(database):
    turns = _turns(database)
    with pytest.raises(Exception, match="state_after_version"):
        turns.record(
            "c1", "main", intent="action", player_text="x",
            response_text="y", history_hash="h",
            state_before_version=5, state_after_version=4,
        )
    with pytest.raises(Exception, match="player_text"):
        turns.record(
            "c1", "main", intent="action", player_text="  ",
            response_text="y", history_hash="h",
            state_before_version=0, state_after_version=0,
        )
