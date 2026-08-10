import pytest

from sillytavern_rpg_engine.domain.errors import NotFoundError, ValidationError
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
    EntityService,
)


@pytest.fixture
def campaign_service(database):
    service = CampaignService(
        database,
        id_factory=iter(f"event-{index}" for index in range(20)).__next__,
        clock=lambda: "2026-08-10T00:00:00Z",
    )
    service.create_campaign("c1", "Campaign")
    return service


def test_one_explicit_command_creates_person_skill_and_value(database, campaign_service):
    service = EntityAttributeService(database, campaign_service.mutation_engine)
    result = service.apply_explicit(
        campaign_id="c1",
        branch_id="main",
        expected_version=0,
        operation=CompositeOperation((
            CreateEntityOperation("erin", EntityKind.CHARACTER, "艾琳", AgeStatus.ADULT, ("Erin",)),
            DefineAttributeOperation(AttributeDefinition(
                "c1", "alchemy", "炼金术", "skill", AttributeType.NUMBER,
                DisplayType.BAR, frozenset({Audience.ENGINE, Audience.PLAYER_UI}), 0, 100,
            ), ("炼金", "Alchemy")),
            SetAttributeOperation("erin", "alchemy", 35, None),
        )),
    )
    assert result.state_version == 1
    assert result.snapshot["entities"][0]["attributes"][0]["value"] == 35


def test_aliases_are_unique_and_adult_attributes_require_adult_entity(database, campaign_service):
    service = EntityAttributeService(database, campaign_service.mutation_engine)
    service.apply_explicit("c1", "main", 0, CreateEntityOperation(
        "erin", EntityKind.CHARACTER, "艾琳", AgeStatus.UNKNOWN, ("Erin",)
    ))
    with pytest.raises(ValidationError, match="confirmed adult"):
        service.apply_explicit("c1", "main", 1, CompositeOperation((
            DefineAttributeOperation(AttributeDefinition(
                "c1", "intimacy_openness", "亲密开放度", "adult_intimacy",
                AttributeType.NUMBER, DisplayType.BAR,
                frozenset({Audience.ENGINE}), 0, 100,
            ), ()),
            SetAttributeOperation("erin", "intimacy_openness", 40, None),
        )))


def test_duplicate_normalized_name_rejected_and_alias_resolution(database, campaign_service):
    service = EntityAttributeService(database, campaign_service.mutation_engine)
    service.apply_explicit("c1", "main", 0, CreateEntityOperation(
        "erin", EntityKind.CHARACTER, "艾琳", AgeStatus.ADULT, ("Erin",)
    ))
    with pytest.raises(ValidationError, match="already exists"):
        service.apply_explicit("c1", "main", 1, CreateEntityOperation(
            "erin-2", EntityKind.CHARACTER, " 艾琳 ", AgeStatus.ADULT, ()
        ))
    assert EntityService(database).resolve("c1", "Erin").id == "erin"
    with pytest.raises(NotFoundError, match="not found"):
        EntityService(database).resolve("c1", "nobody")
