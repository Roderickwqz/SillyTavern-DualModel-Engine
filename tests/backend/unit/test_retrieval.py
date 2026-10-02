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
from sillytavern_rpg_engine.services.facts import AssertFactOperation
from sillytavern_rpg_engine.services.memory_events import MemoryEventService
from sillytavern_rpg_engine.services.retrieval import RetrievalQuery, RetrievalService
from sillytavern_rpg_engine.services.summaries import SummaryService
from sillytavern_rpg_engine.services.traits import TraitService

ALL = frozenset(Audience)


def build_world(database):
    """Seed one campaign and return helpers that track the state version."""
    ids = iter(f"ret-{i}" for i in range(200))
    clock = lambda: "2026-08-10T00:00:00Z"
    campaigns = CampaignService(database, id_factory=ids.__next__, clock=clock)
    campaigns.create_campaign("c1", "Campaign")
    state = EntityAttributeService(database, campaigns.mutation_engine)
    events = MemoryEventService(database, campaigns.mutation_engine,
                                id_factory=ids.__next__, clock=clock)
    traits = TraitService(database, campaigns.mutation_engine,
                          id_factory=ids.__next__, clock=clock)
    summaries = SummaryService(database, campaigns.mutation_engine,
                               id_factory=ids.__next__, clock=clock)
    arcs = ArcService(database, campaigns.mutation_engine,
                      id_factory=ids.__next__, clock=clock)
    version = 0

    def advance(result):
        nonlocal version
        version = result.state_version
        return result

    def apply(operation):
        return advance(state.apply_explicit("c1", "main", version, operation))

    def record(**kwargs):
        return advance(events.record("c1", "main", version, **kwargs))

    def change(**kwargs):
        return advance(traits.record_change("c1", "main", version, **kwargs))

    def open_arc(**kwargs):
        return advance(arcs.open("c1", "main", version, **kwargs))

    def upsert(**kwargs):
        return advance(summaries.upsert("c1", "main", version, **kwargs))

    apply(CreateEntityOperation("erin", EntityKind.CHARACTER, "艾琳", AgeStatus.ADULT, ()))
    apply(DefineAttributeOperation(AttributeDefinition(
        "c1", "openness", "开放度", "trait", AttributeType.NUMBER,
        DisplayType.BAR, ALL, 0, 100), ()))
    apply(SetAttributeOperation("erin", "openness", 35, "turn-0"))
    helpers = {
        "apply": apply, "record": record, "change": change,
        "open_arc": open_arc, "upsert": upsert,
    }
    return events, traits, helpers


def test_assemble_filters_audience_and_pins_commitments(database):
    events, traits, helpers = build_world(database)
    helpers["record"](type=MemoryEventType.COMMITMENT,
                      content="艾琳承诺保护银月城的村民", importance=4,
                      audiences=ALL, participants=("erin",), turn_id="turn-1")
    helpers["record"](type=MemoryEventType.IDENTITY,
                      content="艾琳其实是王国密探", importance=5,
                      audiences=frozenset({Audience.ENGINE}),
                      participants=("erin",), turn_id="turn-2")
    for index in range(8):
        helpers["record"](type=MemoryEventType.SCENE,
                          content=f"集市日常琐事编号 {index}",
                          importance=1, audiences=ALL, turn_id=f"noise-{index}")
    helpers["change"](entity_id="erin", trait_key="openness",
                      tier=TraitTier.NORMAL, delta=2,
                      cause="主动询问村民近况", turn_id="turn-3")
    trait_event_id = traits.history("erin", "openness")[0].id
    helpers["open_arc"](entity_id="erin", dimension="openness",
                        label="主动探索", summary="开始关心他人",
                        source_event_ids=(trait_event_id,), start_turn_id="turn-3")
    commitment_event_id = events.recent("c1", "main", ALL)[0].id
    helpers["upsert"](scope=SummaryScope.CHARACTER, scope_key="erin",
                      content="谨慎的炼金术师，开始主动关心他人",
                      audiences=ALL, source_event_ids=(commitment_event_id,))
    helpers["apply"](AssertFactOperation(
        fact_id="f-1", entity_id="erin", fact_type=FactType.COMMITMENT,
        fact_key="protect_villagers", content="保护银月城的村民", importance=4,
        audiences=ALL, turn_id="turn-1"))

    player = RetrievalService(database).assemble(RetrievalQuery(
        campaign_id="c1", branch_id="main",
        audiences=frozenset({Audience.PLAYER_UI}),
        text="银月城", scene_entity_ids=("erin",), limit=3,
    ))
    assert player["current_state"]["entities"][0]["name"] == "艾琳"
    assert player["recent_events"][0]["content"] == "艾琳承诺保护银月城的村民"
    assert all(
        "王国密探" not in event["content"] for event in player["recent_events"]
    )
    assert player["related_events"][0]["content"] == "艾琳承诺保护银月城的村民"
    assert player["scene_events"]
    assert player["open_commitments"][0]["content"] == "保护银月城的村民"
    personality = player["personality"]["erin"]
    assert personality["traits"] == [{"key": "openness", "value": 37}]
    assert personality["recent_trait_events"][0]["cause"] == "主动询问村民近况"
    assert personality["open_arcs"][0]["label"] == "主动探索"
    assert player["summaries"][0]["source_event_ids"] == [commitment_event_id]

    engine = RetrievalService(database).assemble(RetrievalQuery(
        campaign_id="c1", branch_id="main",
        audiences=frozenset({Audience.ENGINE}), limit=5,
    ))
    assert any(
        "王国密探" in event["content"] for event in engine["recent_events"]
    )


