"""LLM change-proposal extraction: parse, repair, validate — never apply."""

from dataclasses import dataclass
import json
import sqlite3
from typing import Any, Callable

from ..domain.errors import DomainError, ValidationError
from ..llm.client import ChatMessage, LLMClient
from ..services.proposals import OperationCodec
from .scene import resolve_reference

EXTRACTION_SYSTEM_PROMPT = (
    "你是状态变更提取器。阅读玩家输入与剧情草稿,输出需要持久化的状态变更。\n"
    "只输出一个 JSON 对象:{\"operations\": [...]},每个元素是一个操作,\n"
    "允许的 kind 与字段(全部必填):\n"
    "- create_entity: entity_id(可空串), entity_kind(character|location|"
    "organization|item), name, age_status(adult|minor|unknown), aliases\n"
    "- set_attribute: entity_id, attribute_key, value, turn_id(null)\n"
    "- assert_fact: fact_id(空串), entity_id, fact_type(identity|commitment|"
    "quest|conflict|rule_consequence|general), fact_key, content,"
    " importance(1-5), audiences, turn_id(null)\n"
    "- record_memory_event: event_id(空串), event_type(general|scene|identity|"
    "commitment|conflict|quest|rule_consequence|personality_shift), content,"
    " importance, audiences, participant_entity_ids, location_entity_id,"
    " turn_id(null), source\n"
    "- record_trait_event: event_id(空串), entity_id, trait_key,"
    " tier(normal|important|major), delta(数字), cause, turn_id(null), source\n"
    "- set_relationship: from_entity_id, to_entity_id, dimension, value,"
    " audiences, turn_id(null)\n"
    "- upsert_summary: scope(character|relationship|quest|plotline), scope_key,"
    " content, audiences, source_event_ids\n"
    "- open_arc: arc_id(空串), entity_id, dimension, label, summary,"
    " source_event_ids, start_turn_id(null)\n"
    "- close_arc: arc_id, end_turn_id(null), summary\n"
    "- apply_condition: entity_id, condition, level, source\n"
    "- remove_condition: entity_id, condition, level\n"
    "每个操作可附带 \"reason\" 说明依据。entity_id 使用上下文中的稳定 ID。\n"
    "禁止输出 set_rules 与任何骰点/攻击/施法操作。没有变更时输出"
    " {\"operations\": []}。"
)

_ID_FIELDS = {"assert_fact": "fact_id", "record_memory_event": "event_id",
              "record_trait_event": "event_id", "open_arc": "arc_id"}
_TURN_FIELDS = ("turn_id", "start_turn_id", "end_turn_id")
_ENTITY_FIELDS = {
    "set_attribute": ("entity_id",),
    "assert_fact": ("entity_id",),
    "record_trait_event": ("entity_id",),
    "set_relationship": ("from_entity_id", "to_entity_id"),
    "apply_condition": ("entity_id",),
    "remove_condition": ("entity_id",),
    "open_arc": ("entity_id",),
}


@dataclass(frozen=True)
class ExtractedOperation:
    payload: dict[str, Any]
    reason: str


@dataclass(frozen=True)
class ExtractionResult:
    operations: tuple[ExtractedOperation, ...]
    error: str | None
    drops: tuple[dict[str, Any], ...]


def _parse_operations(text: str) -> list[dict[str, Any]]:
    """Locate and parse the {\"operations\": [...]} object in model output."""
    candidates = [text.strip()]
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict) and isinstance(data.get("operations"), list):
            return data["operations"]
    raise ValidationError("no {\"operations\": [...]} JSON object found")


def _repair(
    raw: Any, *, campaign_id: str, turn_id: str,
    connection: sqlite3.Connection, id_factory: Callable[[], str],
) -> ExtractedOperation:
    if not isinstance(raw, dict):
        raise ValidationError("operation must be an object")
    payload = dict(raw)
    reason = str(payload.pop("reason", "") or "")
    kind = payload.get("kind")
    if kind == "set_rules":
        raise ValidationError("set_rules is not allowed from chat")
    if kind == "define_attribute":
        payload["campaign_id"] = campaign_id
    if kind in _ID_FIELDS and not payload.get(_ID_FIELDS[kind]):
        payload[_ID_FIELDS[kind]] = id_factory()
    if kind == "create_entity" and not payload.get("entity_id"):
        payload["entity_id"] = id_factory()
    for field in _TURN_FIELDS:
        if field in payload and payload[field] is None:
            payload[field] = turn_id
    for field in _ENTITY_FIELDS.get(kind, ()):
        payload[field] = resolve_reference(connection, campaign_id, payload[field])
    if kind == "record_memory_event":
        payload["participant_entity_ids"] = [
            resolve_reference(connection, campaign_id, ref)
            for ref in payload.get("participant_entity_ids", [])
        ]
        if payload.get("location_entity_id"):
            payload["location_entity_id"] = resolve_reference(
                connection, campaign_id, payload["location_entity_id"]
            )
    OperationCodec.decode(payload)  # validation only; result discarded
    return ExtractedOperation(payload=payload, reason=reason)


def extract_operations(
    client: LLMClient,
    *,
    campaign_id: str,
    player_text: str,
    narrative: str | None,
    context: dict[str, Any],
    turn_id: str,
    connection: sqlite3.Connection,
    id_factory: Callable[[], str],
) -> ExtractionResult:
    """Extract codec-validated operations; one correction retry, then give up."""
    messages = [
        ChatMessage("system", EXTRACTION_SYSTEM_PROMPT),
        ChatMessage(
            "system",
            "权威状态上下文(JSON):\n" + json.dumps(context, ensure_ascii=False),
        ),
        ChatMessage(
            "user",
            f"玩家输入:\n{player_text}\n\n剧情草稿:\n{narrative or '(无)'}"
            "\n\n输出 JSON。",
        ),
    ]
    last_error: str | None = None
    for attempt in range(2):
        response = client.chat(messages)
        try:
            raw_operations = _parse_operations(response.content)
        except ValidationError as exc:
            last_error = str(exc)
            if attempt == 0:
                messages.append(ChatMessage("assistant", response.content))
                messages.append(ChatMessage(
                    "user",
                    f"输出无效:{last_error}。请只输出正确的 JSON 对象。",
                ))
            continue
        operations: list[ExtractedOperation] = []
        drops: list[dict[str, Any]] = []
        for raw in raw_operations:
            try:
                operations.append(_repair(
                    raw, campaign_id=campaign_id, turn_id=turn_id,
                    connection=connection, id_factory=id_factory,
                ))
            except DomainError as exc:
                drops.append({"operation": raw, "error": str(exc)})
        return ExtractionResult(tuple(operations), None, tuple(drops))
    return ExtractionResult((), last_error, ())
