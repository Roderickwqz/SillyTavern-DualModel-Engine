"""Attribute definition and value operations for dynamic state."""

from dataclasses import dataclass
import json
import sqlite3
from typing import Any

from ..domain.errors import NotFoundError, ValidationError
from ..domain.models import (
    AgeStatus,
    AttributeDefinition,
    AttributeType,
    Audience,
    DisplayType,
)
from ..domain.operations import MutationContext
from ..domain.validation import validate_attribute_value
from ..persistence.repositories import dump_json
from .entities import normalize_key

ADULT_INTIMACY_CATEGORY = "adult_intimacy"


@dataclass(frozen=True)
class DefineAttributeOperation:
    """Register one attribute definition and its aliases for a campaign."""

    definition: AttributeDefinition
    aliases: tuple[str, ...] = ()

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict[str, Any]:
        definition = self.definition
        if definition.campaign_id != context.campaign.id:
            raise ValidationError(
                f"definition campaign {definition.campaign_id!r} does not match"
                f" mutation campaign {context.campaign.id!r}"
            )
        audiences = sorted(audience.value for audience in definition.audiences)
        enum_values = sorted(definition.enum_values)
        try:
            connection.execute(
                "INSERT INTO attribute_definitions(campaign_id, key, label, category,"
                " value_type, display, audiences_json, minimum, maximum,"
                " enum_values_json, unit, created_state_version)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    context.campaign.id,
                    definition.key,
                    definition.label,
                    definition.category,
                    definition.value_type.value,
                    definition.display.value,
                    dump_json(audiences),
                    definition.minimum,
                    definition.maximum,
                    dump_json(enum_values),
                    definition.unit,
                    context.next_state_version,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValidationError("attribute definition already exists") from exc
        try:
            for alias in self.aliases:
                connection.execute(
                    "INSERT INTO attribute_aliases(campaign_id, attribute_key, alias,"
                    " normalized_alias) VALUES (?, ?, ?, ?)",
                    (context.campaign.id, definition.key, alias, normalize_key(alias)),
                )
        except sqlite3.IntegrityError as exc:
            raise ValidationError("attribute alias already exists") from exc
        return {
            "key": definition.key,
            "label": definition.label,
            "category": definition.category,
            "value_type": definition.value_type.value,
            "display": definition.display.value,
            "audiences": audiences,
            "minimum": definition.minimum,
            "maximum": definition.maximum,
            "enum_values": enum_values,
            "unit": definition.unit,
            "aliases": list(self.aliases),
        }


@dataclass(frozen=True)
class SetAttributeOperation:
    """Set one attribute value for one entity at the mutation's state version."""

    entity_id: str
    attribute_key: str
    value: object
    turn_id: str | None = None

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict[str, Any]:
        entity = connection.execute(
            "SELECT id, age_status FROM entities WHERE id = ? AND campaign_id = ?",
            (self.entity_id, context.campaign.id),
        ).fetchone()
        if entity is None:
            raise NotFoundError(
                f"entity {self.entity_id!r} not found in campaign {context.campaign.id}"
            )
        definition_row = connection.execute(
            "SELECT label, category, value_type, display, audiences_json, minimum,"
            " maximum, enum_values_json, unit FROM attribute_definitions"
            " WHERE campaign_id = ? AND key = ?",
            (context.campaign.id, self.attribute_key),
        ).fetchone()
        if definition_row is None:
            raise NotFoundError(
                f"attribute {self.attribute_key!r} not defined in campaign"
                f" {context.campaign.id}"
            )
        if (
            definition_row["category"] == ADULT_INTIMACY_CATEGORY
            and entity["age_status"] != AgeStatus.ADULT.value
        ):
            raise ValidationError("adult attribute requires a confirmed adult entity")
        definition = AttributeDefinition(
            campaign_id=context.campaign.id,
            key=self.attribute_key,
            label=definition_row["label"],
            category=definition_row["category"],
            value_type=AttributeType(definition_row["value_type"]),
            display=DisplayType(definition_row["display"]),
            audiences=frozenset(
                Audience(audience)
                for audience in json.loads(definition_row["audiences_json"])
            ),
            minimum=definition_row["minimum"],
            maximum=definition_row["maximum"],
            enum_values=tuple(json.loads(definition_row["enum_values_json"])),
            unit=definition_row["unit"],
        )
        value = validate_attribute_value(definition, self.value)
        connection.execute(
            "INSERT INTO attribute_values(campaign_id, entity_id, attribute_key,"
            " value_json, state_version, updated_turn_id) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(entity_id, attribute_key) DO UPDATE SET"
            " value_json = excluded.value_json,"
            " state_version = excluded.state_version,"
            " updated_turn_id = excluded.updated_turn_id",
            (
                context.campaign.id,
                self.entity_id,
                self.attribute_key,
                dump_json(value),
                context.next_state_version,
                self.turn_id,
            ),
        )
        return {
            "entity_id": self.entity_id,
            "attribute_key": self.attribute_key,
            "value": value,
            "state_version": context.next_state_version,
            "updated_turn_id": self.turn_id,
        }
