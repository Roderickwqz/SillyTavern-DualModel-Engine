import json

import pytest

from sillytavern_rpg_engine.domain.dice import DiceFormula, SequenceDiceRoller
from sillytavern_rpg_engine.domain.dnd import (
    Condition,
    Cover,
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
from sillytavern_rpg_engine.services.conditions import ConditionService, condition_map
from sillytavern_rpg_engine.services.dnd_pack import DndRulesService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)

SWORD = WeaponProfile(
    key="longsword", damage=DiceFormula(1, 8), damage_type="slashing",
    properties=frozenset(), mastery=MasteryProperty.SAP,
    attack_ability="str", range_normal=5,
)
DAGGER = WeaponProfile(
    key="dagger", damage=DiceFormula(1, 4), damage_type="piercing",
    properties=frozenset({WeaponProperty.LIGHT, WeaponProperty.THROWN}),
    mastery=MasteryProperty.NICK, attack_ability="str", range_normal=5,
    range_long=20,
)
BOW = WeaponProfile(
    key="longbow", damage=DiceFormula(1, 8), damage_type="piercing",
    properties=frozenset({WeaponProperty.RANGED, WeaponProperty.AMMUNITION}),
    mastery=MasteryProperty.SLOW, attack_ability="dex", range_normal=30,
    range_long=100,
)


def _world(database):
    ids = iter(f"e{n}" for n in range(1, 1000))
    clock = lambda: "2026-08-12T00:00:00Z"
    campaigns = CampaignService(database, id_factory=lambda: next(ids), clock=clock)
    dnd = DndRulesService(database, id_factory=lambda: next(ids), clock=clock)
    combat = CombatService(database, id_factory=lambda: next(ids), clock=clock)
    attacks = AttackService(database, id_factory=lambda: next(ids), clock=clock)
    conditions = ConditionService(database, id_factory=lambda: next(ids), clock=clock)
    entities = EntityAttributeService(database, dnd.mutation_engine)
    campaigns.create_campaign("c1", "Dungeon")
    dnd.seed_pack("c1", 0)
    version = 1
    for entity_id, name in (("pc1", "Aria"), ("orc", "Orc")):
        entities.apply_explicit("c1", "main", version, CreateEntityOperation(
            entity_id=entity_id, kind=EntityKind.CHARACTER, name=name,
        ))
        version += 1
        for key, value in (
            ("ability_str", 16), ("ability_dex", 10), ("ability_con", 13),
            ("ability_int", 10), ("ability_wis", 10), ("ability_cha", 10),
            ("proficiency_bonus", 2), ("proficiencies", ["longsword", "dagger"]),
            ("armor_class", 13), ("hp_max", 20), ("hp_current", 20),
            ("hp_temp", 0), ("speed", 30), ("character_level", 1),
            ("spell_slots_max", [0] * 9), ("spell_slots_current", [0] * 9),
            ("hit_die", 10), ("hit_dice_total", 1), ("hit_dice_current", 1),
            ("death_saves_success", 0), ("death_saves_failure", 0),
            ("is_dead", False), ("is_stable", False), ("resistances", []),
            ("vulnerabilities", []), ("immunities", []),
            ("condition_immunities", []), ("weapon_masteries", ["longsword", "dagger"]),
        ):
            entities.apply_explicit(
                "c1", "main", version, SetAttributeOperation(entity_id, key, value)
            )
            version += 1
    dnd.enable("c1", version)
    result = combat.start(
        "c1", expected_version=version + 1,
        roller=SequenceDiceRoller([15, 10]),
        entries=(CombatantEntry("pc1"), CombatantEntry("orc")),
    )
    return attacks, combat, conditions, entities, result.state_version


def _hp(database, entity_id):
    with database.connect() as connection:
        row = connection.execute(
            "SELECT value_json FROM attribute_values"
            " WHERE entity_id = ? AND attribute_key = 'hp_current'",
            (entity_id,),
        ).fetchone()
        return json.loads(row["value_json"])


def test_hit_miss_crit_and_records(database):
    attacks, _, _, _, version = _world(database)
    # attack 15 + 5 = 20 vs AC 13: hit, damage 6 + 3
    result = attacks.attack(
        "c1", version, "pc1",
        AttackSpec(target_id="orc", weapon=SWORD),
        roller=SequenceDiceRoller([15, 6]),
    )
    assert _hp(database, "orc") == 11
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT purpose, total, critical FROM dice_rolls"
            " WHERE purpose LIKE 'attack:%' OR purpose LIKE 'damage:%'"
            " ORDER BY rowid"
        ).fetchall()
        assert [(r["purpose"], r["total"], r["critical"]) for r in rows] == [
            ("attack: longsword", 20, 0),
            ("damage: longsword", 9, 0),
        ]
    with pytest.raises(ValidationError, match="action"):
        attacks.attack(
            "c1", result.state_version, "pc1",
            AttackSpec(target_id="orc", weapon=SWORD),
            roller=SequenceDiceRoller([10, 3]),
        )


