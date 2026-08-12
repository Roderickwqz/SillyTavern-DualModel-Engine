"""2024 Weapon Mastery effects; called from attack resolution."""

import json
import sqlite3
from typing import Any

from ..domain.dice import D20Mode, DiceRoller, resolve_d20
from ..domain.dnd import (
    Condition,
    CreatureSize,
    MasteryProperty,
    WeaponProperty,
    ability_modifier,
)
from ..domain.errors import ValidationError
from ..domain.operations import MutationContext
from .attacks import AttackSpec, resolve_strike
from .combat import add_debuff
from .conditions import ApplyConditionOperation
from .damage import DamageResult, apply_damage
from .dice import record_roll
from .dnd_pack import read_int, read_list

def _mastered(connection, campaign_id: str, attacker_id: str, spec: AttackSpec) -> bool:
    return spec.weapon.mastery is not None and spec.weapon.key in read_list(
        connection, campaign_id, attacker_id, "weapon_masteries"
    )


def mastery_used(row, name: str) -> bool:
    return bool(json.loads(row["mastery_uses_json"]).get(name))


def mark_mastery(connection: sqlite3.Connection, row_id: str, name: str) -> None:
    row = connection.execute(
        "SELECT mastery_uses_json FROM combatants WHERE id = ?", (row_id,)
    ).fetchone()
    uses = json.loads(row["mastery_uses_json"])
    uses[name] = True
    connection.execute(
        "UPDATE combatants SET mastery_uses_json = ? WHERE id = ?",
        (json.dumps(uses, sort_keys=True), row_id),
    )


def on_miss(
    connection: sqlite3.Connection,
    context: MutationContext,
    roller: DiceRoller,
    roll_id_factory,
    attacker_row,
    spec: AttackSpec,
) -> dict[str, Any] | None:
    """Graze: a miss still deals the attack ability modifier as damage."""
    if spec.weapon.mastery is not MasteryProperty.GRAZE:
        return None
    if not _mastered(connection, context.campaign.id, attacker_row["entity_id"], spec):
        return None
    score = read_int(
        connection, context.campaign.id, attacker_row["entity_id"],
        f"ability_{spec.weapon.attack_ability}",
    )
    amount = max(0, ability_modifier(score))
    result = apply_damage(
        connection, context, roller, roll_id_factory, spec.target_id,
        amount, spec.weapon.damage_type,
    )
    return {"property": "graze", "damage": amount, "result": result.__dict__}


def on_hit(
    connection: sqlite3.Connection,
    context: MutationContext,
    roller: DiceRoller,
    roll_id_factory,
    attacker_row,
    spec: AttackSpec,
    damage_result: DamageResult,
) -> dict[str, Any] | None:
    mastery = spec.weapon.mastery
    if mastery is None or not _mastered(
        connection, context.campaign.id, attacker_row["entity_id"], spec
    ):
        return None
    campaign_id = context.campaign.id
    attacker_id = attacker_row["entity_id"]
    if mastery is MasteryProperty.CLEAVE:
        if WeaponProperty.RANGED in spec.weapon.properties:
            return {"property": "cleave", "used": False}
        if mastery_used(attacker_row, mastery.value):
            raise ValidationError(f"{mastery.value} already used this turn")
        if not spec.cleave_target_id:
            return {"property": "cleave", "used": False}
        mark_mastery(connection, attacker_row["id"], mastery.value)
        second = AttackSpec(
            target_id=spec.cleave_target_id,
            weapon=spec.weapon,
            distance_ft=spec.distance_ft,
            suppress_positive_modifier=True,
        )
        payload = resolve_strike(
            connection, context, roller, roll_id_factory, attacker_id, second,
            spend_budget=False,
        )
        return {"property": "cleave", "attack": payload}
    if mastery is MasteryProperty.NICK:
        return {"property": "nick"}
    if mastery is MasteryProperty.PUSH:
        if spec.target_size.rank > CreatureSize.LARGE.rank:
            return {"property": "push", "applied": False}
        return {"property": "push", "applied": True, "feet": 10}
    if mastery is MasteryProperty.SAP:
        target_row = connection.execute(
            "SELECT id FROM combatants WHERE encounter_id = ? AND entity_id = ?",
            (attacker_row["encounter_id"], spec.target_id),
        ).fetchone()
        add_debuff(connection, target_row["id"], "sap",
                   {"source": attacker_id, "clear": "start"})
        return {"property": "sap"}
    if mastery is MasteryProperty.SLOW:
        if damage_result.hp_lost + damage_result.temp_absorbed <= 0:
            return {"property": "slow", "applied": False}
        target_row = connection.execute(
            "SELECT id FROM combatants WHERE encounter_id = ? AND entity_id = ?",
            (attacker_row["encounter_id"], spec.target_id),
        ).fetchone()
        add_debuff(connection, target_row["id"], "slow",
                   {"source": attacker_id, "clear": "start", "amount": 10})
        return {"property": "slow"}
    if mastery is MasteryProperty.TOPPLE:
        modifier = ability_modifier(
            read_int(connection, campaign_id, attacker_id,
                     f"ability_{spec.weapon.attack_ability}")
        )
        dc = 8 + modifier + read_int(
            connection, campaign_id, attacker_id, "proficiency_bonus"
        )
        save_mod = ability_modifier(
            read_int(connection, campaign_id, spec.target_id, "ability_con")
        )
        outcome = resolve_d20(roller, D20Mode.NORMAL, save_mod)
        payload = record_roll(
            connection, context, roll_id_factory(),
            purpose="topple save", formula="1d20", faces=(outcome.kept,),
            modifier=save_mod, total=outcome.total, dc=dc,
            success=outcome.total >= dc, roller_entity_id=spec.target_id,
        )
        applied = outcome.total < dc
        if applied:
            ApplyConditionOperation(
                spec.target_id, Condition.PRONE, f"topple:{attacker_id}"
            ).apply(connection, context)
        return {"property": "topple", "save": payload, "applied": applied}
    if mastery is MasteryProperty.VEX:
        if damage_result.hp_lost + damage_result.temp_absorbed <= 0:
            return {"property": "vex", "applied": False}
        add_debuff(connection, attacker_row["id"], "vex_vs",
                   {"target": spec.target_id, "source": attacker_id,
                    "clear": "end", "turns": 2})
        return {"property": "vex"}
    return None
