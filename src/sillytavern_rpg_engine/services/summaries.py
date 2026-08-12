"""Rebuildable memory summaries with mandatory event provenance."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Callable
from uuid import uuid4

from ..domain.errors import ValidationError
from ..domain.memory import MemorySummary, SummaryScope, validate_audiences
from ..domain.models import Audience
from ..domain.operations import MutationContext
from ..persistence.database import Database
from ..persistence.repositories import dump_json
from .entities import normalize_key
from .mutations import MutationEngine, MutationRequest, MutationResult

_SUMMARY_COLUMNS = (
    "campaign_id, branch_id, scope, scope_key, content, audiences_json,"
    " source_event_ids_json, state_version, created_at"
)


def _to_summary(row) -> MemorySummary:
    """Map one memory_summaries row to the domain contract."""
    return MemorySummary(
        campaign_id=row["campaign_id"],
        branch_id=row["branch_id"],
        scope=SummaryScope(row["scope"]),
        scope_key=row["scope_key"],
        content=row["content"],
        audiences=frozenset(Audience(a) for a in json.loads(row["audiences_json"])),
        source_event_ids=tuple(json.loads(row["source_event_ids_json"])),
        state_version=row["state_version"],
        created_at=row["created_at"],
    )


@dataclass(frozen=True)
class UpsertSummaryOperation:
    """Insert or replace one summary; the cited events keep it rebuildable."""

    scope: SummaryScope
    scope_key: str
    content: str
    audiences: frozenset[Audience]
    source_event_ids: tuple[str, ...]

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict[str, Any]:
        if not self.content.strip():
            raise ValidationError("summary content must not be empty")
        validate_audiences(self.audiences)
        if not self.source_event_ids:
            raise ValidationError("summary must cite at least one source event")
        scope_key = normalize_key(self.scope_key)
        placeholders = ", ".join("?" for _ in self.source_event_ids)
        found = connection.execute(
            f"SELECT COUNT(*) FROM memory_events WHERE id IN ({placeholders})"
            " AND campaign_id = ? AND branch_id = ?",
            (*self.source_event_ids, context.campaign.id, context.branch_id),
        ).fetchone()[0]
        if found != len(self.source_event_ids):
            raise ValidationError(
                "summary references unknown memory events for this branch"
            )
        connection.execute(
            "INSERT INTO memory_summaries(campaign_id, branch_id, scope,"
            " scope_key, content, audiences_json, source_event_ids_json,"
            " state_version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(campaign_id, branch_id, scope, scope_key) DO UPDATE SET"
            " content = excluded.content,"
            " audiences_json = excluded.audiences_json,"
            " source_event_ids_json = excluded.source_event_ids_json,"
            " state_version = excluded.state_version",
            (
                context.campaign.id,
                context.branch_id,
                self.scope.value,
                scope_key,
                self.content,
                dump_json(sorted(a.value for a in self.audiences)),
                dump_json(sorted(self.source_event_ids)),
                context.next_state_version,
                context.now,
            ),
        )
        return {
            "scope": self.scope.value,
            "scope_key": scope_key,
            "content": self.content,
            "source_event_ids": sorted(self.source_event_ids),
        }


class SummaryService:
    """Writes summaries through the mutation engine and reads them back."""

    def __init__(
        self,
        database: Database,
        mutation_engine: MutationEngine | None = None,
        id_factory: Callable[[], str] = lambda: uuid4().hex,
        clock: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(),
    ):
        self.database = database
        self.mutation_engine = mutation_engine or MutationEngine(
            database, id_factory=id_factory, clock=clock
        )
        self.id_factory = id_factory

    def upsert(
        self,
        campaign_id: str,
        branch_id: str,
        expected_version: int,
        *,
        scope: SummaryScope,
        scope_key: str,
        content: str,
        audiences: frozenset[Audience],
        source_event_ids: tuple[str, ...],
    ) -> MutationResult:
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id,
                branch_id=branch_id,
                expected_version=expected_version,
                source="user-command",
                event_type="memory-summary-updated",
                operation=UpsertSummaryOperation(
                    scope=scope,
                    scope_key=scope_key,
                    content=content,
                    audiences=audiences,
                    source_event_ids=source_event_ids,
                ),
            )
        )

    def get(
        self,
        scope: SummaryScope,
        scope_key: str,
        campaign_id: str,
        branch_id: str,
    ) -> MemorySummary | None:
        with self.database.connect() as connection:
            row = connection.execute(
                f"SELECT {_SUMMARY_COLUMNS} FROM memory_summaries"
                " WHERE campaign_id = ? AND branch_id = ?"
                " AND scope = ? AND scope_key = ?",
                (campaign_id, branch_id, scope.value, normalize_key(scope_key)),
            ).fetchone()
        return _to_summary(row) if row is not None else None

    def list(
        self,
        campaign_id: str,
        branch_id: str,
        scope: SummaryScope | None = None,
    ) -> list[MemorySummary]:
        sql = (
            f"SELECT {_SUMMARY_COLUMNS} FROM memory_summaries"
            " WHERE campaign_id = ? AND branch_id = ?"
        )
        params: list[Any] = [campaign_id, branch_id]
        if scope is not None:
            sql += " AND scope = ?"
            params.append(scope.value)
        sql += " ORDER BY scope, scope_key"
        with self.database.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [_to_summary(row) for row in rows]
