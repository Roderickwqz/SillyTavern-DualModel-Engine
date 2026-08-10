import json

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


def test_set_overwrites_value_and_bumps_version(database, campaign_service):
    service = EntityAttributeService(database, campaign_service.mutation_engine)
    service.apply_explicit("c1", "main", 0, CompositeOperation((
        CreateEntityOperation("erin", EntityKind.CHARACTER, "艾琳", AgeStatus.ADULT, ("Erin",)),
        DefineAttributeOperation(AttributeDefinition(
            "c1", "alchemy", "炼金术", "skill", AttributeType.NUMBER,
            DisplayType.BAR, frozenset({Audience.ENGINE, Audience.PLAYER_UI}), 0, 100,
        ), ("炼金", "Alchemy")),
        SetAttributeOperation("erin", "alchemy", 35, None),
    )))
    result = service.apply_explicit(
        "c1", "main", 1, SetAttributeOperation("erin", "alchemy", 80, "turn-7")
    )
    assert result.state_version == 2
    assert result.snapshot["entities"][0]["attributes"][0] == {
        "key": "alchemy",
        "value": 80,
        "state_version": 2,
        "updated_turn_id": "turn-7",
    }
    with database.connect() as connection:
        row = connection.execute(
            "SELECT value_json, state_version, updated_turn_id FROM attribute_values"
            " WHERE entity_id = ? AND attribute_key = ?",
            ("erin", "alchemy"),
        ).fetchone()
    assert json.loads(row["value_json"]) == 80
    assert row["state_version"] == 2
    assert row["updated_turn_id"] == "turn-7"


def test_set_unknown_entity_and_unknown_definition_raise_not_found(database, campaign_service):
    service = EntityAttributeService(database, campaign_service.mutation_engine)
    service.apply_explicit("c1", "main", 0, CompositeOperation((
        CreateEntityOperation("erin", EntityKind.CHARACTER, "艾琳", AgeStatus.ADULT, ("Erin",)),
        DefineAttributeOperation(AttributeDefinition(
            "c1", "alchemy", "炼金术", "skill", AttributeType.NUMBER,
            DisplayType.BAR, frozenset({Audience.ENGINE}), 0, 100,
        ), ()),
        SetAttributeOperation("erin", "alchemy", 35, None),
    )))
    with pytest.raises(NotFoundError, match="not found"):
        service.apply_explicit(
            "c1", "main", 1, SetAttributeOperation("ghost", "alchemy", 5, None)
        )
    with pytest.raises(NotFoundError, match="not defined"):
        service.apply_explicit(
            "c1", "main", 1, SetAttributeOperation("erin", "swordcraft", 5, None)
        )


def test_define_attribute_with_mismatched_campaign_rejected(database, campaign_service):
    service = EntityAttributeService(database, campaign_service.mutation_engine)
    with pytest.raises(ValidationError, match="does not match"):
        service.apply_explicit("c1", "main", 0, DefineAttributeOperation(AttributeDefinition(
            "c2", "alchemy", "炼金术", "skill", AttributeType.NUMBER,
            DisplayType.BAR, frozenset({Audience.ENGINE}), 0, 100,
        ), ()))


def test_duplicate_entity_id_rejected_with_id_message(database, campaign_service):
    service = EntityAttributeService(database, campaign_service.mutation_engine)
    service.apply_explicit("c1", "main", 0, CreateEntityOperation(
        "erin", EntityKind.CHARACTER, "艾琳", AgeStatus.ADULT, ("Erin",)
    ))
    with pytest.raises(ValidationError, match="entity id already exists"):
        service.apply_explicit("c1", "main", 1, CreateEntityOperation(
            "erin", EntityKind.CHARACTER, "别的名字", AgeStatus.UNKNOWN, ()
        ))
