import json

import pytest

from sillytavern_rpg_engine.domain.dice import DiceFormula, SequenceDiceRoller
from sillytavern_rpg_engine.domain.dnd import (
    Condition,
    CreatureSize,
    MasteryProperty,
    WeaponProfile,
    WeaponProperty,
)
from sillytavern_rpg_engine.domain.errors import ValidationError
from sillytavern_rpg_engine.domain.models import EntityKind
from sillytavern_rpg_engine.services.attacks import AttackService, AttackSpec
from sillytavern_rpg_engine.services.attributes import SetAttributeOperation
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.combat import CombatService, CombatantEntry
from sillytavern_rpg_engine.services.conditions import condition_map
from sillytavern_rpg_engine.services.dnd_pack import DndRulesService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)

AXE = WeaponProfile(
    key="greataxe", damage=DiceFormula(1, 12), damage_type="slashing",
    properties=frozenset(), mastery=MasteryProperty.CLEAVE,
    attack_ability="str", range_normal=5,
)
GLAIVE = WeaponProfile(
    key="glaive", damage=DiceFormula(1, 10), damage_type="slashing",
    properties=frozenset({WeaponProperty.REACH}), mastery=MasteryProperty.GRAZE,
    attack_ability="str", range_normal=10,
)
DAGGER = WeaponProfile(
    key="dagger", damage=DiceFormula(1, 4), damage_type="piercing",
    properties=frozenset({WeaponProperty.LIGHT}), mastery=MasteryProperty.NICK,
    attack_ability="str", range_normal=5,
)
STAFF = WeaponProfile(
    key="quarterstaff", damage=DiceFormula(1, 6), damage_type="bludgeoning",
    properties=frozenset(), mastery=MasteryProperty.TOPPLE,
    attack_ability="str", range_normal=5,
)
RAPIER = WeaponProfile(
    key="rapier", damage=DiceFormula(1, 8), damage_type="piercing",
    properties=frozenset({WeaponProperty.FINESSE}), mastery=MasteryProperty.VEX,
    attack_ability="dex", range_normal=5,
)
MACE = WeaponProfile(
    key="mace", damage=DiceFormula(1, 6), damage_type="bludgeoning",
    properties=frozenset(), mastery=MasteryProperty.SAP,
    attack_ability="str", range_normal=5,
)
LONGBOW = WeaponProfile(
    key="longbow", damage=DiceFormula(1, 8), damage_type="piercing",
    properties=frozenset({WeaponProperty.RANGED}), mastery=MasteryProperty.SLOW,
    attack_ability="dex", range_normal=30, range_long=100,
)


def _world(database, masteries, weapon_count=3):
    ids = iter(f"e{n}" for n in range(1, 2000))
    clock = lambda: "2026-08-12T00:00:00Z"
    campaigns = CampaignService(database, id_factory=lambda: next(ids), clock=clock)
    dnd = DndRulesService(database, id_factory=lambda: next(ids), clock=clock)
    combat = CombatService(database, id_factory=lambda: next(ids), clock=clock)
    attacks = AttackService(database, id_factory=lambda: next(ids), clock=clock)
    entities = EntityAttributeService(database, dnd.mutation_engine)
    campaigns.create_campaign("c1", "Dungeon")
    dnd.seed_pack("c1", 0)
    version = 1
    roster = [("pc1", "Aria"), ("orc", "Orc"), ("goblin", "Goblin")][:weapon_count]
    roster[0] = ("pc1", "Aria")
    for entity_id, name in roster:
        entities.apply_explicit("c1", "main", version, CreateEntityOperation(
            entity_id=entity_id, kind=EntityKind.CHARACTER, name=name,
        ))
        version += 1
        for key, value in (
            ("ability_str", 16), ("ability_dex", 14), ("ability_con", 10),
            ("ability_int", 10), ("ability_wis", 10), ("ability_cha", 10),
            ("proficiency_bonus", 2), ("proficiencies", []),
            ("armor_class", 12), ("hp_max", 30), ("hp_current", 30),
            ("hp_temp", 0), ("speed", 30), ("character_level", 1),
            ("spell_slots_max", [0] * 9), ("spell_slots_current", [0] * 9),
            ("hit_die", 8), ("hit_dice_total", 1), ("hit_dice_current", 1),
            ("death_saves_success", 0), ("death_saves_failure", 0),
            ("is_dead", False), ("is_stable", False), ("resistances", []),
            ("vulnerabilities", []), ("immunities", []),
            ("condition_immunities", []),
            ("weapon_masteries", (
                masteries if entity_id == "pc1"
                else (["quarterstaff"] if entity_id == "orc" and "quarterstaff" in masteries else [])
            )),
        ):
            entities.apply_explicit(
                "c1", "main", version, SetAttributeOperation(entity_id, key, value)
            )
            version += 1
    dnd.enable("c1", version)
    result = combat.start(
        "c1", expected_version=version + 1,
        roller=SequenceDiceRoller([18, 10, 5][: len(roster)]),
        entries=tuple(CombatantEntry(r[0]) for r in roster),
    )
    return attacks, combat, result.state_version


def _hp(database, entity_id):
    with database.connect() as connection:
        row = connection.execute(
            "SELECT value_json FROM attribute_values"
            " WHERE entity_id = ? AND attribute_key = 'hp_current'",
            (entity_id,),
        ).fetchone()
        return json.loads(row["value_json"])


