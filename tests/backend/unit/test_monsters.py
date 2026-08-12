import json

import pytest

from sillytavern_rpg_engine.domain.dice import DiceFormula, SequenceDiceRoller
from sillytavern_rpg_engine.domain.dnd import (
    Condition,
    MasteryProperty,
    WeaponProfile,
    WeaponProperty,
)
from sillytavern_rpg_engine.domain.models import EntityKind
from sillytavern_rpg_engine.services.attacks import AttackSpec
from sillytavern_rpg_engine.services.attributes import SetAttributeOperation
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.combat import CombatService, CombatantEntry
from sillytavern_rpg_engine.services.conditions import condition_map
from sillytavern_rpg_engine.services.dnd_pack import DndRulesService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)
from sillytavern_rpg_engine.services.monsters import MonsterService

CLAW = WeaponProfile(
    key="claw", damage=DiceFormula(1, 6), damage_type="slashing",
    properties=frozenset(), mastery=None, attack_ability="str", range_normal=5,
)


def _world(database):
    ids = iter(f"e{n}" for n in range(1, 2000))
    clock = lambda: "2026-08-12T00:00:00Z"
    campaigns = CampaignService(database, id_factory=lambda: next(ids), clock=clock)
    dnd = DndRulesService(database, id_factory=lambda: next(ids), clock=clock)
    combat = CombatService(database, id_factory=lambda: next(ids), clock=clock)
    monsters = MonsterService(database, id_factory=lambda: next(ids), clock=clock)
    entities = EntityAttributeService(database, dnd.mutation_engine)
    campaigns.create_campaign("c1", "Dungeon")
    dnd.seed_pack("c1", 0)
    version = 1
    for entity_id, name in (("orc", "Orc"), ("pc1", "Aria")):
        entities.apply_explicit("c1", "main", version, CreateEntityOperation(
            entity_id=entity_id, kind=EntityKind.CHARACTER, name=name,
        ))
        version += 1
        for key, value in (
            ("ability_str", 16), ("ability_dex", 10), ("ability_con", 10),
            ("ability_int", 10), ("ability_wis", 8), ("ability_cha", 10),
            ("proficiency_bonus", 2),
            ("proficiencies", ["claw"]), ("armor_class", 12),
            ("hp_max", 30), ("hp_current", 30), ("hp_temp", 0), ("speed", 30),
            ("character_level", 1),
            ("spell_slots_max", [0] * 9), ("spell_slots_current", [0] * 9),
            ("hit_die", 8), ("hit_dice_total", 1), ("hit_dice_current", 1),
            ("death_saves_success", 0), ("death_saves_failure", 0),
            ("is_dead", False), ("is_stable", False), ("resistances", []),
            ("vulnerabilities", []), ("immunities", []),
            ("condition_immunities", []), ("weapon_masteries", []),
        ):
            entities.apply_explicit(
                "c1", "main", version, SetAttributeOperation(entity_id, key, value)
            )
            version += 1
    dnd.enable("c1", version)
    return combat, monsters, version + 1


def _hp(database, entity_id):
    with database.connect() as connection:
        row = connection.execute(
            "SELECT value_json FROM attribute_values"
            " WHERE entity_id = ? AND attribute_key = 'hp_current'",
            (entity_id,),
        ).fetchone()
        return json.loads(row["value_json"])


def test_multiattack_resolves_all_strikes_with_one_action(database):
    combat, monsters, version = _world(database)
    result = combat.start(
        "c1", expected_version=version,
        roller=SequenceDiceRoller([15, 10]),
        entries=(CombatantEntry("orc"), CombatantEntry("pc1")),
    )
    result = monsters.multiattack(
        "c1", result.state_version, "orc",
        attacks=(
            AttackSpec(target_id="pc1", weapon=CLAW),
            AttackSpec(target_id="pc1", weapon=CLAW),
        ),
        roller=SequenceDiceRoller([15, 4, 14, 3]),
    )
    assert _hp(database, "pc1") == 30 - 7 - 6
    with database.connect() as connection:
        row = connection.execute(
            "SELECT action_used, attacks_this_turn FROM combatants"
            " WHERE entity_id = 'orc'"
        ).fetchone()
        assert (row["action_used"], row["attacks_this_turn"]) == (1, 2)


def test_recharge_rolls_at_turn_start(database):
    combat, monsters, version = _world(database)
    breath = {"name": "fire-breath", "min": 5, "available": False}
    result = combat.start(
        "c1", expected_version=version,
        roller=SequenceDiceRoller([15, 10]),
        entries=(CombatantEntry("orc", recharge=(breath,)), CombatantEntry("pc1")),
    )
    # pc1's turn, then orc's next turn rolls recharge d6 = 5 -> available
    result = combat.advance_turn("c1", result.state_version, SequenceDiceRoller([]))
    result = combat.advance_turn("c1", result.state_version, SequenceDiceRoller([5]))
    with database.connect() as connection:
        recharge = json.loads(connection.execute(
            "SELECT recharge_json FROM combatants WHERE entity_id = 'orc'"
        ).fetchone()["recharge_json"])
        assert recharge[0]["available"] is True
        roll = connection.execute(
            "SELECT formula, total FROM dice_rolls WHERE purpose LIKE 'recharge%'"
        ).fetchone()
        assert (roll["formula"], roll["total"]) == ("1d6", 5)


def test_turn_triggers_damage_heal_and_condition(database):
    combat, monsters, version = _world(database)
    triggers = (
        {"name": "regeneration", "when": "turn_start",
         "effect": {"kind": "heal", "formula": "1d8"}},
        {"name": "fear-aura", "when": "turn_end",
         "effect": {"kind": "condition", "condition": "frightened",
                    "save_ability": "wis", "save_dc": 13, "source": "orc"}},
    )
    result = combat.start(
        "c1", expected_version=version,
        roller=SequenceDiceRoller([15, 10]),
        entries=(CombatantEntry("orc", triggers=triggers), CombatantEntry("pc1")),
    )
    # Fixture shortcut: direct SQL to set orc HP below max (not production behavior).
    with database.connect() as connection:
        connection.execute(
            "UPDATE attribute_values SET value_json = '20'"
            " WHERE entity_id = 'orc' AND attribute_key = 'hp_current'"
        )
    # orc's turn ends -> fear aura on pc1; pc1 save 6 - 1 = 5 < 13 -> frightened
    result = combat.advance_turn("c1", result.state_version, SequenceDiceRoller([6]))
    with database.connect() as connection:
        assert condition_map(connection, "c1", "pc1") == {Condition.FRIGHTENED: 1}
    result = combat.advance_turn("c1", result.state_version, SequenceDiceRoller([6]))
    # orc's turn start: regeneration heals 6
    assert _hp(database, "orc") == 26


def test_environmental_damage_records_roll(database):
    _, monsters, version = _world(database)
    result = monsters.environmental(
        "c1", version, "pc1", formula="3d6", damage_type="bludgeoning",
        purpose="falling 30 ft", roller=SequenceDiceRoller([4, 4, 4]),
    )
    assert _hp(database, "pc1") == 18
    with database.connect() as connection:
        roll = connection.execute(
            "SELECT purpose, formula, total FROM dice_rolls"
            " WHERE purpose = 'falling 30 ft'"
        ).fetchone()
        assert (roll["formula"], roll["total"]) == ("3d6", 12)
