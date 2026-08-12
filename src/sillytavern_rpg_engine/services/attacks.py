"""Attack resolution: budgets, advantage aggregation, weapon properties,
grapple/shove contests, and unarmed strikes."""

from dataclasses import dataclass
import json
import sqlite3
from typing import Any, Callable

from ..domain.dice import (
    D20Mode,
    DiceFormula,
    DiceRoller,
    combine_modes,
    resolve_d20,
    roll_formula,
)
from ..domain.dnd import (
    CONDITION_EFFECTS,
    Cover,
    CreatureSize,
    Condition,
    WeaponProfile,
    WeaponProperty,
    ability_modifier,
    require_dnd_2024,
)
from ..domain.errors import ValidationError
from ..domain.operations import MutationContext
from ..persistence.database import Database
from .combat import (
    active_combatant,
    require_active_encounter,
)
from .conditions import (
    ApplyConditionOperation,
    RemoveConditionOperation,
    condition_map,
)
from .damage import apply_damage
from .dice import record_roll
from .dnd_pack import read_int, read_list
from .mutations import MutationEngine, MutationRequest, MutationResult


@dataclass(frozen=True)
class AttackSpec:
    """One weapon strike.

    ``offhand`` uses the Bonus Action with a Light weapon (no ability
    modifier on damage unless negative). ``extra_attack`` chains a further
    attack on an already-spent Attack action. ``suppress_positive_modifier``
    serves Cleave's second attack.
    """

    target_id: str
    weapon: WeaponProfile
    distance_ft: int = 5
    cover: Cover = Cover.NONE
    offhand: bool = False
    other_weapon_light: bool = False
    extra_attack: bool = False
    is_opportunity: bool = False
    use_nick: bool = False
    suppress_positive_modifier: bool = False
    flat_damage: int | None = None
    attacker_size: CreatureSize = CreatureSize.MEDIUM
    target_size: CreatureSize = CreatureSize.MEDIUM


def _attack_ability_score(
    connection, campaign_id: str, attacker_id: str, weapon: WeaponProfile
) -> int:
    strength = read_int(connection, campaign_id, attacker_id, "ability_str")
    if WeaponProperty.FINESSE in weapon.properties:
        dexterity = read_int(connection, campaign_id, attacker_id, "ability_dex")
        return max(strength, dexterity)
    return read_int(connection, campaign_id, attacker_id, f"ability_{weapon.attack_ability}")


def _combatant_row(connection, encounter_id: str, entity_id: str):
    row = connection.execute(
        "SELECT * FROM combatants WHERE encounter_id = ? AND entity_id = ?",
        (encounter_id, entity_id),
    ).fetchone()
    if row is None:
        raise ValidationError(f"{entity_id!r} is not in the encounter")
    return row


def _spend_budget(
    connection,
    context: MutationContext,
    encounter,
    combatants,
    attacker_id: str,
    spec: AttackSpec,
) -> Any:
    row = _combatant_row(connection, encounter["id"], attacker_id)
    conditions = condition_map(connection, context.campaign.id, attacker_id)
    if any(CONDITION_EFFECTS[c].incapacitated for c in conditions):
        raise ValidationError("incapacitated creatures cannot attack")
    if spec.is_opportunity:
        if row["reaction_used"]:
            raise ValidationError("reaction already used this round")
        connection.execute(
            "UPDATE combatants SET reaction_used = 1 WHERE id = ?", (row["id"],)
        )
        return row
    current = active_combatant(encounter, combatants)
    if current["entity_id"] != attacker_id:
        raise ValidationError(f"it is not {attacker_id!r}'s turn")
    if spec.offhand:
        if WeaponProperty.LIGHT not in spec.weapon.properties:
            raise ValidationError("offhand attacks require a light weapon")
        if not spec.other_weapon_light:
            raise ValidationError("the other weapon must be light")
        if spec.use_nick:
            pass  # Task 11 validates and consumes the Nick use
        elif row["bonus_used"]:
            raise ValidationError("bonus action already used this turn")
        else:
            connection.execute(
                "UPDATE combatants SET bonus_used = 1 WHERE id = ?", (row["id"],)
            )
    elif spec.extra_attack:
        if not row["action_used"] or not row["attacks_this_turn"]:
            raise ValidationError("extra attacks require a prior attack this turn")
        if WeaponProperty.LOADING in spec.weapon.properties:
            raise ValidationError("loading weapons allow one attack per turn")
    else:
        if row["action_used"]:
            raise ValidationError("action already used this turn")
        if WeaponProperty.LOADING in spec.weapon.properties and row["attacks_this_turn"]:
            raise ValidationError("loading weapons allow one attack per turn")
        connection.execute(
            "UPDATE combatants SET action_used = 1 WHERE id = ?", (row["id"],)
        )
    connection.execute(
        "UPDATE combatants SET attacks_this_turn = attacks_this_turn + 1"
        " WHERE id = ?",
        (row["id"],),
    )
    return row


