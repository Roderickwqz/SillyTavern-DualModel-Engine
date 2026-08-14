import json

from sillytavern_rpg_engine.config import Settings, ModelConfig
from sillytavern_rpg_engine.domain.models import (
    AttributeDefinition,
    AttributeType,
    Audience,
    DisplayType,
    EntityKind,
)
from sillytavern_rpg_engine.llm import ScriptedLLMClient
from sillytavern_rpg_engine.orchestration.graph import (
    TurnRunner,
    default_services,
)
from sillytavern_rpg_engine.services.attributes import DefineAttributeOperation
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)

NARRATOR_CFG = ModelConfig("http://x/v1", "", "narrator-model", 0.8, 5.0, 512)


def _settings():
    return Settings(
        database_path=":memory:", host="127.0.0.1", port=8000,
        max_history_messages=40, narrator=NARRATOR_CFG, critic=None,
    )


def _world(database):
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
    return campaigns


def _payload(text):
    return {"campaign_id": "c1", "messages": [{"role": "user", "content": text}]}


def _content(response):
    return response["choices"][0]["message"]["content"]


def test_action_turn_full_pipeline(database):
    _world(database)
    narrator = ScriptedLLMClient([
        "你推开炼金铺的门,艾琳抬头看你。",          # narrate
        json.dumps({"operations": [{                 # extract
            "kind": "set_attribute", "entity_id": "erin",
            "attribute_key": "alchemy", "value": 36, "turn_id": None,
            "reason": "剧情中练习",
        }]}),
    ])
    runner = TurnRunner(default_services(database, _settings(), narrator))
    response = runner.run(_payload("我走进炼金铺,看艾琳配药。"))
    content = _content(response)
    assert "你推开炼金铺的门" in content
    assert "```json" in content and '"rules"' in content
    assert "待确认" in content
    with database.connect() as connection:
        proposals = connection.execute(
            "SELECT operation_json FROM pending_proposals WHERE status = 'pending'"
        ).fetchall()
        assert len(proposals) == 1
        assert "alchemy" in proposals[0]["operation_json"]
        turn = connection.execute("SELECT * FROM turns").fetchone()
        assert turn["intent"] == "action"
        events = connection.execute(
            "SELECT content FROM memory_events ORDER BY rowid"
        ).fetchall()
        assert [e["content"][:3] for e in events] == ["玩家:", "叙事:"]


def test_query_turn_is_read_only(database):
    _world(database)
    narrator = ScriptedLLMClient(["艾琳的炼金术当前没有记录数值。"])
    runner = TurnRunner(default_services(database, _settings(), narrator))
    response = runner.run(_payload("查询艾琳的属性"))
    assert "炼金术" in _content(response)
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM memory_events"
        ).fetchone()[0] == 0


def test_explicit_change_applies_immediately(database):
    _world(database)
    narrator = ScriptedLLMClient([json.dumps({"operations": [
        {"kind": "set_attribute", "entity_id": "erin", "attribute_key": "alchemy",
         "value": 35, "turn_id": None},
    ]})])
    runner = TurnRunner(default_services(database, _settings(), narrator))
    content = _content(runner.run(_payload("把艾琳的炼金术调整为35")))
    assert "已应用 1 项变更" in content
    with database.connect() as connection:
        assert connection.execute(
            "SELECT value_json FROM attribute_values"
            " WHERE entity_id = 'erin' AND attribute_key = 'alchemy'"
        ).fetchone()["value_json"] == "35"


