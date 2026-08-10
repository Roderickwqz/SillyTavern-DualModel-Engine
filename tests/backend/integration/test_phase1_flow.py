from pathlib import Path

import pytest

from sillytavern_rpg_engine.domain.models import (
    AgeStatus,
    AttributeDefinition,
    AttributeType,
    Audience,
    CampaignRules,
    DisplayType,
    EntityKind,
    RulesMode,
)
from sillytavern_rpg_engine.persistence.database import Database
from sillytavern_rpg_engine.persistence.migrations import MigrationRunner
from sillytavern_rpg_engine.cli import main
from sillytavern_rpg_engine.services.attributes import DefineAttributeOperation, SetAttributeOperation
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import CreateEntityOperation, EntityAttributeService
from sillytavern_rpg_engine.services.projection import ProjectionService


def test_cli_help_exits_zero():
    with pytest.raises(SystemExit) as raised:
        main(["--help"])
    assert raised.value.code == 0


def test_phase1_state_survives_restart_and_campaign_rules_are_optional(tmp_path: Path):
    path = tmp_path / "world.sqlite3"
    database = Database(path)
    MigrationRunner(database).apply()
    ids = iter(f"event-{index}" for index in range(20))
    campaigns = CampaignService(database, id_factory=ids.__next__, clock=lambda: "2026-08-10T00:00:00Z")
    campaigns.create_campaign("story", "自由剧情")
    campaigns.create_campaign("dungeon", "地下城")
    campaigns.set_rules("dungeon", 0, CampaignRules(RulesMode.DND_2024, True, "5.2.1"))

    entity_state = EntityAttributeService(database, campaigns.mutation_engine)
    entity_state.apply_explicit("story", "main", 0, CreateEntityOperation(
        "erin", EntityKind.CHARACTER, "艾琳", AgeStatus.ADULT, ("Erin",)
    ))
    definition = AttributeDefinition(
        "story", "alchemy", "炼金术", "skill", AttributeType.NUMBER,
        DisplayType.BAR, frozenset({Audience.ENGINE, Audience.PLAYER_UI}), 0, 100,
    )
    entity_state.apply_explicit("story", "main", 1, DefineAttributeOperation(definition, ("炼金",)))
    entity_state.apply_explicit("story", "main", 2, SetAttributeOperation("erin", "alchemy", 35, "turn-1"))

    reopened = Database(path)
    story = ProjectionService(reopened).for_audience("story", "main", Audience.PLAYER_UI)
    dungeon = ProjectionService(reopened).for_audience("dungeon", "main", Audience.PLAYER_UI)
    assert story["rules"]["mode"] == "narrative"
    assert story["entities"][0]["attributes"][0]["value"] == 35
    assert dungeon["rules"] == {
        "mode": "dnd-2024",
        "enabled": True,
        "version": "5.2.1",
        "custom_preset_id": None,
    }