def _attack_modifiers(
    connection,
    context: MutationContext,
    attacker_row,
    spec: AttackSpec,
) -> tuple[int, int]:
    """Aggregate (advantage_count, disadvantage_count) for one strike."""
    campaign_id = context.campaign.id
    adv = dis = 0
    attacker_conditions = condition_map(connection, campaign_id, attacker_row["entity_id"])
    target_conditions = condition_map(connection, campaign_id, spec.target_id)
    attacker_effects = CONDITION_EFFECTS  # alias for readability
    for condition in attacker_conditions:
        effects = attacker_effects[condition]
        adv += effects.attacker_advantage
        dis += effects.attacker_disadvantage
    for condition in target_conditions:
        effects = attacker_effects[condition]
        adv += effects.target_advantage
        dis += effects.target_disadvantage
    if Condition.PRONE in target_conditions:
        if spec.distance_ft <= 5:
            adv += 1
        else:
            dis += 1
    target_row = _combatant_row(
        connection, attacker_row["encounter_id"], spec.target_id
    )
    if target_row["dodging"]:
        dis += 1
    if attacker_row["hidden"]:
        adv += 1
    debuffs = json.loads(attacker_row["debuffs_json"])
    if "sap" in debuffs:
        dis += 1
    if debuffs.get("vex_vs", {}).get("target") == spec.target_id:
        adv += 1
    grants = [
        grant
        for grant in json.loads(attacker_row["help_grants_json"])
        if grant["target"] == spec.target_id
    ]
    if grants:
        adv += 1
    if (
        spec.distance_ft > spec.weapon.range_normal
        and spec.weapon.range_long is not None
        and spec.distance_ft <= spec.weapon.range_long
    ):
        dis += 1
    return adv, dis


def _consume_attack_marks(connection, attacker_row, spec: AttackSpec) -> None:
    debuffs = json.loads(attacker_row["debuffs_json"])
    changed = debuffs.pop("sap", None) is not None
    if debuffs.get("vex_vs", {}).get("target") == spec.target_id:
        del debuffs["vex_vs"]
        changed = True
    grants = json.loads(attacker_row["help_grants_json"])
    kept = [g for g in grants if g["target"] != spec.target_id]
    connection.execute(
        "UPDATE combatants SET debuffs_json = ?, help_grants_json = ?,"
        " hidden = 0 WHERE id = ?",
        (json.dumps(debuffs, sort_keys=True), json.dumps(kept), attacker_row["id"]),
    )


