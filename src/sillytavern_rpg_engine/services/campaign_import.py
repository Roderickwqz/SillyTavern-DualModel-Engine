"""Inverse of CampaignExporter: restore a campaign from an export file."""

from datetime import datetime, timezone
import json
import sqlite3
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from ..domain.errors import ValidationError
from ..persistence.database import Database
from ..persistence.repositories import (
    AuditRepository,
    OutboxRepository,
    dump_json,
)
from .campaign_export import CampaignExporter
from .entities import normalize_key


class CampaignImporter:
    """Restore one campaign from an export-schema-v2 payload in a single
    transaction, appending a recovery_import audit event."""

    def __init__(
        self,
        database: Database,
        id_factory: Callable[[], str] = lambda: uuid4().hex,
        clock: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(),
    ):
        self.database = database
        self.id_factory = id_factory
        self.clock = clock

    def import_file(
        self,
        path: Path,
        *,
        new_campaign_id: str | None = None,
        replace: bool = False,
    ) -> str:
        """Validate the export file and restore its campaign, returning the
        campaign id; refuse an existing id unless ``replace`` is set."""
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        verify = CampaignExporter(self.database).verify_export(path)
        if not verify["ok"]:
            raise ValidationError("; ".join(verify["errors"]))
        if payload.get("export_schema_version") != 2:
            raise ValidationError("unsupported export_schema_version")
        campaign_id = new_campaign_id or payload["campaign"]["id"]
        with self.database.transaction() as connection:
            exists = connection.execute(
                "SELECT 1 FROM campaigns WHERE id = ?", (campaign_id,),
            ).fetchone()
            if exists and not replace:
                raise ValidationError(f"campaign {campaign_id!r} already exists")
            if exists and replace:
                self._delete_campaign(connection, campaign_id)
            self._insert_campaign_tree(connection, campaign_id, payload)
            self._insert_recovery_audit(connection, campaign_id, payload)
        return campaign_id

    def _delete_campaign(
        self, connection: sqlite3.Connection, campaign_id: str
    ) -> None:
        """Delete one campaign, relying on ON DELETE CASCADE; FTS rows have
        no foreign key, so they are removed first. Campaigns with dice rolls
        cannot be replaced: dice_rolls is append-only by trigger."""
        connection.execute(
            "DELETE FROM memory_events_fts WHERE rowid IN (SELECT rowid FROM"
            " memory_events WHERE campaign_id = ?)",
            (campaign_id,),
        )
        connection.execute(
            "DELETE FROM campaigns WHERE id = ?", (campaign_id,)
        )

    def _insert_campaign_tree(
        self, connection: sqlite3.Connection, campaign_id: str, payload: dict[str, Any]
    ) -> None:
        """Insert every exported section in FK-safe order."""
        self._insert_campaign(connection, campaign_id, payload)
        self._insert_branches(connection, campaign_id, payload["branches"])
        self._insert_entities(connection, campaign_id, payload)
        self._insert_attribute_state(connection, campaign_id, payload)
        self._insert_facts(connection, campaign_id, payload["facts"])
        self._insert_relationships(connection, campaign_id, payload)
        self._insert_memory(connection, campaign_id, payload)
        self._insert_traits_and_arcs(connection, campaign_id, payload)
        self._insert_dice(connection, campaign_id, payload["dice_rolls"])
        self._insert_conditions(connection, campaign_id, payload)
        self._insert_combat(connection, campaign_id, payload["combat_encounters"])
        self._insert_snapshots(connection, campaign_id, payload)
        self._insert_audit_trail(connection, campaign_id, payload)
        self._insert_turns(connection, campaign_id, payload["turns"])
        self._insert_branch_heads(connection, campaign_id, payload)

    def _insert_campaign(
        self, connection: sqlite3.Connection, campaign_id: str, payload: dict[str, Any]
    ) -> None:
        """Insert the campaigns row from the exported campaign section."""
        campaign = payload["campaign"]
        rules = campaign["rules"]
        now = self.clock()
        connection.execute(
            "INSERT INTO campaigns(id, name, state_version, rules_mode,"
            " rules_enabled, rules_version, custom_preset_id, created_at,"
            " updated_at, last_active_branch_id) VALUES (?, ?, ?, ?, ?, ?, ?,"
            " ?, ?, 'main')",
            (
                campaign_id, campaign["name"], campaign["state_version"],
                rules["mode"], int(rules["enabled"]), rules["version"],
                rules["custom_preset_id"], now, now,
            ),
        )

    def _insert_branches(
        self, connection: sqlite3.Connection, campaign_id: str, rows: list[dict[str, Any]]
    ) -> None:
        """Insert branch rows parent-first (export orders by id, which sorts
        children before 'main')."""
        inserted: set[str] = set()
        remaining = list(rows)
        while remaining:
            progress = False
            for row in list(remaining):
                parent = row["parent_branch_id"]
                if parent is not None and parent not in inserted:
                    continue
                connection.execute(
                    "INSERT INTO branches(id, campaign_id, parent_branch_id,"
                    " status, created_at) VALUES (?, ?, ?, ?, ?)",
                    (row["id"], campaign_id, parent, row["status"], row["created_at"]),
                )
                inserted.add(row["id"])
                remaining.remove(row)
                progress = True
            if not progress:
                raise ValidationError("branches reference a missing parent")

    def _insert_entities(
        self, connection: sqlite3.Connection, campaign_id: str, payload: dict[str, Any]
    ) -> None:
        """Insert entity rows, recomputing normalized_name like entity creation."""
        for entity in payload["entities"]:
            connection.execute(
                "INSERT INTO entities(id, campaign_id, kind, name,"
                " normalized_name, age_status, created_state_version)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    entity["id"], campaign_id, entity["kind"], entity["name"],
                    normalize_key(entity["name"]), entity["age_status"],
                    payload["campaign"]["state_version"],
                ),
            )

    def _insert_attribute_state(
        self, connection: sqlite3.Connection, campaign_id: str, payload: dict[str, Any]
    ) -> None:
        """Insert attribute definitions, aliases, and values."""
        for row in payload["attribute_definitions"]:
            connection.execute(
                "INSERT INTO attribute_definitions(campaign_id, key, label,"
                " category, value_type, display, audiences_json, minimum,"
                " maximum, enum_values_json, unit, created_state_version)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    campaign_id, row["key"], row["label"], row["category"],
                    row["value_type"], row["display"],
                    dump_json(row["audiences"]), row["minimum"], row["maximum"],
                    dump_json(row["enum_values"]), row["unit"],
                    row["created_state_version"],
                ),
            )
        for row in payload["attribute_aliases"]:
            connection.execute(
                "INSERT INTO attribute_aliases(campaign_id, attribute_key,"
                " alias, normalized_alias) VALUES (?, ?, ?, ?)",
                (campaign_id, row["attribute_key"], row["alias"],
                 row["normalized_alias"]),
            )
        for row in payload["attribute_values"]:
            connection.execute(
                "INSERT INTO attribute_values(campaign_id, entity_id,"
                " attribute_key, value_json, state_version, updated_turn_id)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    campaign_id, row["entity_id"], row["attribute_key"],
                    dump_json(row["value"]), row["state_version"],
                    row["updated_turn_id"],
                ),
            )

    def _insert_facts(
        self, connection: sqlite3.Connection, campaign_id: str, rows: list[dict[str, Any]]
    ) -> None:
        """Insert facts, deferring rows whose superseded_by is not yet in."""
        inserted: set[str] = set()
        remaining = list(rows)
        while remaining:
            progress = False
            for row in list(remaining):
                if row["superseded_by"] is not None and row["superseded_by"] not in inserted:
                    continue
                connection.execute(
                    "INSERT INTO facts(id, campaign_id, branch_id, entity_id,"
                    " fact_type, fact_key, content, importance, audiences_json,"
                    " valid_from, valid_until, superseded_by, turn_id, source,"
                    " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
                    " ?, ?, ?)",
                    (
                        row["id"], campaign_id, row["branch_id"],
                        row["entity_id"], row["fact_type"], row["fact_key"],
                        row["content"], row["importance"],
                        dump_json(row["audiences"]), row["valid_from"],
                        row["valid_until"], row["superseded_by"], row["turn_id"],
                        row["source"], row["created_at"],
                    ),
                )
                inserted.add(row["id"])
                remaining.remove(row)
                progress = True
            if not progress:
                raise ValidationError("facts reference a missing superseded_by")

    def _insert_relationships(
        self, connection: sqlite3.Connection, campaign_id: str, payload: dict[str, Any]
    ) -> None:
        """Insert relationship rows from the exported section."""
        for row in payload["relationships"]:
            connection.execute(
                "INSERT INTO relationships(campaign_id, from_entity_id,"
                " to_entity_id, dimension, value_json, audiences_json,"
                " state_version, updated_turn_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    campaign_id, row["from_entity_id"], row["to_entity_id"],
                    row["dimension"], dump_json(row["value"]),
                    dump_json(row["audiences"]), row["state_version"],
                    row["updated_turn_id"],
                ),
            )

    def _insert_memory(
        self, connection: sqlite3.Connection, campaign_id: str, payload: dict[str, Any]
    ) -> None:
        """Insert memory events (with FTS parity), participants, and summaries."""
        for row in payload["memory_events"]:
            connection.execute(
                "INSERT INTO memory_events(id, campaign_id, branch_id, turn_id,"
                " event_type, content, importance, audiences_json,"
                " location_entity_id, source, state_version, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["id"], campaign_id, row["branch_id"], row["turn_id"],
                    row["event_type"], row["content"], row["importance"],
                    dump_json(row["audiences"]), row["location_entity_id"],
                    row["source"], row["state_version"], row["created_at"],
                ),
            )
            connection.execute(
                "INSERT INTO memory_events_fts(rowid, content)"
                " SELECT rowid, content FROM memory_events WHERE id = ?",
                (row["id"],),
            )
        for row in payload["memory_event_participants"]:
            connection.execute(
                "INSERT INTO memory_event_participants(campaign_id, event_id,"
                " entity_id) VALUES (?, ?, ?)",
                (campaign_id, row["event_id"], row["entity_id"]),
            )
        for row in payload["memory_summaries"]:
            connection.execute(
                "INSERT INTO memory_summaries(campaign_id, branch_id, scope,"
                " scope_key, content, audiences_json, source_event_ids_json,"
                " state_version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    campaign_id, row["branch_id"], row["scope"],
                    row["scope_key"], row["content"],
                    dump_json(row["audiences"]),
                    dump_json(row["source_event_ids"]), row["state_version"],
                    row["created_at"],
                ),
            )

    def _insert_traits_and_arcs(
        self, connection: sqlite3.Connection, campaign_id: str, payload: dict[str, Any]
    ) -> None:
        """Insert trait events and development arcs."""
        for row in payload["trait_events"]:
            connection.execute(
                "INSERT INTO trait_events(id, campaign_id, branch_id, entity_id,"
                " trait_key, tier, before_value, delta, after_value, cause,"
                " turn_id, source, state_version, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["id"], campaign_id, row["branch_id"], row["entity_id"],
                    row["trait_key"], row["tier"], row["before_value"],
                    row["delta"], row["after_value"], row["cause"], row["turn_id"],
                    row["source"], row["state_version"], row["created_at"],
                ),
            )
        for row in payload["development_arcs"]:
            connection.execute(
                "INSERT INTO development_arcs(id, campaign_id, branch_id,"
                " entity_id, dimension, label, summary, source_event_ids_json,"
                " start_turn_id, end_turn_id, opened_state_version,"
                " closed_state_version, created_at) VALUES (?, ?, ?, ?, ?, ?,"
                " ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["id"], campaign_id, row["branch_id"], row["entity_id"],
                    row["dimension"], row["label"], row["summary"],
                    dump_json(row["source_event_ids"]), row["start_turn_id"],
                    row["end_turn_id"], row["opened_state_version"],
                    row["closed_state_version"], row["created_at"],
                ),
            )

    def _insert_dice(
        self, connection: sqlite3.Connection, campaign_id: str, rows: list[dict[str, Any]]
    ) -> None:
        """Insert dice roll rows from the exported section."""
        for row in rows:
            connection.execute(
                "INSERT INTO dice_rolls(id, campaign_id, branch_id, turn_id,"
                " roller_entity_id, purpose, formula, faces_json, modifier,"
                " total, dc, success, critical, rules_version, state_version,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
                " ?, ?, ?)",
                (
                    row["id"], campaign_id, row["branch_id"], row["turn_id"],
                    row["roller_entity_id"], row["purpose"], row["formula"],
                    dump_json(row["faces"]), row["modifier"], row["total"],
                    row["dc"], row["success"], int(row["critical"]),
                    row["rules_version"], row["state_version"], row["created_at"],
                ),
            )

    def _insert_conditions(
        self, connection: sqlite3.Connection, campaign_id: str, payload: dict[str, Any]
    ) -> None:
        """Insert entity condition rows from the exported section."""
        for row in payload["entity_conditions"]:
            connection.execute(
                "INSERT INTO entity_conditions(campaign_id, entity_id,"
                " condition, level, source, applied_state_version)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    campaign_id, row["entity_id"], row["condition"],
                    row["level"], row["source"], row["applied_state_version"],
                ),
            )

    def _insert_combat(
        self, connection: sqlite3.Connection, campaign_id: str, rows: list[dict[str, Any]]
    ) -> None:
        """Insert combat encounters and their combatants."""
        for row in rows:
            connection.execute(
                "INSERT INTO combat_encounters(id, campaign_id, branch_id,"
                " status, round_number, active_index, created_state_version,"
                " ended_state_version, created_at) VALUES (?, ?, ?, ?, ?, ?,"
                " ?, ?, ?)",
                (
                    row["id"], campaign_id, row["branch_id"], row["status"],
                    row["round_number"], row["active_index"],
                    row["created_state_version"], row["ended_state_version"],
                    row["created_at"],
                ),
            )
            for combatant in row["combatants"]:
                connection.execute(
                    "INSERT INTO combatants(id, encounter_id, entity_id,"
                    " initiative) VALUES (?, ?, ?, ?)",
                    (
                        f"{row['id']}:{combatant['entity_id']}", row["id"],
                        combatant["entity_id"], combatant["initiative"],
                    ),
                )

    def _insert_snapshots(
        self, connection: sqlite3.Connection, campaign_id: str, payload: dict[str, Any]
    ) -> None:
        """Insert the latest state snapshot per branch."""
        for row in payload["latest_snapshots"]:
            connection.execute(
                "INSERT INTO state_snapshots(campaign_id, branch_id,"
                " state_version, snapshot_json, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    campaign_id, row["branch_id"], row["state_version"],
                    dump_json(row["snapshot"]), row["created_at"],
                ),
            )

    def _insert_audit_trail(
        self, connection: sqlite3.Connection, campaign_id: str, payload: dict[str, Any]
    ) -> None:
        """Re-insert pending proposals and audit events with original ids."""
        for row in payload["pending_proposals"]:
            connection.execute(
                "INSERT INTO pending_proposals(id, campaign_id, branch_id,"
                " base_state_version, operation_json, reason, status,"
                " created_at, resolved_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["id"], campaign_id, row["branch_id"],
                    row["base_state_version"], dump_json(row["operation"]),
                    row["reason"], row["status"], row["created_at"],
                    row["resolved_at"],
                ),
            )
        for row in payload["audit_events"]:
            connection.execute(
                "INSERT INTO audit_events(id, campaign_id, branch_id,"
                " state_version, event_type, source, payload_json, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["id"], campaign_id, row["branch_id"],
                    row["state_version"], row["event_type"], row["source"],
                    dump_json(row["payload"]), row["created_at"],
                ),
            )

    def _insert_turns(
        self, connection: sqlite3.Connection, campaign_id: str, rows: list[dict[str, Any]]
    ) -> None:
        """Insert turn rows; export order keeps parents before children."""
        for row in rows:
            connection.execute(
                "INSERT INTO turns(id, campaign_id, branch_id, parent_turn_id,"
                " intent, player_text, response_text, history_hash,"
                " removed_instructions_json, state_before_version,"
                " state_after_version, created_at, lineage_hash_before,"
                " lineage_hash_after, response_hash, status)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["id"], campaign_id, row["branch_id"], row["parent_turn_id"],
                    row["intent"], row["player_text"], row["response_text"],
                    row["history_hash"], dump_json(row["removed_instructions"]),
                    row["state_before_version"], row["state_after_version"],
                    row["created_at"], row["lineage_hash_before"],
                    row["lineage_hash_after"], row["response_hash"], row["status"],
                ),
            )

    def _insert_branch_heads(
        self, connection: sqlite3.Connection, campaign_id: str, payload: dict[str, Any]
    ) -> None:
        """Insert branch head rows after their referenced turns."""
        for row in payload["branch_heads"]:
            connection.execute(
                "INSERT INTO branch_heads(campaign_id, branch_id, state_version,"
                " latest_turn_id, updated_at) VALUES (?, ?, ?, ?, ?)",
                (
                    campaign_id, row["branch_id"], row["state_version"],
                    row["latest_turn_id"], row["updated_at"],
                ),
            )

    def _insert_recovery_audit(
        self, connection: sqlite3.Connection, campaign_id: str, payload: dict[str, Any]
    ) -> None:
        """Append a recovery_import audit event with export metadata, staged
        for JSONL flush like any other event."""
        event: dict[str, Any] = {
            "event_id": self.id_factory(),
            "campaign_id": campaign_id,
            "branch_id": "main",
            "state_version": payload["campaign"]["state_version"],
            "event_type": "recovery_import",
            "source": "import",
            "payload": {
                "exported_at": payload["exported_at"],
                "export_schema_version": payload["export_schema_version"],
            },
            "created_at": self.clock(),
        }
        AuditRepository().insert(connection, event)
        OutboxRepository().insert(connection, event["event_id"], event)
