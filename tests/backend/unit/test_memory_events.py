import pytest

from sillytavern_rpg_engine.domain.errors import NotFoundError, ValidationError
from sillytavern_rpg_engine.domain.memory import MemoryEventType
from sillytavern_rpg_engine.domain.models import AgeStatus, Audience, EntityKind
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)
from sillytavern_rpg_engine.services.memory_events import (
    MemoryEventService,
    MemoryIndexService,
)

ALL = frozenset(Audience)


@pytest.fixture
def seeded(database):
    ids = iter(f"mem-{i}" for i in range(30))
    campaigns = CampaignService(
        database, id_factory=ids.__next__, clock=lambda: "2026-08-10T00:00:00Z"
    )
    campaigns.create_campaign("c1", "Campaign")
    state = EntityAttributeService(database, campaigns.mutation_engine)
    state.apply_explicit("c1", "main", 0, CreateEntityOperation(
        "erin", EntityKind.CHARACTER, "艾琳", AgeStatus.ADULT, ()))
    state.apply_explicit("c1", "main", 1, CreateEntityOperation(
        "silvermoon", EntityKind.LOCATION, "银月城", AgeStatus.UNKNOWN, ()))
    service = MemoryEventService(
        database, campaigns.mutation_engine,
        id_factory=ids.__next__, clock=lambda: "2026-08-10T00:00:00Z",
    )
    return service, state


def test_record_search_and_audience_filter(database, seeded):
    service, _ = seeded
    service.record("c1", "main", 2, type=MemoryEventType.SCENE,
                   content="艾琳在银月城的集市购买稀有药材", importance=3,
                   audiences=ALL, participants=("erin",),
                   location_entity_id="silvermoon", turn_id="turn-1")
    service.record("c1", "main", 3, type=MemoryEventType.IDENTITY,
                   content="艾琳其实是王国密探", importance=5,
                   audiences=frozenset({Audience.ENGINE}),
                   participants=("erin",), turn_id="turn-2")
    hits = service.search("c1", "main", ALL, "银月城")
    assert [event.content for event in hits] == ["艾琳在银月城的集市购买稀有药材"]
    player_hits = service.search("c1", "main", {Audience.PLAYER_UI}, "王国密探")
    assert player_hits == []
    engine_hits = service.search("c1", "main", {Audience.ENGINE}, "王国密探")
    assert engine_hits[0].participants == ("erin",)
    with pytest.raises(ValidationError, match="3 characters"):
        service.search("c1", "main", ALL, "银月")


def test_unknown_participant_and_index_rebuild(database, seeded):
    service, _ = seeded
    with pytest.raises(NotFoundError, match="ghost"):
        service.record("c1", "main", 2, type=MemoryEventType.SCENE,
                       content="不存在的人路过", importance=1, audiences=ALL,
                       participants=("ghost",))
    service.record("c1", "main", 2, type=MemoryEventType.SCENE,
                   content="艾琳在银月城张贴寻人告示", importance=2,
                   audiences=ALL, participants=("erin",))
    with database.transaction() as connection:
        connection.execute("DELETE FROM memory_events_fts")
    assert service.search("c1", "main", ALL, "寻人告示") == []
    assert MemoryIndexService(database).rebuild() == 1
    assert len(service.search("c1", "main", ALL, "寻人告示")) == 1


def test_recent_orders_pinned_before_newer_unpinned(database, seeded):
    service, _ = seeded
    service.record("c1", "main", 2, type=MemoryEventType.COMMITMENT,
                   content="艾琳承诺保护银月城的村民", importance=4,
                   audiences=ALL, participants=("erin",), turn_id="turn-1")
    version = 3
    for index in range(6):
        service.record("c1", "main", version, type=MemoryEventType.SCENE,
                       content=f"日常琐事 {index}", importance=2, audiences=ALL,
                       turn_id=f"turn-{version}")
        version += 1
    recent = service.recent("c1", "main", ALL, limit=3)
    assert recent[0].event_type is MemoryEventType.COMMITMENT
