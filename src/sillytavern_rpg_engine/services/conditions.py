"""Condition application/removal with 2024 effects and exhaustion levels."""

from dataclasses import dataclass
import sqlite3
from typing import Any, Callable

from ..domain.dnd import (
    EXHAUSTION_MAX_LEVEL,
    Condition,
    require_dnd_2024,
)
from ..domain.errors import NotFoundError, ValidationError
from ..domain.operations import MutationContext
from ..persistence.database import Database
from .attributes import SetAttributeOperation
from .dnd_pack import read_list
from .mutations import MutationEngine, MutationRequest, MutationResult


def condition_map(
    connection: sqlite3.Connection, campaign_id: str, entity_id: str
) -> dict[Condition, int]:
    """Read active conditions of one entity as ``{condition: level}``."""
    rows = connection.execute(
        "SELECT condition, level FROM entity_conditions"
        " WHERE campaign_id = ? AND entity_id = ?",
        (campaign_id, entity_id),
    ).fetchall()
    return {Condition(row["condition"]): row["level"] for row in rows}


@dataclass(frozen=True)
class ApplyConditionOperation:
    """Apply (or refresh) a condition; exhaustion accumulates levels and
    reaching the cap marks the entity dead via its ``is_dead`` attribute."""

    entity_id: str
    condition: Condition
    source: str
    level: int = 1

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict[str, Any]:
        require_dnd_2024(context.campaign)
        campaign_id = context.campaign.id
        if isinstance(self.level, bool) or not isinstance(self.level, int) or self.level < 1:
            raise ValidationError("condition level must be a positive integer")
        immunities = read_list(connection, campaign_id, self.entity_id, "condition_immunities")
        if self.condition.value in immunities:
            raise ValidationError(
                f"entity {self.entity_id!r} is immune to {self.condition.value}"
            )
        existing = condition_map(connection, campaign_id, self.entity_id)
        died = False
        if self.condition is Condition.EXHAUSTION:
            level = min(existing.get(Condition.EXHAUSTION, 0) + self.level, EXHAUSTION_MAX_LEVEL)
            died = level >= EXHAUSTION_MAX_LEVEL
        else:
            level = self.level
        connection.execute(
            "INSERT INTO entity_conditions(campaign_id, entity_id, condition,"
            " level, source, applied_state_version) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(campaign_id, entity_id, condition) DO UPDATE SET"
            " level = excluded.level, source = excluded.source,"
            " applied_state_version = excluded.applied_state_version",
            (
                campaign_id,
                self.entity_id,
                self.condition.value,
                level,
                self.source,
                context.next_state_version,
            ),
        )
        if died:
            SetAttributeOperation(self.entity_id, "is_dead", True).apply(connection, context)
        return {
            "entity_id": self.entity_id,
            "condition": self.condition.value,
            "level": level,
            "source": self.source,
            "died": died,
        }


@dataclass(frozen=True)
class RemoveConditionOperation:
    """Remove a condition; exhaustion drops by ``level`` and ends at 0."""

    entity_id: str
    condition: Condition
    level: int = 1

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict[str, Any]:
        require_dnd_2024(context.campaign)
        campaign_id = context.campaign.id
        if isinstance(self.level, bool) or not isinstance(self.level, int) or self.level < 1:
            raise ValidationError("condition level must be a positive integer")
        existing = condition_map(connection, campaign_id, self.entity_id)
        if self.condition not in existing:
            raise NotFoundError(
                f"entity {self.entity_id!r} does not have {self.condition.value}"
            )
        remaining = 0
        if self.condition is Condition.EXHAUSTION:
            remaining = max(0, existing[Condition.EXHAUSTION] - self.level)
        if remaining:
            connection.execute(
                "UPDATE entity_conditions SET level = ?,"
                " applied_state_version = ?"
                " WHERE campaign_id = ? AND entity_id = ? AND condition = ?",
                (
                    remaining,
                    context.next_state_version,
                    campaign_id,
                    self.entity_id,
                    self.condition.value,
                ),
            )
        else:
            connection.execute(
                "DELETE FROM entity_conditions"
                " WHERE campaign_id = ? AND entity_id = ? AND condition = ?",
                (campaign_id, self.entity_id, self.condition.value),
            )
        return {
            "entity_id": self.entity_id,
            "condition": self.condition.value,
            "remaining_level": remaining,
        }


class ConditionService:
    """User-command entry points for condition changes."""

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

    def apply(
        self,
        campaign_id: str,
        expected_version: int,
        entity_id: str,
        condition: Condition,
        source: str,
        level: int = 1,
        branch_id: str = "main",
    ) -> MutationResult:
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id,
                branch_id=branch_id,
                expected_version=expected_version,
                source="user-command",
                event_type="condition-applied",
                operation=ApplyConditionOperation(entity_id, condition, source, level),
            )
        )

    def remove(
        self,
        campaign_id: str,
        expected_version: int,
        entity_id: str,
        condition: Condition,
        level: int = 1,
        branch_id: str = "main",
    ) -> MutationResult:
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id,
                branch_id=branch_id,
                expected_version=expected_version,
                source="user-command",
                event_type="condition-removed",
                operation=RemoveConditionOperation(entity_id, condition, level),
            )
        )
