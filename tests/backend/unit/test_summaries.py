import pytest

from sillytavern_rpg_engine.domain.errors import ValidationError
from sillytavern_rpg_engine.domain.memory import MemoryEventType, SummaryScope
from sillytavern_rpg_engine.domain.models import AgeStatus, Audience, EntityKind
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)
from sillytavern_rpg_engine.services.memory_events import MemoryEventService
from sillytavern_rpg_engine.services.summaries import SummaryService

ALL = frozenset(Audience)


@pytest.fixture
def seeded(database):
    ids = iter(f"sum-{i}" for i in range(30))
    campaigns = CampaignService(
        database, id_factory=ids.__next__, clock=lambda: "2026-08-10T00:00:00Z"
    )
    campaigns.create_campaign("c1", "Campaign")
    state = EntityAttributeService(database, campaigns.mutation_engine)
    state.apply_explicit("c1", "main", 0, CreateEntityOperation(
        "erin", EntityKind.CHARACTER, "艾琳", AgeStatus.ADULT, ()))
    events = MemoryEventService(
        database, campaigns.mutation_engine,
        id_factory=ids.__next__, clock=lambda: "2026-08-10T00:00:00Z",
    )
    events.record("c1", "main", 1, type=MemoryEventType.SCENE,
                  content="艾琳在银月城的集市购买稀有药材", importance=3,
                  audiences=ALL, participants=("erin",), turn_id="turn-1")
    summaries = SummaryService(
        database, campaigns.mutation_engine,
        id_factory=ids.__next__, clock=lambda: "2026-08-10T00:00:00Z",
    )
    return summaries, events


def test_summary_upsert_and_provenance(database, seeded):
    summaries, events = seeded
    event_id = events.recent("c1", "main", ALL)[0].id
    with pytest.raises(ValidationError, match="unknown memory events"):
        summaries.upsert("c1", "main", 2, scope=SummaryScope.CHARACTER,
                         scope_key="erin", content="半精灵炼金术师",
                         audiences=ALL, source_event_ids=("missing",))
    summaries.upsert("c1", "main", 2, scope=SummaryScope.CHARACTER,
                     scope_key="erin", content="半精灵炼金术师，谨慎",
                     audiences=ALL, source_event_ids=(event_id,))
    first = summaries.get(SummaryScope.CHARACTER, "erin", "c1", "main")
    assert first.source_event_ids == (event_id,)
    assert first.state_version == 3
    summaries.upsert("c1", "main", 3, scope=SummaryScope.CHARACTER,
                     scope_key="erin", content="谨慎但开始主动探索",
                     audiences=ALL, source_event_ids=(event_id,))
    updated = summaries.get(SummaryScope.CHARACTER, "erin", "c1", "main")
    assert updated.content == "谨慎但开始主动探索"
    assert updated.state_version == 4
    assert len(summaries.list("c1", "main")) == 1
