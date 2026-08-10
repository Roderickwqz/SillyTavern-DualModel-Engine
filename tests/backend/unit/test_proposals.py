import json

import pytest

from sillytavern_rpg_engine.domain.errors import StaleStateError, ValidationError
from sillytavern_rpg_engine.domain.models import (
    AgeStatus,
    AttributeDefinition,
    AttributeType,
    Audience,
    DisplayType,
    EntityKind,
    ProposalStatus,
)
from sillytavern_rpg_engine.domain.operations import CompositeOperation
from sillytavern_rpg_engine.services.attributes import DefineAttributeOperation, SetAttributeOperation
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import CreateEntityOperation, EntityAttributeService
from sillytavern_rpg_engine.services.proposals import OperationCodec, ProposalService


@pytest.fixture
def prepared_alchemy(database):
    campaigns = CampaignService(
        database,
        id_factory=iter(f"proposal-event-{index}" for index in range(20)).__next__,
        clock=lambda: "2026-08-10T00:00:00Z",
    )
    campaigns.create_campaign("c1", "Campaign")
    state = EntityAttributeService(database, campaigns.mutation_engine)
    state.apply_explicit("c1", "main", 0, CompositeOperation((
        CreateEntityOperation("erin", EntityKind.CHARACTER, "艾琳", AgeStatus.ADULT, ()),
        DefineAttributeOperation(AttributeDefinition(
            campaign_id="c1", key="alchemy", label="炼金术", category="skill",
            value_type=AttributeType.NUMBER, display=DisplayType.BAR,
            audiences=frozenset({Audience.ENGINE, Audience.PLAYER_UI}),
            minimum=0, maximum=100,
        ), ()),
        SetAttributeOperation("erin", "alchemy", 35, "turn-1"),
    )))
    return state, campaigns.mutation_engine


def test_inferred_change_stays_pending_until_approved(database, prepared_alchemy):
    _, mutation_engine = prepared_alchemy
    service = ProposalService(database, mutation_engine, id_factory=lambda: "p-1", clock=lambda: "2026-08-10T00:00:00Z")
    proposal = service.create(
        campaign_id="c1",
        branch_id="main",
        operation={"kind": "set_attribute", "entity_id": "erin", "attribute_key": "alchemy", "value": 40, "turn_id": "turn-2"},
        reason="艾琳表现出专业药剂分析能力",
    )
    assert proposal.status is ProposalStatus.PENDING
    result = service.approve("p-1", expected_version=1)
    assert result.snapshot["entities"][0]["attributes"][0]["value"] == 40


def test_proposal_is_marked_stale_when_campaign_advanced(database, prepared_alchemy):
    state, mutation_engine = prepared_alchemy
    service = ProposalService(database, mutation_engine, id_factory=lambda: "p-2", clock=lambda: "2026-08-10T00:00:00Z")
    service.create("c1", "main", {"kind": "set_attribute", "entity_id": "erin", "attribute_key": "alchemy", "value": 50, "turn_id": None}, "candidate")
    state.apply_explicit("c1", "main", 1, SetAttributeOperation("erin", "alchemy", 36, "turn-2"))
    with pytest.raises(StaleStateError):
        service.approve("p-2", expected_version=1)
    assert service.get("p-2").status is ProposalStatus.STALE


def test_rejected_proposal_cannot_be_resurrected(database, prepared_alchemy):
    state, mutation_engine = prepared_alchemy
    service = ProposalService(database, mutation_engine, id_factory=lambda: "p-3", clock=lambda: "2026-08-10T00:00:00Z")
    service.create("c1", "main", {"kind": "set_attribute", "entity_id": "erin", "attribute_key": "alchemy", "value": 45, "turn_id": None}, "candidate")
    rejected = service.reject("p-3")
    assert rejected.status is ProposalStatus.REJECTED
    with pytest.raises(ValidationError):
        service.approve("p-3", expected_version=1)
    assert service.get("p-3").status is ProposalStatus.REJECTED
    with database.connect() as connection:
        row = connection.execute(
            "SELECT value_json FROM attribute_values WHERE entity_id = ?"
            " AND attribute_key = ?",
            ("erin", "alchemy"),
        ).fetchone()
    assert json.loads(row["value_json"]) == 35


