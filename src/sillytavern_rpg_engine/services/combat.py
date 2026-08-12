"""Combat encounters: initiative, surprise, joins, and end of combat."""

from dataclasses import dataclass
import json
import sqlite3
from typing import Any, Callable

from ..domain.dice import D20Mode, DiceRoller, resolve_d20
from ..domain.dnd import (
    Condition,
    ability_modifier,
    effective_speed,
    require_dnd_2024,
)
from ..domain.errors import NotFoundError, ValidationError
from ..domain.operations import MutationContext
from ..persistence.database import Database
from .conditions import condition_map
from .dice import record_roll
from .dnd_pack import read_int
from .mutations import MutationEngine, MutationRequest, MutationResult


@dataclass(frozen=True)
class CombatantEntry:
    """One creature joining an encounter.

    ``recharge`` holds ``{"name": ..., "min": 2..6, "available": bool}`` maps
    (monster Recharge abilities); ``triggers`` holds data-driven turn hooks
    (see Task 14).
    """

    entity_id: str
    surprised: bool = False
    recharge: tuple[dict[str, Any], ...] = ()
    triggers: tuple[dict[str, Any], ...] = ()


def require_active_encounter(
    connection: sqlite3.Connection, campaign_id: str, branch_id: str
) -> tuple[Any, list[Any]]:
    """Return ``(encounter_row, combatant_rows)`` of the active encounter."""
    encounter = connection.execute(
        "SELECT id, round_number, active_index FROM combat_encounters"
        " WHERE campaign_id = ? AND branch_id = ? AND status = 'active'",
        (campaign_id, branch_id),
    ).fetchone()
    if encounter is None:
        raise NotFoundError("no active encounter for this branch")
    combatants = connection.execute(
        "SELECT * FROM combatants WHERE encounter_id = ? ORDER BY initiative DESC,"
        " entity_id",
        (encounter["id"],),
    ).fetchall()
    return encounter, list(combatants)


def active_combatant(encounter, combatants) -> Any:
    """The combatant whose turn it is (order matches the sorted row list)."""
    return combatants[encounter["active_index"]]


def _roll_initiative(
    connection: sqlite3.Connection,
    context: MutationContext,
    roller: DiceRoller,
    roll_id: str,
    entry: CombatantEntry,
) -> int:
    campaign_id = context.campaign.id
    dex = read_int(connection, campaign_id, entry.entity_id, "ability_dex")
    modifier = ability_modifier(dex)
    mode = D20Mode.DISADVANTAGE if entry.surprised else D20Mode.NORMAL
    outcome = resolve_d20(roller, mode, modifier)
    faces = (
        (outcome.kept,)
        if outcome.dropped is None
        else tuple(sorted((outcome.kept, outcome.dropped), reverse=True))
    )
    record_roll(
        connection, context, roll_id,
        purpose="initiative", formula="1d20", faces=faces,
        modifier=modifier, total=outcome.total,
        roller_entity_id=entry.entity_id,
    )
    return outcome.total


def _insert_combatant(
    connection: sqlite3.Connection,
    context: MutationContext,
    encounter_id: str,
    entry: CombatantEntry,
    initiative: int,
) -> None:
    conditions = condition_map(connection, context.campaign.id, entry.entity_id)
    speed = read_int(connection, context.campaign.id, entry.entity_id, "speed")
    connection.execute(
        "INSERT INTO combatants(id, encounter_id, entity_id, initiative,"
        " movement_total, recharge_json, triggers_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            f"{encounter_id}:{entry.entity_id}",
            encounter_id,
            entry.entity_id,
            initiative,
            effective_speed(speed, conditions),
            json.dumps(list(entry.recharge)),
            json.dumps(list(entry.triggers)),
        ),
    )


def _order_entries(
    entries: tuple[CombatantEntry, ...],
    initiatives: dict[str, int],
) -> list[CombatantEntry]:
    """Sort by initiative desc; ties break deterministically on entity id.

    Must match the ``ORDER BY initiative DESC, entity_id`` used when loading
    combatants, or turn indices would drift between writes and reads."""
    return sorted(
        entries,
        key=lambda entry: (-initiatives[entry.entity_id], entry.entity_id),
    )


