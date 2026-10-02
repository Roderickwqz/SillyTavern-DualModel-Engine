"""Transaction-local repositories mapping SQLite rows to domain models."""

import json
from typing import Any

from ..domain.errors import NotFoundError
from ..domain.models import Campaign, CampaignRules, RulesMode


def dump_json(value: Any) -> str:
    """Stable, compact JSON used for every persisted document."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class CampaignRepository:
    """Reads campaign rows and performs compare-and-swap version bumps."""

    def get(self, connection, campaign_id: str) -> Campaign | None:
        row = connection.execute(
            "SELECT id, name, state_version, rules_mode, rules_enabled,"
            " rules_version, custom_preset_id FROM campaigns WHERE id = ?",
            (campaign_id,),
        ).fetchone()
        return self._to_campaign(row) if row is not None else None

    def require(self, connection, campaign_id: str) -> Campaign:
        campaign = self.get(connection, campaign_id)
        if campaign is None:
            raise NotFoundError(f"campaign {campaign_id} not found")
        return campaign

    def cas_bump(
        self,
        connection,
        campaign_id: str,
        expected_version: int,
        next_version: int,
        now: str,
    ) -> bool:
        cursor = connection.execute(
            "UPDATE campaigns SET state_version = ?, updated_at = ?"
            " WHERE id = ? AND state_version = ?",
            (next_version, now, campaign_id, expected_version),
        )
        return cursor.rowcount == 1

    @staticmethod
    def _to_campaign(row) -> Campaign:
        return Campaign(
            id=row["id"],
            name=row["name"],
            state_version=row["state_version"],
            rules=CampaignRules(
                mode=RulesMode(row["rules_mode"]),
                enabled=bool(row["rules_enabled"]),
                version=row["rules_version"],
                custom_preset_id=row["custom_preset_id"],
            ),
        )


class BranchRepository:
    """Guards branch membership within a campaign."""

    def require(self, connection, campaign_id: str, branch_id: str) -> None:
        row = connection.execute(
            "SELECT id FROM branches WHERE id = ? AND campaign_id = ?",
            (branch_id, campaign_id),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"branch {branch_id} not found in campaign {campaign_id}")


class AuditRepository:
    """Persists one audit event per mutation."""

    def insert(self, connection, event: dict[str, Any]) -> None:
        connection.execute(
            "INSERT INTO audit_events"
            " (id, campaign_id, branch_id, state_version, event_type, source,"
            " payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event["event_id"],
                event["campaign_id"],
                event["branch_id"],
                event["state_version"],
                event["event_type"],
                event["source"],
                dump_json(event["payload"]),
                event["created_at"],
            ),
        )


class OutboxRepository:
    """Stages JSONL-exportable copies of audit events."""

    def insert(self, connection, event_id: str, event: dict[str, Any]) -> None:
        connection.execute(
            "INSERT INTO jsonl_outbox(event_id, payload_json) VALUES (?, ?)",
            (event_id, dump_json(event)),
        )


class SnapshotRepository:
    """Persists the full campaign snapshot captured at each state version."""

    def insert(
        self,
        connection,
        campaign_id: str,
        branch_id: str,
        state_version: int,
        snapshot: dict[str, Any],
        now: str,
    ) -> None:
        connection.execute(
            "INSERT INTO state_snapshots"
            " (campaign_id, branch_id, state_version, snapshot_json, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (campaign_id, branch_id, state_version, dump_json(snapshot), now),
        )
