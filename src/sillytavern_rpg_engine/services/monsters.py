"""Monster mechanics: multiattack, and data-driven turn triggers."""

from dataclasses import dataclass
import json
import sqlite3
from typing import Any, Callable

from ..domain.dice import D20Mode, DiceFormula, DiceRoller, resolve_d20, roll_formula
from ..domain.dnd import (
    Condition,
    WeaponProperty,
    ability_modifier,
    require_dnd_2024,
)
from ..domain.errors import ValidationError
from ..domain.operations import MutationContext
from ..persistence.database import Database
from .attacks import AttackSpec, resolve_strike
from .conditions import ApplyConditionOperation
from .damage import HealOperation, apply_damage
from .dice import record_roll
from .dnd_pack import read_int
from .mutations import MutationEngine, MutationRequest, MutationResult


@dataclass(frozen=True)
class MultiattackOperation:
    """One Attack action resolving every listed strike (monster multiattack)."""

    roller: DiceRoller
    roll_id_factory: Callable[[], str]
    attacker_id: str
    attacks: tuple[AttackSpec, ...]

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        if len(self.attacks) < 2:
            raise ValidationError("multiattack requires at least two strikes")
        for spec in self.attacks:
            if WeaponProperty.LOADING in spec.weapon.properties:
                raise ValidationError("loading weapons cannot multiattack")
        from .combat import require_active_encounter

        encounter, _ = require_active_encounter(
            connection, context.campaign.id, context.branch_id
        )
        first, *rest = self.attacks
        payloads = [
            resolve_strike(
                connection, context, self.roller, self.roll_id_factory,
                self.attacker_id, first,
            )
        ]
        for spec in rest:
            payloads.append(
                resolve_strike(
                    connection, context, self.roller, self.roll_id_factory,
                    self.attacker_id, spec, spend_budget=False,
                )
            )
            connection.execute(
                "UPDATE combatants SET attacks_this_turn = attacks_this_turn + 1"
                " WHERE encounter_id = ? AND entity_id = ?",
                (encounter["id"], self.attacker_id),
            )
        return {"attacker_id": self.attacker_id, "strikes": payloads}


def process_turn_triggers(
    connection: sqlite3.Connection,
    context: MutationContext,
    roller: DiceRoller,
    roll_id_factory: Callable[[], str],
    combatant_row,
    when: str,
    combatants,
) -> list[dict[str, Any]]:
    """Run ``when`` ("turn_start"/"turn_end") trigger effects.

    ``heal`` and ``damage`` target the owning combatant's entity;
    ``condition`` targets every OTHER combatant whose save fails (aura).
    """
    fired: list[dict[str, Any]] = []
    owner = combatant_row["entity_id"]
    for trigger in json.loads(combatant_row["triggers_json"]):
        if trigger.get("when") != when:
            continue
        effect = trigger["effect"]
        kind = effect["kind"]
        entry: dict[str, Any] = {"name": trigger["name"], "kind": kind}
        if kind == "heal":
            rolled = roll_formula(roller, DiceFormula.parse(effect["formula"]))
            record_roll(
                connection, context, roll_id_factory(),
                purpose=f"trigger: {trigger['name']}", formula=effect["formula"],
                faces=rolled.faces, modifier=0, total=rolled.total,
                roller_entity_id=owner,
            )
            entry["result"] = HealOperation(owner, rolled.total).apply(
                connection, context
            )
        elif kind == "damage":
            rolled = roll_formula(roller, DiceFormula.parse(effect["formula"]))
            record_roll(
                connection, context, roll_id_factory(),
                purpose=f"trigger: {trigger['name']}", formula=effect["formula"],
                faces=rolled.faces, modifier=0, total=rolled.total,
                roller_entity_id=owner,
            )
            entry["result"] = apply_damage(
                connection, context, roller, roll_id_factory, owner,
                rolled.total, effect["damage_type"],
            ).__dict__
        elif kind == "condition":
            targets = [r for r in combatants if r["entity_id"] != owner]
            affected = []
            for target in targets:
                ability = effect["save_ability"]
                modifier = ability_modifier(
                    read_int(connection, context.campaign.id,
                             target["entity_id"], f"ability_{ability}")
                )
                outcome = resolve_d20(roller, D20Mode.NORMAL, modifier)
                record_roll(
                    connection, context, roll_id_factory(),
                    purpose=f"trigger save: {trigger['name']}", formula="1d20",
                    faces=(outcome.kept,), modifier=modifier, total=outcome.total,
                    dc=effect["save_dc"], success=outcome.total >= effect["save_dc"],
                    roller_entity_id=target["entity_id"],
                )
                if outcome.total < effect["save_dc"]:
                    ApplyConditionOperation(
                        target["entity_id"], Condition(effect["condition"]),
                        effect.get("source", trigger["name"]),
                    ).apply(connection, context)
                    affected.append(target["entity_id"])
            entry["affected"] = affected
        else:
            raise ValidationError(f"unknown trigger effect kind {kind!r}")
        fired.append(entry)
    return fired


@dataclass(frozen=True)
class EnvironmentalDamageOperation:
    """Falling, environmental, and improvised damage via explicit formula."""

    roller: DiceRoller
    roll_id_factory: Callable[[], str]
    entity_id: str
    formula: str
    damage_type: str
    purpose: str

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        parsed = DiceFormula.parse(self.formula)
        rolled = roll_formula(self.roller, parsed)
        record_roll(
            connection, context, self.roll_id_factory(),
            purpose=self.purpose, formula=self.formula, faces=rolled.faces,
            modifier=0, total=rolled.total, roller_entity_id=self.entity_id,
        )
        result = apply_damage(
            connection, context, self.roller, self.roll_id_factory,
            self.entity_id, rolled.total, self.damage_type,
        )
        return {"entity_id": self.entity_id, **result.__dict__}


class MonsterService:
    """User-command entry points for monster mechanics."""

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

    def multiattack(self, campaign_id, expected_version, attacker_id, attacks, roller):
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id, branch_id="main",
                expected_version=expected_version, source="user-command",
                event_type="multiattack",
                operation=MultiattackOperation(
                    roller, self.id_factory, attacker_id, tuple(attacks)
                ),
            )
        )

    def environmental(self, campaign_id, expected_version, entity_id, formula,
                      damage_type, purpose, roller):
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id, branch_id="main",
                expected_version=expected_version, source="user-command",
                event_type="environmental-damage",
                operation=EnvironmentalDamageOperation(
                    roller, self.id_factory, entity_id, formula, damage_type, purpose
                ),
            )
        )
