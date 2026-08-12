"""Audience-scoped projections of campaign state built from SQL reads."""

import json
from typing import Any, Iterable

from ..domain.models import Audience
from ..persistence.database import Database
from ..persistence.repositories import BranchRepository, CampaignRepository


class ProjectionService:
    """Build audience-filtered projections of one campaign's current state.

    Only definitions (and their values) whose audience set intersects the
    requested audiences appear; filtering happens while reading, so no
    unrestricted snapshot is ever assembled.
    """

    def __init__(self, database: Database):
        self.database = database
        self.campaign_repository = CampaignRepository()
        self.branch_repository = BranchRepository()

    def for_audience(
        self, campaign_id: str, branch_id: str, audience: Audience
    ) -> dict[str, Any]:
        """Return the projection visible to a single audience."""
        return self.for_audiences(campaign_id, branch_id, {audience})

    def for_audiences(
        self,
        campaign_id: str,
        branch_id: str,
        audiences: Iterable[Audience],
    ) -> dict[str, Any]:
        """Return the projection visible to any of the requested audiences."""
        requested = frozenset(audiences)
        with self.database.connect() as connection:
            campaign = self.campaign_repository.require(connection, campaign_id)
            self.branch_repository.require(connection, campaign_id, branch_id)
            categories = self._visible_categories(connection, campaign_id, requested)
            values = self._values(connection, campaign_id, set(categories))
            facts = self._facts(connection, campaign_id, requested)
            entities = self._entities(connection, campaign_id, categories, values, facts)
        return {
            "campaign_id": campaign.id,
            "branch_id": branch_id,
            "state_version": campaign.state_version,
            "rules": {
                "mode": campaign.rules.mode.value,
                "enabled": campaign.rules.enabled,
                "version": campaign.rules.version,
                "custom_preset_id": campaign.rules.custom_preset_id,
            },
            "entities": entities,
        }

    def _visible_categories(
        self, connection, campaign_id: str, requested: frozenset[Audience]
    ) -> dict[str, str]:
        """Map each definition visible to the requested audiences to its category."""
        rows = connection.execute(
            "SELECT key, category, audiences_json FROM attribute_definitions"
            " WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchall()
        categories: dict[str, str] = {}
        for row in rows:
            definition_audiences = frozenset(
                Audience(audience) for audience in json.loads(row["audiences_json"])
            )
            if definition_audiences & requested:
                categories[row["key"]] = row["category"]
        return categories

    def _values(
        self, connection, campaign_id: str, visible_keys: set[str]
    ) -> dict[str, list[tuple[str, str, Any]]]:
        """Read values only for visible keys, grouped by entity."""
        if not visible_keys:
            return {}
        placeholders = ", ".join("?" for _ in visible_keys)
        rows = connection.execute(
            "SELECT entity_id, attribute_key, value_json FROM attribute_values"
            f" WHERE campaign_id = ? AND attribute_key IN ({placeholders})",
            (campaign_id, *sorted(visible_keys)),
        ).fetchall()
        values: dict[str, list[tuple[str, str, Any]]] = {}
        for row in rows:
            values.setdefault(row["entity_id"], []).append(
                (row["attribute_key"], json.loads(row["value_json"]))
            )
        return values

    def _facts(
        self, connection, campaign_id: str, requested: frozenset[Audience]
    ) -> dict[str, list[dict[str, Any]]]:
        """Read current facts visible to the requested audiences, by entity."""
        audience_values = sorted(audience.value for audience in requested)
        if not audience_values:
            return {}
        placeholders = ", ".join("?" for _ in audience_values)
        rows = connection.execute(
            "SELECT entity_id, fact_type, fact_key, content, importance"
            " FROM facts f WHERE campaign_id = ? AND valid_until IS NULL"
            " AND EXISTS (SELECT 1 FROM json_each(f.audiences_json)"
            f" WHERE value IN ({placeholders}))"
            " ORDER BY entity_id, fact_key",
            (campaign_id, *audience_values),
        ).fetchall()
        facts: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            facts.setdefault(row["entity_id"], []).append(
                {
                    "fact_type": row["fact_type"],
                    "fact_key": row["fact_key"],
                    "content": row["content"],
                    "importance": row["importance"],
                }
            )
        return facts

    def _entities(
        self,
        connection,
        campaign_id: str,
        categories: dict[str, str],
        values: dict[str, list[tuple[str, str, Any]]],
        facts: dict[str, list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT id, kind, name FROM entities"
            " WHERE campaign_id = ? ORDER BY normalized_name",
            (campaign_id,),
        ).fetchall()
        entities = []
        for row in rows:
            entity_values = sorted(
                values.get(row["id"], []),
                key=lambda key_value: (categories[key_value[0]], key_value[0]),
            )
            entities.append(
                {
                    "id": row["id"],
                    "kind": row["kind"],
                    "name": row["name"],
                    "attributes": [
                        {"key": key, "value": value}
                        for key, value in entity_values
                    ],
                    "facts": facts.get(row["id"], []),
                }
            )
        return entities
