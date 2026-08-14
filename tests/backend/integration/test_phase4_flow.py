import json

from fastapi.testclient import TestClient

from sillytavern_rpg_engine.config import ModelConfig, Settings
from sillytavern_rpg_engine.domain.models import (
    AttributeDefinition,
    AttributeType,
    Audience,
    DisplayType,
    EntityKind,
)
from sillytavern_rpg_engine.llm import ScriptedLLMClient
from sillytavern_rpg_engine.server.app import create_app
from sillytavern_rpg_engine.services.attributes import DefineAttributeOperation
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)

NARRATOR_CFG = ModelConfig("http://x/v1", "", "narrator-model", 0.8, 5.0, 512)

TRACKER_INSTRUCTION = (
    "You must update the Tracker in every reply.\n"
    "```json\n{\"userStats\": {\"stats\": []}, \"infoBox\": {}}\n```"
)


def test_phase4_full_flow(database):
    ids = iter(f"evt-{i}" for i in range(1000))
    campaigns = CampaignService(database, id_factory=ids.__next__,
                                clock=lambda: "2026-08-13T00:00:00Z")
    campaigns.create_campaign("c1", "测试")
    entities = EntityAttributeService(database, campaigns.mutation_engine)
    entities.apply_explicit("c1", "main", 0, CreateEntityOperation(
        entity_id="erin", kind=EntityKind.CHARACTER, name="艾琳",
    ))
    entities.apply_explicit("c1", "main", 1, DefineAttributeOperation(
        AttributeDefinition(
            campaign_id="c1", key="alchemy", label="炼金术", category="skill",
            value_type=AttributeType.NUMBER, display=DisplayType.BAR,
            audiences=frozenset(Audience), minimum=0, maximum=100,
        )
    ))
    narrator = ScriptedLLMClient([
        "你推门而入,艾琳正在蒸馏药剂。",                    # turn 1 narrate
        json.dumps({"operations": [{                        # turn 1 extract
            "kind": "set_attribute", "entity_id": "erin",
            "attribute_key": "alchemy", "value": 36, "turn_id": None,
            "reason": "艾琳在练习",
        }]}),
        json.dumps({"operations": [{                        # turn 3 extract
            "kind": "set_attribute", "entity_id": "erin",
            "attribute_key": "alchemy", "value": 35, "turn_id": None,
        }]}),
        "艾琳的炼金术是 35,最高 100。",                     # turn 4 answer
    ])
    settings = Settings(
        database_path=":memory:", host="127.0.0.1", port=8000,
        max_history_messages=40, narrator=NARRATOR_CFG, critic=None,
    )
    client = TestClient(create_app(settings, database, narrator))

    history: list[dict] = []

    def chat(text, **extra):
        payload = {"campaign_id": "c1",
                   "messages": [{"role": "system", "content": TRACKER_INSTRUCTION}]
                               + history
                               + [{"role": "user", "content": text}]}
        payload.update(extra)
        response = client.post("/v1/chat/completions", json=payload)
        assert response.status_code == 200
        content = response.json()["choices"][0]["message"]["content"]
        history.append({"role": "assistant", "content": content})
        return content

    # Turn 1: action -> narrative + one pending proposal + tracker block.
    content = chat("我走进炼金铺。")
    assert "你推门而入" in content and "待确认" in content
    assert "```json" in content
    with database.connect() as connection:
        proposal_id = connection.execute(
            "SELECT id FROM pending_proposals WHERE status = 'pending'"
        ).fetchone()["id"]

    # Turn 2: confirm the proposal -> applied.
    content = chat(f"确认提案 {proposal_id}")
    assert "已确认并应用" in content
    with database.connect() as connection:
        assert connection.execute(
            "SELECT value_json FROM attribute_values"
            " WHERE entity_id = 'erin' AND attribute_key = 'alchemy'"
        ).fetchone()["value_json"] == "36"

    # Turn 3: explicit change -> applied immediately.
    content = chat("把艾琳的炼金术调整为35")
    assert "已应用 1 项变更" in content
    with database.connect() as connection:
        assert connection.execute(
            "SELECT value_json FROM attribute_values"
            " WHERE entity_id = 'erin' AND attribute_key = 'alchemy'"
        ).fetchone()["value_json"] == "35"

    # Turn 4: read-only query -> answer, no new turn row beyond turns 1-3.
    with database.connect() as connection:
        before = connection.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
    content = chat("查询艾琳的炼金术")
    assert "35" in content
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == before
        # Tracker instructions were stripped, never stored as memory.
        contents = " ".join(
            row["content"] for row in connection.execute(
                "SELECT content FROM memory_events"
            ).fetchall()
        )
        assert "userStats" not in contents

    # Turn 5: unknown campaign -> 404 OpenAI envelope.
    response = client.post("/v1/chat/completions", json={
        "campaign_id": "ghost",
        "messages": [{"role": "user", "content": "x"}],
    })
    assert response.status_code == 404
    assert response.json()["error"]["type"] == "not_found"
