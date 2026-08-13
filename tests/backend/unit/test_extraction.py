import json

from sillytavern_rpg_engine.domain.models import EntityKind
from sillytavern_rpg_engine.llm import ScriptedLLMClient
from sillytavern_rpg_engine.orchestration.extraction import extract_operations
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)


def _world(database):
    campaigns = CampaignService(
        database,
        id_factory=iter(f"evt-{i}" for i in range(100)).__next__,
        clock=lambda: "2026-08-13T00:00:00Z",
    )
    campaigns.create_campaign("c1", "测试")
    entities = EntityAttributeService(database, campaigns.mutation_engine)
    entities.apply_explicit("c1", "main", 0, CreateEntityOperation(
        entity_id="erin", kind=EntityKind.CHARACTER, name="艾琳",
    ))
    ids = iter(f"gen-{n}" for n in range(1, 100))
    return lambda: next(ids)


def _extract(database, client, id_factory, narrative="剧情草稿"):
    with database.connect() as connection:
        return extract_operations(
            client,
            campaign_id="c1", player_text="玩家输入", narrative=narrative,
            context={"entities": []}, turn_id="turn-1",
            connection=connection, id_factory=id_factory,
        )


def test_valid_operations_are_repaired_and_decodable(database):
    id_factory = _world(database)
    client = ScriptedLLMClient([json.dumps({
        "operations": [
            {"kind": "assert_fact", "fact_id": "", "entity_id": "艾琳",
             "fact_type": "general", "fact_key": "职业", "content": "炼金术师",
             "importance": 3, "audiences": ["engine", "narrator"],
             "turn_id": None, "reason": "自我介绍"},
            {"kind": "set_rules", "mode": "dnd-2024", "enabled": True,
             "version": None, "custom_preset_id": None},
        ]
    }, ensure_ascii=False)])
    result = _extract(database, client, id_factory)
    assert result.error is None
    assert len(result.operations) == 1
    operation = result.operations[0]
    assert operation.payload["fact_id"] == "gen-1"
    assert operation.payload["entity_id"] == "erin"
    assert operation.payload["turn_id"] == "turn-1"
    assert operation.reason == "自我介绍"
    assert "set_rules" in result.drops[0]["error"]


def test_invalid_json_gets_one_correction_retry(database):
    id_factory = _world(database)
    client = ScriptedLLMClient([
        "这不是 JSON",
        json.dumps({"operations": []}),
    ])
    result = _extract(database, client, id_factory)
    assert result.error is None
    assert result.operations == ()
    assert len(client.requests) == 2
    assert "不是 JSON" in client.requests[1][-2].content  # 回显错误输出


def test_second_failure_yields_error_and_no_operations(database):
    id_factory = _world(database)
    client = ScriptedLLMClient(["坏", "还是坏"])
    result = _extract(database, client, id_factory)
    assert result.error is not None
    assert result.operations == ()


def test_unknown_entity_reference_drops_only_that_operation(database):
    id_factory = _world(database)
    client = ScriptedLLMClient([json.dumps({
        "operations": [
            {"kind": "set_attribute", "entity_id": "不存在", "attribute_key": "hp",
             "value": 1, "turn_id": None},
            {"kind": "set_attribute", "entity_id": "erin", "attribute_key": "hp",
             "value": 1, "turn_id": None},
        ]
    })])
    result = _extract(database, client, id_factory)
    assert len(result.operations) == 1
    assert result.operations[0].payload["entity_id"] == "erin"
    assert len(result.drops) == 1


def test_missing_reference_field_drops_only_that_operation(database):
    id_factory = _world(database)
    client = ScriptedLLMClient([json.dumps({
        "operations": [
            {"kind": "set_attribute", "attribute_key": "hp",
             "value": 1, "turn_id": None},
            {"kind": "set_attribute", "entity_id": "erin", "attribute_key": "hp",
             "value": 1, "turn_id": None},
        ]
    })])
    result = _extract(database, client, id_factory)
    assert result.error is None
    assert len(result.operations) == 1
    assert result.operations[0].payload["entity_id"] == "erin"
    assert len(result.drops) == 1
    assert "entity_id" in result.drops[0]["error"]


def test_empty_location_reference_drops_the_memory_event(database):
    id_factory = _world(database)
    client = ScriptedLLMClient([json.dumps({
        "operations": [
            {"kind": "record_memory_event", "event_id": "",
             "event_type": "general", "content": "在空地交谈",
             "importance": 2, "audiences": ["engine"],
             "participant_entity_ids": ["erin"], "location_entity_id": "",
             "turn_id": None, "source": "chat"},
        ]
    })])
    result = _extract(database, client, id_factory)
    assert result.error is None
    assert result.operations == ()
    assert len(result.drops) == 1
