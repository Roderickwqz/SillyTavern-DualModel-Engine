from pathlib import Path

from sillytavern_rpg_engine.cli import main
from sillytavern_rpg_engine.domain.memory import (
    FactType,
    MemoryEventType,
    SummaryScope,
    TraitTier,
)
from sillytavern_rpg_engine.domain.models import (
    AgeStatus,
    AttributeDefinition,
    AttributeType,
    Audience,
    DisplayType,
    EntityKind,
)
from sillytavern_rpg_engine.persistence.database import Database
from sillytavern_rpg_engine.persistence.migrations import MigrationRunner
from sillytavern_rpg_engine.services.arcs import ArcService
from sillytavern_rpg_engine.services.attributes import (
    DefineAttributeOperation,
    SetAttributeOperation,
)
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)
from sillytavern_rpg_engine.services.facts import AssertFactOperation, FactService
from sillytavern_rpg_engine.services.memory_events import MemoryEventService
from sillytavern_rpg_engine.services.proposals import ProposalService
from sillytavern_rpg_engine.services.retrieval import RetrievalQuery, RetrievalService
from sillytavern_rpg_engine.services.snapshots import SnapshotBuilder
from sillytavern_rpg_engine.services.summaries import SummaryService
from sillytavern_rpg_engine.services.traits import TraitService

ALL = frozenset(Audience)
ENGINE_ONLY = frozenset({Audience.ENGINE})
PLAYER_ONLY = frozenset({Audience.PLAYER_UI})
CLOCK = lambda: "2026-08-10T00:00:00Z"


def _rebuild_snapshot(database: Database, campaign_id: str, branch_id: str) -> dict:
    with database.connect() as connection:
        return SnapshotBuilder().build(connection, campaign_id, branch_id)


def test_phase2_memory_and_personality_survive_restart(tmp_path: Path, capsys):
    path = tmp_path / "world.sqlite3"
    database = Database(path)
    MigrationRunner(database).apply()
    ids = iter(f"flow-{i}" for i in range(60))
    campaigns = CampaignService(database, id_factory=ids.__next__, clock=CLOCK)
    campaigns.create_campaign("story", "长篇战役")
    state = EntityAttributeService(database, campaigns.mutation_engine)
    state.apply_explicit("story", "main", 0, CreateEntityOperation(
        "erin", EntityKind.CHARACTER, "艾琳", AgeStatus.ADULT, ("Erin",)))
    state.apply_explicit("story", "main", 1, DefineAttributeOperation(
        AttributeDefinition("story", "openness", "开放度", "trait",
                            AttributeType.NUMBER, DisplayType.BAR, ALL, 0, 100),
        ("开放性",)))
    state.apply_explicit("story", "main", 2,
                         SetAttributeOperation("erin", "openness", 35, "turn-0"))

    events = MemoryEventService(database, campaigns.mutation_engine,
                                id_factory=ids.__next__, clock=CLOCK)
    traits = TraitService(database, campaigns.mutation_engine,
                          id_factory=ids.__next__, clock=CLOCK)
    summaries = SummaryService(database, campaigns.mutation_engine,
                               id_factory=ids.__next__, clock=CLOCK)
    arcs = ArcService(database, campaigns.mutation_engine,
                      id_factory=ids.__next__, clock=CLOCK)
    events.record("story", "main", 3, type=MemoryEventType.COMMITMENT,
                  content="艾琳承诺保护银月城的村民", importance=4,
                  audiences=ALL, participants=("erin",), turn_id="turn-1")
    traits.record_change("story", "main", 4, entity_id="erin",
                         trait_key="openness", tier=TraitTier.IMPORTANT,
                         delta=8, cause="连续多次主动探索新的关系观念",
                         turn_id="turn-2")
    trait_event_id = traits.history("erin", "openness")[0].id
    arcs.open("story", "main", 5, entity_id="erin", dimension="openness",
              label="主动探索", summary="从拘谨转向主动",
              source_event_ids=(trait_event_id,), start_turn_id="turn-2")
    memory_event_id = events.recent("story", "main", ALL)[0].id
    summaries.upsert("story", "main", 6, scope=SummaryScope.CHARACTER,
                     scope_key="erin", content="谨慎的炼金术师，开始主动探索",
                     audiences=ALL, source_event_ids=(memory_event_id,))
    state.apply_explicit("story", "main", 7, AssertFactOperation(
        fact_id="f-1", entity_id="erin", fact_type=FactType.COMMITMENT,
        fact_key="protect_villagers", content="保护银月城的村民", importance=4,
        audiences=ALL, turn_id="turn-1"))
    events.record("story", "main", 8, type=MemoryEventType.IDENTITY,
                  content="艾琳其实是王国密探", importance=5,
                  audiences=ENGINE_ONLY, participants=("erin",), turn_id="turn-4")

    retrieval_query = RetrievalQuery(
        campaign_id="story", branch_id="main", audiences=ALL,
        text="银月城", scene_entity_ids=("erin",), limit=5,
    )

    proposals = ProposalService(database, campaigns.mutation_engine,
                                id_factory=lambda: "p-flow-1", clock=CLOCK)
    proposals.create("story", "main", {
        "kind": "record_trait_event", "event_id": "t-proposed",
        "entity_id": "erin", "trait_key": "openness", "tier": "normal",
        "delta": 2, "cause": "主动安慰受惊的村民", "turn_id": "turn-3",
        "source": "narrative_development",
    }, reason="剧情表现出进一步开放")
    approved = proposals.approve("p-flow-1", expected_version=9)
    assert approved.snapshot["entities"][0]["attributes"][0]["value"] == 45
    snapshot_before = approved.snapshot

    player_context = RetrievalService(database).assemble(RetrievalQuery(
        campaign_id="story", branch_id="main", audiences=PLAYER_ONLY,
        text="银月城", scene_entity_ids=("erin",), limit=5,
    ))
    engine_context = RetrievalService(database).assemble(RetrievalQuery(
        campaign_id="story", branch_id="main", audiences=ENGINE_ONLY,
        text="银月城", scene_entity_ids=("erin",), limit=5,
    ))
    assert all(
        "王国密探" not in event["content"]
        for event in player_context["recent_events"]
    )
    assert any(
        "王国密探" in event["content"]
        for event in engine_context["recent_events"]
    )
    retrieval_before = RetrievalService(database).assemble(retrieval_query)

    reopened = Database(path)
    snapshot_after = _rebuild_snapshot(reopened, "story", "main")
    assert snapshot_after == snapshot_before
    assert TraitService(reopened).baseline("erin", "openness") == 35
    assert [f.content for f in FactService(reopened).current("story", "erin")] == [
        "保护银月城的村民"
    ]
    assert ArcService(reopened).current("erin", "openness").label == "主动探索"
    retrieval_after = RetrievalService(reopened).assemble(retrieval_query)
    assert retrieval_after == retrieval_before
    context = retrieval_after
    assert context["recent_events"][0]["event_type"] == "identity"
    assert context["recent_events"][0]["content"] == "艾琳其实是王国密探"
    assert context["related_events"][0]["content"] == "艾琳承诺保护银月城的村民"
    assert context["personality"]["erin"]["traits"] == [
        {"key": "openness", "value": 45}
    ]
    assert context["summaries"][0]["source_event_ids"] == [memory_event_id]

    assert main(["rebuild-memory-index", "--database", str(path)]) == 0
    assert "Indexed 2 memory events" in capsys.readouterr().out
