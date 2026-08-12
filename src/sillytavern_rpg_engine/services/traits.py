"""Personality trait change events: capped, inertia-checked, append-only."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Callable
from uuid import uuid4

from ..domain.errors import NotFoundError, ValidationError
from ..domain.memory import (
    TRAIT_INERTIA_CAP,
    TRAIT_INERTIA_WINDOW,
    TRAIT_TIER_CAPS,
    TraitEvent,
    TraitTier,
)
from ..domain.models import (
    AgeStatus,
    AttributeDefinition,
    AttributeType,
    Audience,
    DisplayType,
)
from ..domain.operations import MutationContext
from ..domain.validation import validate_attribute_value
from ..persistence.database import Database
from ..persistence.repositories import dump_json
from .attributes import ADULT_INTIMACY_CATEGORY
from .entities import normalize_key
from .mutations import MutationEngine, MutationRequest, MutationResult

TRAIT_CATEGORY = "trait"
_TRAIT_CATEGORIES = (TRAIT_CATEGORY, ADULT_INTIMACY_CATEGORY)


@dataclass(frozen=True)
class RecordTraitEventOperation:
    """Record one personality change and update the current trait value in
    the same transaction; the event log is the evidence, the value is state."""

    event_id: str
    entity_id: str
    trait_key: str
    tier: TraitTier
    delta: float
    cause: str
    turn_id: str | None = None
    source: str = "narrative_development"

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict[str, Any]:
        if not self.cause.strip():
            raise ValidationError("trait event cause must not be empty")
        if isinstance(self.delta, bool) or not isinstance(self.delta, (int, float)):
            raise ValidationError("delta must be a number")
        if self.delta == 0:
            raise ValidationError("delta must not be zero")
        entity = connection.execute(
            "SELECT id, age_status FROM entities WHERE id = ? AND campaign_id = ?",
            (self.entity_id, context.campaign.id),
        ).fetchone()
        if entity is None:
            raise NotFoundError(
                f"entity {self.entity_id!r} not found in campaign"
                f" {context.campaign.id}"
            )
        row = connection.execute(
            "SELECT key, label, category, value_type, display, audiences_json,"
            " minimum, maximum, enum_values_json, unit FROM attribute_definitions"
            " WHERE campaign_id = ? AND key = ?",
            (context.campaign.id, normalize_key(self.trait_key)),
        ).fetchone()
        if row is None or row["value_type"] not in (
            AttributeType.NUMBER.value,
            AttributeType.INTEGER.value,
        ) or row["category"] not in _TRAIT_CATEGORIES:
            raise NotFoundError(
                f"numeric trait {self.trait_key!r} not defined in campaign"
                f" {context.campaign.id}"
            )
        if (
            row["category"] == ADULT_INTIMACY_CATEGORY
            and entity["age_status"] != AgeStatus.ADULT.value
        ):
            raise ValidationError(
                "adult attribute requires a confirmed adult entity"
            )
        definition = AttributeDefinition(
            campaign_id=context.campaign.id,
            key=row["key"],
            label=row["label"],
            category=row["category"],
            value_type=AttributeType(row["value_type"]),
            display=DisplayType(row["display"]),
            audiences=frozenset(
                Audience(a) for a in json.loads(row["audiences_json"])
            ),
            minimum=row["minimum"],
            maximum=row["maximum"],
            enum_values=tuple(json.loads(row["enum_values_json"])),
            unit=row["unit"],
        )
        cap = TRAIT_TIER_CAPS[self.tier.value]
        if abs(self.delta) > cap:
            raise ValidationError(
                f"delta {self.delta} exceeds {self.tier.value} cap {cap}"
            )
        current = connection.execute(
            "SELECT value_json FROM attribute_values"
            " WHERE entity_id = ? AND attribute_key = ?",
            (self.entity_id, definition.key),
        ).fetchone()
        if current is None:
            raise NotFoundError(
                f"trait {definition.key!r} must be initialized with"
                " set_attribute before recording change events"
            )
        if self.turn_id is not None and connection.execute(
            "SELECT 1 FROM trait_events"
            " WHERE entity_id = ? AND trait_key = ? AND turn_id = ?",
            (self.entity_id, definition.key, self.turn_id),
        ).fetchone() is not None:
            raise ValidationError(
                "duplicate trait event for turn"
            )
        latest = connection.execute(
            "SELECT delta, cause FROM trait_events"
            " WHERE entity_id = ? AND trait_key = ? ORDER BY rowid DESC LIMIT 1",
            (self.entity_id, definition.key),
        ).fetchone()
        if (
            latest is not None
            and latest["cause"] == self.cause
            and latest["delta"] == self.delta
        ):
            raise ValidationError("duplicate trait event evidence")
        recent = connection.execute(
            "SELECT delta FROM trait_events"
            " WHERE entity_id = ? AND trait_key = ?"
            " ORDER BY rowid DESC LIMIT ?",
            (self.entity_id, definition.key, TRAIT_INERTIA_WINDOW),
        ).fetchall()
        inertia = sum(abs(item["delta"]) for item in recent) + abs(self.delta)
        if inertia > TRAIT_INERTIA_CAP:
            raise ValidationError(
                f"trait change exceeds inertia cap {TRAIT_INERTIA_CAP}"
                f" over last {TRAIT_INERTIA_WINDOW} events"
            )
        before = json.loads(current["value_json"])
        after = validate_attribute_value(definition, before + self.delta)
        connection.execute(
            "INSERT INTO trait_events(id, campaign_id, branch_id, entity_id,"
            " trait_key, tier, before_value, delta, after_value, cause, turn_id,"
            " source, state_version, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self.event_id,
                context.campaign.id,
                context.branch_id,
                self.entity_id,
                definition.key,
                self.tier.value,
                before,
                self.delta,
                after,
                self.cause,
                self.turn_id,
                self.source,
                context.next_state_version,
                context.now,
            ),
        )
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
                definition.key,
                dump_json(after),
                context.next_state_version,
                self.turn_id,
            ),
        )
        return {
            "event_id": self.event_id,
            "entity_id": self.entity_id,
            "trait_key": definition.key,
            "tier": self.tier.value,
            "before": before,
            "delta": self.delta,
            "after": after,
            "cause": self.cause,
            "turn_id": self.turn_id,
        }


class TraitService:
    """Records trait changes and reads trait history and baselines."""

    def __init__(
        self,
        database: Database,
        mutation_engine: MutationEngine | None = None,
        id_factory: Callable[[], str] = lambda: uuid4().hex,
        clock: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(),
    ):
        self.database = database
        self.mutation_engine = mutation_engine or MutationEngine(
            database, id_factory=id_factory, clock=clock
        )
        self.id_factory = id_factory

    def record_change(
        self,
        campaign_id: str,
        branch_id: str,
        expected_version: int,
        *,
        entity_id: str,
        trait_key: str,
        tier: TraitTier,
        delta: float,
        cause: str,
        turn_id: str | None = None,
        source: str = "user-command",
    ) -> MutationResult:
        """Record one trait change as an atomic versioned mutation."""
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id,
                branch_id=branch_id,
                expected_version=expected_version,
                source=source,
                event_type="trait-change-recorded",
                operation=RecordTraitEventOperation(
                    event_id=self.id_factory(),
                    entity_id=entity_id,
                    trait_key=trait_key,
                    tier=tier,
                    delta=delta,
                    cause=cause,
                    turn_id=turn_id,
                    source=source,
                ),
            )
        )

    def history(self, entity_id: str, trait_key: str) -> list[TraitEvent]:
        """Return every change event of one dimension, oldest first."""
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT id, campaign_id, branch_id, entity_id, trait_key, tier,"
                " before_value, delta, after_value, cause, turn_id, source,"
                " state_version, created_at FROM trait_events"
                " WHERE entity_id = ? AND trait_key = ? ORDER BY rowid",
                (entity_id, normalize_key(trait_key)),
            ).fetchall()
        return [
            TraitEvent(
                id=row["id"],
                campaign_id=row["campaign_id"],
                branch_id=row["branch_id"],
                entity_id=row["entity_id"],
                trait_key=row["trait_key"],
                tier=TraitTier(row["tier"]),
                before=row["before_value"],
                delta=row["delta"],
                after=row["after_value"],
                cause=row["cause"],
                turn_id=row["turn_id"],
                source=row["source"],
                state_version=row["state_version"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def baseline(self, entity_id: str, trait_key: str) -> float | None:
        """Baseline = the before-value of the first recorded event, else the
        current attribute value, else None when the trait is unset."""
        key = normalize_key(trait_key)
        with self.database.connect() as connection:
            first = connection.execute(
                "SELECT before_value FROM trait_events"
                " WHERE entity_id = ? AND trait_key = ? ORDER BY rowid LIMIT 1",
                (entity_id, key),
            ).fetchone()
            if first is not None:
                return first["before_value"]
            current = connection.execute(
                "SELECT value_json FROM attribute_values"
                " WHERE entity_id = ? AND attribute_key = ?",
                (entity_id, key),
            ).fetchone()
        return json.loads(current["value_json"]) if current is not None else None
