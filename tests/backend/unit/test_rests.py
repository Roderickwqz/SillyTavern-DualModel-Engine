import json

import pytest

from sillytavern_rpg_engine.domain.dice import SequenceDiceRoller
from sillytavern_rpg_engine.domain.dnd import Condition
from sillytavern_rpg_engine.domain.errors import ValidationError
from sillytavern_rpg_engine.domain.models import EntityKind
from sillytavern_rpg_engine.services.attributes import SetAttributeOperation
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.conditions import ConditionService, condition_map
from sillytavern_rpg_engine.services.dnd_pack import DndRulesService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)
from sillytavern_rpg_engine.services.rests import RestService


def _world(database):
    ids = iter(f"e{n}" for n in range(1, 1000))
    clock = lambda: "2026-08-12T00:00:00Z"
    campaigns = CampaignService(database, id_factory=lambda: next(ids), clock=clock)
    dnd = DndRulesService(database, id_factory=lambda: next(ids), clock=clock)
    rests = RestService(database, id_factory=lambda: next(ids), clock=clock)
    conditions = ConditionService(database, id_factory=lambda: next(ids), clock=clock)
    entities = EntityAttributeService(database, dnd.mutation_engine)
    campaigns.create_campaign("c1", "Dungeon")
    dnd.seed_pack("c1", 0)
    entities.apply_explicit("c1", "main", 1, CreateEntityOperation(
        entity_id="pc1", kind=EntityKind.CHARACTER, name="Aria",
    ))
    values = {
        "ability_str": 10, "ability_dex": 10, "ability_con": 14,
        "ability_int": 10, "ability_wis": 10, "ability_cha": 10,
        "proficiency_bonus": 2, "proficiencies": [],
        "armor_class": 12, "hp_max": 20, "hp_current": 7, "hp_temp": 3,
        "speed": 30, "character_level": 3,
        "spell_slots_max": [2, 1, 0, 0, 0, 0, 0, 0, 0],
        "spell_slots_current": [0, 0, 0, 0, 0, 0, 0, 0, 0],
        "hit_die": 8, "hit_dice_total": 3, "hit_dice_current": 1,
        "death_saves_success": 1, "death_saves_failure": 2,
        "is_dead": False, "is_stable": False,
        "resistances": [], "vulnerabilities": [], "immunities": [],
        "condition_immunities": [], "weapon_masteries": [],
    }
    version = 2
    for key, value in values.items():
        entities.apply_explicit(
            "c1", "main", version, SetAttributeOperation("pc1", key, value)
        )
        version += 1
    dnd.enable("c1", version)
    return rests, conditions, version + 1


def _attr(database, key):
    with database.connect() as connection:
        row = connection.execute(
            "SELECT value_json FROM attribute_values"
            " WHERE entity_id = 'pc1' AND attribute_key = ?",
            (key,),
        ).fetchone()
        return json.loads(row["value_json"])


def test_short_rest_spends_hit_dice_and_caps_hp(database):
    rests, _, version = _world(database)
    with pytest.raises(ValidationError, match="hit dice"):
        rests.short_rest("c1", version, "pc1", hit_dice=2,
                         roller=SequenceDiceRoller([]))
    result = rests.short_rest(
        "c1", version, "pc1", hit_dice=1, roller=SequenceDiceRoller([5]),
    )
    assert _attr(database, "hp_current") == 7 + 7  # 5 + 2 con
    assert _attr(database, "hit_dice_current") == 0


def test_long_rest_restores_resources_and_sheds_exhaustion(database):
    rests, conditions, version = _world(database)
    conditions.apply("c1", version, "pc1", Condition.EXHAUSTION, level=2, source="march")
    result = rests.long_rest("c1", version + 1, "pc1")
    assert _attr(database, "hp_current") == 20
    assert _attr(database, "hp_temp") == 0
    assert _attr(database, "spell_slots_current") == [2, 1, 0, 0, 0, 0, 0, 0, 0]
    assert _attr(database, "hit_dice_current") == 2  # 1 + max(1, 3//2)
    assert _attr(database, "death_saves_success") == 0
    assert _attr(database, "death_saves_failure") == 0
    with database.connect() as connection:
        assert condition_map(connection, "c1", "pc1") == {Condition.EXHAUSTION: 1}
