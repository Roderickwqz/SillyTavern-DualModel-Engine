import json

from sillytavern_rpg_engine.domain.memory import MemoryEventType, TraitTier
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
from sillytavern_rpg_engine.services.campaign_export import CampaignExporter
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)
from sillytavern_rpg_engine.services.memory_events import MemoryEventService
from sillytavern_rpg_engine.services.traits import TraitService

ALL = frozenset(Audience)


def test_export_v2_includes_definitions_turns_and_memory(database, tmp_path):
    ids = iter(f"id-{i}" for i in range(20))
    campaigns = CampaignService(
        database,
        id_factory=ids.__next__,
        clock=lambda: "2026-08-16T00:00:00Z",
    )
    campaigns.create_campaign("c1", "Export")
    attrs = EntityAttributeService(database, campaigns.mutation_engine)
    attrs.apply_explicit(
        "c1", "main", 0,
        CreateEntityOperation("erin", EntityKind.CHARACTER, "艾琳"),
    )
    output = tmp_path / "c1.json"
    CampaignExporter(database).export("c1", output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["export_schema_version"] == 2
    for key in (
        "attribute_definitions",
        "attribute_values",
        "turns",
        "branch_heads",
        "memory_events",
    ):
        assert key in payload, f"missing {key}"
    assert payload["campaign"]["id"] == "c1"


def test_export_v2_includes_aliases_participants_and_arcs(database, tmp_path):
    ids = iter(f"aux-{i}" for i in range(30))
    campaigns = CampaignService(
        database,
        id_factory=ids.__next__,
        clock=lambda: "2026-08-16T00:00:00Z",
    )
    campaigns.create_campaign("c2", "Export Aux")
    state = EntityAttributeService(database, campaigns.mutation_engine)
    state.apply_explicit("c2", "main", 0, CreateEntityOperation(
        "erin", EntityKind.CHARACTER, "艾琳", AgeStatus.ADULT, (),
    ))
    state.apply_explicit("c2", "main", 1, DefineAttributeOperation(
        AttributeDefinition(
            "c2", "alchemy", "炼金术", "trait", AttributeType.NUMBER,
            DisplayType.BAR, ALL, 0, 100,
        ),
        ("炼金",),
    ))
    state.apply_explicit("c2", "main", 2,
                         SetAttributeOperation("erin", "alchemy", 50, None))
    traits = TraitService(
        database, state.mutation_engine, id_factory=ids.__next__,
        clock=lambda: "2026-08-16T00:00:00Z",
    )
    traits.record_change("c2", "main", 3, entity_id="erin",
                         trait_key="alchemy", tier=TraitTier.IMPORTANT,
                         delta=5, cause="研习配方", turn_id="turn-1")
    trait_event_id = traits.history("erin", "alchemy")[0].id
    MemoryEventService(
        database, state.mutation_engine, id_factory=ids.__next__,
        clock=lambda: "2026-08-16T00:00:00Z",
    ).record("c2", "main", 4, type=MemoryEventType.SCENE,
             content="艾琳练习炼金", importance=2, audiences=ALL,
             participants=("erin",), turn_id="turn-1")
    arcs = ArcService(
        database, state.mutation_engine, id_factory=ids.__next__,
        clock=lambda: "2026-08-16T00:00:00Z",
    )
    arcs.open("c2", "main", 5, entity_id="erin", dimension="alchemy",
              label="炼金学徒", summary="初学炼金",
              source_event_ids=(trait_event_id,), start_turn_id="turn-1")
    arc_id = arcs.current("erin", "alchemy").id

    output = tmp_path / "c2.json"
    CampaignExporter(database).export("c2", output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    aliases = payload["attribute_aliases"]
    assert len(aliases) == 1
    assert aliases[0] == {
        "campaign_id": "c2",
        "attribute_key": "alchemy",
        "alias": "炼金",
        "normalized_alias": "炼金",
    }
    participants = payload["memory_event_participants"]
    assert len(participants) == 1
    assert participants[0]["campaign_id"] == "c2"
    assert participants[0]["event_id"]
    assert participants[0]["entity_id"] == "erin"
    exported_arcs = payload["development_arcs"]
    assert len(exported_arcs) == 1
    assert exported_arcs[0]["id"] == arc_id
    assert exported_arcs[0]["source_event_ids"] == [trait_event_id]
    assert exported_arcs[0]["entity_id"] == "erin"
