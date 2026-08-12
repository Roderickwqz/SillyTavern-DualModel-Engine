"""Deterministic, credential-free campaign snapshot builder."""

import json
from typing import Any

from ..domain.errors import NotFoundError


class SnapshotBuilder:
    """Build a JSON-dumpable snapshot dict of one campaign's state.

    Ordering is deterministic:
    - entities: by normalized_name
    - entity aliases: by normalized_alias
    - attribute values (per entity): by attribute_key
    - attribute definitions: by (category, key)

    The snapshot excludes audit/outbox state and credentials.
    """

    def build(self, connection, campaign_id: str, branch_id: str) -> dict[str, Any]:
        campaign = self._campaign(connection, campaign_id)
        entities = self._entities(connection, campaign_id)
        definitions = self._definitions(connection, campaign_id)
        facts = self._facts(connection, campaign_id)
        relationships = self._relationships(connection, campaign_id)
        return {
            "campaign": campaign,
            "entities": entities,
            "attribute_definitions": definitions,
            "facts": facts,
            "relationships": relationships,
        }

    def _campaign(self, connection, campaign_id: str) -> dict[str, Any]:
        row = connection.execute(
            "SELECT id, name, state_version, rules_mode, rules_enabled,"
            " rules_version, custom_preset_id FROM campaigns WHERE id = ?",
            (campaign_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"campaign {campaign_id} not found")
        return {
            "id": row["id"],
            "name": row["name"],
            "state_version": row["state_version"],
            "rules": {
                "mode": row["rules_mode"],
                "enabled": bool(row["rules_enabled"]),
                "version": row["rules_version"],
                "custom_preset_id": row["custom_preset_id"],
            },
        }

    def _entities(self, connection, campaign_id: str) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT id, kind, name, age_status FROM entities"
            " WHERE campaign_id = ? ORDER BY normalized_name",
            (campaign_id,),
        ).fetchall()
        aliases = self._aliases_by_entity(connection, campaign_id)
        values = self._values_by_entity(connection, campaign_id)
        entities = []
        for row in rows:
            entity_id = row["id"]
            entities.append(
                {
                    "id": entity_id,
                    "campaign_id": campaign_id,
                    "kind": row["kind"],
                    "name": row["name"],
                    "age_status": row["age_status"],
                    "aliases": aliases.get(entity_id, []),
                    "attributes": values.get(entity_id, []),
                }
            )
        return entities

    def _aliases_by_entity(
        self, connection, campaign_id: str
    ) -> dict[str, list[str]]:
        rows = connection.execute(
            "SELECT entity_id, alias FROM entity_aliases"
            " WHERE campaign_id = ? ORDER BY normalized_alias",
            (campaign_id,),
        ).fetchall()
        aliases: dict[str, list[str]] = {}
        for row in rows:
            aliases.setdefault(row["entity_id"], []).append(row["alias"])
        return aliases

    def _values_by_entity(
        self, connection, campaign_id: str
    ) -> dict[str, list[dict[str, Any]]]:
        rows = connection.execute(
            "SELECT entity_id, attribute_key, value_json, state_version,"
            " updated_turn_id FROM attribute_values WHERE campaign_id = ?"
            " ORDER BY attribute_key",
            (campaign_id,),
        ).fetchall()
        values: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            values.setdefault(row["entity_id"], []).append(
                {
                    "key": row["attribute_key"],
                    "value": json.loads(row["value_json"]),
                    "state_version": row["state_version"],
                    "updated_turn_id": row["updated_turn_id"],
                }
            )
        return values

    def _relationships(self, connection, campaign_id: str) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT from_entity_id, to_entity_id, dimension, value_json,"
            " audiences_json, updated_turn_id FROM relationships"
            " WHERE campaign_id = ?"
            " ORDER BY from_entity_id, to_entity_id, dimension",
            (campaign_id,),
        ).fetchall()
        return [
            {
                "from_entity_id": row["from_entity_id"],
                "to_entity_id": row["to_entity_id"],
                "dimension": row["dimension"],
                "value": json.loads(row["value_json"]),
                "audiences": json.loads(row["audiences_json"]),
                "updated_turn_id": row["updated_turn_id"],
            }
            for row in rows
        ]

    def _facts(self, connection, campaign_id: str) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT entity_id, fact_type, fact_key, content, importance,"
            " audiences_json, valid_from, turn_id, source FROM facts"
            " WHERE campaign_id = ? AND valid_until IS NULL"
            " ORDER BY entity_id, fact_key",
            (campaign_id,),
        ).fetchall()
        return [
            {
                "entity_id": row["entity_id"],
                "fact_type": row["fact_type"],
                "fact_key": row["fact_key"],
                "content": row["content"],
                "importance": row["importance"],
                "audiences": json.loads(row["audiences_json"]),
                "valid_from": row["valid_from"],
                "turn_id": row["turn_id"],
                "source": row["source"],
            }
            for row in rows
        ]

    def _definitions(self, connection, campaign_id: str) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT key, label, category, value_type, display, audiences_json,"
            " minimum, maximum, enum_values_json, unit FROM attribute_definitions"
            " WHERE campaign_id = ? ORDER BY category, key",
            (campaign_id,),
        ).fetchall()
        return [
            {
                "key": row["key"],
                "label": row["label"],
                "category": row["category"],
                "value_type": row["value_type"],
                "display": row["display"],
                "audiences": json.loads(row["audiences_json"]),
                "minimum": row["minimum"],
                "maximum": row["maximum"],
                "enum_values": json.loads(row["enum_values_json"]),
                "unit": row["unit"],
            }
            for row in rows
        ]
