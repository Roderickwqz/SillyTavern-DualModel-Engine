import pytest

from sillytavern_rpg_engine.domain.errors import StaleStateError
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
from sillytavern_rpg_engine.services.proposals import ProposalService


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
