import json

import pytest

from sillytavern_rpg_engine.domain.dice import D20Mode, SequenceDiceRoller
from sillytavern_rpg_engine.domain.dnd import DND2024_RULES_VERSION
from sillytavern_rpg_engine.domain.errors import ValidationError
from sillytavern_rpg_engine.domain.models import EntityKind
from sillytavern_rpg_engine.services.attributes import SetAttributeOperation
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.dice import DiceService
from sillytavern_rpg_engine.services.dnd_pack import DndRulesService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)


def _ready_campaign(database):
    ids = iter(f"e{n}" for n in range(1, 1000))
    clock = lambda: "2026-08-12T00:00:00Z"
    campaigns = CampaignService(database, id_factory=lambda: next(ids), clock=clock)
    dnd = DndRulesService(database, id_factory=lambda: next(ids), clock=clock)
    dice = DiceService(database, id_factory=lambda: next(ids), clock=clock)
    entities = EntityAttributeService(database, dnd.mutation_engine)
    campaigns.create_campaign("c1", "Dungeon")
    campaigns.create_campaign("c2", "Story")
    dnd.seed_pack("c1", 0)
    entities.apply_explicit("c1", "main", 1, CreateEntityOperation(
        entity_id="pc1", kind=EntityKind.CHARACTER, name="Aria",
    ))
    values = {
        "ability_str": 16, "ability_dex": 14, "ability_con": 13,
        "ability_int": 10, "ability_wis": 12, "ability_cha": 8,
        "proficiency_bonus": 2, "proficiencies": ["athletics"],
        "armor_class": 16, "hp_max": 12, "hp_current": 12, "hp_temp": 0,
        "speed": 30, "character_level": 1,
        "spell_slots_max": [0] * 9, "spell_slots_current": [0] * 9,
        "hit_die": 10, "hit_dice_total": 1, "hit_dice_current": 1,
        "death_saves_success": 0, "death_saves_failure": 0,
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
    return dice, version + 1


def test_check_records_immutable_roll_with_pinned_version(database):
    dice, version = _ready_campaign(database)
    result = dice.check(
        "c1", expected_version=version, roller=SequenceDiceRoller([14]),
        entity_id="pc1", ability="str", skill="athletics", dc=15,
        purpose="force the gate",
    )
    # +3 str, +2 proficiency => 14 + 5 = 19 >= 15
    assert result.snapshot["campaign"]["rules"]["version"] == DND2024_RULES_VERSION
    with database.connect() as connection:
        row = connection.execute("SELECT * FROM dice_rolls").fetchone()
        assert row["purpose"] == "force the gate"
        assert row["formula"] == "1d20"
        assert json.loads(row["faces_json"]) == [14]
        assert row["modifier"] == 5
        assert row["total"] == 19
        assert row["dc"] == 15
        assert row["success"] == 1
        assert row["rules_version"] == "srd-5.2.1"


def test_check_without_proficiency_and_narrative_campaign_rejected(database):
    dice, version = _ready_campaign(database)
    result = dice.check(
        "c1", expected_version=version, roller=SequenceDiceRoller([10]),
        entity_id="pc1", ability="int", skill=None, dc=12, purpose="recall lore",
    )
    with database.connect() as connection:
        row = connection.execute(
            "SELECT modifier, total, success FROM dice_rolls ORDER BY rowid DESC"
        ).fetchone()
        assert (row["modifier"], row["total"], row["success"]) == (0, 10, 0)
    with pytest.raises(ValidationError, match="enabled"):
        dice.check(
            "c2", expected_version=0, roller=SequenceDiceRoller([10]),
            entity_id="pc1", ability="int", skill=None, dc=12, purpose="lore",
        )
