import pytest

from sillytavern_rpg_engine.domain.errors import ValidationError
from sillytavern_rpg_engine.domain.models import EntityKind
from sillytavern_rpg_engine.services.attributes import SetAttributeOperation
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.dnd_pack import (
    DndRulesService,
    REQUIRED_CHARACTER_KEYS,
    check_readiness,
    dnd_definitions,
)
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)


def _services(database):
    ids = iter(f"event-{n}" for n in range(1, 1000))
    clock = lambda: "2026-08-12T00:00:00Z"
    campaigns = CampaignService(database, id_factory=lambda: next(ids), clock=clock)
    dnd = DndRulesService(database, id_factory=lambda: next(ids), clock=clock)
    return campaigns, dnd, EntityAttributeService(database, dnd.mutation_engine)


def test_seed_pack_registers_all_definitions_and_readiness_reports_missing(database):
    campaigns, dnd, entities = _services(database)
    campaigns.create_campaign("c1", "Dungeon")
    dnd.seed_pack("c1", expected_version=0)
    entities.apply_explicit("c1", "main", 1, CreateEntityOperation(
        entity_id="pc1", kind=EntityKind.CHARACTER, name="Aria",
    ))
    report = dnd.readiness("c1")
    assert not report.ready
    assert set(report.missing["pc1"]) == REQUIRED_CHARACTER_KEYS


def test_enable_requires_complete_character_data(database):
    campaigns, dnd, entities = _services(database)
    campaigns.create_campaign("c1", "Dungeon")
    dnd.seed_pack("c1", 0)
    entities.apply_explicit("c1", "main", 1, CreateEntityOperation(
        entity_id="pc1", kind=EntityKind.CHARACTER, name="Aria",
    ))
    with pytest.raises(ValidationError, match="pc1"):
        dnd.enable("c1", 2)
    version = 2
    values = {
        "ability_str": 16, "ability_dex": 14, "ability_con": 13,
        "ability_int": 10, "ability_wis": 12, "ability_cha": 8,
        "proficiency_bonus": 2, "proficiencies": ["longsword"],
        "armor_class": 16, "hp_max": 12, "hp_current": 12, "hp_temp": 0,
        "speed": 30, "character_level": 1,
        "spell_slots_max": [0] * 9, "spell_slots_current": [0] * 9,
        "hit_die": 10, "hit_dice_total": 1, "hit_dice_current": 1,
        "death_saves_success": 0, "death_saves_failure": 0,
        "is_dead": False, "is_stable": False,
        "resistances": [], "vulnerabilities": [], "immunities": [],
        "condition_immunities": [], "weapon_masteries": ["longsword"],
    }
    for key, value in values.items():
        entities.apply_explicit(
            "c1", "main", version, SetAttributeOperation("pc1", key, value)
        )
        version += 1
    assert dnd.readiness("c1").ready
    result = dnd.enable("c1", version)
    assert result.snapshot["campaign"]["rules"] == {
        "mode": "dnd-2024", "enabled": True,
        "version": "srd-5.2.1", "custom_preset_id": None,
    }


def test_enable_without_characters_is_ready_and_seed_is_idempotent_guarded(database):
    campaigns, dnd, _ = _services(database)
    campaigns.create_campaign("c1", "Dungeon")
    dnd.seed_pack("c1", 0)
    assert dnd.readiness("c1").ready
    dnd.enable("c1", 1)
    with pytest.raises(ValidationError, match="already exists"):
        dnd.seed_pack("c1", 2)


def test_definitions_are_audience_visible_and_ranges_enforced(database):
    definitions = dnd_definitions("c1")
    by_key = {d.key: d for d in definitions}
    assert "player_ui" in {a.value for a in by_key["hp_current"].audiences}
    assert by_key["spell_slots_max"].value_type.value == "list"
    assert by_key["character_level"].minimum == 1