@dataclass(frozen=True)
class StartCombatOperation:
    """Open an encounter: roll initiative, sort combatants, start round 1."""

    roller: DiceRoller
    roll_ids: tuple[str, ...]
    entries: tuple[CombatantEntry, ...]
    encounter_id: str

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        if len(self.roll_ids) != len(self.entries):
            raise ValidationError("one roll id per combatant entry is required")
        entity_ids = [entry.entity_id for entry in self.entries]
        if len(set(entity_ids)) != len(entity_ids):
            raise ValidationError("duplicate combatant entity")
        existing = connection.execute(
            "SELECT id FROM combat_encounters"
            " WHERE campaign_id = ? AND branch_id = ? AND status = 'active'",
            (context.campaign.id, context.branch_id),
        ).fetchone()
        if existing is not None:
            raise ValidationError("an encounter is already active on this branch")
        initiatives = {
            entry.entity_id: _roll_initiative(
                connection, context, self.roller, roll_id, entry
            )
            for entry, roll_id in zip(self.entries, self.roll_ids)
        }
        connection.execute(
            "INSERT INTO combat_encounters(id, campaign_id, branch_id,"
            " created_state_version, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                self.encounter_id,
                context.campaign.id,
                context.branch_id,
                context.next_state_version,
                context.now,
            ),
        )
        ordered = _order_entries(self.entries, initiatives)
        for entry in ordered:
            _insert_combatant(
                connection, context, self.encounter_id, entry,
                initiatives[entry.entity_id],
            )
        return {
            "encounter_id": self.encounter_id,
            "order": [entry.entity_id for entry in ordered],
            "initiative": initiatives,
            "round": 1,
        }


@dataclass(frozen=True)
class AddCombatantOperation:
    """Join one creature into the active encounter mid-combat."""

    roller: DiceRoller
    roll_id: str
    entry: CombatantEntry

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        encounter, _ = require_active_encounter(
            connection, context.campaign.id, context.branch_id
        )
        initiative = _roll_initiative(
            connection, context, self.roller, self.roll_id, self.entry
        )
        try:
            _insert_combatant(
                connection, context, encounter["id"], self.entry, initiative
            )
        except sqlite3.IntegrityError as exc:
            raise ValidationError("combatant already in encounter") from exc
        return {
            "encounter_id": encounter["id"],
            "entity_id": self.entry.entity_id,
            "initiative": initiative,
        }


@dataclass(frozen=True)
class EndCombatOperation:
    """Close the active encounter; combatant rows stay for audit and replay."""

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        encounter, _ = require_active_encounter(
            connection, context.campaign.id, context.branch_id
        )
        connection.execute(
            "UPDATE combat_encounters SET status = 'ended',"
            " ended_state_version = ? WHERE id = ?",
            (context.next_state_version, encounter["id"]),
        )
        return {
            "encounter_id": encounter["id"],
            "rounds": encounter["round_number"],
            "ended_state_version": context.next_state_version,
        }


class CombatService:
    """User-command entry points for encounter lifecycle."""

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

    def _apply(self, campaign_id: str, expected_version: int, event_type: str, operation):
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id,
                branch_id="main",
                expected_version=expected_version,
                source="user-command",
                event_type=event_type,
                operation=operation,
            )
        )

    def start(
        self,
        campaign_id: str,
        expected_version: int,
        roller: DiceRoller,
        entries: tuple[CombatantEntry, ...],
    ) -> MutationResult:
        roll_ids = tuple(self.id_factory() for _ in entries)
        return self._apply(
            campaign_id, expected_version, "combat-started",
            StartCombatOperation(roller, roll_ids, entries, self.id_factory()),
        )

    def add_combatant(
        self,
        campaign_id: str,
        expected_version: int,
        roller: DiceRoller,
        entry: CombatantEntry,
    ) -> MutationResult:
        return self._apply(
            campaign_id, expected_version, "combatant-joined",
            AddCombatantOperation(roller, self.id_factory(), entry),
        )

    def end(self, campaign_id: str, expected_version: int) -> MutationResult:
        return self._apply(
            campaign_id, expected_version, "combat-ended", EndCombatOperation()
        )
