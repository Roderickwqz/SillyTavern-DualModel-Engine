import json

import pytest

from sillytavern_rpg_engine.domain.dice import DiceFormula, SequenceDiceRoller
from sillytavern_rpg_engine.domain.dnd import SpellProfile
from sillytavern_rpg_engine.domain.errors import ValidationError
from sillytavern_rpg_engine.domain.models import EntityKind
from sillytavern_rpg_engine.services.attributes import SetAttributeOperation
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.combat import CombatService, CombatantEntry
from sillytavern_rpg_engine.services.dnd_pack import DndRulesService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)
from sillytavern_rpg_engine.services.spells import SpellService

MAGIC_MISSILE = SpellProfile(
    key="magic-missile", level=1, attack=False, save_ability=None,
    damage=DiceFormula(3, 4, 3), damage_type="force", healing=None,
    concentration=False, duration_rounds=None, ability="int",
)
SACRED_FLAME = SpellProfile(
    key="sacred-flame", level=0, attack=False, save_ability="dex",
    damage=DiceFormula(1, 8), damage_type="radiant", healing=None,
    concentration=False, duration_rounds=None, ability="wis",
)
BLESS = SpellProfile(
    key="bless", level=1, attack=False, save_ability=None, damage=None,
    damage_type=None, healing=None, concentration=True, duration_rounds=10,
    ability="wis",
)
CURE = SpellProfile(
    key="cure-wounds", level=1, attack=False, save_ability=None, damage=None,
    damage_type=None, healing=DiceFormula(2, 8), concentration=False,
    duration_rounds=None, ability="wis",
)


def _world(database):
    ids = iter(f"e{n}" for n in range(1, 2000))
    clock = lambda: "2026-08-12T00:00:00Z"
    campaigns = CampaignService(database, id_factory=lambda: next(ids), clock=clock)
    dnd = DndRulesService(database, id_factory=lambda: next(ids), clock=clock)
    combat = CombatService(database, id_factory=lambda: next(ids), clock=clock)
    spells = SpellService(database, id_factory=lambda: next(ids), clock=clock)
    entities = EntityAttributeService(database, dnd.mutation_engine)
    campaigns.create_campaign("c1", "Dungeon")
    dnd.seed_pack("c1", 0)
    version = 1
    for entity_id, name, values in (
        ("pc1", "Aria", {"ability_wis": 16, "ability_int": 12,
                          "spell_slots_max": [2, 0, 0, 0, 0, 0, 0, 0, 0]}),
        ("orc", "Orc", {"ability_dex": 10, "ability_wis": 8}),
    ):
        entities.apply_explicit("c1", "main", version, CreateEntityOperation(
            entity_id=entity_id, kind=EntityKind.CHARACTER, name=name,
        ))
        version += 1
        base = {
            "ability_str": 10, "ability_dex": 10, "ability_con": 10,
            "ability_int": 10, "ability_wis": 10, "ability_cha": 10,
            "proficiency_bonus": 2, "proficiencies": [],
            "armor_class": 12, "hp_max": 30, "hp_current": 30, "hp_temp": 0,
            "speed": 30, "character_level": 1,
            "spell_slots_max": [0] * 9, "spell_slots_current": [0] * 9,
            "hit_die": 8, "hit_dice_total": 1, "hit_dice_current": 1,
            "death_saves_success": 0, "death_saves_failure": 0,
            "is_dead": False, "is_stable": False, "resistances": [],
            "vulnerabilities": [], "immunities": [],
            "condition_immunities": [], "weapon_masteries": [],
        }
        base.update(values)
        base["spell_slots_current"] = list(base["spell_slots_max"])
        for key, value in base.items():
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
    return spells, combat, result.state_version


def _slots(database, entity_id="pc1", key="spell_slots_current"):
    with database.connect() as connection:
        row = connection.execute(
            "SELECT value_json FROM attribute_values"
            " WHERE entity_id = ? AND attribute_key = ?",
            (entity_id, key),
        ).fetchone()
        return json.loads(row["value_json"])


def _hp(database, entity_id):
    return _slots(database, entity_id, "hp_current")


