"""Permanent memory events, participant links, and the FTS5 search index."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Callable, Iterable
from uuid import uuid4

from ..domain.errors import NotFoundError, ValidationError
from ..domain.memory import (
    MemoryEvent,
    MemoryEventType,
    PINNED_EVENT_TYPES,
    validate_audiences,
    validate_importance,
)
from ..domain.models import Audience
from ..domain.operations import MutationContext
from ..persistence.database import Database
from ..persistence.repositories import (
    BranchRepository,
    CampaignRepository,
    dump_json,
)
from .mutations import MutationEngine, MutationRequest, MutationResult

_EVENT_COLUMN_NAMES = (
    "id", "campaign_id", "branch_id", "turn_id", "event_type", "content",
    "importance", "audiences_json", "location_entity_id", "source",
    "state_version", "created_at",
)


def _event_columns(alias: str | None = None) -> str:
    """Comma-joined event columns, optionally table-qualified for joins."""
    if alias is None:
        return ", ".join(_EVENT_COLUMN_NAMES)
    return ", ".join(f"{alias}.{column}" for column in _EVENT_COLUMN_NAMES)


_PINNED_SQL_VALUES = tuple(sorted(event.value for event in PINNED_EVENT_TYPES))


def _audience_filter(column: str, audiences: Iterable[Audience]) -> tuple[str, list[str]]:
    """SQL EXISTS clause matching rows whose audience set intersects the
    requested one; filtering stays in the query, never in Python afterward."""
    values = sorted(audience.value for audience in audiences)
    placeholders = ", ".join("?" for _ in values)
    return (
        f" AND EXISTS (SELECT 1 FROM json_each({column})"
        f" WHERE value IN ({placeholders}))",
        values,
    )


@dataclass(frozen=True)
class RecordMemoryEventOperation:
    """Append one memory event, its participants, and its FTS row."""

    event_id: str
    event_type: MemoryEventType
    content: str
    importance: int
    audiences: frozenset[Audience]
    participants: tuple[str, ...] = ()
    location_entity_id: str | None = None
    turn_id: str | None = None
    source: str = "narrative_development"

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict[str, Any]:
        if not self.content.strip():
            raise ValidationError("memory event content must not be empty")
        validate_importance(self.importance)
        validate_audiences(self.audiences)
        for entity_id in (*self.participants, self.location_entity_id):
            if entity_id is None:
                continue
            if connection.execute(
                "SELECT id FROM entities WHERE id = ? AND campaign_id = ?",
                (entity_id, context.campaign.id),
            ).fetchone() is None:
                raise NotFoundError(
                    f"entity {entity_id!r} not found in campaign"
                    f" {context.campaign.id}"
                )
        connection.execute(
            "INSERT INTO memory_events(id, campaign_id, branch_id, turn_id,"
            " event_type, content, importance, audiences_json,"
            " location_entity_id, source, state_version, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self.event_id,
                context.campaign.id,
                context.branch_id,
                self.turn_id,
                self.event_type.value,
                self.content,
                self.importance,
                dump_json(sorted(a.value for a in self.audiences)),
                self.location_entity_id,
                self.source,
                context.next_state_version,
                context.now,
            ),
        )
        connection.execute(
            "INSERT INTO memory_events_fts(rowid, content)"
            " SELECT rowid, content FROM memory_events WHERE id = ?",
            (self.event_id,),
        )
        participants = tuple(dict.fromkeys(self.participants))
        for entity_id in participants:
            connection.execute(
                "INSERT INTO memory_event_participants(campaign_id, event_id,"
                " entity_id) VALUES (?, ?, ?)",
                (context.campaign.id, self.event_id, entity_id),
            )
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "importance": self.importance,
            "participants": list(self.participants),
            "location_entity_id": self.location_entity_id,
            "turn_id": self.turn_id,
        }


class MemoryEventService:
    """Records events through the mutation engine and reads them back."""

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
        self.clock = clock
        self.campaign_repository = CampaignRepository()
        self.branch_repository = BranchRepository()

    def record_transcript(
        self,
        campaign_id: str,
        branch_id: str,
        *,
        type: MemoryEventType,
        content: str,
        importance: int,
        audiences: frozenset[Audience],
        participants: tuple[str, ...] = (),
        turn_id: str | None = None,
    ) -> str:
        """Append a turn transcript event WITHOUT bumping the state version.

        Transcripts are bookkeeping (like the turns row): a version bump per
        turn would invalidate every Pending proposal before the player can
        confirm it. The turns row is the audit record for the transcript.
        """
        if not content.strip():
            raise ValidationError("memory event content must not be empty")
        validate_importance(importance)
        validate_audiences(audiences)
        event_id = self.id_factory()
        with self.database.transaction() as connection:
            campaign = self.campaign_repository.require(connection, campaign_id)
            self.branch_repository.require(connection, campaign_id, branch_id)
            for entity_id in participants:
                if connection.execute(
                    "SELECT id FROM entities WHERE id = ? AND campaign_id = ?",
                    (entity_id, campaign_id),
                ).fetchone() is None:
                    raise NotFoundError(
                        f"entity {entity_id!r} not found in campaign"
                        f" {campaign_id}"
                    )
            connection.execute(
                "INSERT INTO memory_events(id, campaign_id, branch_id, turn_id,"
                " event_type, content, importance, audiences_json,"
                " location_entity_id, source, state_version, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 'turn', ?, ?)",
                (
                    event_id, campaign_id, branch_id, turn_id, type.value,
                    content, importance,
                    dump_json(sorted(a.value for a in audiences)),
                    campaign.state_version, self.clock(),
                ),
            )
            connection.execute(
                "INSERT INTO memory_events_fts(rowid, content)"
                " SELECT rowid, content FROM memory_events WHERE id = ?",
                (event_id,),
            )
            for entity_id in dict.fromkeys(participants):
                connection.execute(
                    "INSERT INTO memory_event_participants(campaign_id,"
                    " event_id, entity_id) VALUES (?, ?, ?)",
                    (campaign_id, event_id, entity_id),
                )
        return event_id

    def record(
        self,
        campaign_id: str,
        branch_id: str,
        expected_version: int,
        *,
        type: MemoryEventType,
        content: str,
        importance: int,
        audiences: frozenset[Audience],
        participants: tuple[str, ...] = (),
        location_entity_id: str | None = None,
        turn_id: str | None = None,
        source: str = "user-command",
    ) -> MutationResult:
        """Record one event as an atomic versioned mutation."""
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id,
                branch_id=branch_id,
                expected_version=expected_version,
                source=source,
                event_type="memory-event-recorded",
                operation=RecordMemoryEventOperation(
                    event_id=self.id_factory(),
                    event_type=type,
                    content=content,
                    importance=importance,
                    audiences=audiences,
                    participants=participants,
                    location_entity_id=location_entity_id,
                    turn_id=turn_id,
                    source=source,
                ),
            )
        )

    def search(
        self,
        campaign_id: str,
        branch_id: str,
        audiences: Iterable[Audience],
        text: str,
        limit: int = 20,
    ) -> list[MemoryEvent]:
        """Full-text search, audience-filtered, importance/recency ordered."""
        terms = text.strip()
        if len(terms) < 3:
            raise ValidationError(
                "FTS5 trigram search needs at least 3 characters"
            )
        phrase = '"' + terms.replace('"', '""') + '"'
        audience_sql, audience_values = _audience_filter(
            "m.audiences_json", audiences
        )
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_event_columns('m')} FROM memory_events m"
                " WHERE m.rowid IN (SELECT rowid FROM memory_events_fts"
                " WHERE memory_events_fts MATCH ?)"
                " AND m.campaign_id = ? AND m.branch_id = ?"
                f"{audience_sql}"
                " ORDER BY m.importance DESC, m.rowid DESC LIMIT ?",
                (phrase, campaign_id, branch_id, *audience_values, limit),
            ).fetchall()
            return [self._to_event(connection, row) for row in rows]

    def recent(
        self,
        campaign_id: str,
        branch_id: str,
        audiences: Iterable[Audience],
        limit: int = 20,
    ) -> list[MemoryEvent]:
        """Latest events, pinned types first, then importance, then recency."""
        audience_sql, audience_values = _audience_filter(
            "m.audiences_json", audiences
        )
        pinned = ", ".join(f"'{value}'" for value in _PINNED_SQL_VALUES)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_event_columns('m')} FROM memory_events m"
                " WHERE m.campaign_id = ? AND m.branch_id = ?"
                f"{audience_sql}"
                f" ORDER BY CASE WHEN m.event_type IN ({pinned}) THEN 1 ELSE 0"
                " END DESC, m.importance DESC, m.rowid DESC LIMIT ?",
                (campaign_id, branch_id, *audience_values, limit),
            ).fetchall()
            return [self._to_event(connection, row) for row in rows]

    def for_entities(
        self,
        campaign_id: str,
        branch_id: str,
        entity_ids: Iterable[str],
        audiences: Iterable[Audience],
        limit: int = 20,
    ) -> list[MemoryEvent]:
        """Latest events involving any of the given entities or locations."""
        ids = tuple(dict.fromkeys(entity_ids))
        if not ids:
            return []
        audience_sql, audience_values = _audience_filter(
            "m.audiences_json", audiences
        )
        placeholders = ", ".join("?" for _ in ids)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_event_columns('m')} FROM memory_events m"
                " LEFT JOIN memory_event_participants p"
                " ON p.event_id = m.id"
                " WHERE m.campaign_id = ? AND m.branch_id = ?"
                f" AND (p.entity_id IN ({placeholders})"
                f" OR m.location_entity_id IN ({placeholders}))"
                f"{audience_sql}"
                " GROUP BY m.id"
                " ORDER BY m.rowid DESC LIMIT ?",
                (campaign_id, branch_id, *ids, *ids, *audience_values, limit),
            ).fetchall()
            return [self._to_event(connection, row) for row in rows]

    def _to_event(
        self, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> MemoryEvent:
        participants = tuple(
            item[0]
            for item in connection.execute(
                "SELECT entity_id FROM memory_event_participants"
                " WHERE event_id = ? ORDER BY entity_id",
                (row["id"],),
            ).fetchall()
        )
        return MemoryEvent(
            id=row["id"],
            campaign_id=row["campaign_id"],
            branch_id=row["branch_id"],
            event_type=MemoryEventType(row["event_type"]),
            content=row["content"],
            importance=row["importance"],
            audiences=frozenset(
                Audience(a) for a in json.loads(row["audiences_json"])
            ),
            participants=participants,
            location_entity_id=row["location_entity_id"],
            turn_id=row["turn_id"],
            source=row["source"],
            state_version=row["state_version"],
            created_at=row["created_at"],
        )


class MemoryIndexService:
    """Maintains the FTS index; rebuild derives it from the event table."""

    def __init__(self, database: Database):
        self.database = database

    def rebuild(self) -> int:
        """Drop and repopulate the FTS index; returns indexed event count."""
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM memory_events_fts")
            connection.execute(
                "INSERT INTO memory_events_fts(rowid, content)"
                " SELECT rowid, content FROM memory_events"
            )
            events_count = connection.execute(
                "SELECT COUNT(*) FROM memory_events"
            ).fetchone()[0]
            fts_count = connection.execute(
                "SELECT COUNT(*) FROM memory_events_fts"
            ).fetchone()[0]
            if events_count != fts_count:
                raise RuntimeError(
                    "memory_events_fts parity mismatch:"
                    f" memory_events={events_count},"
                    f" memory_events_fts={fts_count}"
                )
            return fts_count
