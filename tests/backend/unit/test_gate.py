from sillytavern_rpg_engine.domain.models import EntityKind
from sillytavern_rpg_engine.orchestration.extraction import ExtractedOperation
from sillytavern_rpg_engine.orchestration.gate import (
    parse_proposal_command,
    run_gate,
    run_proposal_command,
)
from sillytavern_rpg_engine.orchestration.intent import Intent
from sillytavern_rpg_engine.services.attributes import DefineAttributeOperation
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)
from sillytavern_rpg_engine.services.proposals import ProposalService


def _world(database):
    ids = iter(f"evt-{i}" for i in range(1000))
    campaigns = CampaignService(database, id_factory=ids.__next__,
                                clock=lambda: "2026-08-13T00:00:00Z")
    campaigns.create_campaign("c1", "测试")
    entities = EntityAttributeService(database, campaigns.mutation_engine)
    entities.apply_explicit("c1", "main", 0, CreateEntityOperation(
        entity_id="erin", kind=EntityKind.CHARACTER, name="艾琳",
    ))
    prop_ids = iter(["prop-1"] + [f"prop-{i}" for i in range(2, 100)])
    proposals = ProposalService(database, campaigns.mutation_engine,
                                id_factory=prop_ids.__next__)
    return campaigns, entities, proposals


def _define_alchemy():
    from sillytavern_rpg_engine.domain.models import (
        AttributeDefinition, AttributeType, Audience, DisplayType,
    )
    return DefineAttributeOperation(AttributeDefinition(
        campaign_id="c1", key="alchemy", label="炼金术", category="skill",
        value_type=AttributeType.NUMBER, display=DisplayType.BAR,
        audiences=frozenset(Audience), minimum=0, maximum=100,
    ))


def test_parse_proposal_command():
    assert parse_proposal_command("确认提案 p-102") == ("approve", "p-102")
    assert parse_proposal_command("reject abc") == ("reject", "abc")
    assert parse_proposal_command("继续剧情") is None


def test_explicit_operations_apply_immediately(database):
    campaigns, entities, proposals = _world(database)
    entities.apply_explicit("c1", "main", 1, _define_alchemy())
    result = run_gate(
        database, campaigns.mutation_engine, proposals, "c1", "main",
        Intent.EXPLICIT_CHANGE,
        (ExtractedOperation(
            {"kind": "set_attribute", "entity_id": "erin",
             "attribute_key": "alchemy", "value": 35, "turn_id": None},
            "玩家指令",
        ),),
    )
    assert len(result.applied) == 1 and not result.pending and not result.dropped
    assert "已应用" in result.message
    with database.connect() as connection:
        row = connection.execute(
            "SELECT value_json FROM attribute_values"
            " WHERE entity_id = 'erin' AND attribute_key = 'alchemy'"
        ).fetchone()
        assert row["value_json"] == "35"


def test_action_operations_become_pending_proposals(database):
    campaigns, entities, proposals = _world(database)
    result = run_gate(
        database, campaigns.mutation_engine, proposals, "c1", "main",
        Intent.ACTION,
        (ExtractedOperation(
            {"kind": "set_attribute", "entity_id": "erin",
             "attribute_key": "alchemy", "value": 40, "turn_id": None},
            "剧情推断",
        ),),
    )
    assert not result.applied and len(result.pending) == 1
    assert result.pending[0]["proposal_id"] == "prop-1"
    assert "待确认" in result.message


def test_invalid_operation_is_dropped_with_error(database):
    campaigns, entities, proposals = _world(database)
    result = run_gate(
        database, campaigns.mutation_engine, proposals, "c1", "main",
        Intent.EXPLICIT_CHANGE,
        (ExtractedOperation({"kind": "set_attribute", "entity_id": "erin",
                             "attribute_key": "alchemy", "value": "abc",
                             "turn_id": None}, ""),
        ),
    )
    assert not result.applied and len(result.dropped) == 1
    assert "拒绝" in result.message


def test_proposal_command_approve_and_reject(database):
    campaigns, entities, proposals = _world(database)
    proposals.create("c1", "main", {
        "kind": "set_attribute", "entity_id": "erin", "attribute_key": "alchemy",
        "value": 40, "turn_id": None,
    }, "剧情推断")
    rejected = run_proposal_command(database, proposals, "reject", "prop-1")
    assert "已拒绝" in rejected.message
    again = run_proposal_command(database, proposals, "approve", "prop-1")
    assert "not pending" in again.message
    missing = run_proposal_command(database, proposals, "approve", "nope")
    assert "not found" in missing.message
