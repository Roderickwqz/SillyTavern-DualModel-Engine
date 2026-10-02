"""LangGraph turn pipeline assembly and the TurnRunner entry point."""

from dataclasses import dataclass
import time
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from ..config import Settings
from ..domain.memory import MemoryEventType
from ..domain.models import Audience
from ..llm.client import LLMClient
from ..persistence.database import Database
from ..persistence.repositories import CampaignRepository
from ..services.branches import BranchService
from ..services.lineage import LineageResolver
from ..services.memory_events import MemoryEventService
from ..services.mutations import MutationEngine
from ..services.proposals import ProposalService
from ..services.retrieval import RetrievalQuery, RetrievalService
from ..services.turns import TurnService
from .critic import run_critic
from .extraction import ExtractedOperation, extract_operations
from .gate import (
    GateResult,
    parse_proposal_command,
    run_gate,
    run_proposal_command,
)
from .intent import Intent, route_intent
from .narrative import ANSWER_SYSTEM_PROMPT, generate
from .normalize import (
    NormalizedRequest,
    lineage_hash_after,
    normalize_request,
    response_hash,
)
from .presenter import TrackerPresenter
from .scene import scan_scene


@dataclass
class TurnServices:
    database: Database
    settings: Settings
    narrator: LLMClient
    critic: LLMClient | None
    mutation_engine: MutationEngine
    proposals: ProposalService
    retrieval: RetrievalService
    memory: MemoryEventService
    turns: TurnService


def default_services(
    database: Database,
    settings: Settings,
    narrator: LLMClient,
    critic: LLMClient | None = None,
) -> TurnServices:
    """Wire the production service bundle around one database and models."""
    mutation_engine = MutationEngine(database)
    return TurnServices(
        database=database,
        settings=settings,
        narrator=narrator,
        critic=critic,
        mutation_engine=mutation_engine,
        proposals=ProposalService(database, mutation_engine),
        retrieval=RetrievalService(database),
        memory=MemoryEventService(database, mutation_engine),
        turns=TurnService(database),
    )


class TurnState(TypedDict, total=False):
    raw: dict[str, Any]
    request: NormalizedRequest
    turn_id: str
    intent: str
    branch_id: str
    parent_turn_id: str | None
    branch_restored: bool
    state_before_version: int
    scene_entity_ids: tuple[str, ...]
    context: dict[str, Any]
    narrative: str
    operations: tuple[ExtractedOperation, ...]
    extraction_error: str | None
    gate_message: str
    applied: tuple[dict[str, Any], ...]
    pending: tuple[dict[str, Any], ...]
    dropped: tuple[dict[str, Any], ...]
    critic_verdict: dict[str, Any] | None
    rewritten: bool
    response: dict[str, Any]


def _make_version_fn(
    database: Database, campaigns: CampaignRepository,
) -> Any:
    def _version(campaign_id: str) -> int:
        with database.connect() as connection:
            return campaigns.require(connection, campaign_id).state_version

    return _version


def _store_gate(result: GateResult) -> dict[str, Any]:
    return {
        "applied": result.applied,
        "pending": result.pending,
        "dropped": result.dropped,
        "gate_message": result.message,
    }


def _make_normalize_node(
    services: TurnServices, _version: Any,
) -> Any:
    def node_normalize(state: TurnState) -> dict[str, Any]:
        request = normalize_request(state["raw"])
        return {
            "request": request,
            "turn_id": services.turns.id_factory(),
            "state_before_version": _version(request.campaign_id),
        }

    return node_normalize


def _make_route_node() -> Any:
    def node_route(state: TurnState) -> dict[str, Any]:
        return {"intent": route_intent(state["request"].player_text).value}

    return node_route


def _make_resolve_lineage_node(database: Database, _version: Any) -> Any:
    def node_resolve_lineage(state: TurnState) -> dict[str, Any]:
        request = state["request"]
        ctx = LineageResolver(database).resolve(
            request.campaign_id, request.messages
        )
        return {
            "branch_id": ctx.branch_id,
            "parent_turn_id": ctx.parent_turn_id,
            "branch_restored": ctx.restored,
            # Re-read: snapshot restore may have rolled campaigns.state_version
            # back; normalize's value is stale on every restored path.
            "state_before_version": _version(request.campaign_id),
        }

    return node_resolve_lineage


def _make_scene_node(database: Database) -> Any:
    def node_scene(state: TurnState) -> dict[str, Any]:
        request = state["request"]
        with database.connect() as connection:
            scan = scan_scene(
                connection, request.campaign_id, request.player_text
            )
        return {"scene_entity_ids": scan.entity_ids}

    return node_scene


