import json

from sillytavern_rpg_engine.domain.models import EntityKind
from sillytavern_rpg_engine.services.campaign_export import CampaignExporter
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)


def test_export_v2_includes_definitions_turns_and_memory(database, tmp_path):
    ids = iter(f"id-{i}" for i in range(20))
    campaigns = CampaignService(
        database,
        id_factory=ids.__next__,
        clock=lambda: "2026-08-16T00:00:00Z",
    )
    campaigns.create_campaign("c1", "Export")
    attrs = EntityAttributeService(database, campaigns.mutation_engine)
    attrs.apply_explicit(
        "c1", "main", 0,
        CreateEntityOperation("erin", EntityKind.CHARACTER, "艾琳"),
    )
    output = tmp_path / "c1.json"
    CampaignExporter(database).export("c1", output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["export_schema_version"] == 2
    for key in (
        "attribute_definitions",
        "attribute_values",
        "turns",
        "branch_heads",
        "memory_events",
    ):
        assert key in payload, f"missing {key}"
    assert payload["campaign"]["id"] == "c1"