def test_natural_20_crits_and_doubles_dice(database):
    attacks, _, _, _, version = _world(database)
    attacks.attack(
        "c1", version, "pc1",
        AttackSpec(target_id="orc", weapon=SWORD),
        roller=SequenceDiceRoller([20, 4, 4]),
    )
    assert _hp(database, "orc") == 9  # 4+4+3


def test_prone_dodge_and_cover_change_the_roll(database):
    attacks, combat, conditions, _, version = _world(database)
    conditions.apply("c1", version, "pc1", Condition.PRONE, source="test")
    # prone attacker has disadvantage: (12, 4) keeps 4 + 5 = 9 < 13 -> miss
    result = attacks.attack(
        "c1", version + 1, "pc1",
        AttackSpec(target_id="orc", weapon=SWORD),
        roller=SequenceDiceRoller([12, 4]),
    )
    assert _hp(database, "orc") == 20
    # three-quarters cover: AC 18
    result = combat.advance_turn("c1", result.state_version, SequenceDiceRoller([]))
    result = combat.advance_turn("c1", result.state_version, SequenceDiceRoller([]))
    attacks.attack(
        "c1", result.state_version, "pc1",
        AttackSpec(target_id="orc", weapon=SWORD, cover=Cover.THREE_QUARTERS),
        roller=SequenceDiceRoller([12, 5]),
    )
    assert _hp(database, "orc") == 20  # 17 < 18
    with pytest.raises(ValidationError, match="cover"):
        attacks.attack(
            "c1", result.state_version + 1, "pc1",
            AttackSpec(target_id="orc", weapon=SWORD, cover=Cover.TOTAL),
            roller=SequenceDiceRoller([20, 5]),
        )


def test_extra_attack_offhand_loading_and_range_rules(database):
    attacks, combat, _, _, version = _world(database)
    result = attacks.attack(
        "c1", version, "pc1",
        AttackSpec(target_id="orc", weapon=SWORD),
        roller=SequenceDiceRoller([15, 6]),
    )
    # extra attack on the same Attack action is allowed
    result = attacks.attack(
        "c1", result.state_version, "pc1",
        AttackSpec(target_id="orc", weapon=SWORD, extra_attack=True),
        roller=SequenceDiceRoller([15, 6]),
    )
    # offhand needs the bonus action and a light weapon
    result = attacks.attack(
        "c1", result.state_version, "pc1",
        AttackSpec(
            target_id="orc", weapon=DAGGER, offhand=True, other_weapon_light=True,
        ),
        roller=SequenceDiceRoller([15, 2]),
    )
    assert _hp(database, "orc") == 20 - 9 - 9 - 2  # offhand: no ability mod
    with pytest.raises(ValidationError, match="bonus"):
        attacks.attack(
            "c1", result.state_version, "pc1",
            AttackSpec(
                target_id="orc", weapon=DAGGER, offhand=True,
                other_weapon_light=True,
            ),
            roller=SequenceDiceRoller([15, 2]),
        )


def test_grapple_shove_and_escape(database):
    attacks, _, _, _, version = _world(database)
    # grapple: orc dex save (10 + 0) vs DC 8 + 3 + 2 = 13 -> grappled
    result = attacks.grapple(
        "c1", version, "pc1", "orc", save_ability="dex",
        attacker_size=CreatureSize.MEDIUM, target_size=CreatureSize.MEDIUM,
        roller=SequenceDiceRoller([10]),
    )
    with database.connect() as connection:
        assert condition_map(connection, "c1", "orc") == {Condition.GRAPPLED: 1}
    # escape: orc spends its next turn; first advance to orc's turn
    # (orc acts after pc1 in the seeded order), then escape directly:
    result = attacks.escape_grapple(
        "c1", result.state_version, "orc", roller=SequenceDiceRoller([12]),
    )
    with database.connect() as connection:
        assert condition_map(connection, "c1", "orc") == {}


def test_unarmed_strike_flat_damage(database):
    attacks, _, _, _, version = _world(database)
    attacks.unarmed("c1", version, "pc1", "orc", roller=SequenceDiceRoller([15]))
    assert _hp(database, "orc") == 16  # 1 + 3
