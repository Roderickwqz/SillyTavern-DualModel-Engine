"""D&D 2024 attribute pack: seeded definitions, readiness, atomic enable."""

from dataclasses import dataclass
import json
import sqlite3
from typing import Any, Callable

from ..domain.dnd import DND2024_RULES_VERSION
from ..domain.errors import ValidationError
from ..domain.models import (
    AttributeDefinition,
    AttributeType,
    Audience,
    CampaignRules,
    DisplayType,
    Entity,
    RulesMode,
)
from ..domain.operations import MutationContext
from ..persistence.database import Database
from .attributes import DefineAttributeOperation
from .campaigns import SetRulesOperation
from .mutations import MutationEngine, MutationRequest, MutationResult

_ALL_AUDIENCES = frozenset(
    {Audience.ENGINE, Audience.NARRATOR, Audience.PLAYER_UI}
)
_DND_CATEGORY = "dnd"

# (key, label, value_type, minimum, maximum, enum_values)
_PACK: tuple[tuple[str, str, AttributeType, float | None, float | None, tuple[str, ...]], ...] = (
    ("ability_str", "Strength", AttributeType.INTEGER, 1, 30, ()),
    ("ability_dex", "Dexterity", AttributeType.INTEGER, 1, 30, ()),
    ("ability_con", "Constitution", AttributeType.INTEGER, 1, 30, ()),
    ("ability_int", "Intelligence", AttributeType.INTEGER, 1, 30, ()),
    ("ability_wis", "Wisdom", AttributeType.INTEGER, 1, 30, ()),
    ("ability_cha", "Charisma", AttributeType.INTEGER, 1, 30, ()),
    ("proficiency_bonus", "Proficiency Bonus", AttributeType.INTEGER, 2, 9, ()),
    ("proficiencies", "Proficiencies", AttributeType.LIST, None, None, ()),
    ("armor_class", "Armor Class", AttributeType.INTEGER, 1, 40, ()),
    ("hp_max", "Max HP", AttributeType.INTEGER, 1, 999, ()),
    ("hp_current", "Current HP", AttributeType.INTEGER, 0, 999, ()),
    ("hp_temp", "Temporary HP", AttributeType.INTEGER, 0, 999, ()),
    ("speed", "Speed", AttributeType.INTEGER, 0, 120, ()),
    ("character_level", "Level", AttributeType.INTEGER, 1, 20, ()),
    ("spell_slots_max", "Spell Slots (Max)", AttributeType.LIST, None, None, ()),
    ("spell_slots_current", "Spell Slots", AttributeType.LIST, None, None, ()),
    ("hit_die", "Hit Die", AttributeType.INTEGER, 4, 20, ()),
    ("hit_dice_total", "Hit Dice (Total)", AttributeType.INTEGER, 0, 20, ()),
    ("hit_dice_current", "Hit Dice", AttributeType.INTEGER, 0, 20, ()),
    ("death_saves_success", "Death Saves (Success)", AttributeType.INTEGER, 0, 3, ()),
    ("death_saves_failure", "Death Saves (Failure)", AttributeType.INTEGER, 0, 3, ()),
    ("is_dead", "Dead", AttributeType.BOOLEAN, None, None, ()),
    ("is_stable", "Stable", AttributeType.BOOLEAN, None, None, ()),
    ("resistances", "Resistances", AttributeType.LIST, None, None, ()),
    ("vulnerabilities", "Vulnerabilities", AttributeType.LIST, None, None, ()),
    ("immunities", "Immunities", AttributeType.LIST, None, None, ()),
    ("condition_immunities", "Condition Immunities", AttributeType.LIST, None, None, ()),
    ("weapon_masteries", "Weapon Masteries", AttributeType.LIST, None, None, ()),
)

REQUIRED_CHARACTER_KEYS = frozenset(key for key, *_ in _PACK)

_SLOT_SHAPE_ERROR = "spell_slots must be a list of 9 non-negative integers"


def dnd_definitions(campaign_id: str) -> list[AttributeDefinition]:
    """Build the full D&D 2024 attribute definition pack for a campaign."""
    return [
        AttributeDefinition(
            campaign_id=campaign_id,
            key=key,
            label=label,
            category=_DND_CATEGORY,
            value_type=value_type,
            display=DisplayType.NUMBER,
            audiences=_ALL_AUDIENCES,
            minimum=minimum,
            maximum=maximum,
            enum_values=enum_values,
        )
        for key, label, value_type, minimum, maximum, enum_values in _PACK
    ]


def validate_slot_list(value: Any) -> list[int]:
    """Require a 9-element list of non-negative ints (spell slot levels 1-9)."""
    if (
        not isinstance(value, list)
        or len(value) != 9
        or any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in value)
    ):
        raise ValidationError(_SLOT_SHAPE_ERROR)
    return list(value)