def test_slotted_cast_spends_slot_and_one_slot_per_turn(database):
    spells, _, version = _world(database)
    result = spells.cast(
        "c1", version, "pc1", MAGIC_MISSILE, slot_level=1,
        target_ids=("orc",), roller=SequenceDiceRoller([2, 2, 2]),
    )
    assert _slots(database)[0] == 1
    assert _hp(database, "orc") == 30 - 9  # 3d4+3 auto-hit
    with pytest.raises(ValidationError, match="one spell slot"):
        spells.cast(
            "c1", result.state_version, "pc1", MAGIC_MISSILE, slot_level=1,
            target_ids=("orc",), roller=SequenceDiceRoller([2, 2, 2]),
        )


def test_cantrip_save_halves_on_success(database):
    spells, combat, version = _world(database)
    # pc1 casts sacred flame on orc: DC 8 + 2 + 3 = 13; orc rolls 14 + 0 = 14
    result = spells.cast(
        "c1", version, "pc1", SACRED_FLAME, slot_level=None,
        target_ids=("orc",), roller=SequenceDiceRoller([14, 6]),
    )
    assert _hp(database, "orc") == 30 - 3  # 6 halved
    assert _slots(database)[0] == 2  # cantrip spends nothing


def test_concentration_replaces_ticks_and_ends(database):
    spells, combat, version = _world(database)
    result = spells.cast(
        "c1", version, "pc1", BLESS, slot_level=1,
        target_ids=("pc1",), roller=SequenceDiceRoller([]),
    )
    with database.connect() as connection:
        row = connection.execute(
            "SELECT concentrating_spell, concentration_rounds FROM combatants"
            " WHERE entity_id = 'pc1'"
        ).fetchone()
        assert (row["concentrating_spell"], row["concentration_rounds"]) == ("bless", 10)
    result = combat.advance_turn("c1", result.state_version, SequenceDiceRoller([]))
    result = combat.advance_turn("c1", result.state_version, SequenceDiceRoller([]))
    with database.connect() as connection:
        row = connection.execute(
            "SELECT concentration_rounds FROM combatants WHERE entity_id = 'pc1'"
        ).fetchone()
        assert row["concentration_rounds"] == 9
    result = spells.end_concentration("c1", result.state_version, "pc1")
    with database.connect() as connection:
        row = connection.execute(
            "SELECT concentrating_spell FROM combatants WHERE entity_id = 'pc1'"
        ).fetchone()
        assert row["concentrating_spell"] is None


def test_non_combatant_cannot_cast_during_encounter(database):
    spells, _, version = _world(database)
    ids = iter(f"e{n}" for n in range(2000, 3000))
    clock = lambda: "2026-08-12T00:00:00Z"
    dnd = DndRulesService(database, id_factory=lambda: next(ids), clock=clock)
    entities = EntityAttributeService(database, dnd.mutation_engine)
    entities.apply_explicit(
        "c1", "main", version,
        CreateEntityOperation(entity_id="out", kind=EntityKind.CHARACTER, name="Outsider"),
    )
    version += 1
    slots = [2, 0, 0, 0, 0, 0, 0, 0, 0]
    for key, value in (
        ("ability_int", 12), ("spell_slots_max", slots), ("spell_slots_current", slots),
    ):
        entities.apply_explicit(
            "c1", "main", version, SetAttributeOperation("out", key, value),
        )
        version += 1
    before = _slots(database, "out")
    with pytest.raises(ValidationError, match="not in the encounter"):
        spells.cast(
            "c1", version, "out", MAGIC_MISSILE, slot_level=1,
            target_ids=("orc",), roller=SequenceDiceRoller([2, 2, 2]),
        )
    assert _slots(database, "out") == before


def test_healing_spell_and_slot_validation(database):
    spells, _, version = _world(database)
    with pytest.raises(ValidationError, match="slot level"):
        spells.cast(
            "c1", version, "pc1", CURE, slot_level=2, target_ids=("pc1",),
            roller=SequenceDiceRoller([3, 3]),
        )
    with pytest.raises(ValidationError, match="cantrip"):
        spells.cast(
            "c1", version, "pc1", CURE, slot_level=None, target_ids=("pc1",),
            roller=SequenceDiceRoller([3, 3]),
        )
    result = spells.cast(
        "c1", version, "pc1", CURE, slot_level=1, target_ids=("pc1",),
        roller=SequenceDiceRoller([3, 3]),
    )
    assert _hp(database, "pc1") == 30  # already full: capped
