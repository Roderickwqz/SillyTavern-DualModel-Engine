"""Temporal facts: assert new values, supersede prior ones, never delete."""

from dataclasses import dataclass
import json
import sqlite3
from typing import Any

from ..domain.errors import NotFoundError, ValidationError
from ..domain.memory import Fact, FactType, validate_audiences, validate_importance
from ..domain.models import Audience
from ..domain.operations import MutationContext
from ..persistence.database import Database
from ..persistence.repositories import dump_json
from .entities import normalize_key


def _to_fact(row) -> Fact:
    """Map one facts row to the domain contract."""
    return Fact(
        id=row["id"],
        campaign_id=row["campaign_id"],
        branch_id=row["branch_id"],
        entity_id=row["entity_id"],
        fact_type=FactType(row["fact_type"]),
        fact_key=row["fact_key"],
        content=row["content"],
        importance=row["importance"],
        audiences=frozenset(Audience(a) for a in json.loads(row["audiences_json"])),
        valid_from=row["valid_from"],
        valid_until=row["valid_until"],
        superseded_by=row["superseded_by"],
        turn_id=row["turn_id"],
        source=row["source"],
        created_at=row["created_at"],
    )


_FACT_COLUMNS = (
    "id, campaign_id, branch_id, entity_id, fact_type, fact_key, content,"
    " importance, audiences_json, valid_from, valid_until, superseded_by,"
    " turn_id, source, created_at"
)


@dataclass(frozen=True)
class AssertFactOperation:
    """Assert one fact; the current fact with the same (entity, key) is
    superseded in the same transaction instead of overwritten or deleted."""

    fact_id: str
    entity_id: str
    fact_type: FactType
    fact_key: str
    content: str
    importance: int
    audiences: frozenset[Audience]
    turn_id: str | None = None

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict[str, Any]:
        if not self.content.strip():
            raise ValidationError("fact content must not be empty")
        validate_importance(self.importance)
        validate_audiences(self.audiences)
        if connection.execute(
            "SELECT id FROM entities WHERE id = ? AND campaign_id = ?",
            (self.entity_id, context.campaign.id),
        ).fetchone() is None:
            raise NotFoundError(
                f"entity {self.entity_id!r} not found in campaign {context.campaign.id}"
            )
        key = normalize_key(self.fact_key)
        current = connection.execute(
            "SELECT id, valid_from FROM facts"
            " WHERE entity_id = ? AND fact_key = ? AND valid_until IS NULL",
            (self.entity_id, key),
        ).fetchone()
        if current is not None:
            connection.execute(
                "UPDATE facts SET valid_until = ? WHERE id = ?",
                (context.next_state_version, current["id"]),
            )
        connection.execute(
            "INSERT INTO facts(id, campaign_id, branch_id, entity_id, fact_type,"
            " fact_key, content, importance, audiences_json, valid_from, turn_id,"
            " source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self.fact_id,
                context.campaign.id,
                context.branch_id,
                self.entity_id,
                self.fact_type.value,
                key,
                self.content,
                self.importance,
                dump_json(sorted(a.value for a in self.audiences)),
                context.next_state_version,
                self.turn_id,
                "user-command",
                context.now,
            ),
        )
        if current is not None:
            connection.execute(
                "UPDATE facts SET superseded_by = ? WHERE id = ?",
                (self.fact_id, current["id"]),
            )
        return {
            "fact_id": self.fact_id,
            "entity_id": self.entity_id,
            "fact_type": self.fact_type.value,
            "fact_key": key,
            "content": self.content,
            "importance": self.importance,
            "superseded_fact_id": current["id"] if current is not None else None,
        }


class FactService:
    """Reads current and historical facts; writes go through operations."""

    def __init__(self, database: Database):
        self.database = database

    def current(self, campaign_id: str, entity_id: str | None = None) -> list[Fact]:
        """Return current facts ordered by entity and key."""
        sql = f"SELECT {_FACT_COLUMNS} FROM facts WHERE campaign_id = ? AND valid_until IS NULL"
        params: list[Any] = [campaign_id]
        if entity_id is not None:
            sql += " AND entity_id = ?"
            params.append(entity_id)
        sql += " ORDER BY entity_id, fact_key"
        with self.database.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [_to_fact(row) for row in rows]

    def history(self, entity_id: str, fact_key: str) -> list[Fact]:
        """Return every version of one fact key, oldest first."""
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_FACT_COLUMNS} FROM facts"
                " WHERE entity_id = ? AND fact_key = ? ORDER BY valid_from",
                (entity_id, normalize_key(fact_key)),
            ).fetchall()
        return [_to_fact(row) for row in rows]