def read_value(
    connection: sqlite3.Connection, campaign_id: str, entity_id: str, key: str
) -> Any:
    """Read one attribute value; raise ValidationError when unset."""
    row = connection.execute(
        "SELECT value_json FROM attribute_values"
        " WHERE campaign_id = ? AND entity_id = ? AND attribute_key = ?",
        (campaign_id, entity_id, key),
    ).fetchone()
    if row is None:
        raise ValidationError(f"entity {entity_id!r} lacks attribute {key!r}")
    return json.loads(row["value_json"])


def read_int(connection, campaign_id: str, entity_id: str, key: str) -> int:
    value = read_value(connection, campaign_id, entity_id, key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"attribute {key!r} is not an integer")
    return value


def read_list(connection, campaign_id: str, entity_id: str, key: str) -> list:
    value = read_value(connection, campaign_id, entity_id, key)
    if not isinstance(value, list):
        raise ValidationError(f"attribute {key!r} is not a list")
    return value


def read_bool(connection, campaign_id: str, entity_id: str, key: str) -> bool:
    value = read_value(connection, campaign_id, entity_id, key)
    if not isinstance(value, bool):
        raise ValidationError(f"attribute {key!r} is not a boolean")
    return value


@dataclass(frozen=True)
class SeedDndPackOperation:
    """Register every D&D attribute definition; fails if any already exists."""

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        from ..domain.operations import CompositeOperation

        composite = CompositeOperation(
            tuple(
                DefineAttributeOperation(definition)
                for definition in dnd_definitions(context.campaign.id)
            )
        )
        return composite.apply(connection, context)


@dataclass(frozen=True)
class ReadinessReport:
    """Per-character missing D&D attribute keys; ready when empty."""

    ready: bool
    missing: dict[str, tuple[str, ...]]


def check_readiness(connection: sqlite3.Connection, campaign_id: str) -> ReadinessReport:
    """List missing/invalid D&D attribute keys for every character entity."""
    entities = connection.execute(
        "SELECT id FROM entities WHERE campaign_id = ? AND kind = 'character'"
        " ORDER BY normalized_name",
        (campaign_id,),
    ).fetchall()
    defined = {
        row[0]
        for row in connection.execute(
            "SELECT key FROM attribute_definitions WHERE campaign_id = ?",
            (campaign_id,),
        )
    }
    missing: dict[str, tuple[str, ...]] = {}
    for row in entities:
        absent = set(REQUIRED_CHARACTER_KEYS) - defined
        valued = {
            r[0]
            for r in connection.execute(
                "SELECT attribute_key FROM attribute_values"
                " WHERE campaign_id = ? AND entity_id = ?",
                (campaign_id, row["id"]),
            )
        }
        absent |= set(REQUIRED_CHARACTER_KEYS) - valued
        for slot_key in ("spell_slots_max", "spell_slots_current"):
            if slot_key not in absent:
                try:
                    validate_slot_list(read_value(connection, campaign_id, row["id"], slot_key))
                except ValidationError:
                    absent.add(slot_key)
        if absent:
            missing[row["id"]] = tuple(sorted(absent))
    return ReadinessReport(ready=not missing, missing=missing)


@dataclass(frozen=True)
class EnableDnd2024Operation:
    """Atomically enable dnd-2024 after a successful readiness check; the
    mutation engine's snapshot captures pre-enable state in the same tx."""

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        report = check_readiness(connection, context.campaign.id)
        if not report.ready:
            raise ValidationError(f"character data incomplete: {report.missing}")
        return SetRulesOperation(
            CampaignRules(
                mode=RulesMode.DND_2024,
                enabled=True,
                version=DND2024_RULES_VERSION,
            )
        ).apply(connection, context)


class DndRulesService:
    """Seeds the attribute pack, reports readiness, and enables dnd-2024."""

    def __init__(
        self,
        database: Database,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], str] | None = None,
    ):
        from datetime import datetime, timezone
        from uuid import uuid4

        self.database = database
        self.id_factory = id_factory or (lambda: uuid4().hex)
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat())
        self.mutation_engine = MutationEngine(
            database, id_factory=self.id_factory, clock=self.clock
        )

    def seed_pack(self, campaign_id: str, expected_version: int) -> MutationResult:
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id,
                branch_id="main",
                expected_version=expected_version,
                source="user-command",
                event_type="dnd-pack-seeded",
                operation=SeedDndPackOperation(),
            )
        )

    def readiness(self, campaign_id: str) -> ReadinessReport:
        with self.database.connect() as connection:
            return check_readiness(connection, campaign_id)

    def enable(self, campaign_id: str, expected_version: int) -> MutationResult:
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id,
                branch_id="main",
                expected_version=expected_version,
                source="user-command",
                event_type="rules-updated",
                operation=EnableDnd2024Operation(),
            )
        )
