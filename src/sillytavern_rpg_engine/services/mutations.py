"""Versioned mutation coordinator: atomic state change + audit + outbox + snapshot."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from ..domain.errors import StaleStateError
from ..domain.operations import MutationContext, MutationOperation
from ..persistence.database import Database
from ..persistence.repositories import (
    AuditRepository,
    BranchRepository,
    CampaignRepository,
    OutboxRepository,
    SnapshotRepository,
)
from .snapshots import SnapshotBuilder


@dataclass(frozen=True)
class MutationRequest:
    campaign_id: str
    branch_id: str
    expected_version: int
    source: str
    event_type: str
    operation: MutationOperation


@dataclass(frozen=True)
class MutationResult:
    campaign_id: str
    branch_id: str
    state_version: int
    event_id: str
    snapshot: dict[str, Any]


class MutationEngine:
    """Apply one mutation atomically: version bump, audit event, outbox row,
    and snapshot are committed together or rolled back together."""

    def __init__(
        self,
        database: Database,
        id_factory: Callable[[], str] = lambda: uuid4().hex,
        clock: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(),
    ):
        self.database = database
        self.id_factory = id_factory
        self.clock = clock
        self.campaign_repository = CampaignRepository()
        self.branch_repository = BranchRepository()
        self.audit_repository = AuditRepository()
        self.outbox_repository = OutboxRepository()
        self.snapshot_repository = SnapshotRepository()
        self.snapshot_builder = SnapshotBuilder()

    def apply(self, request: MutationRequest) -> MutationResult:
        with self.database.transaction() as connection:
            campaign = self.campaign_repository.require(connection, request.campaign_id)
            if campaign.state_version != request.expected_version:
                raise StaleStateError(
                    f"expected {request.expected_version}, found {campaign.state_version}"
                )
            self.branch_repository.require(
                connection, request.campaign_id, request.branch_id
            )
            next_version = campaign.state_version + 1
            context = MutationContext(campaign, request.branch_id, next_version)
            payload = request.operation.apply(connection, context)
            now = self.clock()
            event_id = self.id_factory()
            self.campaign_repository.cas_bump(
                connection,
                campaign.id,
                campaign.state_version,
                next_version,
                now,
            )
            snapshot = self.snapshot_builder.build(
                connection, campaign.id, request.branch_id
            )
            event = {
                "event_id": event_id,
                "campaign_id": campaign.id,
                "branch_id": request.branch_id,
                "state_version": next_version,
                "event_type": request.event_type,
                "source": request.source,
                "payload": payload,
                "created_at": now,
            }
            self.audit_repository.insert(connection, event)
            self.outbox_repository.insert(connection, event_id, event)
            self.snapshot_repository.insert(
                connection,
                campaign.id,
                request.branch_id,
                next_version,
                snapshot,
                now,
            )
        return MutationResult(
            campaign_id=campaign.id,
            branch_id=request.branch_id,
            state_version=next_version,
            event_id=event_id,
            snapshot=snapshot,
        )
