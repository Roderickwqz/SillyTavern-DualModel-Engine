"""Tracker JSON presentation built from committed, player-visible state."""

import json
from typing import Any

from ..domain.models import Audience, EntityKind
from ..persistence.database import Database
from ..persistence.repositories import CampaignRepository
from ..services.mutations import MutationEngine
from ..services.proposals import ProposalService


class TrackerPresenter:
    """Render the spec §12.2 Tracker payload; read-only, audience-filtered."""

    def __init__(self, database: Database):
        self.database = database
        self.campaign_repository = CampaignRepository()
        self.proposals = ProposalService(database, MutationEngine(database))

    def build(self, campaign_id: str, branch_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            campaign = self.campaign_repository.require(connection, campaign_id)
            definitions = connection.execute(
                "SELECT key, label, category, value_type, display, maximum,"
                " audiences_json FROM attribute_definitions"
                " WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchall()
            values = connection.execute(
                "SELECT entity_id, attribute_key, value_json"
                " FROM attribute_values WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchall()
            entities = connection.execute(
                "SELECT id, kind, name FROM entities WHERE campaign_id = ?"
                " ORDER BY normalized_name",
                (campaign_id,),
            ).fetchall()
        visible = {
            row["key"]: row for row in definitions
            if Audience.PLAYER_UI.value in json.loads(row["audiences_json"])
        }
        by_entity: dict[str, list] = {}
        for row in values:
            if row["attribute_key"] in visible:
                by_entity.setdefault(row["entity_id"], []).append(row)
        characters = []
        for entity in entities:
            if entity["kind"] != EntityKind.CHARACTER.value:
                continue
            attributes = []
            for value in sorted(
                by_entity.get(entity["id"], []),
                key=lambda row: row["attribute_key"],
            ):
                definition = visible[value["attribute_key"]]
                attributes.append({
                    "key": definition["key"],
                    "label": definition["label"],
                    "category": definition["category"],
                    "type": definition["value_type"],
                    "value": json.loads(value["value_json"]),
                    "max": definition["maximum"],
                    "display": definition["display"],
                })
            characters.append({
                "name": entity["name"],
                "details": {},
                "relationship": {},
                "attributes": attributes,
            })
        rules = {
            "mode": campaign.rules.mode.value,
            "enabled": campaign.rules.enabled,
            "version": campaign.rules.version,
        }
        if campaign.rules.custom_preset_id is not None:
            rules["custom_preset_id"] = campaign.rules.custom_preset_id
        return {
            "rules": rules,
            "userStats": {
                "stats": [], "status": {}, "skills": [],
                "inventory": {}, "quests": {}, "attributes": [],
            },
            "infoBox": {},
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

    def render(self, campaign_id: str, branch_id: str) -> str:
        """The single fenced Tracker block appended to every response."""
        return (
            "```json\n"
            + json.dumps(
                self.build(campaign_id, branch_id), ensure_ascii=False, indent=2
            )
            + "\n```"
        )
