"""Narrator prompt assembly and text generation."""

import json
from typing import Any, Iterable

from ..llm.client import ChatMessage, LLMClient, LLMError, LLMResponse
from .normalize import strip_tracker_blocks

NARRATOR_SYSTEM_PROMPT = (
    "你是单机文字 RPG 的叙事者。根据玩家行动、权威状态与记忆上下文续写剧情。\n"
    "规则:\n"
    "1. 你不能决定或声明状态变化(属性、物品、关系、规则判定的具体数值);"
    "状态变化由引擎结算。\n"
    "2. 不掷骰,不给出骰点结果。\n"
    "3. 只使用上下文中可见的信息;上下文没有的事实不要编造为既定事实。\n"
    "4. 使用玩家的语言(默认中文),风格与已有剧情保持一致。\n"
    "5. 不要输出任何 JSON 代码块或 Tracker 内容。"
)

ANSWER_SYSTEM_PROMPT = (
    "你是 RPG 状态查询助手。只根据给出的权威状态与记忆上下文回答玩家的问题,"
    "不得编造上下文中没有的数据。简洁回答,使用玩家的语言。"
)


def build_messages(
    context: dict[str, Any],
    history: Iterable[ChatMessage],
    *,
    feedback: str | None = None,
    max_history: int = 40,
    system_prompt: str = NARRATOR_SYSTEM_PROMPT,
) -> list[ChatMessage]:
    """Assemble the prompt: rules, state context, card, capped clean history."""
    messages = [
        ChatMessage("system", system_prompt),
        ChatMessage(
            "system",
            "权威状态与记忆上下文(JSON):\n"
            + json.dumps(context, ensure_ascii=False),
        ),
    ]
    cleaned = [
        ChatMessage(m.role, strip_tracker_blocks(m.content)) for m in history
    ]
    cleaned = [m for m in cleaned if m.content]
    messages.extend(m for m in cleaned if m.role == "system")
    dialogue = [m for m in cleaned if m.role != "system"]
    messages.extend(dialogue[-max_history:])
    if feedback:
        messages.append(
            ChatMessage("user", "一致性修订要求(只改剧情文本):\n" + feedback)
        )
    return messages


def generate(
    client: LLMClient,
    context: dict[str, Any],
    history: Iterable[ChatMessage],
    *,
    feedback: str | None = None,
    max_history: int = 40,
    system_prompt: str = NARRATOR_SYSTEM_PROMPT,
) -> LLMResponse:
    """One model call; empty content is an infrastructure error."""
    response = client.chat(
        build_messages(
            context, history,
            feedback=feedback, max_history=max_history,
            system_prompt=system_prompt,
        )
    )
    if not response.content.strip():
        raise LLMError("model returned empty content")
    return response
