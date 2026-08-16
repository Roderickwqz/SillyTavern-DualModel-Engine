import json
import sqlite3

import pytest

from sillytavern_rpg_engine.domain.dice import SequenceDiceRoller
from sillytavern_rpg_engine.domain.errors import ValidationError
from sillytavern_rpg_engine.services.campaign_export import CampaignExporter
from sillytavern_rpg_engine.services.campaign_import import CampaignImporter
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.dice import DiceService
from sillytavern_rpg_engine.services.dnd_pack import DndRulesService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
    EntityService,
)
from sillytavern_rpg_engine.domain.models import EntityKind
from sillytavern_rpg_engine.services.projection import ProjectionService
from sillytavern_rpg_engine.domain.models import Audience
from sillytavern_rpg_engine.services.attributes import SetAttributeOperation


def _seed(database):
    ids = iter(f"id-{i}" for i in range(50))
    campaigns = CampaignService(database, id_factory=ids.__next__,
                                clock=lambda: "2026-08-16T00:00:00Z")
    campaigns.create_campaign("c1", "Roundtrip")
    attrs = EntityAttributeService(database, campaigns.mutation_engine)
    attrs.apply_explicit("c1", "main", 0, CreateEntityOperation(
        "erin", EntityKind.CHARACTER, "艾琳",
    ))
    return campaigns


def _seed_rolling_campaign(database):
    ids = iter(f"e{n}" for n in range(1, 1000))
    clock = lambda: "2026-08-12T00:00:00Z"
    campaigns = CampaignService(database, id_factory=lambda: next(ids), clock=clock)
    dnd = DndRulesService(database, id_factory=lambda: next(ids), clock=clock)
    dice = DiceService(database, id_factory=lambda: next(ids), clock=clock)
    entities = EntityAttributeService(database, dnd.mutation_engine)
    campaigns.create_campaign("c1", "Dungeon")
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


def test_import_restores_entities_on_empty_database(tmp_path, database):
    _seed(database)
    export_path = tmp_path / "c1.json"
    CampaignExporter(database).export("c1", export_path)
    from sillytavern_rpg_engine.persistence.database import Database
    fresh = Database(tmp_path / "fresh.db")
    from sillytavern_rpg_engine.persistence.migrations import MigrationRunner
    MigrationRunner(fresh).apply()
    campaign_id = CampaignImporter(fresh).import_file(export_path)
    assert campaign_id == "c1"
    projection = ProjectionService(fresh).for_audience(campaign_id, "main", Audience.PLAYER_UI)
    assert any(e["name"] == "艾琳" for e in projection["entities"])


def test_import_refuses_existing_id_without_replace(tmp_path, database):
    _seed(database)
    export_path = tmp_path / "c1.json"
    CampaignExporter(database).export("c1", export_path)
    with pytest.raises(ValidationError, match="already exists"):
        CampaignImporter(database).import_file(export_path)


def test_import_replace_with_dice_rolls(tmp_path, database):
    dice, version = _seed_rolling_campaign(database)
    dice.check(
        "c1", expected_version=version, roller=SequenceDiceRoller([14]),
        entity_id="pc1", ability="str", skill="athletics", dc=15,
        purpose="force the gate",
    )
    export_path = tmp_path / "c1.json"
    CampaignExporter(database).export("c1", export_path)
    payload = json.loads(export_path.read_text(encoding="utf-8"))
    projection_before = ProjectionService(database).for_audience(
        "c1", "main", Audience.PLAYER_UI
    )
    campaign_id = CampaignImporter(database).import_file(export_path, replace=True)
    assert campaign_id == "c1"
    projection = ProjectionService(database).for_audience(
        "c1", "main", Audience.PLAYER_UI
    )
    assert projection == projection_before
    with database.connect() as connection:
        rolls = connection.execute(
            "SELECT * FROM dice_rolls WHERE campaign_id = ?", ("c1",)
        ).fetchall()
        assert len(rolls) == len(payload["dice_rolls"]) == 1
        event = connection.execute(
            "SELECT * FROM audit_events WHERE campaign_id = ?"
            " AND event_type = 'recovery_import'",
            ("c1",),
        ).fetchone()
        assert event is not None
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "DELETE FROM dice_rolls WHERE campaign_id = ?", ("c1",)
            )


def test_import_restores_entity_aliases(tmp_path, database):
    ids = iter(f"id-{i}" for i in range(50))
    campaigns = CampaignService(database, id_factory=ids.__next__,
                                clock=lambda: "2026-08-16T00:00:00Z")
    campaigns.create_campaign("c1", "AliasRoundtrip")
    attrs = EntityAttributeService(database, campaigns.mutation_engine)
    attrs.apply_explicit("c1", "main", 0, CreateEntityOperation(
        "erin", EntityKind.CHARACTER, "艾琳", aliases=("小艾",),
    ))
    export_path = tmp_path / "c1.json"
    CampaignExporter(database).export("c1", export_path)
    from sillytavern_rpg_engine.persistence.database import Database
    fresh = Database(tmp_path / "fresh.db")
    from sillytavern_rpg_engine.persistence.migrations import MigrationRunner
    MigrationRunner(fresh).apply()
    CampaignImporter(fresh).import_file(export_path)
    entity = EntityService(fresh).resolve("c1", "小艾")
    assert entity.id == "erin"
    assert entity.name == "艾琳"
