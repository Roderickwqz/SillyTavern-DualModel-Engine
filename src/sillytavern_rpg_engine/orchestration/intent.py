"""Conservative deterministic intent routing over the player's text."""

from enum import StrEnum
import re


class Intent(StrEnum):
    ACTION = "action"
    QUERY = "query"
    EXPLICIT_CHANGE = "explicit_change"


_EXPLICIT_PATTERNS = (
    r"^(给|把|将)\S{0,20}(新增|添加|增加|修改|调整|设置|删除|移除|扣除|恢复)",
    r"^(新增|添加|修改|调整|设置|删除|移除)人物",
    r"^(确认|拒绝)提案",
    r"^(set|add|update|remove|delete|approve|reject)\s",
)

_QUERY_PATTERNS = (
    r"^(查询|查看|状态|面板)",
    r"^(query|status|sheet)\b",
)


def route_intent(player_text: str) -> Intent:
    """Classify one player message; anything unclear is an ACTION."""
    text = player_text.strip()
    if any(re.search(p, text, re.IGNORECASE) for p in _EXPLICIT_PATTERNS):
        return Intent.EXPLICIT_CHANGE
    if any(re.search(p, text, re.IGNORECASE) for p in _QUERY_PATTERNS):
        return Intent.QUERY
    return Intent.ACTION
