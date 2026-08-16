"""Phase 7 §17.2 integration: export -> fresh-DB import preserves state."""

import json
import sqlite3

from sillytavern_rpg_engine.config import load_settings
from sillytavern_rpg_engine.domain.models import (
    AttributeDefinition,
    AttributeType,
    Audience,
    DisplayType,
    EntityKind,
)
from sillytavern_rpg_engine.llm.scripted import ScriptedLLMClient
from sillytavern_rpg_engine.orchestration.graph import TurnRunner, default_services
from sillytavern_rpg_engine.orchestration.normalize import strip_tracker_blocks
from sillytavern_rpg_engine.persistence.database import Database
from sillytavern_rpg_engine.persistence.migrations import MigrationRunner
from sillytavern_rpg_engine.services.attributes import DefineAttributeOperation
from sillytavern_rpg_engine.services.branches import BranchService
from sillytavern_rpg_engine.services.campaign_export import CampaignExporter
from sillytavern_rpg_engine.services.campaign_import import CampaignImporter
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.database_backup import DatabaseBackupService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)
from sillytavern_rpg_engine.services.projection import ProjectionService

_SET_ATTRIBUTE = (
    '{"kind": "set_attribute", "entity_id": "erin", "attribute_key": "alchemy",'
    ' "value": %d, "turn_id": null}'
)


def _seed_authoritative_state(database, settings):
    """Build source state: entity + attribute definition, an applied attribute
    change, two SCENE memory transcripts, and a forked branch."""
    ids = iter(f"id-{i}" for i in range(50))
    campaigns = CampaignService(
        database, id_factory=ids.__next__, clock=lambda: "2026-08-16T00:00:00Z"
    )
    campaigns.create_campaign("c1", "Roundtrip")
    attrs = EntityAttributeService(database, campaigns.mutation_engine)
    attrs.apply_explicit("c1", "main", 0, CreateEntityOperation(
        "erin", EntityKind.CHARACTER, "艾琳",
    ))
    attrs.apply_explicit("c1", "main", 1, DefineAttributeOperation(
        AttributeDefinition(
            campaign_id="c1", key="alchemy", label="炼金", category="skill",
            value_type=AttributeType.NUMBER, display=DisplayType.BAR,
            audiences=frozenset({Audience.PLAYER_UI}), minimum=0, maximum=100,
        ),
    ))

    narrator = ScriptedLLMClient([
        json.dumps({"operations": [json.loads(_SET_ATTRIBUTE % 40)]}),
        "叙述二",
        json.dumps({"operations": []}),
    ])
    runner = TurnRunner(default_services(database, settings, narrator))
    history = []
    for text in ("把艾琳的炼金术调整为40", "我让艾琳继续探索地牢。"):
        history.append({"role": "user", "content": text})
        result = runner.run({"campaign_id": "c1", "messages": list(history)})
        history.append({
            "role": "assistant",
            "content": strip_tracker_blocks(
                result["choices"][0]["message"]["content"]
            ),
        })

    with database.connect() as connection:
        turn1 = connection.execute(
            "SELECT id FROM turns WHERE player_text = '把艾琳的炼金术调整为40'"
        ).fetchone()
        assert turn1 is not None, "turn 1 missing after scripted play"
    BranchService(database).fork(
        "c1", "main", turn1["id"], new_branch_id="branch-side",
    )


def _authoritative_fields(database):
    """Return the roundtrip-relevant authoritative fields of campaign c1."""
    with database.connect() as connection:
        return {
            "state_version": connection.execute(
                "SELECT state_version FROM campaigns WHERE id = 'c1'"
            ).fetchone()["state_version"],
            "memory_events": connection.execute(
                "SELECT COUNT(*) FROM memory_events WHERE campaign_id = 'c1'"
            ).fetchone()[0],
            "branch_heads": [
                (row["branch_id"], row["state_version"])
                for row in connection.execute(
                    "SELECT branch_id, state_version FROM branch_heads"
                    " WHERE campaign_id = 'c1' ORDER BY branch_id"
                )
            ],
        }


def test_export_backup_import_roundtrip_preserves_state(tmp_path):
    source_db = Database(tmp_path / "source.db")
    MigrationRunner(source_db).apply()
    _seed_authoritative_state(
        source_db, load_settings({"RPG_NARRATOR_MODEL": "narrator-model"})
    )

    export_path = tmp_path / "c1.json"
    CampaignExporter(source_db).export("c1", export_path)

    backup_dir = tmp_path / "backups"
    DatabaseBackupService(
        source_db, clock=lambda: "2026-08-16T12:00:00Z"
    ).backup(backup_dir)
    backup = next(backup_dir.glob("*.db"))
    with sqlite3.connect(str(backup)) as connection:
        assert connection.execute(
            "PRAGMA integrity_check"
        ).fetchall() == [("ok",)]

    target_db = Database(tmp_path / "target.db")
    MigrationRunner(target_db).apply()
    assert CampaignImporter(target_db).import_file(export_path) == "c1"

    src_projection = ProjectionService(source_db).for_audience(
        "c1", "main", Audience.PLAYER_UI
    )
    target_projection = ProjectionService(target_db).for_audience(
        "c1", "main", Audience.PLAYER_UI
    )
    assert target_projection == src_projection
    assert src_projection["state_version"] == 3
    assert src_projection["entities"] == [{
        "id": "erin", "kind": "character", "name": "艾琳",
        "attributes": [{"key": "alchemy", "value": 40}],
        "facts": [], "relationships": [],
    }]

    assert _authoritative_fields(target_db) == _authoritative_fields(source_db)

    with target_db.connect() as connection:
        event = connection.execute(
            "SELECT payload_json FROM audit_events"
            " WHERE campaign_id = 'c1' AND event_type = 'recovery_import'"
            " AND source = 'import'"
        ).fetchone()
        assert event is not None, "recovery_import audit event missing"
        assert json.loads(event["payload_json"])["export_schema_version"] == 2


def test_import_reexport_verify_import_preserves_state(tmp_path):
    source_db = Database(tmp_path / "source.db")
    MigrationRunner(source_db).apply()
    _seed_authoritative_state(
        source_db, load_settings({"RPG_NARRATOR_MODEL": "narrator-model"})
    )

    export_path = tmp_path / "c1.json"
    CampaignExporter(source_db).export("c1", export_path)

    imported_db = Database(tmp_path / "imported.db")
    MigrationRunner(imported_db).apply()
    assert CampaignImporter(imported_db).import_file(export_path) == "c1"

    reexport_path = tmp_path / "c1-reexport.json"
    CampaignExporter(imported_db).export("c1", reexport_path)
    assert (
        CampaignExporter(imported_db).verify_export(reexport_path)["ok"] is True
    ), "re-export of imported DB must verify clean"

    third_db = Database(tmp_path / "third.db")
    MigrationRunner(third_db).apply()
    assert CampaignImporter(third_db).import_file(reexport_path) == "c1"

    src_projection = ProjectionService(source_db).for_audience(
        "c1", "main", Audience.PLAYER_UI
    )
    third_projection = ProjectionService(third_db).for_audience(
        "c1", "main", Audience.PLAYER_UI
    )
    assert third_projection == src_projection
    with third_db.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE campaign_id = 'c1'"
            " AND event_type = 'recovery_import'"
        ).fetchone()[0]
        assert count == 2, "one recovery_import per import hop expected"

    payload = json.loads(reexport_path.read_text(encoding="utf-8"))
    versions = CampaignExporter._state_versions(payload)
    assert all(a + 1 == b for a, b in zip(versions, versions[1:])), versions
