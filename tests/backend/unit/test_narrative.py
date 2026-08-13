import pytest

from sillytavern_rpg_engine.llm import ChatMessage, LLMError, ScriptedLLMClient
from sillytavern_rpg_engine.orchestration.narrative import (
    ANSWER_SYSTEM_PROMPT,
    NARRATOR_SYSTEM_PROMPT,
    build_messages,
    generate,
)

HISTORY = (
    ChatMessage("system", "角色卡:艾琳,半精灵炼金术师。"),
    ChatMessage("user", "我走进炼金铺。"),
    ChatMessage("assistant", "你推门而入。\n```json\n{\"userStats\": {}}\n```"),
    ChatMessage("user", "我打量货架。"),
)


def test_build_messages_strips_tracker_blocks_and_keeps_card():
    messages = build_messages({"entities": []}, HISTORY)
    assert messages[0].content == NARRATOR_SYSTEM_PROMPT
    assert messages[1].role == "system" and "权威状态" in messages[1].content
    assert messages[2].content == "角色卡:艾琳,半精灵炼金术师。"
    assert "userStats" not in messages[4].content
    assert messages[4].content == "你推门而入。"
    assert messages[-1].content == "我打量货架。"


def test_history_cap_keeps_system_and_recent_dialogue():
    history = [ChatMessage("system", "卡")] + [
        ChatMessage("user", f"第{i}句") for i in range(10)
    ]
    messages = build_messages({}, history, max_history=3)
    assert "卡" in [m.content for m in messages]
    assert messages[-1].content == "第9句"
    assert messages[-3].content == "第7句"


def test_generate_returns_response_and_appends_feedback_on_rewrite():
    client = ScriptedLLMClient(["修订后的剧情"])
    response = generate(client, {}, HISTORY, feedback="不要提及已死亡的 NPC")
    assert response.content == "修订后的剧情"
    assert "一致性修订要求" in client.requests[0][-1].content


def test_generate_rejects_empty_content():
    client = ScriptedLLMClient(["   "])
    with pytest.raises(LLMError, match="empty"):
        generate(client, {}, HISTORY)


def test_answer_prompt_used_for_queries():
    client = ScriptedLLMClient(["艾琳的炼金术是 35。"])
    generate(client, {"entities": []}, HISTORY, system_prompt=ANSWER_SYSTEM_PROMPT)
    assert client.requests[0][0].content == ANSWER_SYSTEM_PROMPT