def _make_retrieve_node(services: TurnServices) -> Any:
    def node_retrieve(state: TurnState) -> dict[str, Any]:
        request = state["request"]
        context = services.retrieval.assemble(RetrievalQuery(
            campaign_id=request.campaign_id,
            branch_id=state["branch_id"],
            audiences=frozenset({Audience.NARRATOR}),
            text=request.player_text,
            scene_entity_ids=state["scene_entity_ids"],
        ))
        return {"context": context}

    return node_retrieve


def _make_answer_node(services: TurnServices) -> Any:
    def node_answer(state: TurnState) -> dict[str, Any]:
        request = state["request"]
        response = generate(
            services.narrator, state["context"], request.messages,
            max_history=services.settings.max_history_messages,
            system_prompt=ANSWER_SYSTEM_PROMPT,
        )
        return {"narrative": response.content.strip()}

    return node_answer


def _make_narrate_node(services: TurnServices) -> Any:
    def node_narrate(state: TurnState) -> dict[str, Any]:
        request = state["request"]
        response = generate(
            services.narrator, state["context"], request.messages,
            max_history=services.settings.max_history_messages,
        )
        return {"narrative": response.content.strip()}

    return node_narrate


def _make_extract_node(services: TurnServices, database: Database) -> Any:
    def node_extract(state: TurnState) -> dict[str, Any]:
        request = state["request"]
        with database.connect() as connection:
            result = extract_operations(
                services.narrator,
                campaign_id=request.campaign_id,
                player_text=request.player_text,
                narrative=state.get("narrative"),
                context=state["context"],
                turn_id=state["turn_id"],
                connection=connection,
                id_factory=services.turns.id_factory,
            )
        return {
            "operations": result.operations,
            "extraction_error": result.error,
        }

    return node_extract


def _make_gate_explicit_node(services: TurnServices, database: Database) -> Any:
    def node_gate_explicit(state: TurnState) -> dict[str, Any]:
        request = state["request"]
        command = parse_proposal_command(request.player_text)
        if command is not None:
            return _store_gate(run_proposal_command(
                database, services.proposals, *command
            ))
        with database.connect() as connection:
            extraction = extract_operations(
                services.narrator,
                campaign_id=request.campaign_id,
                player_text=request.player_text,
                narrative=None,
                context=state["context"],
                turn_id=state["turn_id"],
                connection=connection,
                id_factory=services.turns.id_factory,
            )
        if extraction.error is not None:
            return _store_gate(GateResult(
                (), (), (), f"无法解析修改指令:{extraction.error}"
            ))
        return _store_gate(run_gate(
            database, services.mutation_engine, services.proposals,
            request.campaign_id, state["branch_id"],
            Intent.EXPLICIT_CHANGE, extraction.operations,
        ))

    return node_gate_explicit


def _make_gate_action_node(services: TurnServices, database: Database) -> Any:
    def node_gate_action(state: TurnState) -> dict[str, Any]:
        request = state["request"]
        return _store_gate(run_gate(
            database, services.mutation_engine, services.proposals,
            request.campaign_id, state["branch_id"],
            Intent.ACTION, state.get("operations", ()),
        ))

    return node_gate_action


def _make_critic_node(services: TurnServices) -> Any:
    def node_critic(state: TurnState) -> dict[str, Any]:
        request = state["request"]
        outcome = run_critic(
            critic=services.critic,
            narrator=services.narrator,
            draft=state["narrative"],
            context=state["context"],
            history=request.messages,
            gate=GateResult(
                state.get("applied", ()), state.get("pending", ()),
                state.get("dropped", ()), state.get("gate_message", ""),
            ),
            max_history=services.settings.max_history_messages,
        )
        verdict = None
        if outcome.verdict is not None:
            verdict = {
                "consistent": outcome.verdict.consistent,
                "issues": list(outcome.verdict.issues),
            }
        return {
            "narrative": outcome.narrative,
            "critic_verdict": verdict,
            "rewritten": outcome.rewritten,
        }

    return node_critic


def _assemble_response_parts(state: TurnState) -> str:
    """Join the visible response parts (narrative/gate/error) without the
    tracker block; both the recorded response_text and the echoed chat
    content are built from this so lineage hashes match on every turn."""
    parts = []
    if state.get("narrative"):
        parts.append(state["narrative"])
    if state.get("gate_message"):
        parts.append(state["gate_message"])
    if state.get("extraction_error"):
        parts.append(f"(状态变更解析失败:{state['extraction_error']})")
    return "\n\n".join(parts)


