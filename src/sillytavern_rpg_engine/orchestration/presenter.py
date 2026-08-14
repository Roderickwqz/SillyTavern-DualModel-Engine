"""Tracker JSON presentation built from committed, player-visible state."""

import json
from typing import Any

from ..domain.models import Audience, EntityKind
from ..persistence.database import Database
from ..services.projection import ProjectionService
from ..services.proposals import ProposalService
from ..services.mutations import MutationEngine

_PLAYER_ENTITY_ID = "player"
_SCENE_ENTITY_ID = "_scene"
_INFOBOX_ATTR_KEYS = frozenset({
    "location", "date", "time", "weather", "temperature", "recent_events",
})


def _attribute_dict(definition: dict[str, Any], value: Any) -> dict[str, Any]:
    return {
        "key": definition["key"],
        "label": definition["label"],
        "category": definition["category"],
        "type": definition["value_type"],
        "value": value,
        "max": definition["maximum"],
        "display": definition["display"],
    }


def _resolve_player_entity_id(entities: list[dict[str, Any]]) -> str | None:
    for entity in entities:
        if entity["id"] == _PLAYER_ENTITY_ID:
            return entity["id"]
    for entity in entities:
        if entity["kind"] == EntityKind.CHARACTER.value:
            return entity["id"]
    return None


def _build_info_box(
    definitions: dict[str, dict[str, Any]],
    values_by_entity: dict[str, list],
) -> dict[str, Any]:
    info_box: dict[str, Any] = {}
    for row in values_by_entity.get(_SCENE_ENTITY_ID, []):
        definition = definitions.get(row["attribute_key"])
        if definition is None or definition["category"] != "scene":
            continue
        if definition["key"] in _INFOBOX_ATTR_KEYS:
            info_box[definition["key"]] = json.loads(row["value_json"])
    return info_box


def _relationship_badge(
    relationships: list[dict[str, Any]], player_id: str | None, entity_id: str,
) -> dict[str, Any]:
    if player_id is None:
        return {}
    for rel in relationships:
        if rel["to"] == player_id:
            return {rel["dimension"]: rel["value"]}
    return {}


class TrackerPresenter:
    """Render the spec §12.2 Tracker payload; read-only, audience-filtered."""

    def __init__(self, database: Database):
        self.database = database
        self.projection = ProjectionService(database)
        self.proposals = ProposalService(database, MutationEngine(database))

    def build(self, campaign_id: str, branch_id: str) -> dict[str, Any]:
        projection = self.projection.for_audience(
            campaign_id, branch_id, Audience.PLAYER_UI,
        )
        with self.database.connect() as connection:
            definitions = {
                row["key"]: row
                for row in connection.execute(
                    "SELECT key, label, category, value_type, display, maximum,"
                    " audiences_json FROM attribute_definitions WHERE campaign_id = ?",
                    (campaign_id,),
                ).fetchall()
                if Audience.PLAYER_UI.value in json.loads(row["audiences_json"])
            }
            value_rows = connection.execute(
                "SELECT entity_id, attribute_key, value_json"
                " FROM attribute_values WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchall()
        values_by_entity: dict[str, list] = {}
        for row in value_rows:
            if row["attribute_key"] in definitions:
                values_by_entity.setdefault(row["entity_id"], []).append(row)

        player_id = _resolve_player_entity_id(projection["entities"])
        user_attributes: list[dict[str, Any]] = []
        if player_id is not None:
            for row in sorted(
                values_by_entity.get(player_id, []),
                key=lambda item: (definitions[item["attribute_key"]]["category"],
                                  item["attribute_key"]),
            ):
                user_attributes.append(_attribute_dict(
                    definitions[row["attribute_key"]],
                    json.loads(row["value_json"]),
                ))

        characters = []
        for entity in projection["entities"]:
            if entity["kind"] != EntityKind.CHARACTER.value:
                continue
            if entity["id"] == player_id:
                continue
            attrs = []
            for item in entity["attributes"]:
                definition = definitions.get(item["key"])
                if definition is None:
                    continue
                attrs.append(_attribute_dict(definition, item["value"]))
            details = {
                fact["fact_key"]: fact["content"]
                for fact in entity.get("facts", [])
                if fact["fact_type"] in {"identity", "appearance"}
            }
            characters.append({
                "name": entity["name"],
                "details": details,
                "relationship": _relationship_badge(
                    entity.get("relationships", []), player_id, entity["id"],
                ),
                "attributes": attrs,
            })

        rules = dict(projection["rules"])
        if rules.get("custom_preset_id") is None:
            rules.pop("custom_preset_id", None)
        payload: dict[str, Any] = {
            "rules": rules,
            "state_version": projection["state_version"],
            "userStats": {
                "stats": [],
                "status": {},
                "skills": [],
                "inventory": {},
                "quests": {},
                "attributes": user_attributes,
            },
            "infoBox": _build_info_box(definitions, values_by_entity),
            "characters": characters,
            "pending_proposals": [
                {
                    "id": proposal.id,
                    "operation": proposal.operation,
                    "reason": proposal.reason,
                    "base_state_version": proposal.base_state_version,
                }
                for proposal in self.proposals.pending(campaign_id, branch_id)
            ],
        }
        if projection.get("combat") is not None:
            payload["combat"] = projection["combat"]
        return payload

    def render(self, campaign_id: str, branch_id: str) -> str:
        """The single fenced Tracker block appended to every response."""
        return (
            "```json\n"
            + json.dumps(self.build(campaign_id, branch_id), ensure_ascii=False, indent=2)
            + "\n```"
        )
