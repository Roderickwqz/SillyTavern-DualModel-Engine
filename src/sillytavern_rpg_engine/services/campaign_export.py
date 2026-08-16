"""Deterministic, credential-free snapshot of a whole campaign."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..persistence.database import Database
from ..persistence.repositories import dump_json
from .snapshots import SnapshotBuilder

_EXPECTED_ROOT_KEYS = frozenset(
    {
        "export_schema_version",
        "exported_at",
        "campaign",
        "branches",
        "entities",
        "pending_proposals",
        "audit_events",
        "latest_snapshots",
        "dice_rolls",
        "entity_conditions",
        "combat_encounters",
        "attribute_definitions",
        "attribute_values",
        "attribute_aliases",
        "entity_aliases",
        "facts",
        "relationships",
        "memory_events",
        "memory_event_participants",
        "memory_summaries",
        "trait_events",
        "development_arcs",
        "turns",
        "branch_heads",
    }
)
_FORBIDDEN_KEYS = frozenset({"api_key", "authorization", "token", "secret"})


class CampaignExporter:
    """Write one campaign's full state to a JSON file, atomically replacing
    the destination only after the temporary sibling file is fsynced."""

    def __init__(
        self,
        database: Database,
        clock: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(),
    ):
        self.database = database
        self.clock = clock
        self.snapshot_builder = SnapshotBuilder()

    def export(self, campaign_id: str, path: Path) -> Path:
        """Serialize ``campaign_id`` deterministically and atomically replace
        ``path`` with the result; returns ``path``."""
        output = Path(path)
        parent = output.parent
        if not parent.is_dir():
            raise FileNotFoundError(
                f"output directory does not exist: {parent}"
            )
        payload = self.build_payload(campaign_id)
        temporary = output.with_name(output.name + ".tmp")
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(dump_json(payload))
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(output)
        return output

    def verify_export(self, path: Path) -> dict[str, Any]:
        """Validate an exported file; return a summary dict with ``ok`` and
        a list of ``errors`` when any check fails."""
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except ValueError as exc:
            return {
                "ok": False,
                "campaign_id": None,
                "state_versions": [],
                "errors": [f"invalid JSON: {exc}"],
            }
        errors: list[str] = []
        missing = _EXPECTED_ROOT_KEYS - set(payload)
        if missing:
            errors.append(f"missing root keys: {sorted(missing)}")
        campaign = payload.get("campaign")
        campaign_id = (
            campaign.get("id") if isinstance(campaign, dict) else None
        )
        if not isinstance(campaign_id, str) or not campaign_id:
            errors.append("campaign.id must be a non-empty string")
        else:
            self._check_campaign_matches(campaign_id, payload, errors)
        self._check_credential_keys(payload, errors)
        versions = self._state_versions(payload)
        for previous, current in zip(versions, versions[1:]):
            if current != previous + 1:
                errors.append(
                    f"state versions not strictly increasing: {versions}"
                )
                break
        return {
            "ok": not errors,
            "campaign_id": campaign_id,
            "state_versions": versions,
            "errors": errors,
        }

    def build_payload(self, campaign_id: str) -> dict[str, Any]:
        now = self.clock()
        with self.database.connect() as connection:
            snapshot = self.snapshot_builder.build(
                connection, campaign_id, "main"
            )
            branches = self._branches(connection, campaign_id)
            proposals = self._proposals(connection, campaign_id)
            audit_events = self._audit_events(connection, campaign_id)
            snapshots = self._latest_snapshots(connection, campaign_id)
            dice_rolls = self._dice_rolls(connection, campaign_id)
            conditions = self._entity_conditions(connection, campaign_id)
            encounters = self._combat_encounters(connection, campaign_id)
            definitions = self._attribute_definitions(connection, campaign_id)
            values = self._attribute_values(connection, campaign_id)
            attribute_aliases = self._attribute_aliases(
                connection, campaign_id
            )
            aliases = self._entity_aliases(connection, campaign_id)
            facts = self._facts(connection, campaign_id)
            relationships = self._relationships(connection, campaign_id)
            memory_events = self._memory_events(connection, campaign_id)
            participants = self._memory_event_participants(
                connection, campaign_id
            )
            summaries = self._memory_summaries(connection, campaign_id)
            trait_events = self._trait_events(connection, campaign_id)
            arcs = self._development_arcs(connection, campaign_id)
            turns = self._turns(connection, campaign_id)
            heads = self._branch_heads(connection, campaign_id)
        return {
            "export_schema_version": 2,
            "exported_at": now,
            "campaign": snapshot["campaign"],
            "branches": branches,
            "entities": snapshot["entities"],
            "pending_proposals": proposals,
            "audit_events": audit_events,
            "latest_snapshots": snapshots,
            "dice_rolls": dice_rolls,
            "entity_conditions": conditions,
            "combat_encounters": encounters,
            "attribute_definitions": definitions,
            "attribute_values": values,
            "attribute_aliases": attribute_aliases,
            "entity_aliases": aliases,
            "facts": facts,
            "relationships": relationships,
            "memory_events": memory_events,
            "memory_event_participants": participants,
            "memory_summaries": summaries,
            "trait_events": trait_events,
            "development_arcs": arcs,
            "turns": turns,
            "branch_heads": heads,
        }

    _build_payload = build_payload

    @staticmethod
    def _branches(connection, campaign_id: str) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT id, parent_branch_id, status, created_at FROM branches"
            " WHERE campaign_id = ? ORDER BY id",
            (campaign_id,),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "parent_branch_id": row["parent_branch_id"],
                "status": row["status"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    @staticmethod
    def _proposals(connection, campaign_id: str) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT id, campaign_id, branch_id, base_state_version,"
            " operation_json, reason, status, created_at, resolved_at"
            " FROM pending_proposals WHERE campaign_id = ? ORDER BY id",
            (campaign_id,),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "campaign_id": row["campaign_id"],
                "branch_id": row["branch_id"],
                "base_state_version": row["base_state_version"],
                "operation": json.loads(row["operation_json"]),
                "reason": row["reason"],
                "status": row["status"],
                "created_at": row["created_at"],
                "resolved_at": row["resolved_at"],
            }
            for row in rows
        ]

    @staticmethod
    def _audit_events(connection, campaign_id: str) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT id, campaign_id, branch_id, state_version, event_type,"
            " source, payload_json, created_at FROM audit_events"
            " WHERE campaign_id = ? ORDER BY state_version, id",
            (campaign_id,),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "campaign_id": row["campaign_id"],
                "branch_id": row["branch_id"],
                "state_version": row["state_version"],
                "event_type": row["event_type"],
                "source": row["source"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    @staticmethod
    def _latest_snapshots(
        connection, campaign_id: str
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT branch_id, state_version, snapshot_json, created_at"
            " FROM state_snapshots s"
            " WHERE campaign_id = ? AND state_version ="
            " (SELECT MAX(state_version) FROM state_snapshots t"
            "  WHERE t.campaign_id = s.campaign_id"
            "  AND t.branch_id = s.branch_id)"
            " ORDER BY branch_id",
            (campaign_id,),
        ).fetchall()
        return [
            {
                "branch_id": row["branch_id"],
                "state_version": row["state_version"],
                "snapshot": json.loads(row["snapshot_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    @staticmethod
    def _dice_rolls(connection, campaign_id) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT id, branch_id, turn_id, roller_entity_id, purpose, formula,"
            " faces_json, modifier, total, dc, success, critical, rules_version,"
            " state_version, created_at FROM dice_rolls"
            " WHERE campaign_id = ? ORDER BY state_version, id",
            (campaign_id,),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "branch_id": row["branch_id"],
                "turn_id": row["turn_id"],
                "roller_entity_id": row["roller_entity_id"],
                "purpose": row["purpose"],
                "formula": row["formula"],
                "faces": json.loads(row["faces_json"]),
                "modifier": row["modifier"],
                "total": row["total"],
                "dc": row["dc"],
                "success": None if row["success"] is None else bool(row["success"]),
                "critical": bool(row["critical"]),
                "rules_version": row["rules_version"],
                "state_version": row["state_version"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    @staticmethod
    def _entity_conditions(connection, campaign_id) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT entity_id, condition, level, source, applied_state_version"
            " FROM entity_conditions WHERE campaign_id = ?"
            " ORDER BY entity_id, condition",
            (campaign_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _combat_encounters(connection, campaign_id) -> list[dict[str, Any]]:
        encounters = connection.execute(
            "SELECT id, branch_id, status, round_number, active_index,"
            " created_state_version, ended_state_version, created_at"
            " FROM combat_encounters WHERE campaign_id = ? ORDER BY id",
            (campaign_id,),
        ).fetchall()
        result = []
        for encounter in encounters:
            combatants = connection.execute(
                "SELECT entity_id, initiative FROM combatants"
                " WHERE encounter_id = ? ORDER BY initiative DESC, entity_id",
                (encounter["id"],),
            ).fetchall()
            result.append(
                {
                    **dict(encounter),
                    "combatants": [dict(row) for row in combatants],
                }
            )
        return result

    @staticmethod
    def _attribute_definitions(
        connection, campaign_id: str
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT campaign_id, key, label, category, value_type, display,"
            " audiences_json, minimum, maximum, enum_values_json, unit,"
            " created_state_version FROM attribute_definitions"
            " WHERE campaign_id = ? ORDER BY key",
            (campaign_id,),
        ).fetchall()
        return [
            {
                **dict(row),
                "audiences": json.loads(row["audiences_json"]),
                "enum_values": json.loads(row["enum_values_json"]),
            }
            for row in rows
        ]

    @staticmethod
    def _attribute_aliases(
        connection, campaign_id: str
    ) -> list[dict[str, Any]]:
        """Export attribute alias rows with schema column names."""
        rows = connection.execute(
            "SELECT campaign_id, attribute_key, alias, normalized_alias"
            " FROM attribute_aliases WHERE campaign_id = ?"
            " ORDER BY attribute_key, alias",
            (campaign_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _attribute_values(
        connection, campaign_id: str
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT campaign_id, entity_id, attribute_key, value_json,"
            " state_version, updated_turn_id FROM attribute_values"
            " WHERE campaign_id = ? ORDER BY entity_id, attribute_key",
            (campaign_id,),
        ).fetchall()
        return [
            {
                **dict(row),
                "value": json.loads(row["value_json"]),
            }
            for row in rows
        ]

    @staticmethod
    def _entity_aliases(
        connection, campaign_id: str
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT campaign_id, entity_id, alias, normalized_alias"
            " FROM entity_aliases WHERE campaign_id = ?"
            " ORDER BY entity_id, alias",
            (campaign_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _facts(connection, campaign_id: str) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT id, campaign_id, branch_id, entity_id, fact_type,"
            " fact_key, content, importance, audiences_json, valid_from,"
            " valid_until, superseded_by, turn_id, source, created_at"
            " FROM facts WHERE campaign_id = ? ORDER BY id",
            (campaign_id,),
        ).fetchall()
        return [
            {
                **dict(row),
                "audiences": json.loads(row["audiences_json"]),
            }
            for row in rows
        ]

    @staticmethod
    def _relationships(
        connection, campaign_id: str
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT campaign_id, from_entity_id, to_entity_id, dimension,"
            " value_json, audiences_json, state_version, updated_turn_id"
            " FROM relationships WHERE campaign_id = ?"
            " ORDER BY from_entity_id, to_entity_id, dimension",
            (campaign_id,),
        ).fetchall()
        return [
            {
                **dict(row),
                "value": json.loads(row["value_json"]),
                "audiences": json.loads(row["audiences_json"]),
            }
            for row in rows
        ]

    @staticmethod
    def _memory_events(
        connection, campaign_id: str
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT id, campaign_id, branch_id, turn_id, event_type, content,"
            " importance, audiences_json, location_entity_id, source,"
            " state_version, created_at FROM memory_events"
            " WHERE campaign_id = ? ORDER BY state_version, id",
            (campaign_id,),
        ).fetchall()
        return [
            {
                **dict(row),
                "audiences": json.loads(row["audiences_json"]),
            }
            for row in rows
        ]

    @staticmethod
    def _memory_event_participants(
        connection, campaign_id: str
    ) -> list[dict[str, Any]]:
        """Export participant rows of one campaign's memory events."""
        rows = connection.execute(
            "SELECT campaign_id, event_id, entity_id"
            " FROM memory_event_participants WHERE campaign_id = ?"
            " ORDER BY event_id, entity_id",
            (campaign_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _memory_summaries(
        connection, campaign_id: str
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT campaign_id, branch_id, scope, scope_key, content,"
            " audiences_json, source_event_ids_json, state_version, created_at"
            " FROM memory_summaries WHERE campaign_id = ?"
            " ORDER BY branch_id, scope, scope_key",
            (campaign_id,),
        ).fetchall()
        return [
            {
                **dict(row),
                "audiences": json.loads(row["audiences_json"]),
                "source_event_ids": json.loads(row["source_event_ids_json"]),
            }
            for row in rows
        ]

    @staticmethod
    def _trait_events(
        connection, campaign_id: str
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT id, campaign_id, branch_id, entity_id, trait_key, tier,"
            " before_value, delta, after_value, cause, turn_id, source,"
            " state_version, created_at FROM trait_events"
            " WHERE campaign_id = ? ORDER BY state_version, id",
            (campaign_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _development_arcs(
        connection, campaign_id: str
    ) -> list[dict[str, Any]]:
        """Export development arcs with json columns decoded."""
        rows = connection.execute(
            "SELECT id, campaign_id, branch_id, entity_id, dimension, label,"
            " summary, source_event_ids_json, start_turn_id, end_turn_id,"
            " opened_state_version, closed_state_version, created_at"
            " FROM development_arcs WHERE campaign_id = ? ORDER BY id",
            (campaign_id,),
        ).fetchall()
        return [
            {
                **dict(row),
                "source_event_ids": json.loads(row["source_event_ids_json"]),
            }
            for row in rows
        ]

    @staticmethod
    def _turns(connection, campaign_id: str) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT id, campaign_id, branch_id, parent_turn_id, intent,"
            " player_text, response_text, history_hash,"
            " removed_instructions_json, state_before_version,"
            " state_after_version, created_at, lineage_hash_before,"
            " lineage_hash_after, response_hash, status FROM turns"
            " WHERE campaign_id = ? ORDER BY state_before_version, id",
            (campaign_id,),
        ).fetchall()
        return [
            {
                **dict(row),
                "removed_instructions": json.loads(row["removed_instructions_json"]),
            }
            for row in rows
        ]

    @staticmethod
    def _branch_heads(
        connection, campaign_id: str
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT campaign_id, branch_id, state_version, latest_turn_id,"
            " updated_at FROM branch_heads WHERE campaign_id = ?"
            " ORDER BY branch_id",
            (campaign_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _check_campaign_matches(
        campaign_id: str, payload: dict[str, Any], errors: list[str]
    ) -> None:
        """Reject sections that reference a different campaign."""
        for section in (
            "entities",
            "pending_proposals",
            "audit_events",
            "attribute_definitions",
            "attribute_values",
            "attribute_aliases",
            "entity_aliases",
            "facts",
            "relationships",
            "memory_events",
            "memory_event_participants",
            "memory_summaries",
            "trait_events",
            "development_arcs",
            "turns",
            "branch_heads",
        ):
            items = payload.get(section)
            if not isinstance(items, list):
                continue
            for item in items:
                if (
                    isinstance(item, dict)
                    and item.get("campaign_id") != campaign_id
                ):
                    errors.append(f"{section} references another campaign")
                    return

    @staticmethod
    def _state_versions(payload: dict[str, Any]) -> list[int]:
        events = payload.get("audit_events")
        if not isinstance(events, list):
            return []
        return [
            event["state_version"]
            for event in events
            if isinstance(event, dict)
            and isinstance(event.get("state_version"), int)
            and event.get("event_type") != "recovery_import"
        ]

    @staticmethod
    def _check_credential_keys(value: Any, errors: list[str]) -> None:
        """Recursively flag any key that could carry credentials."""
        if isinstance(value, dict):
            for key, child in value.items():
                if key.casefold() in _FORBIDDEN_KEYS:
                    errors.append(f"forbidden key {key!r} in export")
                CampaignExporter._check_credential_keys(child, errors)
        elif isinstance(value, list):
            for child in value:
                CampaignExporter._check_credential_keys(child, errors)
