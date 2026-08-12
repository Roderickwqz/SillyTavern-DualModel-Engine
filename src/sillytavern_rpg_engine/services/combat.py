"""Combat encounters: initiative, surprise, joins, and end of combat."""

from dataclasses import dataclass
from enum import StrEnum
import json
import sqlite3
from typing import Any, Callable

from ..domain.dice import D20Mode, DiceRoller, resolve_d20
from ..domain.dnd import (
    CONDITION_EFFECTS,
    ActionType,
    HIDE_DC,
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


class MovementMode(StrEnum):
    """Movement cost modes; numeric multiplier on feet spent."""

    WALK = "walk"
    JUMP = "jump"
    DIFFICULT = "difficult"
    CRAWL = "crawl"
    CLIMB = "climb"
    SWIM = "swim"
    STAND_UP = "stand_up"
    FORCED = "forced"
    MOUNTED = "mounted"


def _movement_cost(mode: MovementMode, feet: int, speed: int) -> int:
    if mode is MovementMode.FORCED:
        return 0
    if mode is MovementMode.STAND_UP:
        return speed // 2
    if mode in (MovementMode.DIFFICULT, MovementMode.CRAWL, MovementMode.CLIMB, MovementMode.SWIM):
        return feet * 2
    return feet


def _debuffs(row) -> dict[str, Any]:
    return json.loads(row["debuffs_json"])


def add_debuff(
    connection: sqlite3.Connection,
    combatant_id: str,
    name: str,
    value: dict[str, Any],
) -> None:
    """Attach one debuff/marker; ``value`` must carry ``source`` and
    ``clear`` ("start" = source's next turn start, "end" = source's next
    turn end)."""
    row = connection.execute(
        "SELECT debuffs_json FROM combatants WHERE id = ?", (combatant_id,)
    ).fetchone()
    debuffs = json.loads(row["debuffs_json"])
    debuffs[name] = value
    connection.execute(
        "UPDATE combatants SET debuffs_json = ? WHERE id = ?",
        (json.dumps(debuffs, sort_keys=True), combatant_id),
    )


def _clear_debuffs(
    connection: sqlite3.Connection, combatants, source_id: str, policy: str
) -> None:
    """Expire debuffs sourced by ``source_id`` under ``policy`` ("start"/"end");
    entries with a ``turns`` counter survive until it reaches 0."""
    for row in combatants:
        debuffs = _debuffs(row)
        kept: dict[str, Any] = {}
        changed = False
        for key, value in debuffs.items():
            if value.get("source") == source_id and value.get("clear") == policy:
                turns = value.get("turns")
                if turns is None or turns <= 1:
                    changed = True
                    continue
                value = {**value, "turns": turns - 1}
                changed = True
            kept[key] = value
        if changed:
            connection.execute(
                "UPDATE combatants SET debuffs_json = ? WHERE id = ?",
                (json.dumps(kept, sort_keys=True), row["id"]),
            )


@dataclass(frozen=True)
class AdvanceTurnOperation:
    """End the active turn, start the next: budget resets, debuff expiry,
    concentration countdown, and Recharge rolls. Wrapping starts a new round."""

    roller: DiceRoller
    roll_id_factory: Callable[[], str]

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        encounter, combatants = require_active_encounter(
            connection, context.campaign.id, context.branch_id
        )
        ending = combatants[encounter["active_index"]]
        _clear_debuffs(connection, combatants, ending["entity_id"], "end")
        next_index = encounter["active_index"] + 1
        round_number = encounter["round_number"]
        if next_index >= len(combatants):
            next_index = 0
            round_number += 1
        connection.execute(
            "UPDATE combat_encounters SET active_index = ?, round_number = ?"
            " WHERE id = ?",
            (next_index, round_number, encounter["id"]),
        )
        starting = combatants[next_index]
        _clear_debuffs(connection, combatants, starting["entity_id"], "start")
        conditions = condition_map(
            connection, context.campaign.id, starting["entity_id"]
        )
        speed = read_int(
            connection, context.campaign.id, starting["entity_id"], "speed"
        )
        concentration_rounds = starting["concentration_rounds"]
        concentrating = starting["concentrating_spell"]
        if concentrating is not None and concentration_rounds is not None:
            concentration_rounds -= 1
            if concentration_rounds <= 0:
                concentrating = None
                concentration_rounds = None
        recharge = json.loads(starting["recharge_json"])
        recharged: list[str] = []
        for ability in recharge:
            if not ability.get("available", True):
                faces = self.roller.roll(1, 6)
                record_roll(
                    connection, context, self.roll_id_factory(),
                    purpose=f"recharge: {ability['name']}", formula="1d6",
                    faces=faces, modifier=0, total=faces[0],
                    roller_entity_id=starting["entity_id"],
                )
                if faces[0] >= ability["min"]:
                    ability["available"] = True
                    recharged.append(ability["name"])
        debuffs = _debuffs(starting)
        movement = effective_speed(speed, conditions)
        if "slow" in debuffs:
            movement = max(0, movement - int(debuffs["slow"].get("amount", 0)))
        connection.execute(
            "UPDATE combatants SET action_used = 0, bonus_used = 0,"
            " reaction_used = 0, interaction_used = 0, slot_spent_this_turn = 0,"
            " attacks_this_turn = 0,"
            " movement_total = ?, movement_used = 0, dodging = 0, disengaged = 0,"
            " mastery_uses_json = '{}', concentrating_spell = ?,"
            " concentration_rounds = ?, recharge_json = ? WHERE id = ?",
            (
                movement,
                concentrating,
                concentration_rounds,
                json.dumps(recharge),
                starting["id"],
            ),
        )
        return {
            "encounter_id": encounter["id"],
            "round": round_number,
            "active_entity_id": starting["entity_id"],
            "concentration_ended": concentrating is None
            and starting["concentrating_spell"] is not None,
            "recharged": recharged,
        }


@dataclass(frozen=True)
class UseActionOperation:
    """Spend the active combatant's action on a 2024 action type."""

    entity_id: str
    action: ActionType
    roller: DiceRoller | None = None
    roll_id: str | None = None
    skill: str | None = None
    ability: str = "dex"
    dc: int = HIDE_DC
    help_ally: str | None = None
    help_target: str | None = None
    readied: dict[str, Any] | None = None
    turn_id: str | None = None

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        if self.action in (ActionType.ATTACK, ActionType.MAGIC):
            raise ValidationError(
                "attack and magic are resolved by their own operations"
            )
        encounter, combatants = require_active_encounter(
            connection, context.campaign.id, context.branch_id
        )
        current = active_combatant(encounter, combatants)
        if current["entity_id"] != self.entity_id:
            raise ValidationError(
                f"it is not {self.entity_id!r}'s turn (not the active combatant)"
            )
        if current["action_used"]:
            raise ValidationError("action already used this turn")
        conditions = condition_map(
            connection, context.campaign.id, self.entity_id
        )
        if any(CONDITION_EFFECTS[c].incapacitated for c in conditions):
            raise ValidationError("incapacitated creatures cannot take actions")
        payload: dict[str, Any] = {
            "entity_id": self.entity_id,
            "action": self.action.value,
        }
        if self.action is ActionType.DASH:
            speed = read_int(connection, context.campaign.id, self.entity_id, "speed")
            connection.execute(
                "UPDATE combatants SET movement_total = movement_total + ?"
                " WHERE id = ?",
                (effective_speed(speed, conditions), current["id"]),
            )
        elif self.action is ActionType.DISENGAGE:
            connection.execute(
                "UPDATE combatants SET disengaged = 1 WHERE id = ?", (current["id"],)
            )
        elif self.action is ActionType.DODGE:
            connection.execute(
                "UPDATE combatants SET dodging = 1 WHERE id = ?", (current["id"],)
            )
        elif self.action is ActionType.HELP:
            if not self.help_ally or not self.help_target:
                raise ValidationError("help requires help_ally and help_target")
            ally = next(
                (row for row in combatants if row["entity_id"] == self.help_ally),
                None,
            )
            if ally is None:
                raise ValidationError("help target ally is not in the encounter")
            grants = json.loads(ally["help_grants_json"])
            grants.append({"ally": self.help_ally, "target": self.help_target})
            connection.execute(
                "UPDATE combatants SET help_grants_json = ? WHERE id = ?",
                (json.dumps(grants), ally["id"]),
            )
        elif self.action is ActionType.HIDE:
            payload["check"] = self._skill_check(
                connection, context, "dex", "stealth", HIDE_DC, "hide"
            )
            if payload["check"]["success"]:
                connection.execute(
                    "UPDATE combatants SET hidden = 1 WHERE id = ?", (current["id"],)
                )
        elif self.action in (ActionType.SEARCH, ActionType.STUDY, ActionType.INFLUENCE):
            if not self.skill:
                raise ValidationError(f"{self.action.value} requires a skill")
            payload["check"] = self._skill_check(
                connection, context, self.ability, self.skill, self.dc,
                f"{self.action.value}: {self.skill}",
            )
        elif self.action is ActionType.READY:
            if not self.readied:
                raise ValidationError("ready requires a readied action payload")
            connection.execute(
                "UPDATE combatants SET readied_action_json = ? WHERE id = ?",
                (json.dumps(self.readied), current["id"]),
            )
        connection.execute(
            "UPDATE combatants SET action_used = 1 WHERE id = ?", (current["id"],)
        )
        return payload

    def _skill_check(self, connection, context, ability, skill, dc, purpose) -> dict:
        if self.roller is None or self.roll_id is None:
            raise ValidationError(f"{purpose} requires a roller")
        from .dice import CheckOperation

        return CheckOperation(
            roller=self.roller,
            roll_id=self.roll_id,
            entity_id=self.entity_id,
            ability=ability,
            skill=skill,
            dc=dc,
            purpose=purpose,
            turn_id=self.turn_id,
        ).apply(connection, context)


@dataclass(frozen=True)
class UseReactionOperation:
    """Mark the active-round reaction spent (opportunity attack, readied
    trigger, etc.); the concrete effect is resolved by its own operation."""

    entity_id: str
    purpose: str

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        _, combatants = require_active_encounter(
            connection, context.campaign.id, context.branch_id
        )
        row = next(
            (r for r in combatants if r["entity_id"] == self.entity_id), None
        )
        if row is None:
            raise ValidationError(f"{self.entity_id!r} is not in the encounter")
        if row["reaction_used"]:
            raise ValidationError("reaction already used this round")
        conditions = condition_map(
            connection, context.campaign.id, self.entity_id
        )
        if any(CONDITION_EFFECTS[c].incapacitated for c in conditions):
            raise ValidationError("incapacitated creatures cannot take reactions")
        connection.execute(
            "UPDATE combatants SET reaction_used = 1 WHERE id = ?", (row["id"],)
        )
        return {"entity_id": self.entity_id, "purpose": self.purpose}


@dataclass(frozen=True)
class MoveOperation:
    """Spend movement on the active turn; cost depends on the mode."""

    entity_id: str
    feet: int
    mode: MovementMode

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        if isinstance(self.feet, bool) or not isinstance(self.feet, int) or self.feet < 0:
            raise ValidationError("feet must be a non-negative integer")
        encounter, combatants = require_active_encounter(
            connection, context.campaign.id, context.branch_id
        )
        row = next(
            (r for r in combatants if r["entity_id"] == self.entity_id), None
        )
        if row is None:
            raise ValidationError(f"{self.entity_id!r} is not in the encounter")
        if self.mode is not MovementMode.FORCED:
            current = active_combatant(encounter, combatants)
            if current["entity_id"] != self.entity_id:
                raise ValidationError(
                    f"it is not {self.entity_id!r}'s turn (not the active combatant)"
                )
        conditions = condition_map(
            connection, context.campaign.id, self.entity_id
        )
        base = effective_speed(
            read_int(connection, context.campaign.id, self.entity_id, "speed"),
            conditions,
        )
        cost = _movement_cost(self.mode, self.feet, base)
        if row["movement_used"] + cost > row["movement_total"]:
            raise ValidationError(
                f"movement budget exceeded: used {row['movement_used']},"
                f" cost {cost}, total {row['movement_total']}"
            )
        connection.execute(
            "UPDATE combatants SET movement_used = movement_used + ? WHERE id = ?",
            (cost, row["id"]),
        )
        return {
            "entity_id": self.entity_id,
            "mode": self.mode.value,
            "feet": self.feet,
            "cost": cost,
            "movement_used": row["movement_used"] + cost,
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

    def advance_turn(
        self, campaign_id: str, expected_version: int, roller: DiceRoller
    ) -> MutationResult:
        return self._apply(
            campaign_id, expected_version, "turn-advanced",
            AdvanceTurnOperation(roller, self.id_factory),
        )

    def use_action(
        self,
        campaign_id: str,
        expected_version: int,
        entity_id: str,
        action: ActionType,
        **kwargs,
    ) -> MutationResult:
        if "roller" in kwargs and kwargs["roller"] is not None:
            kwargs.setdefault("roll_id", self.id_factory())
        return self._apply(
            campaign_id, expected_version, "action-used",
            UseActionOperation(entity_id, action, **kwargs),
        )

    def use_reaction(
        self, campaign_id: str, expected_version: int, entity_id: str, purpose: str
    ) -> MutationResult:
        return self._apply(
            campaign_id, expected_version, "reaction-used",
            UseReactionOperation(entity_id, purpose),
        )

    def move(
        self,
        campaign_id: str,
        expected_version: int,
        entity_id: str,
        feet: int,
        mode: MovementMode,
    ) -> MutationResult:
        return self._apply(
            campaign_id, expected_version, "moved",
            MoveOperation(entity_id, feet, mode),
        )