def _make_commit_node(
    services: TurnServices, _version: Any,
) -> Any:
    def node_commit(state: TurnState) -> dict[str, Any]:
        request = state["request"]
        intent = state["intent"]
        if intent == Intent.QUERY.value:
            return {}
        response_text = _assemble_response_parts(state)
        if intent == Intent.ACTION.value:
            audiences = frozenset(Audience)
            participants = state.get("scene_entity_ids", ())
            services.memory.record_transcript(
                request.campaign_id, state["branch_id"],
                type=MemoryEventType.SCENE,
                content=f"玩家:{request.player_text}",
                importance=3, audiences=audiences, participants=participants,
                turn_id=state["turn_id"],
            )
            services.memory.record_transcript(
                request.campaign_id, state["branch_id"],
                type=MemoryEventType.SCENE,
                content=f"叙事:{response_text}",
                importance=3, audiences=audiences, participants=participants,
                turn_id=state["turn_id"],
            )
        after_version = _version(request.campaign_id)
        services.turns.record(
            request.campaign_id, state["branch_id"],
            intent=intent,
            player_text=request.player_text,
            response_text=response_text,
            history_hash=request.history_hash,
            state_before_version=state["state_before_version"],
            state_after_version=after_version,
            turn_id=state["turn_id"],
            removed_instructions=request.removed_instructions,
            parent_turn_id=state.get("parent_turn_id"),
            lineage_hash_before=request.lineage_hash_before,
            lineage_hash_after=lineage_hash_after(
                request.messages, response_text
            ),
            response_hash=response_hash(response_text),
        )
        BranchService(services.database).update_head(
            request.campaign_id, state["branch_id"], after_version,
            state["turn_id"],
        )
        return {}

    return node_commit


def _make_respond_node(services: TurnServices, database: Database) -> Any:
    def node_respond(state: TurnState) -> dict[str, Any]:
        request = state["request"]
        tracker = TrackerPresenter(database).render(
            request.campaign_id, state["branch_id"]
        )
        content = _assemble_response_parts(state) + "\n\n" + tracker
        return {"response": {
            "id": f"chatcmpl-{state['turn_id']}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": services.settings.narrator.model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0,
                      "total_tokens": 0},
        }}

    return node_respond


def _route_after_retrieve(state: TurnState) -> str:
    return state["intent"]


def build_graph(services: TurnServices):
    database = services.database
    campaigns = CampaignRepository()
    _version = _make_version_fn(database, campaigns)

    builder = StateGraph(TurnState)
    builder.add_node("normalize", _make_normalize_node(services, _version))
    builder.add_node(
        "resolve_lineage",
        _make_resolve_lineage_node(database, _version),
    )
    builder.add_node("route", _make_route_node())
    builder.add_node("scene", _make_scene_node(database))
    builder.add_node("retrieve", _make_retrieve_node(services))
    builder.add_node("answer", _make_answer_node(services))
    builder.add_node("narrate", _make_narrate_node(services))
    builder.add_node("extract", _make_extract_node(services, database))
    builder.add_node(
        "gate_explicit", _make_gate_explicit_node(services, database),
    )
    builder.add_node(
        "gate_action", _make_gate_action_node(services, database),
    )
    builder.add_node("critic", _make_critic_node(services))
    builder.add_node("commit", _make_commit_node(services, _version))
    builder.add_node("respond", _make_respond_node(services, database))
    builder.add_edge(START, "normalize")
    builder.add_edge("normalize", "resolve_lineage")
    builder.add_edge("resolve_lineage", "route")
    builder.add_edge("route", "scene")
    builder.add_edge("scene", "retrieve")
    builder.add_conditional_edges("retrieve", _route_after_retrieve, {
        Intent.QUERY.value: "answer",
        Intent.EXPLICIT_CHANGE.value: "gate_explicit",
        Intent.ACTION.value: "narrate",
    })
    builder.add_edge("answer", "respond")
    builder.add_edge("narrate", "extract")
    builder.add_edge("extract", "gate_action")
    builder.add_edge("gate_explicit", "commit")
    builder.add_edge("gate_action", "critic")
    builder.add_edge("critic", "commit")
    builder.add_edge("commit", "respond")
    builder.add_edge("respond", END)
    return builder.compile()


class TurnRunner:
    """Synchronous entry point: one raw request body in, one response out."""

    def __init__(self, services: TurnServices):
        self.services = services
        self.graph = build_graph(services)

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        final = self.graph.invoke({"raw": payload})
        return final["response"]
