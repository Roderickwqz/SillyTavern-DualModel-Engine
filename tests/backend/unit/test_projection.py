import pytest

from sillytavern_rpg_engine.domain.models import (
    AgeStatus,
    AttributeDefinition,
    AttributeType,
    Audience,
    DisplayType,
    EntityKind,
)
from sillytavern_rpg_engine.domain.operations import CompositeOperation
from sillytavern_rpg_engine.services.attributes import (
    DefineAttributeOperation,
    SetAttributeOperation,
)
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)
from sillytavern_rpg_engine.services.projection import ProjectionService


@pytest.fixture
def seeded_attributes(database):
    campaigns = CampaignService(
        database,
        id_factory=iter(f"projection-event-{index}" for index in range(10)).__next__,
        clock=lambda: "2026-08-10T00:00:00Z",
    )
    campaigns.create_campaign("c1", "Campaign")
    state = EntityAttributeService(database, campaigns.mutation_engine)
    state.apply_explicit("c1", "main", 0, CompositeOperation((
        CreateEntityOperation("erin", EntityKind.CHARACTER, "艾琳", AgeStatus.ADULT, ()),
        DefineAttributeOperation(AttributeDefinition(
            campaign_id="c1", key="alchemy", label="炼金术", category="skill",
            value_type=AttributeType.NUMBER, display=DisplayType.BAR,
            audiences=frozenset({Audience.ENGINE, Audience.PLAYER_UI}),
            minimum=0, maximum=100,
        ), ()),
        SetAttributeOperation("erin", "alchemy", 35, None),
        DefineAttributeOperation(AttributeDefinition(
            campaign_id="c1", key="secret_identity", label="真实身份", category="secret",
            value_type=AttributeType.TEXT, display=DisplayType.TEXT,
            audiences=frozenset({Audience.ENGINE}),
        ), ()),
        SetAttributeOperation("erin", "secret_identity", "王国密探", None),
    )))
    return "c1"


def test_player_projection_never_contains_engine_only_value(seeded_attributes, database):
    service = ProjectionService(database)
    player = service.for_audience("c1", "main", Audience.PLAYER_UI)
    engine = service.for_audience("c1", "main", Audience.ENGINE)
    player_text = str(player)
    assert "alchemy" in player_text
    assert "secret_identity" not in player_text
    assert "王国密探" not in player_text
    assert "secret_identity" in str(engine)
    assert player["rules"] == {"mode": "narrative", "enabled": False, "version": None, "custom_preset_id": None}


def test_for_audiences_unions_intersecting_definitions(seeded_attributes, database):
    campaigns = CampaignService(
        database,
        id_factory=iter(f"projection-event-{index}" for index in range(10, 20)).__next__,
        clock=lambda: "2026-08-10T00:00:00Z",
    )
    state = EntityAttributeService(database, campaigns.mutation_engine)
    state.apply_explicit("c1", "main", 1, CompositeOperation((
        DefineAttributeOperation(AttributeDefinition(
            campaign_id="c1", key="loot_cache", label="赃物点", category="secret",
            value_type=AttributeType.TEXT, display=DisplayType.TEXT,
            audiences=frozenset({Audience.NPC_AGENT}),
        ), ()),
        SetAttributeOperation("erin", "loot_cache", "废弃地窖", None),
    )))
    service = ProjectionService(database)
    agents = service.for_audiences("c1", "main", {Audience.ENGINE, Audience.NPC_AGENT})
    agents_text = str(agents)
    assert "secret_identity" in agents_text
    assert "loot_cache" in agents_text
    assert "王国密探" in agents_text
    assert "废弃地窖" in agents_text
    player = service.for_audiences("c1", "main", [Audience.PLAYER_UI])
    assert "loot_cache" not in str(player)
    assert "secret_identity" not in str(player)


def test_projection_orders_entities_by_normalized_name_and_attributes_by_category_key(
    seeded_attributes, database
):
    campaigns = CampaignService(
        database,
        id_factory=iter(f"projection-event-{index}" for index in range(10, 20)).__next__,
        clock=lambda: "2026-08-10T00:00:00Z",
    )
    state = EntityAttributeService(database, campaigns.mutation_engine)
    state.apply_explicit("c1", "main", 1, CompositeOperation((
        CreateEntityOperation("bravo", EntityKind.CHARACTER, "Bravo", AgeStatus.UNKNOWN, ()),
        CreateEntityOperation("alpha", EntityKind.CHARACTER, "Alpha", AgeStatus.UNKNOWN, ()),
        DefineAttributeOperation(AttributeDefinition(
            campaign_id="c1", key="agility", label="敏捷", category="skill",
            value_type=AttributeType.NUMBER, display=DisplayType.BAR,
            audiences=frozenset({Audience.ENGINE, Audience.PLAYER_UI}),
            minimum=0, maximum=100,
        ), ()),
        SetAttributeOperation("bravo", "alchemy", 90, None),
        SetAttributeOperation("alpha", "alchemy", 10, None),
        SetAttributeOperation("alpha", "agility", 5, None),
    )))
    service = ProjectionService(database)
    player = service.for_audience("c1", "main", Audience.PLAYER_UI)
    assert [entity["id"] for entity in player["entities"]] == ["alpha", "bravo", "erin"]
    attributes = player["entities"][0]["attributes"]
    assert attributes == [
        {"key": "agility", "value": 5},
        {"key": "alchemy", "value": 10},
    ]
