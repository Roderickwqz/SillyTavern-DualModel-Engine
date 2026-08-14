import json
import re

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
from sillytavern_rpg_engine.services.attributes import (
    DefineAttributeOperation, SetAttributeOperation,
)
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation, EntityAttributeService,
)

NARRATOR_CFG = ModelConfig("http://x/v1", "", "narrator-model", 0.8, 5.0, 512)


def _world(database):
    ids = iter(f"evt-{i}" for i in range(200))
    campaigns = CampaignService(database, id_factory=ids.__next__,
                                clock=lambda: "2026-08-14T00:00:00Z")
    campaigns.create_campaign("c1", "Compat")
    attrs = EntityAttributeService(database, campaigns.mutation_engine)
    attrs.apply_explicit("c1", "main", 0, CreateEntityOperation(
        entity_id="erin", kind=EntityKind.CHARACTER, name="艾琳",
    ))
    attrs.apply_explicit("c1", "main", 1, CreateEntityOperation(
        entity_id="player", kind=EntityKind.CHARACTER, name="旅人",
    ))
    attrs.apply_explicit("c1", "main", 2, DefineAttributeOperation(
        AttributeDefinition(
            campaign_id="c1", key="alchemy", label="炼金术", category="skill",
            value_type=AttributeType.NUMBER, display=DisplayType.BAR,
            audiences=frozenset({Audience.PLAYER_UI}), minimum=0, maximum=100,
        ),
    ))
    attrs.apply_explicit("c1", "main", 3, SetAttributeOperation(
        "erin", "alchemy", 35, None,
    ))
    return campaigns


def test_chat_tracker_json_is_compat_shaped(database, tmp_path):
    _world(database)
    narrator = ScriptedLLMClient([
        "艾琳检查炼金设备。",                      # narrate
        json.dumps({"operations": []}),            # extract (unused for query turns)
    ])
    settings = Settings(
        database_path=":memory:", host="127.0.0.1", port=8000,
        max_history_messages=40, narrator=NARRATOR_CFG, critic=None,
    )
    client = TestClient(create_app(settings, database, narrator))
    response = client.post("/v1/chat/completions", json={
        "campaign_id": "c1",
        "messages": [{"role": "user", "content": "查看艾琳"}],
    })
    assert response.status_code == 200
    content = response.json()["choices"][0]["message"]["content"]
    match = re.search(r"```json\s*\n(.*?)```", content, re.DOTALL)
    assert match, "tracker fence missing"
    tracker = json.loads(match.group(1))
    assert tracker["rules"]["mode"] == "narrative"
    assert "state_version" in tracker
    erin = next(c for c in tracker["characters"] if c["name"] == "艾琳")
    assert erin["attributes"][0]["key"] == "alchemy"
    assert erin["attributes"][0]["display"] == "bar"
