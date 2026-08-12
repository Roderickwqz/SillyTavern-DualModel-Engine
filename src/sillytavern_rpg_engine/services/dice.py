"""Authoritative dice resolution: every roll is recorded immutably inside the
same transaction as the state change it belongs to."""

from dataclasses import dataclass
import json
import sqlite3
from typing import Any, Callable

from ..domain.dice import D20Mode, DiceRoller, resolve_d20
from ..domain.dnd import (
    DND2024_RULES_VERSION,
    Condition,
    ability_modifier,
    exhaustion_penalty,
    require_dnd_2024,
)
from ..domain.operations import MutationContext
from ..persistence.database import Database
from .dnd_pack import read_int, read_list
from .mutations import MutationEngine, MutationRequest, MutationResult


def read_exhaustion(connection: sqlite3.Connection, campaign_id: str, entity_id: str) -> int:
    """Current exhaustion level of one entity (0 when unaffected)."""
    row = connection.execute(
        "SELECT level FROM entity_conditions"
        " WHERE campaign_id = ? AND entity_id = ? AND condition = ?",
        (campaign_id, entity_id, Condition.EXHAUSTION.value),
    ).fetchone()
    return row["level"] if row is not None else 0


def record_roll(
    connection: sqlite3.Connection,
    context: MutationContext,
    roll_id: str,
    *,
    purpose: str,
    formula: str,
    faces: tuple[int, ...],
    modifier: int,
    total: int,
    dc: int | None = None,
    success: bool | None = None,
    critical: bool = False,
    roller_entity_id: str | None = None,
    turn_id: str | None = None,
) -> dict[str, Any]:
    """Append one immutable roll row; returns its audit-safe payload."""
    connection.execute(
        "INSERT INTO dice_rolls(id, campaign_id, branch_id, turn_id,"
        " roller_entity_id, purpose, formula, faces_json, modifier, total, dc,"
        " success, critical, rules_version, state_version, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            roll_id,
            context.campaign.id,
            context.branch_id,
            turn_id,
            roller_entity_id,
            purpose,
            formula,
            json.dumps(list(faces)),
            modifier,
            total,
            dc,
            None if success is None else int(success),
            int(critical),
            DND2024_RULES_VERSION,
            context.next_state_version,
            context.now,
        ),
    )
    return {
        "roll_id": roll_id,
        "purpose": purpose,
        "formula": formula,
        "faces": list(faces),
        "modifier": modifier,
        "total": total,
        "dc": dc,
        "success": success,
        "critical": critical,
    }


@dataclass(frozen=True)
class CheckOperation:
    """Resolve one ability check: 1d20 + ability mod + proficiency (when the
    skill is proficient) + exhaustion penalty vs a DC."""

    roller: DiceRoller
    roll_id: str
    entity_id: str
    ability: str
    skill: str | None
    dc: int
    purpose: str
    mode: D20Mode = D20Mode.NORMAL
    turn_id: str | None = None

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        campaign_id = context.campaign.id
        score = read_int(connection, campaign_id, self.entity_id, f"ability_{self.ability}")
        modifier = ability_modifier(score)
        if self.skill is not None and self.skill in read_list(
            connection, campaign_id, self.entity_id, "proficiencies"
        ):
            modifier += read_int(connection, campaign_id, self.entity_id, "proficiency_bonus")
        modifier += exhaustion_penalty(
            read_exhaustion(connection, campaign_id, self.entity_id)
        )
        outcome = resolve_d20(self.roller, self.mode, modifier)
        faces = (
            (outcome.kept,)
            if outcome.dropped is None
            else tuple(sorted((outcome.kept, outcome.dropped), reverse=True))
        )
        return record_roll(
            connection, context, self.roll_id,
            purpose=self.purpose, formula="1d20", faces=faces,
            modifier=modifier, total=outcome.total, dc=self.dc,
            success=outcome.total >= self.dc,
            roller_entity_id=self.entity_id, turn_id=self.turn_id,
        )


class DiceService:
    """User-command entry point for standalone authorized checks."""

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

    def check(
        self,
        campaign_id: str,
        expected_version: int,
        roller: DiceRoller,
        entity_id: str,
        ability: str,
        skill: str | None,
        dc: int,
        purpose: str,
        branch_id: str = "main",
        mode: D20Mode = D20Mode.NORMAL,
        turn_id: str | None = None,
    ) -> MutationResult:
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id,
                branch_id=branch_id,
                expected_version=expected_version,
                source="user-command",
                event_type="dice-check",
                operation=CheckOperation(
                    roller=roller, roll_id=self.id_factory(),
                    entity_id=entity_id, ability=ability, skill=skill,
                    dc=dc, purpose=purpose, mode=mode, turn_id=turn_id,
                ),
            )
        )
