"""D&D 2024 / SRD 5.2.1 rules constants, profiles, and gating.

Mechanics boundary: public 2024 Free Rules / SRD 5.2.1 only. No proprietary
class, spell, monster, or adventure text is bundled.
"""

from dataclasses import dataclass
from enum import StrEnum

from .dice import DiceFormula
from .errors import ValidationError
from .models import Campaign, RulesMode

DND2024_RULESET_ID = "dnd-2024"
DND2024_RULES_VERSION = "srd-5.2.1"

ABILITIES = ("str", "dex", "con", "int", "wis", "cha")

EXHAUSTION_MAX_LEVEL = 6
EXHAUSTION_D20_PENALTY_PER_LEVEL = 2
EXHAUSTION_SPEED_PENALTY_PER_LEVEL = 5
HIDE_DC = 15


class DamageType(StrEnum):
    ACID = "acid"
    BLUDGEONING = "bludgeoning"
    COLD = "cold"
    FIRE = "fire"
    FORCE = "force"
    LIGHTNING = "lightning"
    NECROTIC = "necrotic"
    PIERCING = "piercing"
    POISON = "poison"
    PSYCHIC = "psychic"
    RADIANT = "radiant"
    SLASHING = "slashing"
    THUNDER = "thunder"


class Condition(StrEnum):
    """2024 core conditions; exhaustion carries a level 1..6."""

    BLINDED = "blinded"
    CHARMED = "charmed"
    DEAFENED = "deafened"
    EXHAUSTION = "exhaustion"
    FRIGHTENED = "frightened"
    GRAPPLED = "grappled"
    INCAPACITATED = "incapacitated"
    INVISIBLE = "invisible"
    PARALYZED = "paralyzed"
    PETRIFIED = "petrified"
    POISONED = "poisoned"
    PRONE = "prone"
    RESTRAINED = "restrained"
    STUNNED = "stunned"
    UNCONSCIOUS = "unconscious"


class ActionType(StrEnum):
    ATTACK = "attack"
    DASH = "dash"
    DISENGAGE = "disengage"
    DODGE = "dodge"
    HELP = "help"
    HIDE = "hide"
    INFLUENCE = "influence"
    MAGIC = "magic"
    READY = "ready"
    SEARCH = "search"
    STUDY = "study"
    UTILIZE = "utilize"


class WeaponProperty(StrEnum):
    LIGHT = "light"
    FINESSE = "finesse"
    HEAVY = "heavy"
    LOADING = "loading"
    REACH = "reach"
    THROWN = "thrown"
    TWO_HANDED = "two_handed"
    AMMUNITION = "ammunition"
    RANGED = "ranged"


class MasteryProperty(StrEnum):
    CLEAVE = "cleave"
    GRAZE = "graze"
    NICK = "nick"
    PUSH = "push"
    SAP = "sap"
    SLOW = "slow"
    TOPPLE = "topple"
    VEX = "vex"


class Cover(StrEnum):
    NONE = "none"
    HALF = "half"
    THREE_QUARTERS = "three_quarters"
    TOTAL = "total"

    @property
    def ac_bonus(self) -> int | None:
        """AC bonus vs attacks; None means the target cannot be targeted."""
        return {
            Cover.NONE: 0,
            Cover.HALF: 2,
            Cover.THREE_QUARTERS: 5,
            Cover.TOTAL: None,
        }[self]


class CreatureSize(StrEnum):
    TINY = "tiny"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    HUGE = "huge"
    GARGANTUAN = "gargantuan"

    @property
    def rank(self) -> int:
        return list(CreatureSize).index(self)


def ability_modifier(score: int) -> int:
    """2024 ability modifier: floor((score - 10) / 2), correct for negatives."""
    return (score - 10) // 2


@dataclass(frozen=True)
class WeaponProfile:
    """Data-driven weapon description supplied with an attack command.

    ``attack_ability`` is an ability key; finesse weapons resolve to the
    better of str/dex at attack time. ``range_normal`` is reach in feet for
    melee weapons.
    """

    key: str
    damage: DiceFormula
    damage_type: str
    properties: frozenset[WeaponProperty]
    mastery: MasteryProperty | None
    attack_ability: str
    range_normal: int
    range_long: int | None = None

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValidationError("weapon key must not be empty")
        try:
            DamageType(self.damage_type)
        except ValueError as exc:
            raise ValidationError(f"invalid damage_type {self.damage_type!r}") from exc
        if self.attack_ability not in ABILITIES:
            raise ValidationError(f"invalid attack_ability {self.attack_ability!r}")
        if self.range_normal < 0 or (self.range_long is not None and self.range_long < self.range_normal):
            raise ValidationError("range_long must be >= range_normal")
        if (
            WeaponProperty.THROWN in self.properties or WeaponProperty.RANGED in self.properties
        ) and self.range_long is None:
            raise ValidationError("ranged/thrown weapons require range_long")