def resolve_strike(
    connection: sqlite3.Connection,
    context: MutationContext,
    roller: DiceRoller,
    roll_id_factory: Callable[[], str],
    attacker_id: str,
    spec: AttackSpec,
    *,
    spend_budget: bool = True,
    mastery_on_hit: Callable[..., dict] | None = None,
    mastery_on_miss: Callable[..., dict] | None = None,
) -> dict[str, Any]:
    """Resolve one strike; shared by AttackOperation and MultiattackOperation."""
    require_dnd_2024(context.campaign)
    campaign_id = context.campaign.id
    encounter, combatants = require_active_encounter(
        connection, campaign_id, context.branch_id
    )
    weapon = spec.weapon
    if WeaponProperty.RANGED not in weapon.properties:
        reach = 10 if WeaponProperty.REACH in weapon.properties else weapon.range_normal
        if WeaponProperty.THROWN not in weapon.properties or spec.distance_ft <= reach:
            if spec.distance_ft > reach:
                raise ValidationError("target is beyond melee reach")
    if spec.distance_ft > (weapon.range_long or weapon.range_normal):
        raise ValidationError("target is beyond long range")
    if spec.cover is Cover.TOTAL:
        raise ValidationError("cannot target a creature behind total cover")
    attacker_row = (
        _spend_budget(connection, context, encounter, combatants, attacker_id, spec)
        if spend_budget
        else _combatant_row(connection, encounter["id"], attacker_id)
    )
    score = _attack_ability_score(connection, campaign_id, attacker_id, weapon)
    ability_mod = ability_modifier(score)
    modifier = ability_mod
    if weapon.key in read_list(connection, campaign_id, attacker_id, "proficiencies"):
        modifier += read_int(connection, campaign_id, attacker_id, "proficiency_bonus")
    from .dice import read_exhaustion

    modifier += read_exhaustion(connection, campaign_id, attacker_id) * -2
    adv, dis = _attack_modifiers(connection, context, attacker_row, spec)
    outcome = resolve_d20(roller, combine_modes(adv, dis), modifier)
    target_row = _combatant_row(connection, encounter["id"], spec.target_id)
    armor_class = read_int(connection, campaign_id, spec.target_id, "armor_class")
    armor_class += spec.cover.ac_bonus or 0
    target_conditions = condition_map(connection, campaign_id, spec.target_id)
    crit_within_5 = spec.distance_ft <= 5 and any(
        CONDITION_EFFECTS[c].crit_when_hit_within_5ft for c in target_conditions
    )
    hit = not outcome.is_natural_1 and (
        outcome.is_natural_20 or outcome.total >= armor_class
    )
    critical = hit and (outcome.is_natural_20 or crit_within_5)
    faces = (
        (outcome.kept,)
        if outcome.dropped is None
        else tuple(sorted((outcome.kept, outcome.dropped), reverse=True))
    )
    payload = record_roll(
        connection, context, roll_id_factory(),
        purpose=f"attack: {weapon.key}", formula="1d20", faces=faces,
        modifier=modifier, total=outcome.total, dc=armor_class,
        success=hit, critical=critical, roller_entity_id=attacker_id,
    )
    _consume_attack_marks(connection, attacker_row, spec)
    payload["hit"] = hit
    payload["critical"] = critical
    if hit:
        if spec.flat_damage is not None:
            damage_amount = spec.flat_damage + ability_mod
            damage_formula = "flat"
            damage_faces: tuple[int, ...] = ()
        else:
            formula = weapon.damage
            if critical:
                formula = DiceFormula(formula.count * 2, formula.sides, 0)
            rolled = roll_formula(roller, formula)
            damage_faces = rolled.faces
            damage_modifier = ability_mod
            if (spec.offhand or spec.suppress_positive_modifier) and ability_mod > 0:
                damage_modifier = 0
            damage_amount = rolled.total - formula.modifier + damage_modifier
            damage_formula = str(formula)
        damage_amount = max(0, damage_amount)
        damage_payload = record_roll(
            connection, context, roll_id_factory(),
            purpose=f"damage: {weapon.key}", formula=damage_formula,
            faces=damage_faces, modifier=ability_mod, total=damage_amount,
            roller_entity_id=attacker_id,
        )
        result = apply_damage(
            connection, context, roller, roll_id_factory, spec.target_id,
            damage_amount, weapon.damage_type,
            is_crit_hit_5ft=critical and spec.distance_ft <= 5,
        )
        payload["damage"] = {**damage_payload, **result.__dict__}
        if mastery_on_hit is not None:
            payload["mastery"] = mastery_on_hit(
                connection, context, roller, roll_id_factory,
                attacker_row, spec, result,
            )
    elif mastery_on_miss is not None:
        payload["mastery"] = mastery_on_miss(
            connection, context, roller, roll_id_factory, attacker_row, spec,
        )
    return payload


@dataclass(frozen=True)
class AttackOperation:
    roller: DiceRoller
    roll_id_factory: Callable[[], str]
    attacker_id: str
    spec: AttackSpec

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        # Mastery callbacks are wired in Task 11 once mastery.py exists.
        return resolve_strike(
            connection, context, self.roller, self.roll_id_factory,
            self.attacker_id, self.spec,
        )


@dataclass(frozen=True)
class GrappleOperation:
    """2024 grapple: the target makes a Str/Dex save vs 8 + str mod + PB."""

    roller: DiceRoller
    roll_id: str
    attacker_id: str
    target_id: str
    save_ability: str
    attacker_size: CreatureSize
    target_size: CreatureSize

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        return _contest(
            connection, context, self.roller, self.roll_id,
            self.attacker_id, self.target_id, self.save_ability,
            self.attacker_size, self.target_size, effect="grapple",
        )


@dataclass(frozen=True)
class ShoveOperation:
    roller: DiceRoller
    roll_id: str
    attacker_id: str
    target_id: str
    save_ability: str
    attacker_size: CreatureSize
    target_size: CreatureSize
    prone: bool = True

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        return _contest(
            connection, context, self.roller, self.roll_id,
            self.attacker_id, self.target_id, self.save_ability,
            self.attacker_size, self.target_size,
            effect="shove-prone" if self.prone else "shove-push",
        )


