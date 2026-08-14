from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.turns import TurnService


def _setup(database):
    campaigns = CampaignService(
        database,
        id_factory=lambda: "cid",
        clock=lambda: "2026-08-14T00:00:00Z",
    )
    campaigns.create_campaign("c1", "测试")
    ids = iter(f"turn-{n}" for n in range(1, 100))
    return TurnService(
        database,
        id_factory=lambda: next(ids),
        clock=lambda: "2026-08-14T01:00:00Z",
    )


def _record(turns, *, history_hash="h", lineage_after=None):
    return turns.record(
        "c1", "main", intent="action", player_text="开始",
        response_text="世界展开。", history_hash=history_hash,
        lineage_hash_before="before-" + history_hash,
        lineage_hash_after=lineage_after or ("after-" + history_hash),
        response_hash="resp-" + history_hash,
        state_before_version=0, state_after_version=1,
    )


def test_record_stores_lineage_fields(database):
    turns = _setup(database)
    turn = _record(turns, history_hash="a" * 64)
    assert turn.lineage_hash_before == "before-" + "a" * 64
    assert turn.lineage_hash_after == "after-" + "a" * 64
    assert turn.response_hash == "resp-" + "a" * 64
    assert turn.status == "active"
    fetched = turns.get(turn.id)
    assert fetched.lineage_hash_before == turn.lineage_hash_before
    assert fetched.lineage_hash_after == turn.lineage_hash_after
    assert fetched.response_hash == turn.response_hash
    assert fetched.status == "active"
    assert turns.latest("c1", "main").status == "active"


def test_record_defaults_lineage_fields_when_omitted(database):
    turns = _setup(database)
    turn = turns.record(
        "c1", "main", intent="action", player_text="开始",
        response_text="世界展开。", history_hash="h",
        state_before_version=0, state_after_version=1,
    )
    assert turn.lineage_hash_before is None
    assert turn.lineage_hash_after is None
    assert turn.response_hash is None
    assert turn.status == "active"
    assert turns.get(turn.id).status == "active"


def test_find_by_lineage_after_ignores_detached(database):
    turns = _setup(database)
    shared = "shared-after"
    first = _record(turns, history_hash="h1", lineage_after=shared)
    _record(turns, history_hash="h2", lineage_after=shared)
    assert [t.id for t in turns.find_by_lineage_after("c1", shared)] == [
        "turn-2", "turn-1",
    ]
    turns.detach_after("c1", "main", first.id)
    found = turns.find_by_lineage_after("c1", shared)
    assert [t.id for t in found] == ["turn-1"]
    assert all(t.status == "active" for t in found)


def test_find_by_lineage_after_returns_empty_when_none(database):
    turns = _setup(database)
    assert turns.find_by_lineage_after("c1", "missing") == []


def test_find_by_lineage_before_filters_by_hash_and_status(database):
    turns = _setup(database)
    first = _record(turns, history_hash="h1")
    _record(turns, history_hash="h2")
    found = turns.find_by_lineage_before("c1", "before-h1")
    assert [t.id for t in found] == ["turn-1"]
    assert turns.find_by_lineage_before("c1", "missing") == []
    turns.detach_after("c1", "main", first.id)
    assert turns.find_by_lineage_before("c1", "before-h2") == []


def test_detach_after_marks_later_turns(database):
    turns = _setup(database)
    first = _record(turns, history_hash="h1")
    second = _record(turns, history_hash="h2")
    third = _record(turns, history_hash="h3")
    rowcount = turns.detach_after("c1", "main", second.id)
    assert rowcount == 1
    assert turns.get(first.id).status == "active"
    assert turns.get(second.id).status == "active"
    assert turns.get(third.id).status == "detached"
