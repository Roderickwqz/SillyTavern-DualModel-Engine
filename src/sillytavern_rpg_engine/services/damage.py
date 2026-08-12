"""Damage, healing, dying, death saves, and concentration checks."""

from dataclasses import dataclass
import sqlite3
from typing import Any, Callable

from ..domain.dice import D20Mode, DiceRoller, resolve_d20
from ..domain.dnd import (
    CONDITION_EFFECTS,
    Condition,
    DamageType,
    ability_modifier,
    require_dnd_2024,
)
from ..domain.errors import ValidationError
from ..domain.operations import MutationContext
from ..persistence.database import Database
from .attributes import SetAttributeOperation
from .conditions import ApplyConditionOperation, RemoveConditionOperation, condition_map
from .dice import record_roll
from .dnd_pack import read_bool, read_int, read_list
from .mutations import MutationEngine, MutationRequest, MutationResult


@dataclass(frozen=True)
class DamageResult:
    raw: int
    mitigated: int
    temp_absorbed: int
    hp_lost: int
    new_hp: int
    unconscious: bool
    died: bool
    concentration_broken: bool


def _mitigate(
    connection: sqlite3.Connection,
    campaign_id: str,
    target_id: str,
    amount: int,
    damage_type: DamageType,
) -> int:
    if damage_type.value in read_list(connection, campaign_id, target_id, "immunities"):
        return 0
    conditions = condition_map(connection, campaign_id, target_id)
    resistant = CONDITION_EFFECTS[Condition.PETRIFIED].resist_all_damage and Condition.PETRIFIED in conditions
    resistant = resistant or damage_type.value in read_list(
        connection, campaign_id, target_id, "resistances"
    )
    vulnerable = damage_type.value in read_list(
        connection, campaign_id, target_id, "vulnerabilities"
    )
    if resistant:
        amount //= 2
    if vulnerable:
        amount *= 2
    return amount


