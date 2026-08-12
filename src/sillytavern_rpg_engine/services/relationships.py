"""Directed, dimensioned relationship values between entities."""

from dataclasses import dataclass
import json
import sqlite3
from typing import Any

from ..domain.errors import NotFoundError, ValidationError
from ..domain.memory import Relationship, validate_audiences
from ..domain.models import Audience
from ..domain.operations import MutationContext
from ..persistence.database import Database
from ..persistence.repositories import dump_json
from .entities import normalize_key

DEFAULT_RELATIONSHIP_AUDIENCES = frozenset({Audience.ENGINE, Audience.NPC_AGENT})


def _to_relationship(row) -> Relationship:
    """Map one relationships row to the domain contract."""
    return Relationship(
        campaign_id=row["campaign_id"],
        from_entity_id=row["from_entity_id"],
        to_entity_id=row["to_entity_id"],
        dimension=row["dimension"],
        value=json.loads(row["value_json"]),
        audiences=frozenset(Audience(a) for a in json.loads(row["audiences_json"])),
        state_version=row["state_version"],
        updated_turn_id=row["updated_turn_id"],
    )


@dataclass(frozen=True)
class SetRelationshipOperation:
    """Upsert one directed relationship dimension; defaults to hidden
    audiences because precise relationship values are engine-internal."""

    from_entity_id: str
    to_entity_id: str
    dimension: str
    value: Any
    audiences: frozenset[Audience] | None = None
    turn_id: str | None = None

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict[str, Any]:
        if self.from_entity_id == self.to_entity_id:
            raise ValidationError("self-relationship is not allowed")
        for entity_id in (self.from_entity_id, self.to_entity_id):
            if connection.execute(
                "SELECT id FROM entities WHERE id = ? AND campaign_id = ?",
                (entity_id, context.campaign.id),
            ).fetchone() is None:
                raise NotFoundError(
                    f"entity {entity_id!r} not found in campaign"
                    f" {context.campaign.id}"
                )
        dimension = normalize_key(self.dimension)
        audiences = (
            self.audiences
            if self.audiences is not None
            else DEFAULT_RELATIONSHIP_AUDIENCES
        )
        validate_audiences(audiences)
        try:
            value_json = dump_json(self.value)
        except TypeError as exc:
            raise ValidationError(
                "relationship value must be JSON-serializable"
            ) from exc
        connection.execute(
            "INSERT INTO relationships(campaign_id, from_entity_id, to_entity_id,"
            " dimension, value_json, audiences_json, state_version,"
            " updated_turn_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(from_entity_id, to_entity_id, dimension) DO UPDATE SET"
            " value_json = excluded.value_json,"
            " audiences_json = excluded.audiences_json,"
            " state_version = excluded.state_version,"
            " updated_turn_id = excluded.updated_turn_id",
            (
                context.campaign.id,
                self.from_entity_id,
                self.to_entity_id,
                dimension,
                value_json,
                dump_json(sorted(a.value for a in audiences)),
                context.next_state_version,
                self.turn_id,
            ),
        )
        return {
            "from_entity_id": self.from_entity_id,
            "to_entity_id": self.to_entity_id,
            "dimension": dimension,
            "value": self.value,
            "audiences": sorted(a.value for a in audiences),
            "updated_turn_id": self.turn_id,
        }


class RelationshipService:
    """Reads current relationships; writes go through operations."""

    def __init__(self, database: Database):
        self.database = database

    def between(
        self, from_entity_id: str, to_entity_id: str
    ) -> list[Relationship]:
        """Return every dimension from one entity to another, by dimension."""
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT campaign_id, from_entity_id, to_entity_id, dimension,"
                " value_json, audiences_json, state_version, updated_turn_id"
                " FROM relationships WHERE from_entity_id = ? AND to_entity_id = ?"
                " ORDER BY dimension",
                (from_entity_id, to_entity_id),
            ).fetchall()
        return [_to_relationship(row) for row in rows]

    def list_current(self, campaign_id: str) -> list[Relationship]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT campaign_id, from_entity_id, to_entity_id, dimension,"
                " value_json, audiences_json, state_version, updated_turn_id"
                " FROM relationships WHERE campaign_id = ?"
                " ORDER BY from_entity_id, to_entity_id, dimension",
                (campaign_id,),
            ).fetchall()
        return [_to_relationship(row) for row in rows]