@dataclass(frozen=True)
class SpellProfile:
    """Data-driven spell description supplied with a cast command."""

    key: str
    level: int
    attack: bool
    save_ability: str | None
    damage: DiceFormula | None
    damage_type: str | None
    healing: DiceFormula | None
    concentration: bool
    duration_rounds: int | None
    ability: str

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValidationError("spell key must not be empty")
        if isinstance(self.level, bool) or not 0 <= self.level <= 9:
            raise ValidationError("spell level must be an integer in 0..9")
        if self.attack and self.save_ability is not None:
            raise ValidationError("a spell is either an attack or a save")
        if not self.attack and self.save_ability is None and not self.healing:
            raise ValidationError("save_ability is required for non-attack spells")
        if self.save_ability is not None and self.save_ability not in ABILITIES:
            raise ValidationError(f"invalid save_ability {self.save_ability!r}")
        if self.ability not in ABILITIES:
            raise ValidationError(f"invalid casting ability {self.ability!r}")
        if self.damage is not None:
            if self.damage_type is None:
                raise ValidationError("damage_type is required with damage")
            try:
                DamageType(self.damage_type)
            except ValueError as exc:
                raise ValidationError(f"invalid damage_type {self.damage_type!r}") from exc
        if self.concentration and self.duration_rounds is None:
            raise ValidationError("concentration spells require duration_rounds")


@dataclass(frozen=True)
class ConditionEffects:
    """2024 combat-relevant effects of one condition."""

    attacker_advantage: bool = False
    attacker_disadvantage: bool = False
    target_advantage: bool = False
    target_disadvantage: bool = False
    speed_zero: bool = False
    incapacitated: bool = False
    crit_when_hit_within_5ft: bool = False
    resist_all_damage: bool = False


_UNCONSCIOUS = ConditionEffects(
    target_advantage=True, speed_zero=True, incapacitated=True,
    crit_when_hit_within_5ft=True,
)

CONDITION_EFFECTS: dict[Condition, ConditionEffects] = {
    Condition.BLINDED: ConditionEffects(attacker_disadvantage=True, target_advantage=True),
    Condition.CHARMED: ConditionEffects(),
    Condition.DEAFENED: ConditionEffects(),
    Condition.EXHAUSTION: ConditionEffects(),
    Condition.FRIGHTENED: ConditionEffects(attacker_disadvantage=True),
    Condition.GRAPPLED: ConditionEffects(speed_zero=True),
    Condition.INCAPACITATED: ConditionEffects(incapacitated=True),
    Condition.INVISIBLE: ConditionEffects(attacker_advantage=True, target_disadvantage=True),
    Condition.PARALYZED: ConditionEffects(
        target_advantage=True, incapacitated=True, crit_when_hit_within_5ft=True
    ),
    Condition.PETRIFIED: ConditionEffects(
        target_advantage=True, incapacitated=True, resist_all_damage=True
    ),
    Condition.POISONED: ConditionEffects(attacker_disadvantage=True),
    Condition.PRONE: ConditionEffects(attacker_disadvantage=True),
    Condition.RESTRAINED: ConditionEffects(attacker_disadvantage=True, target_advantage=True),
    Condition.STUNNED: ConditionEffects(target_advantage=True, incapacitated=True),
    Condition.UNCONSCIOUS: _UNCONSCIOUS,
}


def aggregate_effects(conditions: dict[Condition, int]) -> ConditionEffects:
    """Combine the effects of every active condition (levels only matter for
    exhaustion, which is handled by exhaustion_penalty/effective_speed)."""
    merged = ConditionEffects()
    for condition in conditions:
        effects = CONDITION_EFFECTS[condition]
        merged = ConditionEffects(
            attacker_advantage=merged.attacker_advantage or effects.attacker_advantage,
            attacker_disadvantage=merged.attacker_disadvantage or effects.attacker_disadvantage,
            target_advantage=merged.target_advantage or effects.target_advantage,
            target_disadvantage=merged.target_disadvantage or effects.target_disadvantage,
            speed_zero=merged.speed_zero or effects.speed_zero,
            incapacitated=merged.incapacitated or effects.incapacitated,
            crit_when_hit_within_5ft=(
                merged.crit_when_hit_within_5ft or effects.crit_when_hit_within_5ft
            ),
            resist_all_damage=merged.resist_all_damage or effects.resist_all_damage,
        )
    return merged


def exhaustion_penalty(level: int) -> int:
    """d20 Test penalty: −2 per exhaustion level."""
    return -EXHAUSTION_D20_PENALTY_PER_LEVEL * level


def effective_speed(base_speed: int, conditions: dict[Condition, int]) -> int:
    """Speed after condition effects; never below 0."""
    if any(CONDITION_EFFECTS[c].speed_zero for c in conditions):
        return 0
    level = conditions.get(Condition.EXHAUSTION, 0)
    return max(0, base_speed - EXHAUSTION_SPEED_PENALTY_PER_LEVEL * level)


def require_dnd_2024(campaign: Campaign) -> None:
    """Gate every D&D operation on the campaign's enabled, pinned ruleset."""
    rules = campaign.rules
    if rules.mode is not RulesMode.DND_2024 or not rules.enabled:
        raise ValidationError("campaign does not have dnd-2024 rules enabled")
    if rules.version != DND2024_RULES_VERSION:
        raise ValidationError(
            f"unsupported dnd-2024 rules version {rules.version!r};"
            f" engine implements {DND2024_RULES_VERSION!r}"
        )
