"""OpenAI request validation, tracker-instruction stripping, history hash."""

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

from ..domain.errors import ValidationError
from ..llm.client import ChatMessage

_JSON_FENCE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)
_TRACKER_ROOT_KEYS = ("userStats", "infoBox")
_ALLOWED_ROLES = frozenset({"system", "user", "assistant"})


@dataclass(frozen=True)
class NormalizedRequest:
    """A validated, tracker-free request plus its audit trail."""

    campaign_id: str
    branch_id: str
    messages: tuple[ChatMessage, ...]
    player_text: str
    removed_instructions: tuple[str, ...]
    history_hash: str
    lineage_hash_before: str
    raw: dict[str, Any]


def _is_tracker_block(block: str) -> bool:
    """Structure check: a fenced JSON object carrying tracker root keys."""
    try:
        data = json.loads(block)
    except ValueError:
        return False
    return isinstance(data, dict) and any(
        key in data for key in _TRACKER_ROOT_KEYS
    )


def strip_tracker_blocks(content: str) -> str:
    """Remove fenced tracker JSON blocks; keep every other code block."""
    return _JSON_FENCE.sub(
        lambda match: "" if _is_tracker_block(match.group(1)) else match.group(0),
        content,
    ).strip()


def _is_tracker_instruction(content: str) -> bool:
    """Signature + structure: tracker phrasing AND a tracker JSON template."""
    if "tracker" not in content.lower():
        return False
    return any(
        _is_tracker_block(block) for block in _JSON_FENCE.findall(content)
    )


def _clean_message(message: ChatMessage) -> tuple[ChatMessage | None, str | None]:
    """Drop whole tracker instructions; strip tracker blocks from mixed text."""
    if _is_tracker_instruction(message.content):
        return None, message.content
    stripped = strip_tracker_blocks(message.content)
    if stripped != message.content.strip():
        if not stripped:
            return None, message.content
        return ChatMessage(message.role, stripped), message.content
    return message, None


def _history_hash(messages: tuple[ChatMessage, ...]) -> str:
    digest = hashlib.sha256()
    for message in messages:
        digest.update(message.role.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(message.content.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def lineage_hash_before(messages: tuple[ChatMessage, ...]) -> str:
    """Hash the history preceding the trailing user message.

    Input must already be tracker-free: callers pass normalized messages,
    and normalize_request applies strip_tracker_blocks upstream.
    """
    if not messages or messages[-1].role != "user":
        raise ValidationError("lineage requires trailing user message")
    return _history_hash(messages[:-1])


def lineage_hash_after(
    prefix: tuple[ChatMessage, ...], assistant_text: str,
) -> str:
    """Hash a turn's full history plus the cleaned assistant reply.

    Contract: prefix must be the turn's message sequence INCLUDING its
    trailing user message; the cleaned assistant reply is appended after
    it. The result equals the next request's lineage_hash_before.
    """
    cleaned = strip_tracker_blocks(assistant_text)
    return _history_hash(prefix + (ChatMessage("assistant", cleaned),))


def response_hash(assistant_text: str) -> str:
    """Hash a cleaned assistant reply on its own."""
    return _history_hash(
        (ChatMessage("assistant", strip_tracker_blocks(assistant_text)),)
    )


def normalize_request(payload: dict[str, Any]) -> NormalizedRequest:
    """Validate one chat-completions body and remove tracker instructions."""
    if not isinstance(payload, dict):
        raise ValidationError("request body must be a JSON object")
    campaign_id = payload.get("campaign_id")
    if not isinstance(campaign_id, str) or not campaign_id.strip():
        raise ValidationError("campaign_id is required in the request body")
    branch_id = payload.get("branch_id", "main")
    if not isinstance(branch_id, str) or not branch_id.strip():
        raise ValidationError("branch_id must be a non-empty string")
    if payload.get("stream"):
        raise ValidationError("stream is not supported by this engine")
    raw_messages = payload.get("messages")
    if not isinstance(raw_messages, list) or not raw_messages:
        raise ValidationError("messages must be a non-empty list")
    messages: list[ChatMessage] = []
    removed: list[str] = []
    for raw in raw_messages:
        if not isinstance(raw, dict):
            raise ValidationError("each message must be an object")
        role = raw.get("role")
        content = raw.get("content")
        if role not in _ALLOWED_ROLES:
            raise ValidationError(f"unsupported message role {role!r}")
        if not isinstance(content, str):
            raise ValidationError("message content must be a string")
        cleaned, dropped = _clean_message(ChatMessage(role, content))
        if dropped is not None:
            removed.append(dropped)
        if cleaned is not None:
            messages.append(cleaned)
    player_text = next(
        (m.content for m in reversed(messages) if m.role == "user"), None
    )
    if not player_text or not player_text.strip():
        raise ValidationError("no user message in the request")
    return NormalizedRequest(
        campaign_id=campaign_id.strip(),
        branch_id=branch_id.strip(),
        messages=tuple(messages),
        player_text=player_text.strip(),
        removed_instructions=tuple(removed),
        history_hash=_history_hash(tuple(messages)),
        lineage_hash_before=lineage_hash_before(tuple(messages)),
        raw=payload,
    )