def _concentration_save(
    connection: sqlite3.Connection,
    context: MutationContext,
    roller: DiceRoller | None,
    roll_id_factory: Callable[[], str] | None,
    target_id: str,
    damage_taken: int,
) -> bool:
    """Roll the concentration save when the target concentrates in combat;
    returns True when concentration broke."""
    row = connection.execute(
        "SELECT c.id FROM combatants c"
        " JOIN combat_encounters e ON e.id = c.encounter_id"
        " WHERE e.campaign_id = ? AND e.branch_id = ? AND e.status = 'active'"
        " AND c.entity_id = ? AND c.concentrating_spell IS NOT NULL",
        (context.campaign.id, context.branch_id, target_id),
    ).fetchone()
    if row is None or damage_taken <= 0:
        return False
    if roller is None or roll_id_factory is None:
        raise ValidationError("concentration check requires a roller")
    dc = max(10, damage_taken // 2)
    modifier = ability_modifier(
        read_int(connection, context.campaign.id, target_id, "ability_con")
    )
    outcome = resolve_d20(roller, D20Mode.NORMAL, modifier)
    record_roll(
        connection, context, roll_id_factory(),
        purpose="concentration", formula="1d20", faces=(outcome.kept,),
        modifier=modifier, total=outcome.total, dc=dc,
        success=outcome.total >= dc, roller_entity_id=target_id,
    )
    if outcome.total >= dc:
        return False
    connection.execute(
        "UPDATE combatants SET concentrating_spell = NULL,"
        " concentration_rounds = NULL WHERE id = ?",
        (row["id"],),
    )
    return True


def apply_damage(
    connection: sqlite3.Connection,
    context: MutationContext,
    roller: DiceRoller | None,
    roll_id_factory: Callable[[], str] | None,
    target_id: str,
    amount: int,
    damage_type: str,
    *,
    is_crit_hit_5ft: bool = False,
    turn_id: str | None = None,
) -> DamageResult:
    """Mitigate, consume temp HP, reduce HP, and apply dying/concentration."""
    require_dnd_2024(context.campaign)
    campaign_id = context.campaign.id
    try:
        dtype = DamageType(damage_type)
    except ValueError as exc:
        raise ValidationError(f"invalid damage_type {damage_type!r}") from exc
    if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
        raise ValidationError("damage amount must be a non-negative integer")
    if read_bool(connection, campaign_id, target_id, "is_dead"):
        raise ValidationError("cannot damage a dead entity")
    mitigated = _mitigate(connection, campaign_id, target_id, amount, dtype)
    temp = read_int(connection, campaign_id, target_id, "hp_temp")
    hp = read_int(connection, campaign_id, target_id, "hp_current")
    absorbed = min(temp, mitigated)
    if absorbed:
        SetAttributeOperation(target_id, "hp_temp", temp - absorbed).apply(
            connection, context
        )
    remaining = mitigated - absorbed
    hp_lost = min(hp, remaining)
    new_hp = hp - hp_lost
    died = False
    unconscious = False
    conditions = condition_map(connection, campaign_id, target_id)
    if Condition.UNCONSCIOUS in conditions and hp == 0 and mitigated > 0:
        failures = read_int(connection, campaign_id, target_id, "death_saves_failure")
        failures += 2 if is_crit_hit_5ft else 1
        SetAttributeOperation(
            target_id, "death_saves_failure", min(failures, 3)
        ).apply(connection, context)
        if failures >= 3:
            died = True
    if hp_lost:
        SetAttributeOperation(target_id, "hp_current", new_hp).apply(connection, context)
    if new_hp == 0 and hp > 0:
        overflow = remaining - hp_lost
        if overflow >= read_int(connection, campaign_id, target_id, "hp_max"):
            died = True
        else:
            unconscious = True
            if Condition.UNCONSCIOUS not in conditions:
                ApplyConditionOperation(target_id, Condition.UNCONSCIOUS, "damage").apply(
                    connection, context
                )
            SetAttributeOperation(target_id, "is_stable", False).apply(connection, context)
    if died:
        SetAttributeOperation(target_id, "is_dead", True).apply(connection, context)
    broken = _concentration_save(
        connection, context, roller, roll_id_factory, target_id, mitigated
    )
    return DamageResult(
        raw=amount, mitigated=mitigated, temp_absorbed=absorbed, hp_lost=hp_lost,
        new_hp=new_hp, unconscious=unconscious, died=died,
        concentration_broken=broken,
    )


@dataclass(frozen=True)
class ApplyDamageOperation:
    roller: DiceRoller | None
    roll_id_factory: Callable[[], str] | None
    target_id: str
    amount: int
    damage_type: str
    is_crit_hit_5ft: bool = False
    turn_id: str | None = None

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        result = apply_damage(
            connection, context, self.roller, self.roll_id_factory,
            self.target_id, self.amount, self.damage_type,
            is_crit_hit_5ft=self.is_crit_hit_5ft, turn_id=self.turn_id,
        )
        return {"target_id": self.target_id, **result.__dict__}


@dataclass(frozen=True)
class HealOperation:
    target_id: str
    amount: int

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        campaign_id = context.campaign.id
        if isinstance(self.amount, bool) or not isinstance(self.amount, int) or self.amount < 1:
            raise ValidationError("healing amount must be a positive integer")
        if read_bool(connection, campaign_id, self.target_id, "is_dead"):
            raise ValidationError("cannot heal a dead entity")
        hp_max = read_int(connection, campaign_id, self.target_id, "hp_max")
        hp = read_int(connection, campaign_id, self.target_id, "hp_current")
        new_hp = min(hp_max, hp + self.amount)
        SetAttributeOperation(self.target_id, "hp_current", new_hp).apply(
            connection, context
        )
        if hp == 0 < new_hp:
            conditions = condition_map(connection, campaign_id, self.target_id)
            if Condition.UNCONSCIOUS in conditions:
                RemoveConditionOperation(self.target_id, Condition.UNCONSCIOUS).apply(
                    connection, context
                )
            SetAttributeOperation(self.target_id, "is_stable", False).apply(
                connection, context
            )
            SetAttributeOperation(self.target_id, "death_saves_success", 0).apply(
                connection, context
            )
            SetAttributeOperation(self.target_id, "death_saves_failure", 0).apply(
                connection, context
            )
        return {"target_id": self.target_id, "healed": new_hp - hp, "hp": new_hp}


@dataclass(frozen=True)
class StabilizeOperation:
    target_id: str

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        campaign_id = context.campaign.id
        if read_bool(connection, campaign_id, self.target_id, "is_dead"):
            raise ValidationError("cannot stabilize a dead entity")
        conditions = condition_map(connection, campaign_id, self.target_id)
        if Condition.UNCONSCIOUS not in conditions or read_int(
            connection, campaign_id, self.target_id, "hp_current"
        ) != 0:
            raise ValidationError("entity is not dying")
        SetAttributeOperation(self.target_id, "is_stable", True).apply(connection, context)
        SetAttributeOperation(self.target_id, "death_saves_success", 0).apply(
            connection, context
        )
        SetAttributeOperation(self.target_id, "death_saves_failure", 0).apply(
            connection, context
        )
        return {"target_id": self.target_id, "stable": True}


@dataclass(frozen=True)
class DeathSaveOperation:
    roller: DiceRoller
    roll_id: str
    target_id: str

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        campaign_id = context.campaign.id
        conditions = condition_map(connection, campaign_id, self.target_id)
        if read_bool(connection, campaign_id, self.target_id, "is_dead"):
            raise ValidationError("the entity is already dead")
        if Condition.UNCONSCIOUS not in conditions or read_int(
            connection, campaign_id, self.target_id, "hp_current"
        ) != 0:
            raise ValidationError("death saves require a dying (0 HP) entity")
        outcome = resolve_d20(self.roller, D20Mode.NORMAL, 0)
        payload = record_roll(
            connection, context, self.roll_id,
            purpose="death save", formula="1d20", faces=(outcome.kept,),
            modifier=0, total=outcome.total, roller_entity_id=self.target_id,
        )
        if outcome.is_natural_20:
            SetAttributeOperation(self.target_id, "hp_current", 1).apply(connection, context)
            RemoveConditionOperation(self.target_id, Condition.UNCONSCIOUS).apply(
                connection, context
            )
            SetAttributeOperation(self.target_id, "is_stable", False).apply(
                connection, context
            )
            SetAttributeOperation(self.target_id, "death_saves_success", 0).apply(
                connection, context
            )
            SetAttributeOperation(self.target_id, "death_saves_failure", 0).apply(
                connection, context
            )
            return {**payload, "outcome": "revived"}
        successes = read_int(connection, campaign_id, self.target_id, "death_saves_success")
        failures = read_int(connection, campaign_id, self.target_id, "death_saves_failure")
        if outcome.is_natural_1:
            failures += 2
        elif outcome.total >= 10:
            successes += 1
        else:
            failures += 1
        result = "rolling"
        if failures >= 3:
            SetAttributeOperation(
                self.target_id, "death_saves_failure", min(failures, 3)
            ).apply(connection, context)
            SetAttributeOperation(self.target_id, "is_dead", True).apply(connection, context)
            result = "died"
        else:
            SetAttributeOperation(
                self.target_id, "death_saves_success", successes
            ).apply(connection, context)
            SetAttributeOperation(
                self.target_id, "death_saves_failure", failures
            ).apply(connection, context)
            if successes >= 3:
                SetAttributeOperation(self.target_id, "is_stable", True).apply(
                    connection, context
                )
                result = "stable"
        return {**payload, "outcome": result}


class DamageService:
    """User-command entry points for damage, healing, and death saves."""

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

    def _apply(self, campaign_id, expected_version, event_type, operation):
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id, branch_id="main",
                expected_version=expected_version, source="user-command",
                event_type=event_type, operation=operation,
            )
        )

    def deal(self, campaign_id, expected_version, target_id, amount, damage_type,
             is_crit_hit_5ft=False, roller=None):
        factory = self.id_factory if roller is not None else None
        return self._apply(campaign_id, expected_version, "damage-dealt",
                           ApplyDamageOperation(roller, factory, target_id, amount,
                                                damage_type, is_crit_hit_5ft))

    def heal(self, campaign_id, expected_version, target_id, amount):
        return self._apply(campaign_id, expected_version, "healed",
                           HealOperation(target_id, amount))

    def stabilize(self, campaign_id, expected_version, target_id):
        return self._apply(campaign_id, expected_version, "stabilized",
                           StabilizeOperation(target_id))

    def death_save(self, campaign_id, expected_version, roller, target_id):
        return self._apply(campaign_id, expected_version, "death-save",
                           DeathSaveOperation(roller, self.id_factory(), target_id))
