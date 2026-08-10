from dataclasses import FrozenInstanceError

import pytest

from sillytavern_rpg_engine.domain.errors import ValidationError
from sillytavern_rpg_engine.domain.models import (
    AgeStatus,
    AttributeDefinition,
    AttributeType,
    Audience,
    CampaignRules,
    DisplayType,
    RulesMode,
)
from sillytavern_rpg_engine.domain.validation import (
    validate_attribute_value,
    validate_rules,
)


def test_campaign_rules_default_to_narrative_disabled():
    rules = CampaignRules()
    assert rules.mode is RulesMode.NARRATIVE
    assert rules.enabled is False
    assert rules.version is None
    with pytest.raises(FrozenInstanceError):
        rules.enabled = True


def test_number_attribute_enforces_range_and_rejects_boolean():
    definition = AttributeDefinition(
        campaign_id="c1",
        key="alchemy",
        label="炼金术",
        category="skill",
        value_type=AttributeType.NUMBER,
        display=DisplayType.BAR,
        audiences=frozenset({Audience.ENGINE, Audience.PLAYER_UI}),
        minimum=0,
        maximum=100,
    )
    assert validate_attribute_value(definition, 35) == 35
    with pytest.raises(ValidationError, match="boolean is not a number"):
        validate_attribute_value(definition, True)
    with pytest.raises(ValidationError, match="outside range"):
        validate_attribute_value(definition, 101)


def test_dnd_rules_require_version_only_when_enabled():
    validate_rules(CampaignRules())
    validate_rules(CampaignRules(mode=RulesMode.DND_2024, enabled=False))
    with pytest.raises(ValidationError, match="version"):
        validate_rules(CampaignRules(mode=RulesMode.DND_2024, enabled=True))
