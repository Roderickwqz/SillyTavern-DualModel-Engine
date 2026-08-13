import json

from sillytavern_rpg_engine.llm import ChatMessage, ScriptedLLMClient
from sillytavern_rpg_engine.orchestration.critic import run_critic
from sillytavern_rpg_engine.orchestration.gate import GateResult

HISTORY = [ChatMessage("user", "我走进炼金铺。")]
EMPTY_GATE = GateResult((), (), (), "")


def test_critic_disabled_passes_draft_through():
    outcome = run_critic(
        critic=None, narrator=ScriptedLLMClient([]), draft="草稿",
        context={}, history=HISTORY, gate=EMPTY_GATE,
    )
    assert outcome.narrative == "草稿"
    assert outcome.verdict is None and outcome.rewritten is False


def test_consistent_verdict_keeps_draft():
    critic = ScriptedLLMClient([json.dumps({"consistent": True, "issues": []})])
    outcome = run_critic(
        critic=critic, narrator=ScriptedLLMClient([]), draft="草稿",
        context={}, history=HISTORY, gate=EMPTY_GATE,
    )
    assert outcome.narrative == "草稿" and outcome.rewritten is False
    assert outcome.verdict.consistent is True


def test_inconsistent_verdict_triggers_one_rewrite_with_feedback():
    critic = ScriptedLLMClient([json.dumps({
        "consistent": False, "issues": ["草稿称艾琳已离开,但状态显示她仍在银月城"],
    })])
    narrator = ScriptedLLMClient(["修订稿"])
    outcome = run_critic(
        critic=critic, narrator=narrator, draft="草稿",
        context={}, history=HISTORY, gate=EMPTY_GATE,
    )
    assert outcome.narrative == "修订稿" and outcome.rewritten is True
    assert "艾琳" in narrator.requests[0][-1].content


def test_invalid_verdict_json_is_fail_open():
    critic = ScriptedLLMClient(["看不懂"])
    outcome = run_critic(
        critic=critic, narrator=ScriptedLLMClient([]), draft="草稿",
        context={}, history=HISTORY, gate=EMPTY_GATE,
    )
    assert outcome.narrative == "草稿"
    assert outcome.verdict.consistent is True
    assert outcome.verdict.raw == "看不懂"
