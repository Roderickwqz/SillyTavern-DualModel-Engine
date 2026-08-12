"""Spell casting: Magic action budget, one-slot-per-turn, saves/attacks,
concentration lifecycle."""

from dataclasses import dataclass
import sqlite3
from typing import Any, Callable

from ..domain.dice import D20Mode, DiceRoller, resolve_d20, roll_formula
from ..domain.dnd import (
    CONDITION_EFFECTS,
    Cover,
    SpellProfile,
    ability_modifier,
    require_dnd_2024,
)
from ..domain.errors import NotFoundError, ValidationError
from ..domain.operations import MutationContext
from ..persistence.database import Database
from .attributes import SetAttributeOperation
from .combat import active_combatant, require_active_encounter
from .conditions import condition_map
from .damage import HealOperation, apply_damage
from .dice import read_exhaustion, record_roll
from .dnd_pack import read_int, read_list, validate_slot_list
from .mutations import MutationEngine, MutationRequest, MutationResult


@dataclass(frozen=True)
class CastSpellOperation:
    """Cast one spell, consuming action/slot budgets and resolving effects."""

    roller: DiceRoller
    roll_id_factory: Callable[[], str]
    caster_id: str
    spell: SpellProfile
    slot_level: int | None
    target_ids: tuple[str, ...] = ()
    cover: Cover = Cover.NONE
    distance_ft: int = 30
    turn_id: str | None = None

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        campaign_id = context.campaign.id
        combatant = self._spend_budgets(connection, context)
        self._spend_slot(connection, context)
        dc = 8 + read_int(connection, campaign_id, self.caster_id, "proficiency_bonus")
        dc += ability_modifier(
            read_int(connection, campaign_id, self.caster_id,
                     f"ability_{self.spell.ability}")
        )
        payload: dict[str, Any] = {
            "caster_id": self.caster_id,
            "spell": self.spell.key,
            "slot_level": self.slot_level,
            "save_dc": dc if self.spell.save_ability else None,
            "targets": [],
        }
        for target_id in self.target_ids:
            payload["targets"].append(
                self._resolve_target(connection, context, target_id, dc)
            )
        if self.spell.concentration and combatant is not None:
            connection.execute(
                "UPDATE combatants SET concentrating_spell = ?,"
                " concentration_rounds = ? WHERE id = ?",
                (self.spell.key, self.spell.duration_rounds, combatant["id"]),
            )
            payload["concentration"] = self.spell.duration_rounds
        elif self.spell.concentration:
            payload["concentration"] = "untracked out of combat"
        return payload

    def _spend_budgets(self, connection, context) -> Any | None:
        """Consume the Magic action; returns the caster combatant row (or
        None when no encounter is active)."""
        try:
            encounter, combatants = require_active_encounter(
                connection, context.campaign.id, context.branch_id
            )
        except NotFoundError:
            return None
        row = next(
            (r for r in combatants if r["entity_id"] == self.caster_id), None
        )
        if row is None:
            return None
        current = active_combatant(encounter, combatants)
        if current["entity_id"] != self.caster_id:
            raise ValidationError(f"it is not {self.caster_id!r}'s turn")
        if self.slot_level is not None and row["slot_spent_this_turn"]:
            raise ValidationError("only one spell slot may be spent per turn")
        if row["action_used"]:
            raise ValidationError("action already used this turn")
        conditions = condition_map(connection, context.campaign.id, self.caster_id)
        if any(CONDITION_EFFECTS[c].incapacitated for c in conditions):
            raise ValidationError("incapacitated creatures cannot cast")
        connection.execute(
            "UPDATE combatants SET action_used = 1, slot_spent_this_turn = ?"
            " WHERE id = ?",
            (1 if self.slot_level is not None else 0, row["id"]),
        )
        return row

    def _spend_slot(self, connection, context) -> None:
        campaign_id = context.campaign.id
        if self.slot_level is None:
            if self.spell.level != 0:
                raise ValidationError("only cantrips cast without a slot level")
            return
        if not 1 <= self.slot_level <= 9:
            raise ValidationError("slot level must be in 1..9")
        if self.slot_level < self.spell.level:
            raise ValidationError("slot level below the spell's level")
        current = validate_slot_list(
            read_list(connection, campaign_id, self.caster_id, "spell_slots_current")
        )
        maximum = validate_slot_list(
            read_list(connection, campaign_id, self.caster_id, "spell_slots_max")
        )
        if self.spell.level > 0 and maximum[self.spell.level - 1] == 0:
            raise ValidationError("caster has no slots of the spell's level")
        if maximum[self.slot_level - 1] == 0:
            raise ValidationError("caster has no slots at that slot level")
        if current[self.slot_level - 1] < 1:
            raise ValidationError("no remaining slots at that level")
        current[self.slot_level - 1] -= 1
        SetAttributeOperation(
            self.caster_id, "spell_slots_current", current
        ).apply(connection, context)

    def _resolve_target(self, connection, context, target_id, dc) -> dict:
        campaign_id = context.campaign.id
        spell = self.spell
        damage = spell.damage
        if spell.attack and damage is not None:
            modifier = ability_modifier(
                read_int(connection, campaign_id, self.caster_id,
                         f"ability_{spell.ability}")
            ) + read_int(connection, campaign_id, self.caster_id, "proficiency_bonus")
            modifier += -2 * read_exhaustion(connection, campaign_id, self.caster_id)
            outcome = resolve_d20(self.roller, D20Mode.NORMAL, modifier)
            armor_class = read_int(
                connection, campaign_id, target_id, "armor_class"
            ) + (self.cover.ac_bonus or 0)
            hit = not outcome.is_natural_1 and (
                outcome.is_natural_20 or outcome.total >= armor_class
            )
            record_roll(
                connection, context, self.roll_id_factory(),
                purpose=f"spell attack: {spell.key}", formula="1d20",
                faces=(outcome.kept,), modifier=modifier, total=outcome.total,
                dc=armor_class, success=hit, roller_entity_id=self.caster_id,
                turn_id=self.turn_id,
            )
            if not hit:
                return {"target_id": target_id, "hit": False}
            formula = damage
            if outcome.is_natural_20:
                from ..domain.dice import DiceFormula

                formula = DiceFormula(damage.count * 2, damage.sides, 0)
            rolled = roll_formula(self.roller, formula)
            result = apply_damage(
                connection, context, self.roller, self.roll_id_factory,
                target_id, rolled.total, spell.damage_type,
            )
            return {"target_id": target_id, "hit": True, "damage": result.__dict__}
        if spell.save_ability is not None:
            save_mod = ability_modifier(
                read_int(connection, campaign_id, target_id,
                         f"ability_{spell.save_ability}")
            )
            if f"save_{spell.save_ability}" in read_list(
                connection, campaign_id, target_id, "proficiencies"
            ):
                save_mod += read_int(
                    connection, campaign_id, target_id, "proficiency_bonus"
                )
            save_mod += -2 * read_exhaustion(connection, campaign_id, target_id)
            outcome = resolve_d20(self.roller, D20Mode.NORMAL, save_mod)
            success = outcome.total >= dc
            record_roll(
                connection, context, self.roll_id_factory(),
                purpose=f"save: {spell.key}", formula="1d20",
                faces=(outcome.kept,), modifier=save_mod, total=outcome.total,
                dc=dc, success=success, roller_entity_id=target_id,
                turn_id=self.turn_id,
            )
            entry: dict[str, Any] = {"target_id": target_id, "save": success}
            if damage is not None:
                rolled = roll_formula(self.roller, damage)
                amount = rolled.total // 2 if success else rolled.total
                result = apply_damage(
                    connection, context, self.roller, self.roll_id_factory,
                    target_id, amount, spell.damage_type,
                )
                entry["damage"] = result.__dict__
            return entry
        if damage is not None:
            rolled = roll_formula(self.roller, damage)
            result = apply_damage(
                connection, context, self.roller, self.roll_id_factory,
                target_id, rolled.total, spell.damage_type,
            )
            return {"target_id": target_id, "damage": result.__dict__}
        if spell.healing is not None:
            rolled = roll_formula(self.roller, spell.healing)
            healed = HealOperation(target_id, rolled.total).apply(connection, context)
            return {"target_id": target_id, "healed": healed}
        return {"target_id": target_id}


