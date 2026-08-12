import pytest

from sillytavern_rpg_engine.domain.errors import NotFoundError, ValidationError
from sillytavern_rpg_engine.domain.memory import TraitTier
from sillytavern_rpg_engine.domain.models import (
    AgeStatus,
    AttributeDefinition,
    AttributeType,
    Audience,
    DisplayType,
    EntityKind,
)
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
    ids = iter(f"trait-{i}" for i in range(40))
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
    service = TraitService(
        database, campaigns.mutation_engine,
        id_factory=ids.__next__, clock=lambda: "2026-08-10T00:00:00Z",
    )
    return service, state


def test_trait_event_updates_value_and_baseline(database, seeded):
    service, _ = seeded
    result = service.record_change(
        "c1", "main", 3, entity_id="erin", trait_key="openness",
        tier=TraitTier.IMPORTANT, delta=8, cause="连续多次主动探索新的关系观念",
        turn_id="turn-182",
    )
    assert result.snapshot["entities"][0]["attributes"][0]["value"] == 43
    history = service.history("erin", "openness")
    assert (history[0].before, history[0].delta, history[0].after) == (35, 8, 43)
    assert service.baseline("erin", "openness") == 35


def test_caps_inertia_duplicates_and_bounds(database, seeded):
    service, state = seeded
    with pytest.raises(ValidationError, match="cap"):
        service.record_change("c1", "main", 3, entity_id="erin",
                              trait_key="openness", tier=TraitTier.NORMAL,
                              delta=4, cause="小幅好奇", turn_id="t-1")
    service.record_change("c1", "main", 3, entity_id="erin", trait_key="openness",
                          tier=TraitTier.MAJOR, delta=20, cause="重大转折",
                          turn_id="t-2")
    with pytest.raises(ValidationError, match="inertia"):
        service.record_change("c1", "main", 4, entity_id="erin",
                              trait_key="openness", tier=TraitTier.IMPORTANT,
                              delta=8, cause="再次波动", turn_id="t-3")
    with pytest.raises(ValidationError, match="duplicate"):
        service.record_change("c1", "main", 4, entity_id="erin",
                              trait_key="openness", tier=TraitTier.MAJOR,
                              delta=20, cause="重大转折", turn_id="t-4")
    with pytest.raises(ValidationError, match="turn"):
        service.record_change("c1", "main", 4, entity_id="erin",
                              trait_key="openness", tier=TraitTier.NORMAL,
                              delta=1, cause="新证据", turn_id="t-2")
    with pytest.raises(NotFoundError):
        service.record_change("c1", "main", 4, entity_id="erin",
                              trait_key="curiosity", tier=TraitTier.NORMAL,
                              delta=1, cause="未初始化的维度", turn_id="t-6")
    state.apply_explicit("c1", "main", 4, DefineAttributeOperation(
        AttributeDefinition("c1", "risk", "风险容忍", "trait",
                            AttributeType.NUMBER, DisplayType.BAR, ALL, 0, 40),
        (),
    ))
    state.apply_explicit("c1", "main", 5,
                         SetAttributeOperation("erin", "risk", 35, "t-7"))
    with pytest.raises(ValidationError, match="range"):
        service.record_change("c1", "main", 6, entity_id="erin",
                              trait_key="risk", tier=TraitTier.MAJOR,
                              delta=20, cause="过度冒险", turn_id="t-8")


def test_inertia_succeeds_when_oldest_event_falls_out_of_window(database, seeded):
    service, _ = seeded
    for i in range(5):
        service.record_change(
            "c1", "main", 3 + i, entity_id="erin", trait_key="openness",
            tier=TraitTier.IMPORTANT, delta=5, cause=f"累积波动{i}",
            turn_id=f"inertia-t-{i}",
        )
    result = service.record_change(
        "c1", "main", 8, entity_id="erin", trait_key="openness",
        tier=TraitTier.NORMAL, delta=1, cause="小幅调整",
        turn_id="inertia-t-5",
    )
    assert result.snapshot["entities"][0]["attributes"][0]["value"] == 61


def test_inertia_rejects_when_pending_fills_window_at_cap(database, seeded):
    service, _ = seeded
    setup = (
        (TraitTier.IMPORTANT, 5),
        (TraitTier.IMPORTANT, 5),
        (TraitTier.IMPORTANT, 5),
        (TraitTier.MAJOR, 10),
    )
    for i, (tier, delta) in enumerate(setup):
        service.record_change(
            "c1", "main", 3 + i, entity_id="erin", trait_key="openness",
            tier=tier, delta=delta, cause=f"边界波动{i}",
            turn_id=f"cap-t-{i}",
        )
    with pytest.raises(ValidationError, match="inertia"):
        service.record_change(
            "c1", "main", 7, entity_id="erin", trait_key="openness",
            tier=TraitTier.NORMAL, delta=1, cause="越界调整",
            turn_id="cap-t-4",
        )