def _contest(
    connection, context, roller, roll_id, attacker_id, target_id,
    save_ability, attacker_size, target_size, effect,
) -> dict:
    require_dnd_2024(context.campaign)
    if save_ability not in ("str", "dex"):
        raise ValidationError("the save ability must be str or dex")
    if target_size.rank > attacker_size.rank + 1:
        raise ValidationError("target is too large")
    campaign_id = context.campaign.id
    encounter, combatants = require_active_encounter(
        connection, campaign_id, context.branch_id
    )
    _spend_budget(
        connection, context, encounter, combatants, attacker_id,
        AttackSpec(target_id=target_id, weapon=WeaponProfile(
            key="unarmed", damage=DiceFormula(1, 2), damage_type="bludgeoning",
            properties=frozenset(), mastery=None, attack_ability="str",
            range_normal=5,
        )),
    )
    dc = 8 + ability_modifier(
        read_int(connection, campaign_id, attacker_id, "ability_str")
    ) + read_int(connection, campaign_id, attacker_id, "proficiency_bonus")
    save_mod = ability_modifier(
        read_int(connection, campaign_id, target_id, f"ability_{save_ability}")
    )
    if f"save_{save_ability}" in read_list(
        connection, campaign_id, target_id, "proficiencies"
    ):
        save_mod += read_int(connection, campaign_id, target_id, "proficiency_bonus")
    outcome = resolve_d20(roller, D20Mode.NORMAL, save_mod)
    payload = record_roll(
        connection, context, roll_id,
        purpose=f"{effect} save", formula="1d20", faces=(outcome.kept,),
        modifier=save_mod, total=outcome.total, dc=dc,
        success=outcome.total >= dc, roller_entity_id=target_id,
    )
    if outcome.total < dc:
        if effect == "grapple":
            ApplyConditionOperation(
                target_id, Condition.GRAPPLED, attacker_id
            ).apply(connection, context)
        elif effect == "shove-prone":
            ApplyConditionOperation(
                target_id, Condition.PRONE, attacker_id
            ).apply(connection, context)
        payload["applied"] = effect
    else:
        payload["applied"] = None
    return payload


@dataclass(frozen=True)
class EscapeGrappleOperation:
    roller: DiceRoller
    roll_id: str
    entity_id: str

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        campaign_id = context.campaign.id
        conditions = condition_map(connection, campaign_id, self.entity_id)
        if Condition.GRAPPLED not in conditions:
            raise ValidationError("entity is not grappled")
        row = connection.execute(
            "SELECT source FROM entity_conditions"
            " WHERE campaign_id = ? AND entity_id = ? AND condition = 'grappled'",
            (campaign_id, self.entity_id),
        ).fetchone()
        grappler = row["source"]
        dc = 8 + ability_modifier(
            read_int(connection, campaign_id, grappler, "ability_str")
        ) + read_int(connection, campaign_id, grappler, "proficiency_bonus")
        score = max(
            read_int(connection, campaign_id, self.entity_id, "ability_str"),
            read_int(connection, campaign_id, self.entity_id, "ability_dex"),
        )
        modifier = ability_modifier(score)
        outcome = resolve_d20(self.roller, D20Mode.NORMAL, modifier)
        payload = record_roll(
            connection, context, self.roll_id,
            purpose="escape grapple", formula="1d20", faces=(outcome.kept,),
            modifier=modifier, total=outcome.total, dc=dc,
            success=outcome.total >= dc, roller_entity_id=self.entity_id,
        )
        if outcome.total >= dc:
            RemoveConditionOperation(self.entity_id, Condition.GRAPPLED).apply(
                connection, context
            )
        return payload


class AttackService:
    """User-command entry points for attacks and contests."""

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

    def attack(self, campaign_id, expected_version, attacker_id, spec, roller):
        return self._apply(campaign_id, expected_version, "attack",
                           AttackOperation(roller, self.id_factory, attacker_id, spec))

    def grapple(self, campaign_id, expected_version, attacker_id, target_id,
                save_ability, attacker_size, target_size, roller):
        return self._apply(campaign_id, expected_version, "grapple",
                           GrappleOperation(roller, self.id_factory(), attacker_id,
                                            target_id, save_ability,
                                            attacker_size, target_size))

    def shove(self, campaign_id, expected_version, attacker_id, target_id,
              save_ability, attacker_size, target_size, roller, prone=True):
        return self._apply(campaign_id, expected_version, "shove",
                           ShoveOperation(roller, self.id_factory(), attacker_id,
                                          target_id, save_ability,
                                          attacker_size, target_size, prone))

    def escape_grapple(self, campaign_id, expected_version, entity_id, roller):
        return self._apply(campaign_id, expected_version, "escape-grapple",
                           EscapeGrappleOperation(roller, self.id_factory(), entity_id))

    def unarmed(self, campaign_id, expected_version, attacker_id, target_id, roller):
        spec = AttackSpec(
            target_id=target_id,
            weapon=WeaponProfile(
                key="unarmed", damage=DiceFormula(1, 2),
                damage_type="bludgeoning", properties=frozenset(), mastery=None,
                attack_ability="str", range_normal=5,
            ),
            flat_damage=1,
        )
        return self.attack(campaign_id, expected_version, attacker_id, spec, roller)
