"""Entity creation, canonical name normalization, and resolution."""

from dataclasses import dataclass
import re
import sqlite3
import unicodedata
from typing import Any

from ..domain.errors import AmbiguousEntityError, NotFoundError, ValidationError
from ..domain.models import AgeStatus, Entity, EntityKind
from ..domain.operations import MutationContext, MutationOperation
from ..persistence.database import Database
from .mutations import MutationEngine, MutationRequest, MutationResult


def normalize_key(value: str) -> str:
    """Canonical key: NFKC, trimmed, casefolded, whitespace collapsed, and
    non-alphanumeric runs converted to a single underscore."""
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"[^\w]+", "_", normalized).strip("_")
    if not normalized:
        raise ValidationError("normalized key must not be empty")
    return normalized


@dataclass(frozen=True)
class CreateEntityOperation:
    """Insert one entity and its aliases at the mutation's state version."""

    entity_id: str
    kind: EntityKind
    name: str
    age_status: AgeStatus = AgeStatus.UNKNOWN
    aliases: tuple[str, ...] = ()

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict[str, Any]:
        normalized_name = normalize_key(self.name)
        try:
            connection.execute(
                "INSERT INTO entities(id, campaign_id, kind, name, normalized_name,"
                " age_status, created_state_version) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    self.entity_id,
                    context.campaign.id,
                    self.kind.value,
                    self.name,
                    normalized_name,
                    self.age_status.value,
                    context.next_state_version,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValidationError("entity name already exists") from exc
        try:
            for alias in self.aliases:
                connection.execute(
                    "INSERT INTO entity_aliases(campaign_id, entity_id, alias,"
                    " normalized_alias) VALUES (?, ?, ?, ?)",
                    (context.campaign.id, self.entity_id, alias, normalize_key(alias)),
                )
        except sqlite3.IntegrityError as exc:
            raise ValidationError("entity alias already exists") from exc
        return {
            "entity_id": self.entity_id,
            "campaign_id": context.campaign.id,
            "kind": self.kind.value,
            "name": self.name,
            "age_status": self.age_status.value,
            "aliases": list(self.aliases),
        }


class EntityService:
    """Resolves entity references by normalized primary name or alias."""

    def __init__(self, database: Database):
        self.database = database

    def resolve(self, campaign_id: str, name_or_alias: str) -> Entity:
        """Return the one entity matching a name or alias; raise
        NotFoundError for zero matches or AmbiguousEntityError for many."""
        normalized = normalize_key(name_or_alias)
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT id, campaign_id, kind, name, age_status FROM entities"
                " WHERE campaign_id = ? AND (normalized_name = ? OR id IN ("
                " SELECT entity_id FROM entity_aliases"
                " WHERE campaign_id = ? AND normalized_alias = ?))",
                (campaign_id, normalized, campaign_id, normalized),
            ).fetchall()
        if not rows:
            raise NotFoundError(
                f"entity {name_or_alias!r} not found in campaign {campaign_id}"
            )
        if len(rows) > 1:
            raise AmbiguousEntityError(
                f"entity name {name_or_alias!r} is ambiguous in campaign {campaign_id}"
            )
        row = rows[0]
        return Entity(
            id=row["id"],
            campaign_id=row["campaign_id"],
            kind=EntityKind(row["kind"]),
            name=row["name"],
            age_status=AgeStatus(row["age_status"]),
        )


class EntityAttributeService:
    """Applies explicit entity and attribute operations through the mutation engine."""

    def __init__(self, database: Database, mutation_engine: MutationEngine):
        self.database = database
        self.mutation_engine = mutation_engine
        self.entities = EntityService(database)

    def apply_explicit(
        self,
        campaign_id: str,
        branch_id: str,
        expected_version: int,
        operation: MutationOperation,
    ) -> MutationResult:
        """Submit a user-command operation as one atomic, versioned mutation."""
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id,
                branch_id=branch_id,
                expected_version=expected_version,
                source="user-command",
                event_type="explicit-state-change",
                operation=operation,
            )
        )
