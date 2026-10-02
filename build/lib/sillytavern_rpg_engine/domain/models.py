"""Immutable value contracts for campaigns, entities, and attributes."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class RulesMode(StrEnum):
    NARRATIVE = "narrative"
    DND_2024 = "dnd-2024"
    CUSTOM = "custom"


class Audience(StrEnum):
    ENGINE = "engine"
    NPC_AGENT = "npc_agent"
    NARRATOR = "narrator"
    PLAYER_UI = "player_ui"


class AgeStatus(StrEnum):
    ADULT = "adult"
    MINOR = "minor"
    UNKNOWN = "unknown"


class EntityKind(StrEnum):
    CHARACTER = "character"
    LOCATION = "location"
    ORGANIZATION = "organization"
    ITEM = "item"


class AttributeType(StrEnum):
    NUMBER = "number"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    TEXT = "text"
    ENUM = "enum"
    LIST = "list"


class DisplayType(StrEnum):
    BAR = "bar"
    NUMBER = "number"
    BADGE = "badge"
    TEXT = "text"
    LIST = "list"
    PROGRESS = "progress"


class ProposalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    STALE = "stale"


@dataclass(frozen=True)
class CampaignRules:
    """Optional rule-system configuration; narrative mode is the default."""

    mode: RulesMode = RulesMode.NARRATIVE
    enabled: bool = False
    version: str | None = None
    custom_preset_id: str | None = None


@dataclass(frozen=True)
class Campaign:
    """A single-player campaign; state_version is bumped on every mutation."""

    id: str
    name: str
    state_version: int
    rules: CampaignRules = field(default_factory=CampaignRules)


@dataclass(frozen=True)
class Entity:
    """A character, location, organization, or item within a campaign."""

    id: str
    campaign_id: str
    kind: EntityKind
    name: str
    age_status: AgeStatus = AgeStatus.UNKNOWN


@dataclass(frozen=True)
class AttributeDefinition:
    """Schema for one dynamic attribute key within a campaign."""

    campaign_id: str
    key: str
    label: str
    category: str
    value_type: AttributeType
    display: DisplayType
    audiences: frozenset[Audience]
    minimum: float | None = None
    maximum: float | None = None
    enum_values: tuple[str, ...] = ()
    unit: str | None = None


@dataclass(frozen=True)
class AttributeValue:
    """An immutable, versioned value for one attribute of one entity."""

    entity_id: str
    attribute_key: str
    value: Any
    state_version: int
    updated_turn_id: str | None = None