def test_codec_rejects_unknown_kind_and_bad_key_sets():
    with pytest.raises(ValidationError, match="unsupported operation kind"):
        OperationCodec.decode({"kind": "drop_table", "target": "campaigns"})
    with pytest.raises(ValidationError, match="extra keys"):
        OperationCodec.decode({"kind": "set_attribute", "entity_id": "erin", "attribute_key": "alchemy", "value": 1, "turn_id": None, "sneaky": 1})
    with pytest.raises(ValidationError, match="missing keys"):
        OperationCodec.decode({"kind": "set_attribute", "entity_id": "erin", "attribute_key": "alchemy", "value": 1})


def test_codec_rejects_wrong_field_types():
    with pytest.raises(ValidationError, match="aliases must be a list"):
        OperationCodec.decode({"kind": "create_entity", "entity_id": "phil", "entity_kind": "character", "name": "Phil", "age_status": "adult", "aliases": "phil"})
    with pytest.raises(ValidationError, match="enum_values must be a list"):
        OperationCodec.decode({"kind": "define_attribute", "campaign_id": "c1", "key": "x", "label": "X", "category": "skill", "value_type": "number", "display": "bar", "audiences": ["engine"], "minimum": 0, "maximum": 10, "enum_values": "one,two", "unit": None, "aliases": []})
    with pytest.raises(ValidationError, match="audiences must be a list"):
        OperationCodec.decode({"kind": "define_attribute", "campaign_id": "c1", "key": "x", "label": "X", "category": "skill", "value_type": "number", "display": "bar", "audiences": "engine", "minimum": 0, "maximum": 10, "enum_values": [], "unit": None, "aliases": []})
    with pytest.raises(ValidationError, match="enabled must be a boolean"):
        OperationCodec.decode({"kind": "set_rules", "mode": "narrative", "enabled": "true", "version": None, "custom_preset_id": None})
    with pytest.raises(ValidationError, match="enabled must be a boolean"):
        OperationCodec.decode({"kind": "set_rules", "mode": "narrative", "enabled": 0, "version": None, "custom_preset_id": None})
    with pytest.raises(ValidationError, match="minimum must be a number"):
        OperationCodec.decode({"kind": "define_attribute", "campaign_id": "c1", "key": "x", "label": "X", "category": "skill", "value_type": "number", "display": "bar", "audiences": ["engine"], "minimum": "5", "maximum": 10, "enum_values": [], "unit": None, "aliases": []})
    with pytest.raises(ValidationError, match="maximum must be a number"):
        OperationCodec.decode({"kind": "define_attribute", "campaign_id": "c1", "key": "x", "label": "X", "category": "skill", "value_type": "number", "display": "bar", "audiences": ["engine"], "minimum": 0, "maximum": True, "enum_values": [], "unit": None, "aliases": []})


def test_set_rules_proposal_approves_end_to_end(database, prepared_alchemy):
    _, mutation_engine = prepared_alchemy
    service = ProposalService(database, mutation_engine, id_factory=lambda: "p-4", clock=lambda: "2026-08-10T00:00:00Z")
    service.create("c1", "main", {"kind": "set_rules", "mode": "dnd-2024", "enabled": True, "version": "2024", "custom_preset_id": None}, "switch to dnd")
    result = service.approve("p-4", expected_version=1)
    assert result.state_version == 2
    assert result.snapshot["campaign"]["rules"]["mode"] == "dnd-2024"
    assert result.snapshot["campaign"]["rules"]["enabled"] is True
    assert service.get("p-4").status is ProposalStatus.APPROVED
