import json

import pytest

from sillytavern_rpg_engine.domain.dice import SequenceDiceRoller
from sillytavern_rpg_engine.domain.dnd import Condition
from sillytavern_rpg_engine.domain.errors import ValidationError
from sillytavern_rpg_engine.domain.models import EntityKind
from sillytavern_rpg_engine.services.attributes import SetAttributeOperation
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.conditions import condition_map
from sillytavern_rpg_engine.services.damage import DamageService
from sillytavern_rpg_engine.services.dnd_pack import DndRulesService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)


def _world(database, resistances=(), immunities=(), vulnerabilities=()):
    ids = iter(f"e{n}" for n in range(1, 1000))
    clock = lambda: "2026-08-12T00:00:00Z"
    campaigns = CampaignService(database, id_factory=lambda: next(ids), clock=clock)
    dnd = DndRulesService(database, id_factory=lambda: next(ids), clock=clock)
    damage = DamageService(database, id_factory=lambda: next(ids), clock=clock)
    entities = EntityAttributeService(database, dnd.mutation_engine)
    campaigns.create_campaign("c1", "Dungeon")
    dnd.seed_pack("c1", 0)
    entities.apply_explicit("c1", "main", 1, CreateEntityOperation(
        entity_id="pc1", kind=EntityKind.CHARACTER, name="Aria",
    ))
    values = {
        "ability_str": 10, "ability_dex": 10, "ability_con": 13,
        "ability_int": 10, "ability_wis": 10, "ability_cha": 10,
        "proficiency_bonus": 2, "proficiencies": [],
        "armor_class": 10, "hp_max": 12, "hp_current": 12, "hp_temp": 0,
        "speed": 30, "character_level": 1,
        "spell_slots_max": [0] * 9, "spell_slots_current": [0] * 9,
        "hit_die": 10, "hit_dice_total": 1, "hit_dice_current": 1,
        "death_saves_success": 0, "death_saves_failure": 0,
        "is_dead": False, "is_stable": False,
        "resistances": list(resistances), "immunities": list(immunities),
        "vulnerabilities": list(vulnerabilities),
        "condition_immunities": [], "weapon_masteries": [],
    }
    version = 2
    for key, value in values.items():
        entities.apply_explicit(
            "c1", "main", version, SetAttributeOperation("pc1", key, value)
        )
        version += 1
    dnd.enable("c1", version)
    return damage, entities, version + 1


def _hp(connection, key="hp_current"):
    row = connection.execute(
        "SELECT value_json FROM attribute_values"
        " WHERE entity_id = 'pc1' AND attribute_key = ?",
        (key,),
    ).fetchone()
    return json.loads(row["value_json"])


def _hp_at(database, key="hp_current"):
    with database.connect() as connection:
        return _hp(connection, key)


def test_resistance_vulnerability_immunity_and_temp_hp(database):
    damage, entities, version = _world(database, resistances=["fire"], immunities=["cold"])
    result = damage.deal("c1", version, "pc1", 9, "fire")
    assert result.snapshot and _hp_at(database) == 8
    result = damage.deal("c1", result.state_version, "pc1", 5, "cold")
    assert _hp_at(database) == 8
    attr = entities.apply_explicit(
        "c1", "main", result.state_version,
        SetAttributeOperation("pc1", "hp_temp", 5),
    )
    result = damage.deal("c1", attr.state_version, "pc1", 5, "slashing")
    assert _hp_at(database) == 8
    assert _hp_at(database, "hp_temp") == 0


def test_zero_hp_unconscious_massive_damage_and_death_save_hits(database):
    damage, _, version = _world(database)
    result = damage.deal("c1", version, "pc1", 12, "slashing")
    assert _hp_at(database) == 0
    with database.connect() as connection:
        assert condition_map(connection, "c1", "pc1") == {Condition.UNCONSCIOUS: 1}
    result = damage.deal("c1", result.state_version, "pc1", 3, "slashing")
    assert _hp_at(database, "death_saves_failure") == 1
    result = damage.deal(
        "c1", result.state_version, "pc1", 2, "slashing", is_crit_hit_5ft=True
    )
    assert _hp_at(database, "is_dead") is True
    with pytest.raises(ValidationError, match="dead"):
        damage.stabilize("c1", result.state_version, "pc1")


def test_massive_damage_kills_instantly(database):
    damage, _, version = _world(database)
    damage.deal("c1", version, "pc1", 24, "fire")
    assert _hp_at(database, "is_dead") is True


def test_healing_from_zero_clears_dying_state(database):
    damage, _, version = _world(database)
    result = damage.deal("c1", version, "pc1", 12, "fire")
    result = damage.heal("c1", result.state_version, "pc1", 5)
    assert _hp_at(database) == 5
    with database.connect() as connection:
        assert condition_map(connection, "c1", "pc1") == {}
    result = damage.heal("c1", result.state_version, "pc1", 99)
    assert _hp_at(database) == 12


def test_death_saves_natural_outcomes_and_stabilize(database):
    damage, _, version = _world(database)
    result = damage.deal("c1", version, "pc1", 12, "fire")
    result = damage.death_save(
        "c1", result.state_version, SequenceDiceRoller([1]), "pc1"
    )
    assert _hp_at(database, "death_saves_failure") == 2
    result = damage.death_save(
        "c1", result.state_version, SequenceDiceRoller([12]), "pc1"
    )
    assert _hp_at(database, "death_saves_success") == 1
    result = damage.death_save(
        "c1", result.state_version, SequenceDiceRoller([3]), "pc1"
    )
    assert _hp_at(database, "is_dead") is True


def test_natural_20_death_save_revives_with_1_hp(database):
    damage, _, version = _world(database)
    result = damage.deal("c1", version, "pc1", 12, "fire")
    damage.death_save("c1", result.state_version, SequenceDiceRoller([20]), "pc1")
    assert _hp_at(database) == 1
    with database.connect() as connection:
        assert condition_map(connection, "c1", "pc1") == {}
