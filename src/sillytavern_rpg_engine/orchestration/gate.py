"""Authorization gate: explicit commands apply, inferences become proposals."""

from dataclasses import dataclass
import re
from typing import Any

from ..domain.errors import DomainError
from ..persistence.database import Database
from ..persistence.repositories import CampaignRepository
from ..services.mutations import MutationEngine, MutationRequest
from ..services.proposals import OperationCodec, ProposalService
from .extraction import ExtractedOperation
from .intent import Intent

_PROPOSAL_COMMAND = re.compile(
    r"^(?:确认提案|approve)\s+(\S+)$|^(?:拒绝提案|reject)\s+(\S+)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GateResult:
    applied: tuple[dict[str, Any], ...]
    pending: tuple[dict[str, Any], ...]
    dropped: tuple[dict[str, Any], ...]
    message: str


def parse_proposal_command(text: str) -> tuple[str, str] | None:
    """Deterministic proposal confirmation: (\"approve\"|\"reject\", id)."""
    match = _PROPOSAL_COMMAND.match(text.strip())
    if not match:
        return None
    if match.group(1):
        return ("approve", match.group(1))
    return ("reject", match.group(2))


def _current_version(database: Database, campaign_id: str) -> int:
    with database.connect() as connection:
        return CampaignRepository().require(connection, campaign_id).state_version


def _summarize(applied, pending, dropped) -> str:
    parts = []
    if applied:
        parts.append(f"已应用 {len(applied)} 项变更。")
    if pending:
        parts.append(f"记录 {len(pending)} 项待确认提案。")
    if dropped:
        reasons = "; ".join(d["error"] for d in dropped)
        parts.append(f"拒绝 {len(dropped)} 项变更:{reasons}")
    return "".join(parts)


def run_proposal_command(
    database: Database,
    proposal_service: ProposalService,
    command: str,
    proposal_id: str,
) -> GateResult:
    """Execute one deterministic proposal approve/reject command."""
    try:
        if command == "reject":
            proposal_service.reject(proposal_id)
            return GateResult((), (), (), f"已拒绝提案 {proposal_id}。")
        proposal_service.approve(
            proposal_id, expected_version=_current_version(
                database, proposal_service.get(proposal_id).campaign_id
            ),
        )
        return GateResult(
            ({"kind": "approve_proposal", "proposal_id": proposal_id},), (), (),
            f"提案 {proposal_id} 已确认并应用。",
        )
    except DomainError as exc:
        return GateResult((), (), (), str(exc))


def run_gate(
    database: Database,
    mutation_engine: MutationEngine,
    proposal_service: ProposalService,
    campaign_id: str,
    branch_id: str,
    intent: Intent,
    operations: tuple[ExtractedOperation, ...],
) -> GateResult:
    """Apply explicit operations; park inferred ones as Pending proposals."""
    applied: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for extracted in operations:
        try:
            operation = OperationCodec.decode(extracted.payload)
        except DomainError as exc:
            dropped.append({"operation": extracted.payload, "error": str(exc)})
            continue
        if intent is Intent.ACTION:
            proposal = proposal_service.create(
                campaign_id, branch_id, extracted.payload,
                extracted.reason or "剧情推断",
            )
            pending.append({
                "proposal_id": proposal.id,
                "kind": extracted.payload["kind"],
                "operation": extracted.payload,
                "reason": extracted.reason,
            })
            continue
        try:
            mutation_engine.apply(MutationRequest(
                campaign_id=campaign_id,
                branch_id=branch_id,
                expected_version=_current_version(database, campaign_id),
                source="user-command",
                event_type="explicit-state-change",
                operation=operation,
            ))
            applied.append({
                "kind": extracted.payload["kind"],
                "operation": extracted.payload,
            })
        except DomainError as exc:
            dropped.append({"operation": extracted.payload, "error": str(exc)})
    return GateResult(
        tuple(applied), tuple(pending), tuple(dropped),
        _summarize(applied, pending, dropped),
    )