def test_cleave_hits_second_target_once_per_turn(database):
    attacks, _, version = _world(database, ["greataxe"])
    result = attacks.attack(
        "c1", version, "pc1",
        AttackSpec(target_id="orc", weapon=AXE, cleave_target_id="goblin"),
        roller=SequenceDiceRoller([15, 5, 14, 3]),
    )
    assert _hp(database, "orc") == 22  # 5+3 damage (die + ability mod)
    assert _hp(database, "goblin") == 27  # cleave: no ability mod
    with pytest.raises(ValidationError, match="cleave"):
        attacks.attack(
            "c1", result.state_version, "pc1",
            AttackSpec(
                target_id="orc", weapon=AXE, extra_attack=True,
                cleave_target_id="goblin",
            ),
            roller=SequenceDiceRoller([15, 5, 14, 3]),
        )


def test_graze_damages_on_miss_only_with_mastery(database):
    attacks, _, version = _world(database, ["glaive"])
    attacks.attack(
        "c1", version, "pc1",
        AttackSpec(target_id="orc", weapon=GLAIVE, distance_ft=10),
        roller=SequenceDiceRoller([1]),
    )
    assert _hp(database, "orc") == 27  # graze: ability mod 3


def test_graze_miss_without_mastery_deals_no_damage(database):
    attacks, _, version = _world(database, [])
    attacks.attack(
        "c1", version, "pc1",
        AttackSpec(target_id="orc", weapon=GLAIVE, distance_ft=10),
        roller=SequenceDiceRoller([1]),
    )
    assert _hp(database, "orc") == 30


def test_nick_frees_bonus_action_once_per_turn(database):
    attacks, _, version = _world(database, ["dagger"])
    result = attacks.attack(
        "c1", version, "pc1",
        AttackSpec(target_id="orc", weapon=DAGGER),
        roller=SequenceDiceRoller([15, 2]),
    )
    result = attacks.attack(
        "c1", result.state_version, "pc1",
        AttackSpec(
            target_id="orc", weapon=DAGGER, offhand=True,
            other_weapon_light=True, use_nick=True,
        ),
        roller=SequenceDiceRoller([15, 2]),
    )
    with pytest.raises(ValidationError, match="nick"):
        attacks.attack(
            "c1", result.state_version, "pc1",
            AttackSpec(
                target_id="orc", weapon=DAGGER, offhand=True,
                other_weapon_light=True, use_nick=True, extra_attack=True,
            ),
            roller=SequenceDiceRoller([15, 2]),
        )


def test_sap_and_topple(database):
    attacks, combat, version = _world(database, ["mace", "rapier", "quarterstaff"])
    # sap: hit -> orc's next attack has disadvantage
    result = attacks.attack(
        "c1", version, "pc1",
        AttackSpec(target_id="orc", weapon=MACE),
        roller=SequenceDiceRoller([15, 4]),
    )
    with database.connect() as connection:
        debuffs = json.loads(connection.execute(
            "SELECT debuffs_json FROM combatants WHERE entity_id = 'orc'"
        ).fetchone()["debuffs_json"])
        assert debuffs["sap"]["source"] == "pc1"
    # orc's turn: attacks at disadvantage, sap consumed
    result = combat.advance_turn("c1", result.state_version, SequenceDiceRoller([]))
    # topple: orc con save (8 + 0) vs DC 8+3+2=13 -> prone
    result = attacks.attack(
        "c1", result.state_version, "orc",
        AttackSpec(target_id="pc1", weapon=STAFF),
        roller=SequenceDiceRoller([12, 12, 4, 8]),
    )
    with database.connect() as connection:
        assert condition_map(connection, "c1", "pc1") == {Condition.PRONE: 1}


def test_slow_debuff_reduces_movement_and_does_not_stack(database):
    attacks, combat, version = _world(database, ["longbow"])
    result = attacks.attack(
        "c1", version, "pc1",
        AttackSpec(target_id="orc", weapon=LONGBOW, distance_ft=30),
        roller=SequenceDiceRoller([15, 5]),
    )
    with database.connect() as connection:
        debuffs = json.loads(connection.execute(
            "SELECT debuffs_json FROM combatants WHERE entity_id = 'orc'"
        ).fetchone()["debuffs_json"])
        assert debuffs["slow"] == {
            "source": "pc1", "clear": "start", "amount": 10,
        }
    result = attacks.attack(
        "c1", result.state_version, "pc1",
        AttackSpec(
            target_id="orc", weapon=LONGBOW, distance_ft=30,
            extra_attack=True,
        ),
        roller=SequenceDiceRoller([15, 5]),
    )
    with database.connect() as connection:
        debuffs = json.loads(connection.execute(
            "SELECT debuffs_json FROM combatants WHERE entity_id = 'orc'"
        ).fetchone()["debuffs_json"])
        assert debuffs["slow"]["amount"] == 10
    result = combat.advance_turn("c1", result.state_version, SequenceDiceRoller([]))
    with database.connect() as connection:
        assert connection.execute(
            "SELECT movement_total FROM combatants WHERE entity_id = 'orc'"
        ).fetchone()["movement_total"] == 20


def test_vex_grants_advantage_until_end_of_next_turn(database):
    attacks, combat, version = _world(database, ["rapier"])
    result = attacks.attack(
        "c1", version, "pc1",
        AttackSpec(target_id="orc", weapon=RAPIER),
        roller=SequenceDiceRoller([15, 4]),
    )
    with database.connect() as connection:
        debuffs = json.loads(connection.execute(
            "SELECT debuffs_json FROM combatants WHERE entity_id = 'pc1'"
        ).fetchone()["debuffs_json"])
        assert debuffs["vex_vs"] == {"target": "orc", "source": "pc1",
                                     "clear": "end", "turns": 2}
