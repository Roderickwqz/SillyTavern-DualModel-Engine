"""Persistent inferred change proposals: strict decode, Pending lifecycle,
and atomic approval inside the authoritative mutation transaction."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Callable
from uuid import uuid4

from ..domain.errors import NotFoundError, StaleStateError, ValidationError
from ..domain.models import (
    AgeStatus,
    AttributeDefinition,
    AttributeType,
    Audience,
    CampaignRules,
    DisplayType,
    EntityKind,
    ProposalStatus,
    RulesMode,
)
from ..domain.operations import MutationContext, MutationOperation
from ..persistence.database import Database
from ..persistence.repositories import BranchRepository, CampaignRepository
from .attributes import DefineAttributeOperation, SetAttributeOperation
from .campaigns import SetRulesOperation
from .entities import CreateEntityOperation
from .mutations import MutationEngine, MutationRequest, MutationResult

ALLOWED_KINDS = frozenset({
    "create_entity",
    "define_attribute",
    "set_attribute",
    "set_rules",
})

_EXPECTED_KEYS: dict[str, frozenset[str]] = {
    "create_entity": frozenset({
        "kind", "entity_id", "entity_kind", "name", "age_status", "aliases",
    }),
    "define_attribute": frozenset({
        "kind", "campaign_id", "key", "label", "category", "value_type",
        "display", "audiences", "minimum", "maximum", "enum_values", "unit",
        "aliases",
    }),
    "set_attribute": frozenset({
        "kind", "entity_id", "attribute_key", "value", "turn_id",
    }),
    "set_rules": frozenset({
        "kind", "mode", "enabled", "version", "custom_preset_id",
    }),
}


def _enum(enum_type, value: str, field: str):
    """Reconstruct a domain enum from its string value with a stable error."""
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ValidationError(f"invalid {field} value {value!r}") from exc


def _as_tuple(payload: dict[str, Any], key: str) -> tuple:
    """Require a JSON list for list-shaped fields; a string would be split
    into characters and silently persisted."""
    value = payload[key]
    if not isinstance(value, list):
        raise ValidationError(f"{key} must be a list")
    return tuple(value)


def _as_number(payload: dict[str, Any], key: str) -> float | None:
    """Require a real number (not bool) or null for numeric fields."""
    value = payload[key]
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, (int, float))
    ):
        raise ValidationError(f"{key} must be a number or null")
    return value


class OperationCodec:
    """Decode plain-JSON operation payloads into real MutationOperations.

    Only the four Phase 1 kinds are accepted, each with an exact key set;
    unknown keys, missing keys, and unknown kinds raise ValidationError.
    Class names, import paths, callables, SQL, and pickle data are never
    accepted because nothing beyond the fixed field shapes is read.
    """

    @staticmethod
    def decode(payload: dict[str, Any]) -> MutationOperation:
        kind = payload.get("kind")
        if kind not in ALLOWED_KINDS:
            raise ValidationError(f"unsupported operation kind {kind!r}")
        keys = _EXPECTED_KEYS[kind]
        extra = set(payload) - keys
        missing = keys - set(payload)
        if extra or missing:
            raise ValidationError(
                f"invalid {kind!r} payload: extra keys {sorted(extra)},"
                f" missing keys {sorted(missing)}"
            )
        if kind == "create_entity":
            return CreateEntityOperation(
                entity_id=payload["entity_id"],
                kind=_enum(EntityKind, payload["entity_kind"], "entity_kind"),
                name=payload["name"],
                age_status=_enum(AgeStatus, payload["age_status"], "age_status"),
                aliases=_as_tuple(payload, "aliases"),
            )
        if kind == "define_attribute":
            definition = AttributeDefinition(
                campaign_id=payload["campaign_id"],
                key=payload["key"],
                label=payload["label"],
                category=payload["category"],
                value_type=_enum(AttributeType, payload["value_type"], "value_type"),
                display=_enum(DisplayType, payload["display"], "display"),
                audiences=frozenset(
                    _enum(Audience, audience, "audience")
                    for audience in _as_tuple(payload, "audiences")
                ),
                minimum=_as_number(payload, "minimum"),
                maximum=_as_number(payload, "maximum"),
                enum_values=_as_tuple(payload, "enum_values"),
                unit=payload["unit"],
            )
            return DefineAttributeOperation(
                definition, aliases=_as_tuple(payload, "aliases")
            )
        if kind == "set_attribute":
            return SetAttributeOperation(
                entity_id=payload["entity_id"],
                attribute_key=payload["attribute_key"],
                value=payload["value"],
                turn_id=payload["turn_id"],
            )
        enabled = payload["enabled"]
        if not isinstance(enabled, bool):
            raise ValidationError("enabled must be a boolean")
        rules = CampaignRules(
            mode=_enum(RulesMode, payload["mode"], "mode"),
            enabled=enabled,
            version=payload["version"],
            custom_preset_id=payload["custom_preset_id"],
        )
        return SetRulesOperation(rules)


@dataclass(frozen=True)
class Proposal:
    """A pending inferred change and its lifecycle status."""

    id: str
    campaign_id: str
    branch_id: str
    base_state_version: int
    operation: dict[str, Any]
    reason: str
    status: ProposalStatus
    created_at: str
    resolved_at: str | None = None


@dataclass(frozen=True)
class ApproveProposalOperation:
    """Approve a proposal only while it is still Pending, inside the
    authoritative mutation transaction; otherwise the whole mutation
    rolls back and the inner operation never executes."""

    proposal_id: str
    operation_json: str
    resolved_at: str

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict[str, Any]:
        cursor = connection.execute(
            "UPDATE pending_proposals SET status = 'approved', resolved_at = ?"
            " WHERE id = ? AND status = 'pending'",
            (self.resolved_at, self.proposal_id),
        )
        if cursor.rowcount == 0:
            raise ValidationError(
                f"proposal {self.proposal_id} is not pending and cannot be approved"
            )
        inner = OperationCodec.decode(json.loads(self.operation_json))
        payload = inner.apply(connection, context)
        return {"proposal_id": self.proposal_id, "operation": payload}


def _to_proposal(row) -> Proposal:
    return Proposal(
        id=row["id"],
        campaign_id=row["campaign_id"],
        branch_id=row["branch_id"],
        base_state_version=row["base_state_version"],
        operation=json.loads(row["operation_json"]),
        reason=row["reason"],
        status=ProposalStatus(row["status"]),
        created_at=row["created_at"],
        resolved_at=row["resolved_at"],
    )


class ProposalService:
    """Create, query, approve, and reject persistent change proposals."""

    def __init__(
        self,
        database: Database,
        mutation_engine: MutationEngine,
        id_factory: Callable[[], str] = lambda: uuid4().hex,
        clock: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(),
    ):
        self.database = database
        self.mutation_engine = mutation_engine
        self.id_factory = id_factory
        self.clock = clock
        self.campaign_repository = CampaignRepository()
        self.branch_repository = BranchRepository()

    def create(
        self, campaign_id: str, branch_id: str, operation: dict, reason: str
    ) -> Proposal:
        """Record a Pending proposal at the campaign's current state version
        without changing any authoritative game fact."""
        proposal_id = self.id_factory()
        now = self.clock()
        with self.database.transaction() as connection:
            campaign = self.campaign_repository.require(connection, campaign_id)
            self.branch_repository.require(connection, campaign_id, branch_id)
            connection.execute(
                "INSERT INTO pending_proposals(id, campaign_id, branch_id,"
                " base_state_version, operation_json, reason, status, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
                (
                    proposal_id,
                    campaign_id,
                    branch_id,
                    campaign.state_version,
                    json.dumps(operation, ensure_ascii=False, sort_keys=True),
                    reason,
                    now,
                ),
            )
        return Proposal(
            id=proposal_id,
            campaign_id=campaign_id,
            branch_id=branch_id,
            base_state_version=campaign.state_version,
            operation=dict(operation),
            reason=reason,
            status=ProposalStatus.PENDING,
            created_at=now,
        )

    def get(self, proposal_id: str) -> Proposal:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT id, campaign_id, branch_id, base_state_version,"
                " operation_json, reason, status, created_at, resolved_at"
                " FROM pending_proposals WHERE id = ?",
                (proposal_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"proposal {proposal_id} not found")
        return _to_proposal(row)

    def approve(self, proposal_id: str, expected_version: int) -> MutationResult:
        """Approve a proposal atomically with its authoritative mutation, or
        mark it Stale and refuse when either version check fails."""
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT id, campaign_id, branch_id, base_state_version,"
                " operation_json, reason, status, created_at, resolved_at"
                " FROM pending_proposals WHERE id = ?",
                (proposal_id,),
            ).fetchone()
            if row is None:
                raise NotFoundError(f"proposal {proposal_id} not found")
            campaign = self.campaign_repository.require(
                connection, row["campaign_id"]
            )
        proposal = _to_proposal(row)
        if (
            campaign.state_version != expected_version
            or campaign.state_version != proposal.base_state_version
        ):
            self._mark_stale(proposal_id)
            raise StaleStateError(
                f"proposal {proposal_id} is stale: campaign at"
                f" {campaign.state_version}, proposal based on"
                f" {proposal.base_state_version}"
            )
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=proposal.campaign_id,
                branch_id=proposal.branch_id,
                expected_version=campaign.state_version,
                source="user-command",
                event_type="proposal-approved",
                operation=ApproveProposalOperation(
                    proposal_id,
                    row["operation_json"],
                    resolved_at=self.clock(),
                ),
            )
        )

    def reject(self, proposal_id: str) -> Proposal:
        """Mark a Pending proposal Rejected; campaign state is never mutated."""
        now = self.clock()
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE pending_proposals SET status = 'rejected', resolved_at = ?"
                " WHERE id = ? AND status = 'pending'",
                (now, proposal_id),
            )
            if cursor.rowcount == 0:
                raise ValidationError(
                    f"proposal {proposal_id} is not pending and cannot be rejected"
                )
        return self.get(proposal_id)

    def _mark_stale(self, proposal_id: str) -> None:
        """Persist the Stale status in its own transaction so the refusal to
        apply the mutation cannot roll the status update back; only a Pending
        proposal may be marked, so an already resolved record is untouched."""
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE pending_proposals SET status = 'stale', resolved_at = ?"
                " WHERE id = ? AND status = 'pending'",
                (self.clock(), proposal_id),
            )
