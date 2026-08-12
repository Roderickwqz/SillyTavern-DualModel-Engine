import pytest

from sillytavern_rpg_engine.domain.errors import ValidationError
from sillytavern_rpg_engine.domain.memory import TraitTier
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
from sillytavern_rpg_engine.services.traits import TraitService

ALL = frozenset(Audience)


@pytest.fixture
def seeded(database):
    ids = iter(f"arc-{i}" for i in range(40))
    campaigns = CampaignService(
        database, id_factory=ids.__next__, clock=lambda: "2026-08-10T00:00:00Z"
    )
    campaigns.create_campaign("c1", "Campaign")
    state = EntityAttributeService(database, campaigns.mutation_engine)
    state.apply_explicit("c1", "main", 0, CreateEntityOperation(
        "erin", EntityKind.CHARACTER, "艾琳", AgeStatus.ADULT, ()))
    state.apply_explicit("c1", "main", 1, DefineAttributeOperation(
        AttributeDefinition("c1", "openness", "开放度", "trait",
                            AttributeType.NUMBER, DisplayType.BAR, ALL, 0, 100),
        (),
    ))
    state.apply_explicit("c1", "main", 2,
                         SetAttributeOperation("erin", "openness", 35, "turn-0"))
    traits = TraitService(
        database, campaigns.mutation_engine,
        id_factory=ids.__next__, clock=lambda: "2026-08-10T00:00:00Z",
    )
    traits.record_change("c1", "main", 3, entity_id="erin", trait_key="openness",
                         tier=TraitTier.MAJOR, delta=8, cause="首次主动袒露过去",
                         turn_id="turn-1")
    return state, traits, ids


def test_open_arc_closes_previous_and_validates_provenance(database, seeded):
    state, traits, ids = seeded
    event_id = traits.history("erin", "openness")[0].id
    arcs = ArcService(database, state.mutation_engine, id_factory=ids.__next__,
                      clock=lambda: "2026-08-10T00:00:00Z")
    with pytest.raises(ValidationError, match="unknown trait events"):
        arcs.open("c1", "main", 4, entity_id="erin", dimension="openness",
                  label="好奇与矛盾", summary="开始动摇",
                  source_event_ids=("missing-event",), start_turn_id="turn-1")
    arcs.open("c1", "main", 4, entity_id="erin", dimension="openness",
              label="好奇与矛盾", summary="开始动摇",
              source_event_ids=(event_id,), start_turn_id="turn-1")
    first = arcs.current("erin", "openness")
    assert first.label == "好奇与矛盾"
    arcs.open("c1", "main", 5, entity_id="erin", dimension="openness",
              label="主动探索", summary="愿意尝试新关系",
              source_event_ids=(event_id,), start_turn_id="turn-9")
    arcs_list = arcs.list("erin", "openness")
    assert [arc.label for arc in arcs_list] == ["好奇与矛盾", "主动探索"]
    assert arcs_list[0].closed_state_version == 6
    assert arcs_list[0].end_turn_id == "turn-9"
    assert arcs.current("erin", "openness").label == "主动探索"


def test_close_arc(database, seeded):
    state, traits, ids = seeded
    event_id = traits.history("erin", "openness")[0].id
    arcs = ArcService(database, state.mutation_engine, id_factory=ids.__next__,
                      clock=lambda: "2026-08-10T00:00:00Z")
    arcs.open("c1", "main", 4, entity_id="erin", dimension="openness",
              label="好奇与矛盾", summary="开始动摇",
              source_event_ids=(event_id,), start_turn_id="turn-1")
    arc_id = arcs.current("erin", "openness").id
    arcs.close("c1", "main", 5, arc_id=arc_id, end_turn_id="turn-12")
    assert arcs.current("erin", "openness") is None
    with pytest.raises(ValidationError, match="not open"):
        arcs.close("c1", "main", 6, arc_id=arc_id)
