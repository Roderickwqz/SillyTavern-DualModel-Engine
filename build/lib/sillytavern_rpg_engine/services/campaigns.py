"""Campaign bootstrap and optional rules lifecycle."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import sqlite3
from typing import Any, Callable
from uuid import uuid4

from ..domain.errors import ConfirmationRequiredError, ValidationError
from ..domain.models import Campaign, CampaignRules
from ..domain.operations import MutationContext
from ..domain.validation import validate_rules
from ..persistence.database import Database
from ..persistence.repositories import (
    AuditRepository,
    CampaignRepository,
    OutboxRepository,
    SnapshotRepository,
)
from .mutations import MutationEngine, MutationRequest, MutationResult
from .snapshots import SnapshotBuilder


@dataclass(frozen=True)
class SetRulesOperation:
    rules: CampaignRules

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        validate_rules(self.rules)
        connection.execute(
            """
            UPDATE campaigns
            SET rules_mode = ?, rules_enabled = ?, rules_version = ?, custom_preset_id = ?
            WHERE id = ?
            """,
            (
                self.rules.mode.value,
                int(self.rules.enabled),
                self.rules.version,
                self.rules.custom_preset_id,
                context.campaign.id,
            ),
        )
        return {"rules": asdict(self.rules)}


@dataclass(frozen=True)
class DisableRulesOperation:
    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        connection.execute(
            "UPDATE campaigns SET rules_enabled = 0 WHERE id = ?",
            (context.campaign.id,),
        )
        rules = context.campaign.rules
        return {
            "rules": {
                "mode": rules.mode.value,
                "enabled": False,
                "version": rules.version,
                "custom_preset_id": rules.custom_preset_id,
            }
        }


class CampaignService:
    """Creates campaigns and manages their optional rules configuration."""

    def __init__(
        self,
        database: Database,
        id_factory: Callable[[], str] = lambda: uuid4().hex,
        clock: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(),
    ):
        self.database = database
        self.id_factory = id_factory
        self.clock = clock
        self.mutation_engine = MutationEngine(
            database, id_factory=id_factory, clock=clock
        )
        self.campaign_repository = CampaignRepository()
        self.audit_repository = AuditRepository()
        self.outbox_repository = OutboxRepository()
        self.snapshot_repository = SnapshotRepository()
        self.snapshot_builder = SnapshotBuilder()

    def create_campaign(self, campaign_id: str, name: str) -> Campaign:
        """Bootstrap a campaign: version 0, branch main, audit event, outbox
        row, and snapshot v0, committed together or not at all."""
        now = self.clock()
        event_id = self.id_factory()
        with self.database.transaction() as connection:
            try:
                connection.execute(
                    "INSERT INTO campaigns(id, name, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?)",
                    (campaign_id, name, now, now),
                )
            except sqlite3.IntegrityError as exc:
                raise ValidationError("campaign already exists") from exc
            connection.execute(
                "INSERT INTO branches(id, campaign_id, status, created_at)"
                " VALUES ('main', ?, 'active', ?)",
                (campaign_id, now),
            )
            event: dict[str, Any] = {
                "event_id": event_id,
                "campaign_id": campaign_id,
                "branch_id": "main",
                "state_version": 0,
                "event_type": "campaign-created",
                "source": "user-command",
                "payload": {"campaign_id": campaign_id, "name": name},
                "created_at": now,
            }
            self.audit_repository.insert(connection, event)
            self.outbox_repository.insert(connection, event_id, event)
            snapshot = self.snapshot_builder.build(connection, campaign_id, "main")
            self.snapshot_repository.insert(
                connection, campaign_id, "main", 0, snapshot, now
            )
            return self.campaign_repository.require(connection, campaign_id)

    def get_campaign(self, campaign_id: str) -> Campaign:
        with self.database.connect() as connection:
            return self.campaign_repository.require(connection, campaign_id)

    def set_rules(
        self, campaign_id: str, expected_version: int, rules: CampaignRules
    ) -> MutationResult:
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id,
                branch_id="main",
                expected_version=expected_version,
                source="user-command",
                event_type="rules-updated",
                operation=SetRulesOperation(rules),
            )
        )

    def disable_rules(
        self,
        campaign_id: str,
        expected_version: int,
        active_combat: bool = False,
        confirmed: bool = False,
    ) -> MutationResult:
        if active_combat and not confirmed:
            raise ConfirmationRequiredError(
                "cannot disable rules during active combat without confirmation"
            )
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id,
                branch_id="main",
                expected_version=expected_version,
                source="user-command",
                event_type="rules-disabled",
                operation=DisableRulesOperation(),
            )
        )
