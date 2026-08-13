"""Consistency critic: check narrative against gate results; rewrite once."""

from dataclasses import dataclass
import json
from typing import Any, Iterable

from ..llm.client import ChatMessage, LLMClient
from .gate import GateResult
from .narrative import generate

CRITIC_SYSTEM_PROMPT = (
    "你是一致性审查员。检查剧情草稿是否与权威状态、已应用变更、待确认提案"
    "矛盾。\n只输出 JSON:{\"consistent\": true 或 false, \"issues\":"
    " [\"问题描述\", ...]}。\n你只审查剧情文本;状态与规则结果不可修改。"
)


@dataclass(frozen=True)
class CriticVerdict:
    consistent: bool
    issues: tuple[str, ...]
    raw: str


@dataclass(frozen=True)
class CriticOutcome:
    narrative: str
    verdict: CriticVerdict | None  # None when the critic is disabled
    rewritten: bool


def _parse_verdict(text: str) -> CriticVerdict:
    """Parse the verdict; unparseable output is fail-open and kept in raw."""
    start, end = text.find("{"), text.rfind("}")
    data: Any = None
    if 0 <= start < end:
        try:
            data = json.loads(text[start:end + 1])
        except ValueError:
            data = None
    if not isinstance(data, dict) or "consistent" not in data:
        return CriticVerdict(True, (), text)
    issues = data.get("issues") or []
    if not isinstance(issues, list):
        issues = [str(issues)]
    return CriticVerdict(
        bool(data["consistent"]), tuple(str(issue) for issue in issues), text
    )


def run_critic(
    *,
    critic: LLMClient | None,
    narrator: LLMClient,
    draft: str,
    context: dict[str, Any],
    history: Iterable[ChatMessage],
    gate: GateResult,
    max_history: int = 40,
) -> CriticOutcome:
    """Return the final narrative; at most one rewrite, state never touched."""
    if critic is None:
        return CriticOutcome(draft, None, False)
    review = json.dumps(
        {
            "draft": draft,
            "applied": list(gate.applied),
            "pending": list(gate.pending),
            "dropped": list(gate.dropped),
        },
        ensure_ascii=False,
    )
    response = critic.chat([
        ChatMessage("system", CRITIC_SYSTEM_PROMPT),
        ChatMessage(
            "system",
            "权威状态上下文(JSON):\n" + json.dumps(context, ensure_ascii=False),
        ),
        ChatMessage("user", "审查以下剧情草稿:\n" + review),
    ])
    verdict = _parse_verdict(response.content)
    if verdict.consistent:
        return CriticOutcome(draft, verdict, False)
    rewritten = generate(
        narrator, context, history,
        feedback="审查发现以下矛盾:" + ";".join(verdict.issues),
        max_history=max_history,
    )
    return CriticOutcome(rewritten.content.strip(), verdict, True)
