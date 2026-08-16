import json
import pytest

from sillytavern_rpg_engine.domain.errors import ValidationError
from sillytavern_rpg_engine.services.campaign_export import CampaignExporter
from sillytavern_rpg_engine.services.campaign_import import CampaignImporter
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)
from sillytavern_rpg_engine.domain.models import EntityKind
from sillytavern_rpg_engine.services.projection import ProjectionService
from sillytavern_rpg_engine.domain.models import Audience


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