@dataclass(frozen=True)
class EndConcentrationOperation:
    caster_id: str

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        row = connection.execute(
            "SELECT c.id FROM combatants c"
            " JOIN combat_encounters e ON e.id = c.encounter_id"
            " WHERE e.campaign_id = ? AND e.status = 'active'"
            " AND c.entity_id = ? AND c.concentrating_spell IS NOT NULL",
            (context.campaign.id, self.caster_id),
        ).fetchone()
        if row is None:
            raise ValidationError("the caster is not concentrating")
        connection.execute(
            "UPDATE combatants SET concentrating_spell = NULL,"
            " concentration_rounds = NULL WHERE id = ?",
            (row["id"],),
        )
        return {"caster_id": self.caster_id, "concentration": "ended"}


class SpellService:
    """User-command entry points for casting and concentration."""

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

    def cast(self, campaign_id, expected_version, caster_id, spell, slot_level,
             target_ids=(), roller=None, cover=Cover.NONE, distance_ft=30,
             turn_id=None):
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id, branch_id="main",
                expected_version=expected_version, source="user-command",
                event_type="spell-cast",
                operation=CastSpellOperation(
                    roller=roller or _ZERO_ROLLER,
                    roll_id_factory=self.id_factory,
                    caster_id=caster_id, spell=spell, slot_level=slot_level,
                    target_ids=tuple(target_ids), cover=cover,
                    distance_ft=distance_ft, turn_id=turn_id,
                ),
            )
        )

    def end_concentration(self, campaign_id, expected_version, caster_id):
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id, branch_id="main",
                expected_version=expected_version, source="user-command",
                event_type="concentration-ended",
                operation=EndConcentrationOperation(caster_id),
            )
        )


class _ZeroRoller:
    """Fallback roller that only supports roll calls never needed by
    no-roll spells; using it for a real roll fails loudly."""

    def roll(self, count: int, sides: int) -> tuple[int, ...]:
        raise ValidationError("this spell requires a roller")
