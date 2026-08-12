"""Tiered, audience-filtered memory context assembly for generation."""

from dataclasses import dataclass
import json
from typing import Any

from ..domain.models import Audience
from ..persistence.database import Database
from ..persistence.repositories import BranchRepository, CampaignRepository
from .memory_events import MemoryEventService
from .projection import ProjectionService

@dataclass(frozen=True)
class RetrievalQuery:
    """One bounded context request; ``limit`` caps event list sections."""

    campaign_id: str
    branch_id: str
    audiences: frozenset[Audience]
    text: str | None = None
    scene_entity_ids: tuple[str, ...] = ()
    limit: int = 5


class RetrievalService:
    """Assemble the prompt-facing memory sections from committed state.

    Section order and row order are deterministic: pinned event types first,
    then importance, then recency. Nothing is ever deleted; unpinned old
    events simply fall below the limit ("forgetting" is ranking only).
    """

    def __init__(self, database: Database):
        self.database = database
        self.projections = ProjectionService(database)
        self.events = MemoryEventService(database)
        self.campaign_repository = CampaignRepository()
        self.branch_repository = BranchRepository()

    def assemble(self, query: RetrievalQuery) -> dict[str, Any]:
        with self.database.connect() as connection:
            self.campaign_repository.require(connection, query.campaign_id)
            self.branch_repository.require(
                connection, query.campaign_id, query.branch_id
            )
        return {
            "current_state": self.projections.for_audiences(
                query.campaign_id, query.branch_id, query.audiences
            ),
            "recent_events": self._event_dicts(
                self.events.recent(
                    query.campaign_id, query.branch_id, query.audiences,
                    limit=query.limit,
                )
            ),
            "related_events": self._event_dicts(
                self.events.search(
                    query.campaign_id, query.branch_id, query.audiences,
                    query.text, limit=query.limit,
                )
            ) if query.text else [],
            "scene_events": self._event_dicts(
                self.events.for_entities(
                    query.campaign_id, query.branch_id,
                    query.scene_entity_ids, query.audiences, limit=query.limit,
                )
            ),
            "open_commitments": self._open_commitments(query),
            "personality": self._personality(query),
            "summaries": self._summaries(query),
        }

    @staticmethod
    def _event_dicts(events) -> list[dict[str, Any]]:
        return [
            {
                "id": event.id,
                "event_type": event.event_type.value,
                "content": event.content,
                "importance": event.importance,
                "participants": list(event.participants),
                "location_entity_id": event.location_entity_id,
                "turn_id": event.turn_id,
            }
            for event in events
        ]

    def _open_commitments(self, query: RetrievalQuery) -> list[dict[str, Any]]:
        audience_values = sorted(a.value for a in query.audiences)
        placeholders = ", ".join("?" for _ in audience_values)
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT entity_id, fact_type, fact_key, content, importance"
                " FROM facts f"
                " WHERE campaign_id = ? AND valid_until IS NULL"
                " AND fact_type IN ('commitment', 'quest', 'conflict')"
                " AND EXISTS (SELECT 1 FROM json_each(f.audiences_json)"
                f" WHERE value IN ({placeholders}))"
                " ORDER BY importance DESC, valid_from DESC LIMIT ?",
                (query.campaign_id, *audience_values, query.limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def _personality(self, query: RetrievalQuery) -> dict[str, Any]:
        """Per scene entity: visible trait values, recent change evidence,
        and open development arcs (hidden dimensions excluded by join)."""
        if not query.scene_entity_ids:
            return {}
        audience_values = sorted(a.value for a in query.audiences)
        placeholders = ", ".join("?" for _ in audience_values)
        entity_placeholders = ", ".join("?" for _ in query.scene_entity_ids)
        result: dict[str, Any] = {}
        with self.database.connect() as connection:
            trait_rows = connection.execute(
                "SELECT v.entity_id, v.attribute_key, v.value_json"
                " FROM attribute_values v"
                " JOIN attribute_definitions d"
                " ON d.campaign_id = v.campaign_id AND d.key = v.attribute_key"
                " WHERE v.campaign_id = ? AND d.category IN ('trait', 'adult_intimacy')"
                f" AND v.entity_id IN ({entity_placeholders})"
                " AND EXISTS (SELECT 1 FROM json_each(d.audiences_json)"
                f" WHERE value IN ({placeholders}))"
                " ORDER BY v.entity_id, v.attribute_key",
                (query.campaign_id, *query.scene_entity_ids, *audience_values),
            ).fetchall()
            event_rows = connection.execute(
                "SELECT t.entity_id, t.trait_key, t.before_value, t.delta,"
                " t.after_value, t.cause, t.turn_id, t.tier FROM trait_events t"
                " JOIN attribute_definitions d"
                " ON d.campaign_id = t.campaign_id AND d.key = t.trait_key"
                " WHERE t.campaign_id = ? AND t.branch_id = ?"
                f" AND t.entity_id IN ({entity_placeholders})"
                " AND EXISTS (SELECT 1 FROM json_each(d.audiences_json)"
                f" WHERE value IN ({placeholders}))"
                " ORDER BY t.entity_id, t.trait_key, t.rowid DESC",
                (query.campaign_id, query.branch_id, *query.scene_entity_ids,
                 *audience_values),
            ).fetchall()
            arc_rows = connection.execute(
                "SELECT a.entity_id, a.dimension, a.label, a.summary,"
                " a.start_turn_id FROM development_arcs a"
                " JOIN attribute_definitions d"
                " ON d.campaign_id = a.campaign_id AND d.key = a.dimension"
                " WHERE a.campaign_id = ? AND a.branch_id = ?"
                " AND a.closed_state_version IS NULL"
                f" AND a.entity_id IN ({entity_placeholders})"
                " AND EXISTS (SELECT 1 FROM json_each(d.audiences_json)"
                f" WHERE value IN ({placeholders}))"
                " ORDER BY a.entity_id, a.dimension",
                (query.campaign_id, query.branch_id, *query.scene_entity_ids,
                 *audience_values),
            ).fetchall()
        for entity_id in query.scene_entity_ids:
            traits = [
                {"key": row["attribute_key"], "value": json.loads(row["value_json"])}
                for row in trait_rows if row["entity_id"] == entity_id
            ]
            seen: set[str] = set()
            evidence = []
            for row in event_rows:
                if row["entity_id"] != entity_id or row["trait_key"] in seen:
                    continue
                seen.add(row["trait_key"])
                evidence.append({
                    "trait_key": row["trait_key"],
                    "before": row["before_value"],
                    "delta": row["delta"],
                    "after": row["after_value"],
                    "cause": row["cause"],
                    "turn_id": row["turn_id"],
                    "tier": row["tier"],
                })
            arcs = [
                {"dimension": row["dimension"], "label": row["label"],
                 "summary": row["summary"], "start_turn_id": row["start_turn_id"]}
                for row in arc_rows if row["entity_id"] == entity_id
            ]
            if traits or evidence or arcs:
                result[entity_id] = {
                    "traits": traits,
                    "recent_trait_events": evidence,
                    "open_arcs": arcs,
                }
        return result

    def _summaries(self, query: RetrievalQuery) -> list[dict[str, Any]]:
        audience_values = sorted(a.value for a in query.audiences)
        placeholders = ", ".join("?" for _ in audience_values)
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT scope, scope_key, content, source_event_ids_json"
                " FROM memory_summaries s"
                " WHERE campaign_id = ? AND branch_id = ?"
                " AND EXISTS (SELECT 1 FROM json_each(s.audiences_json)"
                f" WHERE value IN ({placeholders}))"
                " ORDER BY scope, scope_key LIMIT ?",
                (query.campaign_id, query.branch_id, *audience_values, query.limit),
            ).fetchall()
        return [
            {
                "scope": row["scope"],
                "scope_key": row["scope_key"],
                "content": row["content"],
                "source_event_ids": json.loads(row["source_event_ids_json"]),
            }
            for row in rows
        ]