def test_proposal_command_approves_pending(database):
    """Regression: transcript writes must not stale a proposal created during
    the previous turn (they bypass the version bump via record_transcript)."""
    _world(database)
    narrator = ScriptedLLMClient([
        "艾琳继续练习。",
        json.dumps({"operations": [
            {"kind": "set_attribute", "entity_id": "erin",
             "attribute_key": "alchemy", "value": 36, "turn_id": None},
        ]}),
    ])
    services = default_services(database, _settings(), narrator)
    runner = TurnRunner(services)
    runner.run(_payload("艾琳练习了一整天炼金术。"))
    proposal_id = services.proposals.pending("c1", "main")[0].id
    content = _content(runner.run(_payload(f"确认提案 {proposal_id}")))
    assert "已确认并应用" in content
    with database.connect() as connection:
        assert connection.execute(
            "SELECT value_json FROM attribute_values"
            " WHERE entity_id = 'erin' AND attribute_key = 'alchemy'"
        ).fetchone()["value_json"] == "36"


def test_extraction_failure_returns_narrative_without_commit(database):
    _world(database)
    narrator = ScriptedLLMClient(["剧情继续。", "坏输出", "仍然坏"])
    runner = TurnRunner(default_services(database, _settings(), narrator))
    content = _content(runner.run(_payload("我继续前进。")))
    assert "剧情继续。" in content
    assert "解析失败" in content
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM pending_proposals"
        ).fetchone()[0] == 0


def test_critic_rewrite_replaces_narrative(database):
    _world(database)
    narrator = ScriptedLLMClient([
        "草稿:艾琳在银月城。", json.dumps({"operations": []}), "修订:艾琳在炼金铺。",
    ])
    critic = ScriptedLLMClient([json.dumps({
        "consistent": False, "issues": ["地点与上下文矛盾"],
    })])
    settings = _settings()
    services = default_services(database, settings, narrator, critic)
    runner = TurnRunner(services)
    content = _content(runner.run(_payload("我环顾四周。")))
    assert "修订:艾琳在炼金铺。" in content
    assert "草稿" not in content


def test_short_query_text_completes_without_error(database):
    """Regression: 2-char QUERY input (状态) must not 400 on FTS validation."""
    _world(database)
    narrator = ScriptedLLMClient(["艾琳的炼金术当前没有记录数值。"])
    runner = TurnRunner(default_services(database, _settings(), narrator))
    response = runner.run(_payload("状态"))
    assert response["object"] == "chat.completion"
    assert "choices" in response
    assert response["choices"][0]["message"]["content"]


def test_edit_history_detaches_and_continues(database):
    """Editing away an assistant reply must detach later turns on the branch
    and continue from the matched prefix (full echoed content incl. tracker
    block, which normalize strips before lineage hashing)."""
    CampaignService(
        database, id_factory=lambda: "cid", clock=lambda: "2026-08-13T00:00:00Z",
    ).create_campaign("c1", "测试")
    # ACTION turns need two narrator calls each: narrate + extract.
    narrator = ScriptedLLMClient([
        "回复甲", json.dumps({"operations": []}),
        "回复乙", json.dumps({"operations": []}),
        "回复丙", json.dumps({"operations": []}),
    ])
    runner = TurnRunner(default_services(database, _settings(), narrator))

    history = []
    first = runner.run({
        "campaign_id": "c1",
        "messages": history + [{"role": "user", "content": "a"}],
    })
    history.extend([
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": _content(first)},
    ])
    second = runner.run({
        "campaign_id": "c1",
        "messages": history + [{"role": "user", "content": "b"}],
    })
    history.extend([
        {"role": "user", "content": "b"},
        {"role": "assistant", "content": _content(second)},
    ])
    # Edit: drop both assistant replies, keep user messages only, continue.
    edited = [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}]
    result = runner.run({
        "campaign_id": "c1",
        "messages": edited + [{"role": "user", "content": "c"}],
    })
    assert _content(result)
    with database.connect() as connection:
        detached = connection.execute(
            "SELECT COUNT(*) FROM turns WHERE status = 'detached'"
        ).fetchone()[0]
        active = connection.execute(
            "SELECT COUNT(*) FROM turns WHERE status = 'active'"
        ).fetchone()[0]
    assert detached >= 1
    assert active >= 1