def test_narrator_context_excludes_engine_only_attributes(database):
    """Narrator retrieval must not surface ENGINE-only attribute values."""
    ids = iter(f"ret-{i}" for i in range(20))
    campaigns = CampaignService(database, id_factory=ids.__next__,
                                clock=lambda: "2026-08-10T00:00:00Z")
    campaigns.create_campaign("c1", "Campaign")
    state = EntityAttributeService(database, campaigns.mutation_engine)
    state.apply_explicit("c1", "main", 0, CreateEntityOperation(
        "erin", EntityKind.CHARACTER, "艾琳",
    ))
    state.apply_explicit("c1", "main", 1, DefineAttributeOperation(AttributeDefinition(
        "c1", "secret_rank", "密级", "meta", AttributeType.NUMBER,
        DisplayType.TEXT, frozenset({Audience.ENGINE}), 0, 10,
    )))
    state.apply_explicit("c1", "main", 2, DefineAttributeOperation(AttributeDefinition(
        "c1", "alchemy", "炼金", "skill", AttributeType.NUMBER,
        DisplayType.BAR, frozenset({Audience.NARRATOR, Audience.PLAYER_UI}),
        0, 100,
    )))
    state.apply_explicit("c1", "main", 3, SetAttributeOperation(
        "erin", "secret_rank", 9, None,
    ))
    state.apply_explicit("c1", "main", 4, SetAttributeOperation(
        "erin", "alchemy", 42, None,
    ))

    narrator = RetrievalService(database).assemble(RetrievalQuery(
        campaign_id="c1", branch_id="main",
        audiences=frozenset({Audience.NARRATOR}),
        scene_entity_ids=("erin",),
    ))
    attrs = {
        item["key"]: item["value"]
        for item in narrator["current_state"]["entities"][0]["attributes"]
    }
    assert "alchemy" in attrs
    assert attrs["alchemy"] == 42
    assert "secret_rank" not in attrs

    leaked = RetrievalService(database).assemble(RetrievalQuery(
        campaign_id="c1", branch_id="main",
        audiences=frozenset({Audience.ENGINE, Audience.NARRATOR}),
        scene_entity_ids=("erin",),
    ))
    leaked_attrs = {
        item["key"]: item["value"]
        for item in leaked["current_state"]["entities"][0]["attributes"]
    }
    assert leaked_attrs["secret_rank"] == 9


def test_assemble_skips_fts_for_short_text(database):
    """Short player text (<3 chars) must not call memory_events.search."""
    build_world(database)
    context = RetrievalService(database).assemble(RetrievalQuery(
        campaign_id="c1", branch_id="main",
        audiences=frozenset({Audience.PLAYER_UI}),
        text="继续", scene_entity_ids=("erin",), limit=3,
    ))
    assert context["related_events"] == []
    assert context["current_state"]["entities"][0]["name"] == "艾琳"
