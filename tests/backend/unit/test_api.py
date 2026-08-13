import json

from fastapi.testclient import TestClient

from sillytavern_rpg_engine.config import ModelConfig, Settings
from sillytavern_rpg_engine.llm import ScriptedLLMClient
from sillytavern_rpg_engine.server.app import create_app
from sillytavern_rpg_engine.services.campaigns import CampaignService

NARRATOR_CFG = ModelConfig("http://x/v1", "", "narrator-model", 0.8, 5.0, 512)


def _client(database, narrator):
    settings = Settings(
        database_path=":memory:", host="127.0.0.1", port=8000,
        max_history_messages=40, narrator=NARRATOR_CFG, critic=None,
    )
    return TestClient(create_app(settings, database, narrator))


def _campaign(database):
    ids = iter(f"evt-{i}" for i in range(1000))
    campaigns = CampaignService(database, id_factory=ids.__next__,
                                clock=lambda: "2026-08-13T00:00:00Z")
    campaigns.create_campaign("c1", "测试")


def test_models_and_health(database):
    _campaign(database)
    client = _client(database, ScriptedLLMClient([]))
    models = client.get("/v1/models")
    assert models.status_code == 200
    assert models.json()["data"][0]["id"] == "narrator-model"
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["schema_version"] >= 5
    assert health.json()["models"] == {"narrator": "configured",
                                       "critic": "disabled"}


def test_chat_completion_roundtrip(database):
    _campaign(database)
    narrator = ScriptedLLMClient(["你好,冒险者。", json.dumps({"operations": []})])
    client = _client(database, narrator)
    response = client.post("/v1/chat/completions", json={
        "campaign_id": "c1",
        "messages": [{"role": "user", "content": "我四处看看。"}],
    })
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    content = body["choices"][0]["message"]["content"]
    assert "你好,冒险者。" in content and "```json" in content


def test_errors_use_openai_envelope(database):
    _campaign(database)
    client = _client(database, ScriptedLLMClient([]))
    missing = client.post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "x"}],
    })
    assert missing.status_code == 400
    assert missing.json()["error"]["type"] == "invalid_request_error"
    streaming = client.post("/v1/chat/completions", json={
        "campaign_id": "c1", "stream": True,
        "messages": [{"role": "user", "content": "x"}],
    })
    assert streaming.status_code == 400
    unknown = client.post("/v1/chat/completions", json={
        "campaign_id": "nope",
        "messages": [{"role": "user", "content": "x"}],
    })
    assert unknown.status_code == 404
    assert unknown.json()["error"]["message"]


def test_admin_export_contains_no_credentials(database):
    _campaign(database)
    client = _client(database, ScriptedLLMClient([]))
    response = client.get("/admin/campaigns/c1/export")
    assert response.status_code == 200
    assert response.json()["campaign"]["id"] == "c1"
    missing = client.get("/admin/campaigns/nope/export")
    assert missing.status_code == 404


def test_degraded_mode_serves_diagnostics_only(database):
    _campaign(database)
    settings = Settings(
        database_path=":memory:", host="127.0.0.1", port=8000,
        max_history_messages=40, narrator=NARRATOR_CFG, critic=None,
    )
    client = TestClient(create_app(
        settings, database, ScriptedLLMClient([]), degraded=True
    ))
    chat = client.post("/v1/chat/completions", json={
        "campaign_id": "c1",
        "messages": [{"role": "user", "content": "x"}],
    })
    assert chat.status_code == 503
    assert chat.json()["error"]["type"] == "degraded_mode"
    assert client.get("/health").json()["status"] == "degraded"
    assert client.get("/admin/campaigns/c1/export").status_code == 200
