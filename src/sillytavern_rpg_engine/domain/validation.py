"""Validation for dynamic attribute values and campaign rule modes."""

import math

from .errors import ValidationError
from .models import AttributeDefinition, AttributeType, CampaignRules, RulesMode


def validate_rules(rules: CampaignRules) -> CampaignRules:
    """Validate a rules configuration, raising ValidationError on invalid combos."""
    if rules.enabled and rules.mode is RulesMode.NARRATIVE:
        raise ValidationError("narrative mode cannot be enabled")
    if rules.enabled and rules.mode is RulesMode.DND_2024 and not rules.version:
        raise ValidationError("dnd-2024 rules require a version when enabled")
    if rules.mode is RulesMode.CUSTOM and not rules.custom_preset_id:
        raise ValidationError("custom rules require custom_preset_id")
    if rules.mode is not RulesMode.CUSTOM and rules.custom_preset_id is not None:
        raise ValidationError("custom_preset_id is only valid for custom mode")
    return rules


def validate_attribute_value(
    definition: AttributeDefinition, value: object
) -> object:
    """Validate a dynamic attribute value against its definition.

    Returns the value as a JSON-serializable plain type, or raises
    ValidationError when the value violates the definition's type, range,
    or enum constraints.
    """
    value_type = definition.value_type
    if value_type in (AttributeType.NUMBER, AttributeType.INTEGER):
        if isinstance(value, bool):
            raise ValidationError(
                f"boolean is not a {value_type.value} for {definition.key!r}"
            )
        if value_type is AttributeType.NUMBER:
            if not isinstance(value, (int, float)):
                raise ValidationError(
                    f"expected a number for {definition.key!r}, got {type(value).__name__}"
                )
            if not math.isfinite(value):
                raise ValidationError(
                    f"expected a finite number for {definition.key!r}"
                )
        elif not isinstance(value, int):
            raise ValidationError(
                f"expected an integer for {definition.key!r}, got {type(value).__name__}"
            )
        if definition.minimum is not None and value < definition.minimum:
            raise ValidationError(
                f"{value} is outside range [{definition.minimum}, {definition.maximum}]"
            )
        if definition.maximum is not None and value > definition.maximum:
            raise ValidationError(
                f"{value} is outside range [{definition.minimum}, {definition.maximum}]"
            )
    elif value_type is AttributeType.BOOLEAN:
        if not isinstance(value, bool):
            raise ValidationError(
                f"expected a boolean for {definition.key!r}, got {type(value).__name__}"
            )
    elif value_type is AttributeType.TEXT:
        if not isinstance(value, str):
            raise ValidationError(
                f"expected text for {definition.key!r}, got {type(value).__name__}"
            )
    elif value_type is AttributeType.ENUM:
        if value not in definition.enum_values:
            raise ValidationError(
                f"{value!r} is not one of {definition.enum_values} for {definition.key!r}"
            )
    elif value_type is AttributeType.LIST:
        if not isinstance(value, list):
            raise ValidationError(
                f"expected a list for {definition.key!r}, got {type(value).__name__}"
            )
    else:
        raise ValidationError(f"unsupported attribute type {value_type!r}")
    return value
