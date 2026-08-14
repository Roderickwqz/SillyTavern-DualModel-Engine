import json
import sqlite3

from fastapi.testclient import TestClient

from sillytavern_rpg_engine.config import ModelConfig, Settings
from sillytavern_rpg_engine.llm import ScriptedLLMClient
from sillytavern_rpg_engine.server.app import create_app
from sillytavern_rpg_engine.services.branches import BranchService
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


def test_unmappable_history_returns_409_branch_resolution(database):
    _campaign(database)
    client = _client(database, ScriptedLLMClient([]))
    response = client.post("/v1/chat/completions", json={
        "campaign_id": "c1",
        "messages": [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "X"},
            {"role": "user", "content": "b"},
        ],
    })
    assert response.status_code == 409
    assert response.json()["error"]["type"] == "branch_resolution_error"


def test_malformed_json_body_returns_400(database):
    _campaign(database)
    client = _client(database, ScriptedLLMClient([]))
    response = client.post(
        "/v1/chat/completions",
        content=b"{not json",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"


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
    sentinel = "sk-sentinel-key"
    settings = Settings(
        database_path=":memory:", host="127.0.0.1", port=8000,
        max_history_messages=40,
        narrator=ModelConfig(
            "http://x/v1", sentinel, "narrator-model", 0.8, 5.0, 512,
        ),
        critic=None,
    )
    client = TestClient(create_app(settings, database, ScriptedLLMClient([])))
    response = client.get("/admin/campaigns/c1/export")
    assert response.status_code == 200
    payload = response.json()
    assert payload["campaign"]["id"] == "c1"
    assert sentinel not in json.dumps(payload)
    missing = client.get("/admin/campaigns/nope/export")
    assert missing.status_code == 404


def test_admin_lists_branches(database):
    _campaign(database)
    client = _client(database, ScriptedLLMClient([]))
    response = client.get("/admin/campaigns/c1/branches")
    assert response.status_code == 200
    body = response.json()
    assert body["branches"][0]["id"] == "main"
    assert body["branches"][0]["parent_branch_id"] is None
    assert body["branches"][0]["status"] == "active"
    assert body["branches"][0]["head"] == {
        "state_version": 0, "latest_turn_id": None,
    }
    assert body["detached_turns"] == 0


def test_admin_branches_unknown_campaign_returns_404(database):
    _campaign(database)
    client = _client(database, ScriptedLLMClient([]))
    response = client.get("/admin/campaigns/nope/branches")
    assert response.status_code == 404
    assert response.json()["error"]["type"] == "not_found"


def test_admin_branches_lists_forked_branch_with_head(database):
    _campaign(database)
    narrator = ScriptedLLMClient([
        "叙述一", json.dumps({"operations": []}),
    ])
    client = _client(database, narrator)
    response = client.post("/v1/chat/completions", json={
        "campaign_id": "c1",
        "messages": [{"role": "user", "content": "我走进炼金铺。"}],
    })
    assert response.status_code == 200, response.text
    with database.connect() as connection:
        turn = connection.execute(
            "SELECT id, state_after_version FROM turns"
            " ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    BranchService(database, id_factory=lambda: "fork-1",
                  clock=lambda: "2026-08-13T00:01:00Z").fork(
        "c1", "main", turn["id"], new_branch_id="fork-1",
    )
    body = client.get("/admin/campaigns/c1/branches").json()
    assert {branch["id"] for branch in body["branches"]} == {"main", "fork-1"}
    child = next(branch for branch in body["branches"]
                 if branch["id"] == "fork-1")
    assert child["parent_branch_id"] == "main"
    assert child["status"] == "active"
    assert child["head"] == {
        "state_version": turn["state_after_version"],
        "latest_turn_id": turn["id"],
    }
    main = next(branch for branch in body["branches"]
                if branch["id"] == "main")
    assert main["head"]["latest_turn_id"] == turn["id"]


def test_admin_branches_counts_detached_turns(database):
    _campaign(database)
    narrator = ScriptedLLMClient([
        "叙述一", json.dumps({"operations": []}),
        "叙述二", json.dumps({"operations": []}),
        "叙述三", json.dumps({"operations": []}),
        "叙述四", json.dumps({"operations": []}),
    ])
    client = _client(database, narrator)
    history: list[dict] = []
    for text in ("我走进炼金铺。", "我拿起一瓶药剂。"):
        history.append({"role": "user", "content": text})
        response = client.post("/v1/chat/completions", json={
            "campaign_id": "c1", "messages": history,
        })
        assert response.status_code == 200, response.text
        history.append({
            "role": "assistant",
            "content": response.json()["choices"][0]["message"]["content"],
        })
    history = [
        {"role": "user", "content": "我走进炼金铺。"},
        {"role": "user", "content": "我拿起一瓶药剂。"},
    ]
    response = client.post("/v1/chat/completions", json={
        "campaign_id": "c1",
        "messages": history + [{"role": "user", "content": "我放下药剂。"}],
    })
    assert response.status_code == 200, response.text
    body = client.get("/admin/campaigns/c1/branches").json()
    assert body["detached_turns"] == 1


def test_health_sqlite_failure_returns_complete_body(database, monkeypatch):
    _campaign(database)
    client = _client(database, ScriptedLLMClient([]))

    def _fail_connect():
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(database, "connect", _fail_connect)
    response = client.get("/health")
    assert response.status_code == 503
    body = response.json()
    assert body == {
        "status": "degraded",
        "integrity": "disk I/O error",
        "schema_version": None,
        "models": {"narrator": "configured", "critic": "disabled"},
    }


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
