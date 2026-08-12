import pytest

from sillytavern_rpg_engine.domain.dice import DiceFormula
from sillytavern_rpg_engine.domain.dnd import (
    CONDITION_EFFECTS,
    DND2024_RULES_VERSION,
    Condition,
    Cover,
    MasteryProperty,
    SpellProfile,
    WeaponProfile,
    WeaponProperty,
    ability_modifier,
    effective_speed,
    exhaustion_penalty,
    require_dnd_2024,
)
from sillytavern_rpg_engine.domain.errors import ValidationError
from sillytavern_rpg_engine.domain.models import Campaign, CampaignRules, RulesMode


def test_ability_modifier_rounds_down_toward_negative():
    assert [ability_modifier(s) for s in (1, 8, 10, 15, 20, 30)] == [-5, -1, 0, 2, 5, 10]


def test_weapon_profile_validation():
    sword = WeaponProfile(
        key="longsword", damage=DiceFormula(1, 8), damage_type="slashing",
        properties=frozenset({WeaponProperty.LIGHT}),
        mastery=MasteryProperty.SAP, attack_ability="str", range_normal=5,
    )
    assert sword.damage_type == "slashing"
    with pytest.raises(ValidationError, match="damage_type"):
        WeaponProfile(key="x", damage=DiceFormula(1, 6), damage_type="sparkle",
                      properties=frozenset(), mastery=None,
                      attack_ability="str", range_normal=5)
    with pytest.raises(ValidationError, match="attack_ability"):
        WeaponProfile(key="x", damage=DiceFormula(1, 6), damage_type="slashing",
                      properties=frozenset(), mastery=None,
                      attack_ability="charisma", range_normal=5)
    with pytest.raises(ValidationError, match="range_long"):
        WeaponProfile(key="x", damage=DiceFormula(1, 6), damage_type="piercing",
                      properties=frozenset({WeaponProperty.THROWN}), mastery=None,
                      attack_ability="str", range_normal=20, range_long=None)


def test_spell_profile_validation():
    bolt = SpellProfile(key="fire-bolt", level=0, attack=True, save_ability=None,
                        damage=DiceFormula(1, 10), damage_type="fire",
                        healing=None, concentration=False, duration_rounds=None,
                        ability="int")
    assert bolt.level == 0
    with pytest.raises(ValidationError, match="save_ability"):
        SpellProfile(key="bad", level=1, attack=False, save_ability=None,
                     damage=None, damage_type=None, healing=None,
                     concentration=False, duration_rounds=None, ability="int")
    with pytest.raises(ValidationError, match="concentration"):
        SpellProfile(key="bad", level=1, attack=False, save_ability="dex",
                     damage=None, damage_type=None, healing=None,
                     concentration=True, duration_rounds=None, ability="int")


def test_spell_profile_invalid_damage_type():
    with pytest.raises(ValidationError, match="damage_type"):
        SpellProfile(key="bad", level=0, attack=True, save_ability=None,
                     damage=DiceFormula(1, 10), damage_type="sparkle",
                     healing=None, concentration=False, duration_rounds=None,
                     ability="int")


def test_cover_bonus_and_total_cover():
    assert Cover.NONE.ac_bonus == 0
    assert Cover.HALF.ac_bonus == 2
    assert Cover.THREE_QUARTERS.ac_bonus == 5
    assert Cover.TOTAL.ac_bonus is None


def test_condition_effects_table_covers_2024_core():
    assert set(CONDITION_EFFECTS) == set(Condition)
    assert CONDITION_EFFECTS[Condition.POISONED].attacker_disadvantage
    assert CONDITION_EFFECTS[Condition.INVISIBLE].attacker_advantage
    assert CONDITION_EFFECTS[Condition.PARALYZED].crit_when_hit_within_5ft
    assert CONDITION_EFFECTS[Condition.GRAPPLED].speed_zero
    assert CONDITION_EFFECTS[Condition.UNCONSCIOUS].incapacitated


def test_exhaustion_penalty_and_effective_speed():
    assert exhaustion_penalty(0) == 0
    assert exhaustion_penalty(3) == -6
    assert effective_speed(30, {Condition.EXHAUSTION: 2}) == 20
    assert effective_speed(30, {Condition.GRAPPLED: 1}) == 0
    assert effective_speed(10, {Condition.EXHAUSTION: 3}) == 0


def test_require_dnd_2024_gates_on_mode_enabled_and_version():
    campaign = Campaign("c1", "Story", 0)
    with pytest.raises(ValidationError, match="not .*enabled"):
        require_dnd_2024(campaign)
    wrong_version = Campaign(
        "c1", "Story", 0,
        CampaignRules(RulesMode.DND_2024, True, "srd-9.9"),
    )
    with pytest.raises(ValidationError, match="version"):
        require_dnd_2024(wrong_version)
    ready = Campaign(
        "c1", "Dungeon", 0,
        CampaignRules(RulesMode.DND_2024, True, DND2024_RULES_VERSION),
    )
    require_dnd_2024(ready)
