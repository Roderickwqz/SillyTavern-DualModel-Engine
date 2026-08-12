import sqlite3
from dataclasses import dataclass

import pytest

from sillytavern_rpg_engine.domain.errors import ValidationError
from sillytavern_rpg_engine.domain.memory import (
    FactType,
    MemoryEventType,
    PINNED_EVENT_TYPES,
    SummaryScope,
    TRAIT_TIER_CAPS,
    TraitTier,
    validate_audiences,
    validate_importance,
)
from sillytavern_rpg_engine.domain.models import Audience
from sillytavern_rpg_engine.domain.operations import MutationContext
from sillytavern_rpg_engine.services.mutations import MutationEngine, MutationRequest


def test_importance_bounds_and_bool_rejection():
    assert validate_importance(1) == 1
    assert validate_importance(5) == 5
    with pytest.raises(ValidationError, match="integer"):
        validate_importance(True)
    with pytest.raises(ValidationError, match="outside range"):
        validate_importance(0)
    with pytest.raises(ValidationError, match="outside range"):
        validate_importance(6)


def test_pinned_types_are_known_event_types():
    assert PINNED_EVENT_TYPES <= set(MemoryEventType)
    assert MemoryEventType.GENERAL not in PINNED_EVENT_TYPES


def test_tier_caps_cover_every_tier():
    assert set(TRAIT_TIER_CAPS) == {tier.value for tier in TraitTier}
    assert TRAIT_TIER_CAPS["normal"] < TRAIT_TIER_CAPS["important"]
    assert TRAIT_TIER_CAPS["important"] < TRAIT_TIER_CAPS["major"]


def test_audiences_must_not_be_empty():
    assert validate_audiences(frozenset({Audience.ENGINE}))
    with pytest.raises(ValidationError, match="audiences"):
        validate_audiences(frozenset())


@dataclass(frozen=True)
class CaptureNow:
    seen: list

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        self.seen.append(context.now)
        return {"now": context.now}


def test_mutation_context_carries_injected_clock(database):
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO campaigns(id, name, created_at, updated_at)"
            " VALUES ('c1', 'Campaign', '2026-08-10T00:00:00Z', '2026-08-10T00:00:00Z')"
        )
        connection.execute(
            "INSERT INTO branches(id, campaign_id, status, created_at)"
            " VALUES ('main', 'c1', 'active', '2026-08-10T00:00:00Z')"
        )
    seen = []
    engine = MutationEngine(
        database,
        id_factory=lambda: "event-now",
        clock=lambda: "2026-08-12T09:30:00Z",
    )
    engine.apply(MutationRequest("c1", "main", 0, "test", "tick", CaptureNow(seen)))
    assert seen == ["2026-08-12T09:30:00Z"]
