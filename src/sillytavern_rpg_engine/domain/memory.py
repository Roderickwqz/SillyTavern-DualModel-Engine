"""Memory and personality contracts: facts, events, traits, arcs, summaries."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .errors import ValidationError
from .models import Audience

MIN_IMPORTANCE = 1
MAX_IMPORTANCE = 5

TRAIT_TIER_CAPS: dict[str, float] = {
    "normal": 3.0,
    "important": 8.0,
    "major": 20.0,
}
TRAIT_INERTIA_WINDOW = 5
TRAIT_INERTIA_CAP = 25.0


class FactType(StrEnum):
    IDENTITY = "identity"
    COMMITMENT = "commitment"
    QUEST = "quest"
    CONFLICT = "conflict"
    RULE_CONSEQUENCE = "rule_consequence"
    GENERAL = "general"


class MemoryEventType(StrEnum):
    GENERAL = "general"
    SCENE = "scene"
    IDENTITY = "identity"
    COMMITMENT = "commitment"
    CONFLICT = "conflict"
    QUEST = "quest"
    RULE_CONSEQUENCE = "rule_consequence"
    PERSONALITY_SHIFT = "personality_shift"


PINNED_EVENT_TYPES = frozenset({
    MemoryEventType.IDENTITY,
    MemoryEventType.COMMITMENT,
    MemoryEventType.RULE_CONSEQUENCE,
    MemoryEventType.PERSONALITY_SHIFT,
})


class TraitTier(StrEnum):
    NORMAL = "normal"
    IMPORTANT = "important"
    MAJOR = "major"


class SummaryScope(StrEnum):
    CHARACTER = "character"
    RELATIONSHIP = "relationship"
    QUEST = "quest"
    PLOTLINE = "plotline"


@dataclass(frozen=True)
class Fact:
    """One keyed fact about an entity; ``valid_until`` NULL means current."""

    id: str
    campaign_id: str
    branch_id: str
    entity_id: str
    fact_type: FactType
    fact_key: str
    content: str
    importance: int
    audiences: frozenset[Audience]
    valid_from: int
    valid_until: int | None
    superseded_by: str | None
    turn_id: str | None
    source: str
    created_at: str


@dataclass(frozen=True)
class MemoryEvent:
    """One permanent raw memory event; participants load separately."""

    id: str
    campaign_id: str
    branch_id: str
    event_type: MemoryEventType
    content: str
    importance: int
    audiences: frozenset[Audience]
    participants: tuple[str, ...]
    location_entity_id: str | None
    turn_id: str | None
    source: str
    state_version: int
    created_at: str


@dataclass(frozen=True)
class TraitEvent:
    """One validated personality change with before/delta/after evidence."""

    id: str
    campaign_id: str
    branch_id: str
    entity_id: str
    trait_key: str
    tier: TraitTier
    before: float
    delta: float
    after: float
    cause: str
    turn_id: str | None
    source: str
    state_version: int
    created_at: str


@dataclass(frozen=True)
class DevelopmentArc:
    """A curated development phase with provenance to trait events."""

    id: str
    campaign_id: str
    branch_id: str
    entity_id: str
    dimension: str
    label: str
    summary: str
    source_event_ids: tuple[str, ...]
    start_turn_id: str | None
    end_turn_id: str | None
    opened_state_version: int
    closed_state_version: int | None
    created_at: str


@dataclass(frozen=True)
class MemorySummary:
    """A rebuildable summary that must cite its source memory events."""

    campaign_id: str
    branch_id: str
    scope: SummaryScope
    scope_key: str
    content: str
    audiences: frozenset[Audience]
    source_event_ids: tuple[str, ...]
    state_version: int
    created_at: str


@dataclass(frozen=True)
class Relationship:
    """One directed relationship dimension between two entities."""

    campaign_id: str
    from_entity_id: str
    to_entity_id: str
    dimension: str
    value: Any
    audiences: frozenset[Audience]
    state_version: int
    updated_turn_id: str | None


def validate_importance(value: int) -> int:
    """Require an integer importance within MIN_IMPORTANCE..MAX_IMPORTANCE."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError("importance must be an integer")
    if not MIN_IMPORTANCE <= value <= MAX_IMPORTANCE:
        raise ValidationError(
            f"importance {value} outside range {MIN_IMPORTANCE}..{MAX_IMPORTANCE}"
        )
    return value


def validate_audiences(audiences: frozenset[Audience]) -> frozenset[Audience]:
    """Require a non-empty Audience set."""
    if not audiences:
        raise ValidationError("audiences must not be empty")
    return audiences
