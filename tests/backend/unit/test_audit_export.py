import json

import pytest

from sillytavern_rpg_engine import cli
from sillytavern_rpg_engine.persistence.database import Database
from sillytavern_rpg_engine.persistence.migrations import MigrationRunner
from sillytavern_rpg_engine.services.audit_export import JsonlAuditExporter
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.campaign_export import CampaignExporter


def create_campaign(database, event_id, campaign_id="c1"):
    service = CampaignService(
        database,
        id_factory=lambda: event_id,
        clock=lambda: "2026-08-10T00:00:00Z",
    )
    service.create_campaign(campaign_id, "Campaign")


def test_jsonl_retry_does_not_duplicate_event(database, tmp_path):
    create_campaign(database, "event-1")
    output = tmp_path / "audit.jsonl"
    exporter = JsonlAuditExporter(database)
    assert exporter.flush(output) == 1
    with database.transaction() as connection:
        connection.execute("UPDATE jsonl_outbox SET exported_at = NULL")
    assert exporter.flush(output) == 0
    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert [record["event_id"] for record in records] == ["event-1"]


def test_flush_skips_valid_json_line_without_event_id(database, tmp_path):
    create_campaign(database, "event-1")
    output = tmp_path / "audit.jsonl"
    exporter = JsonlAuditExporter(database)
    assert exporter.flush(output) == 1
    with open(output, "a", encoding="utf-8") as handle:
        handle.write('{"no_event_id": true}\n')
    with database.transaction() as connection:
        connection.execute("UPDATE jsonl_outbox SET exported_at = NULL")
    assert exporter.flush(output) == 0
    records = [
        json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()
    ]
    assert [record["event_id"] for record in records if "event_id" in record] == ["event-1"]


def test_flush_returns_zero_when_nothing_pending(database, tmp_path):
    output = tmp_path / "audit.jsonl"
    exporter = JsonlAuditExporter(database)
    assert exporter.flush(output) == 0
    create_campaign(database, "event-1")
    assert exporter.flush(output) == 1
    assert exporter.flush(output) == 0


def test_campaign_export_contains_schema_and_no_credentials(database, tmp_path):
    create_campaign(database, "event-2")
    output = tmp_path / "campaign.json"
    CampaignExporter(database).export("c1", output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["export_schema_version"] == 2
    assert payload["campaign"]["id"] == "c1"
    serialized = json.dumps(payload).casefold()
    assert "api_key" not in serialized
    assert "authorization" not in serialized


def test_flush_appends_later_events_after_first_flush(database, tmp_path):
    create_campaign(database, "event-1")
    output = tmp_path / "audit.jsonl"
    exporter = JsonlAuditExporter(database)
    assert exporter.flush(output) == 1
    create_campaign(database, "event-2", "c2")
    assert exporter.flush(output) == 1
    records = [
        json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()
    ]
    assert [record["event_id"] for record in records] == ["event-1", "event-2"]


def test_exporters_require_existing_parent_directory(database, tmp_path):
    create_campaign(database, "event-1")
    missing = tmp_path / "missing" / "audit.jsonl"
    with pytest.raises(FileNotFoundError):
        JsonlAuditExporter(database).flush(missing)
    with pytest.raises(FileNotFoundError):
        CampaignExporter(database).export("c1", tmp_path / "missing" / "campaign.json")


def test_export_verifies_clean(database, tmp_path):
    create_campaign(database, "event-2")
    output = tmp_path / "campaign.json"
    exporter = CampaignExporter(database)
    exporter.export("c1", output)
    result = exporter.verify_export(output)
    assert result["ok"] is True
    assert result["campaign_id"] == "c1"
    assert result["state_versions"] == [0]


def test_verify_export_rejects_deeply_nested_forbidden_key(database, tmp_path):
    create_campaign(database, "event-2")
    output = tmp_path / "campaign.json"
    exporter = CampaignExporter(database)
    exporter.export("c1", output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    payload["extra"] = {"inner": {"api_key": "x"}}
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(payload), encoding="utf-8")
    result = exporter.verify_export(tampered)
    assert result["ok"] is False
    assert any("api_key" in error for error in result["errors"])


def test_export_leaves_no_temporary_sibling(database, tmp_path):
    create_campaign(database, "event-2")
    output = tmp_path / "campaign.json"
    CampaignExporter(database).export("c1", output)
    assert output.is_file()
    assert not (tmp_path / "campaign.json.tmp").exists()


def test_verify_export_flags_tampered_payload(database, tmp_path):
    create_campaign(database, "event-2")
    output = tmp_path / "campaign.json"
    exporter = CampaignExporter(database)
    exporter.export("c1", output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    payload["audit_events"].append({"state_version": 6})
    payload["extra"] = {"api_key": "leaked"}
    output.write_text(json.dumps(payload), encoding="utf-8")
    result = exporter.verify_export(output)
    assert result["ok"] is False
    assert any("api_key" in error for error in result["errors"])
    assert any("state versions" in error for error in result["errors"])
    output.write_text("{not json", encoding="utf-8")
    assert exporter.verify_export(output)["ok"] is False


def test_cli_migrate_status_reports_ok(database, tmp_path, capsys):
    db = Database(tmp_path / "t.db")
    MigrationRunner(db).apply()
    assert cli.main(["migrate-status", "--database", str(tmp_path / "t.db")]) == 0
    assert "pending" in capsys.readouterr().out


def test_cli_flush_audit_writes_jsonl(database, tmp_path, capsys):
    create_campaign(database, "cli-1")
    output = tmp_path / "audit.jsonl"
    assert cli.main(["flush-audit", "--database", str(database.path), "--output", str(output)]) == 0
    assert output.is_file()
    assert '"cli-1"' in output.read_text(encoding="utf-8")


def test_cli_import_and_verify_export_roundtrip(database, tmp_path, capsys):
    create_campaign(database, "cli-2")
    exported = tmp_path / "c1.json"
    CampaignExporter(database).export("c1", exported)
    assert cli.main(["verify-export", "--input", str(exported)]) == 0
    fresh = tmp_path / "fresh.db"
    assert cli.main(["import", "--database", str(fresh), "--input", str(exported)]) == 0
    imported = Database(fresh)
    with imported.connect() as connection:
        assert connection.execute(
            "SELECT id FROM campaigns WHERE id = 'c1'"
        ).fetchone()["id"] == "c1"
