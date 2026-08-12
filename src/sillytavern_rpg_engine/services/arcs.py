"""Development arcs: curated phases with trait-event provenance."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Callable
from uuid import uuid4

from ..domain.errors import NotFoundError, ValidationError
from ..domain.memory import DevelopmentArc
from ..domain.operations import MutationContext
from ..persistence.database import Database
from ..persistence.repositories import dump_json
from .entities import normalize_key
from .mutations import MutationEngine, MutationRequest, MutationResult

_ARC_COLUMNS = (
    "id, campaign_id, branch_id, entity_id, dimension, label, summary,"
    " source_event_ids_json, start_turn_id, end_turn_id,"
    " opened_state_version, closed_state_version, created_at"
)


def _to_arc(row) -> DevelopmentArc:
    """Map one development_arcs row to the domain contract."""
    return DevelopmentArc(
        id=row["id"],
        campaign_id=row["campaign_id"],
        branch_id=row["branch_id"],
        entity_id=row["entity_id"],
        dimension=row["dimension"],
        label=row["label"],
        summary=row["summary"],
        source_event_ids=tuple(json.loads(row["source_event_ids_json"])),
        start_turn_id=row["start_turn_id"],
        end_turn_id=row["end_turn_id"],
        opened_state_version=row["opened_state_version"],
        closed_state_version=row["closed_state_version"],
        created_at=row["created_at"],
    )


@dataclass(frozen=True)
class OpenArcOperation:
    """Open one arc; any open arc for the same (entity, dimension) closes
    atomically with the new arc's start turn."""

    arc_id: str
    entity_id: str
    dimension: str
    label: str
    summary: str
    source_event_ids: tuple[str, ...]
    start_turn_id: str | None = None

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict[str, Any]:
        if not self.label.strip() or not self.summary.strip():
            raise ValidationError("arc label and summary must not be empty")
        if not self.source_event_ids:
            raise ValidationError("arc must cite at least one source event")
        dimension = normalize_key(self.dimension)
        if connection.execute(
            "SELECT id FROM entities WHERE id = ? AND campaign_id = ?",
            (self.entity_id, context.campaign.id),
        ).fetchone() is None:
            raise NotFoundError(
                f"entity {self.entity_id!r} not found in campaign"
                f" {context.campaign.id}"
            )
        placeholders = ", ".join("?" for _ in self.source_event_ids)
        found = connection.execute(
            f"SELECT COUNT(*) FROM trait_events WHERE id IN ({placeholders})"
            " AND entity_id = ? AND trait_key = ?",
            (*self.source_event_ids, self.entity_id, dimension),
        ).fetchone()[0]
        if found != len(self.source_event_ids):
            raise ValidationError(
                "arc references unknown trait events for this entity/dimension"
            )
        closed = connection.execute(
            "UPDATE development_arcs"
            " SET closed_state_version = ?, end_turn_id = ?"
            " WHERE entity_id = ? AND dimension = ?"
            " AND closed_state_version IS NULL",
            (context.next_state_version, self.start_turn_id,
             self.entity_id, dimension),
        ).rowcount
        connection.execute(
            "INSERT INTO development_arcs(id, campaign_id, branch_id, entity_id,"
            " dimension, label, summary, source_event_ids_json, start_turn_id,"
            " opened_state_version, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self.arc_id,
                context.campaign.id,
                context.branch_id,
                self.entity_id,
                dimension,
                self.label,
                self.summary,
                dump_json(sorted(self.source_event_ids)),
                self.start_turn_id,
                context.next_state_version,
                context.now,
            ),
        )
        return {
            "arc_id": self.arc_id,
            "entity_id": self.entity_id,
            "dimension": dimension,
            "label": self.label,
            "closed_previous": closed == 1,
            "source_event_ids": sorted(self.source_event_ids),
        }


@dataclass(frozen=True)
class CloseArcOperation:
    """Close an open arc without opening a successor."""

    arc_id: str
    end_turn_id: str | None = None
    summary: str | None = None

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict[str, Any]:
        cursor = connection.execute(
            "UPDATE development_arcs"
            " SET closed_state_version = ?, end_turn_id = ?,"
            " summary = COALESCE(?, summary)"
            " WHERE id = ? AND closed_state_version IS NULL",
            (context.next_state_version, self.end_turn_id,
             self.summary, self.arc_id),
        )
        if cursor.rowcount == 0:
            raise ValidationError(f"arc {self.arc_id!r} is not open")
        return {"arc_id": self.arc_id, "closed": True}


class ArcService:
    """Opens/closes arcs through the mutation engine and reads them back."""

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

    def open(
        self,
        campaign_id: str,
        branch_id: str,
        expected_version: int,
        *,
        entity_id: str,
        dimension: str,
        label: str,
        summary: str,
        source_event_ids: tuple[str, ...],
        start_turn_id: str | None = None,
        source: str = "user-command",
    ) -> MutationResult:
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id,
                branch_id=branch_id,
                expected_version=expected_version,
                source=source,
                event_type="development-arc-opened",
                operation=OpenArcOperation(
                    arc_id=self.id_factory(),
                    entity_id=entity_id,
                    dimension=dimension,
                    label=label,
                    summary=summary,
                    source_event_ids=source_event_ids,
                    start_turn_id=start_turn_id,
                ),
            )
        )

    def close(
        self,
        campaign_id: str,
        branch_id: str,
        expected_version: int,
        *,
        arc_id: str,
        end_turn_id: str | None = None,
        summary: str | None = None,
        source: str = "user-command",
    ) -> MutationResult:
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id,
                branch_id=branch_id,
                expected_version=expected_version,
                source=source,
                event_type="development-arc-closed",
                operation=CloseArcOperation(arc_id, end_turn_id, summary),
            )
        )

    def list(
        self, entity_id: str, dimension: str | None = None
    ) -> list[DevelopmentArc]:
        """Return arcs of one entity, oldest first, optionally one dimension."""
        sql = f"SELECT {_ARC_COLUMNS} FROM development_arcs WHERE entity_id = ?"
        params: list[Any] = [entity_id]
        if dimension is not None:
            sql += " AND dimension = ?"
            params.append(normalize_key(dimension))
        sql += " ORDER BY opened_state_version"
        with self.database.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [_to_arc(row) for row in rows]

    def current(self, entity_id: str, dimension: str) -> DevelopmentArc | None:
        """Return the open arc for one dimension, or None."""
        with self.database.connect() as connection:
            row = connection.execute(
                f"SELECT {_ARC_COLUMNS} FROM development_arcs"
                " WHERE entity_id = ? AND dimension = ?"
                " AND closed_state_version IS NULL",
                (entity_id, normalize_key(dimension)),
            ).fetchone()
        return _to_arc(row) if row is not None else None
