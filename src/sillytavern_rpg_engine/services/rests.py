"""Short and long rest transactions (2024 resource recovery)."""

from dataclasses import dataclass
import sqlite3
from typing import Any, Callable

from ..domain.dice import DiceRoller
from ..domain.dnd import Condition, ability_modifier, require_dnd_2024
from ..domain.errors import ValidationError
from ..domain.operations import MutationContext
from ..persistence.database import Database
from .attributes import SetAttributeOperation
from .conditions import RemoveConditionOperation, condition_map
from .dice import record_roll
from .dnd_pack import read_bool, read_int, read_list, validate_slot_list
from .mutations import MutationEngine, MutationRequest, MutationResult


def _require_not_in_combat(connection, context, entity_id: str) -> None:
    row = connection.execute(
        "SELECT c.id FROM combatants c"
        " JOIN combat_encounters e ON e.id = c.encounter_id"
        " WHERE e.campaign_id = ? AND e.branch_id = ? AND e.status = 'active'"
        " AND c.entity_id = ?",
        (context.campaign.id, context.branch_id, entity_id),
    ).fetchone()
    if row is not None:
        raise ValidationError("cannot rest during an active encounter")


def _require_alive(connection, campaign_id: str, entity_id: str) -> None:
    if read_bool(connection, campaign_id, entity_id, "is_dead"):
        raise ValidationError("dead entities cannot rest")


@dataclass(frozen=True)
class ShortRestOperation:
    """Spend Hit Dice to heal; each die heals ``roll + con mod`` (min 0)."""

    roller: DiceRoller
    roll_id_factory: Callable[[], str]
    entity_id: str
    hit_dice: int

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        campaign_id = context.campaign.id
        _require_not_in_combat(connection, context, self.entity_id)
        _require_alive(connection, campaign_id, self.entity_id)
        available = read_int(connection, campaign_id, self.entity_id, "hit_dice_current")
        if not 1 <= self.hit_dice <= available:
            raise ValidationError(
                f"hit dice must be in 1..{available}"
            )
        die = read_int(connection, campaign_id, self.entity_id, "hit_die")
        con_mod = ability_modifier(
            read_int(connection, campaign_id, self.entity_id, "ability_con")
        )
        hp_max = read_int(connection, campaign_id, self.entity_id, "hp_max")
        hp = read_int(connection, campaign_id, self.entity_id, "hp_current")
        healed = 0
        for _ in range(self.hit_dice):
            face = self.roller.roll(1, die)[0]
            amount = max(0, face + con_mod)
            record_roll(
                connection, context, self.roll_id_factory(),
                purpose="short rest hit die", formula=f"1d{die}",
                faces=(face,), modifier=con_mod, total=face + con_mod,
                roller_entity_id=self.entity_id,
            )
            healed += amount
        new_hp = min(hp_max, hp + healed)
        SetAttributeOperation(self.entity_id, "hp_current", new_hp).apply(
            connection, context
        )
        SetAttributeOperation(
            self.entity_id, "hit_dice_current", available - self.hit_dice
        ).apply(connection, context)
        return {
            "entity_id": self.entity_id,
            "dice_spent": self.hit_dice,
            "healed": new_hp - hp,
            "hp": new_hp,
        }


@dataclass(frozen=True)
class LongRestOperation:
    """Full recovery: HP, slots, half of total Hit Dice, −1 exhaustion."""

    entity_id: str

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        campaign_id = context.campaign.id
        _require_not_in_combat(connection, context, self.entity_id)
        _require_alive(connection, campaign_id, self.entity_id)
        hp_max = read_int(connection, campaign_id, self.entity_id, "hp_max")
        SetAttributeOperation(self.entity_id, "hp_current", hp_max).apply(
            connection, context
        )
        SetAttributeOperation(self.entity_id, "hp_temp", 0).apply(connection, context)
        maximum = validate_slot_list(
            read_list(connection, campaign_id, self.entity_id, "spell_slots_max")
        )
        SetAttributeOperation(
            self.entity_id, "spell_slots_current", list(maximum)
        ).apply(connection, context)
        total = read_int(connection, campaign_id, self.entity_id, "hit_dice_total")
        current = read_int(connection, campaign_id, self.entity_id, "hit_dice_current")
        regained = min(total, current + max(1, total // 2))
        SetAttributeOperation(self.entity_id, "hit_dice_current", regained).apply(
            connection, context
        )
        SetAttributeOperation(self.entity_id, "death_saves_success", 0).apply(
            connection, context
        )
        SetAttributeOperation(self.entity_id, "death_saves_failure", 0).apply(
            connection, context
        )
        SetAttributeOperation(self.entity_id, "is_stable", False).apply(
            connection, context
        )
        conditions = condition_map(connection, campaign_id, self.entity_id)
        if Condition.EXHAUSTION in conditions:
            RemoveConditionOperation(self.entity_id, Condition.EXHAUSTION, 1).apply(
                connection, context
            )
        connection.execute(
            "UPDATE combatants SET concentrating_spell = NULL,"
            " concentration_rounds = NULL WHERE entity_id = ?",
            (self.entity_id,),
        )
        return {
            "entity_id": self.entity_id,
            "hp": hp_max,
            "hit_dice_current": regained,
        }


class RestService:
    """User-command entry points for rests."""

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

    def short_rest(self, campaign_id, expected_version, entity_id, hit_dice, roller):
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id, branch_id="main",
                expected_version=expected_version, source="user-command",
                event_type="short-rest",
                operation=ShortRestOperation(
                    roller, self.id_factory, entity_id, hit_dice
                ),
            )
        )

    def long_rest(self, campaign_id, expected_version, entity_id):
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id, branch_id="main",
                expected_version=expected_version, source="user-command",
                event_type="long-rest",
                operation=LongRestOperation(entity_id),
            )
        )
