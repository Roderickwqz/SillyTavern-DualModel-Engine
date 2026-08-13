# LangGraph RPG Core — Phase 4 (Turn Orchestration & OpenAI-Compatible API) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the LangGraph turn orchestration layer and the OpenAI-compatible FastAPI entry point on top of the Phase 1–3 core: request normalization, intent routing, scene/memory context assembly, dual-model (narrator + critic) generation, LLM change-proposal extraction with authorization gating, turn recording, Tracker JSON presentation, and the HTTP API SillyTavern connects to.

**Architecture:** A LangGraph `StateGraph` runs each chat turn as a deterministic pipeline of plain-Python nodes; every node is a function over an immutable-ish `TurnState` dict with services injected via closures, so nodes are unit-testable without LangGraph or network. LLMs only ever produce candidate text and candidate operations — all state changes still go through `MutationEngine` (explicit changes) or `ProposalService` (inferred changes), keeping the single-transaction atomicity guarantees from Phases 1–3. The FastAPI app is a thin adapter: parse JSON → run the turn graph → map results/errors to the OpenAI Chat Completions wire format. Non-streaming only.

**Tech Stack:** Python 3.11+, FastAPI + uvicorn (API), httpx (upstream LLM client), LangGraph 1.x (`StateGraph`, `START`, `END`, conditional edges), SQLite via the existing stdlib persistence layer, pytest 8.x + FastAPI `TestClient` (httpx-based, no server needed).

## Global Constraints

- All Phase 1–3 guarantees unchanged: LLMs never write state directly; every game-state change is a `MutationOperation` through `MutationEngine` (one CAS version bump, one audit event, one outbox row, one snapshot per mutation) or a Pending proposal through `ProposalService`. The graph never executes SQL.
- LLM proposal output is validated by `OperationCodec.decode` before it can touch anything; kinds that roll dice remain absent from the codec, so no LLM proposal can trigger randomness. Explicit player commands apply immediately (`source="user-command"`); narrative-inferred changes become Pending proposals only.
- LLM structured output gets exactly one format-correction retry; still invalid → no state commit for that turn's proposals, and the failure is surfaced in the response text (never a fake success).
- The Consistency Critic may only rewrite narrative text. It never modifies rule results, proposals, or state. Critic is optional: when `RPG_CRITIC_MODEL` is unset the pipeline runs single-model and skips critique.
- First version is non-streaming: `stream: true` is rejected with a 400 OpenAI-format error. Responses are returned only after validation and commit complete.
- RPG Companion synthetic Tracker instructions are identified by content signature AND structure together (a fenced JSON block carrying tracker root keys plus tracker instruction phrasing). Ordinary user messages containing similar words are never silently dropped; removed spans are kept for audit.
- API keys come from environment variables only, never from request bodies, never appear in logs, exports, or snapshots. The server binds to `127.0.0.1` by default.
- Custom Body carries only `campaign_id` (and optionally `branch_id`). Rules enable/disable never arrives via API parameters, and dice/combat operations are not chat-triggered in this phase — D&D settlement stays at the service layer (delivered in Phase 3); the chat pipeline contains no random operations.
- On schema-migration failure the server still boots in a degraded read-only diagnostic mode: `/health` and `/admin/.../export` answer, chat returns 503.
- Turn recording is bookkeeping, not game state: the `turns` row and the two transcript memory events (player action, response) are written after all gate mutations, so `state_after_version` covers them. Read-only query turns record nothing.
- History hashing excludes Tracker JSON blocks; swipe/edit branch *recovery* is Phase 6 — this plan only stores the hash and parent linkage.
- Conda env `py313`; install with `python -m pip install -e ".[dev]"`; test with `python -m pytest tests/backend -q`; guard with `python .harness/scripts/guard.py src/sillytavern_rpg_engine tests/backend`; commit via `bash .harness/scripts/committer "<msg>" <files...>`.

## Multi-Plan Roadmap

The approved specification (`docs/superpowers/specs/2026-08-10-langgraph-rpg-companion-design.md`) is split into independent plans:

1. Phase 1 (done): authoritative Python core and persistence.
2. Phase 2 (done): memory and personality.
3. Phase 3 (done): D&D 2024 / 5.5e rules layer.
4. **This plan:** LangGraph turn orchestration and OpenAI-compatible API.
5. RPG Companion compatibility fork: dynamic `attributes` rendering, rules metadata panels, read-only mode, narrowed JSON cleaning.
6. Branching and release: SillyTavern history-hash swipe/edit/delete recovery, backups, long-run simulation, E2E.

## File Structure

```text
src/sillytavern_rpg_engine/
├── config.py                     # NEW: env-based Settings (server, narrator, critic)
├── llm/
│   ├── __init__.py               # NEW: re-exports
│   ├── client.py                 # NEW: ChatMessage, LLMResponse, LLMError, LLMClient protocol
│   ├── openai.py                 # NEW: OpenAIChatClient over httpx
│   └── scripted.py               # NEW: ScriptedLLMClient test double
├── orchestration/
│   ├── __init__.py               # NEW: re-exports
│   ├── normalize.py              # NEW: request validation, tracker stripping, history hash
│   ├── intent.py                 # NEW: deterministic intent router
│   ├── scene.py                  # NEW: scene entity scan, entity reference resolution
│   ├── narrative.py              # NEW: narrator prompt assembly + generation
│   ├── extraction.py             # NEW: LLM proposal extraction + one correction retry
│   ├── gate.py                   # NEW: authorization gate (apply vs pending), proposal commands
│   ├── critic.py                 # NEW: consistency critic + one narrative rewrite
│   ├── presenter.py              # NEW: projection -> Tracker JSON code block
│   └── graph.py                  # NEW: TurnState, node closures, StateGraph assembly, TurnRunner
├── services/
│   ├── turns.py                  # NEW: turn records (migration 0005)
│   ├── proposals.py              # MODIFY: + ProposalService.pending(campaign_id, branch_id)
│   └── campaign_export.py        # MODIFY: _build_payload -> public build_payload
├── persistence/
│   └── schema/
│       └── 0005_turns.sql        # NEW: turns table
├── server/
│   ├── __init__.py               # NEW
│   └── app.py                    # NEW: create_app, routes, OpenAI error mapping
├── cli.py                        # MODIFY: + serve command
└── pyproject.toml                # MODIFY: + fastapi, uvicorn, httpx, langgraph deps
tests/backend/
├── unit/
│   ├── test_config.py
│   ├── test_llm_client.py
│   ├── test_openai_client.py
│   ├── test_normalize.py
│   ├── test_intent.py
│   ├── test_scene.py
│   ├── test_turns.py
│   ├── test_narrative.py
│   ├── test_extraction.py
│   ├── test_gate.py
│   ├── test_critic.py
│   ├── test_presenter.py
│   ├── test_graph.py
│   └── test_api.py
└── integration/
    └── test_phase4_flow.py
```

---

### Task 1: Dependencies and environment-based settings

**Files:**
- Modify: `pyproject.toml`
- Create: `src/sillytavern_rpg_engine/config.py`
- Test: `tests/backend/unit/test_config.py`

**Interfaces:**
- Consumes: `domain.errors.ValidationError`.
- Produces: `ModelConfig(base_url, api_key, model, temperature, timeout_seconds, max_tokens)`; `Settings(database_path, host, port, max_history_messages, narrator, critic)`; `load_settings(env: dict | None = None) -> Settings`. Every later task reads config exclusively through these.

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from sillytavern_rpg_engine.config import load_settings
from sillytavern_rpg_engine.domain.errors import ValidationError


def test_defaults_when_only_narrator_model_set():
    settings = load_settings({"RPG_NARRATOR_MODEL": "my-narrator"})
    assert settings.narrator.model == "my-narrator"
    assert settings.narrator.base_url == "http://127.0.0.1:5000/v1"
    assert settings.narrator.temperature == 0.8
    assert settings.critic is None
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.max_history_messages == 40


def test_critic_enabled_only_when_model_set_and_inherits_connection():
    settings = load_settings({
        "RPG_NARRATOR_MODEL": "narr",
        "RPG_NARRATOR_BASE_URL": "http://localhost:1234/v1",
        "RPG_NARRATOR_API_KEY": "secret",
        "RPG_CRITIC_MODEL": "critic-7b",
        "RPG_CRITIC_TEMPERATURE": "0.2",
    })
    assert settings.critic is not None
    assert settings.critic.model == "critic-7b"
    assert settings.critic.base_url == "http://localhost:1234/v1"
    assert settings.critic.api_key == "secret"
    assert settings.critic.temperature == 0.2


def test_invalid_numbers_and_port_rejected():
    with pytest.raises(ValidationError, match="RPG_NARRATOR_TEMPERATURE"):
        load_settings({"RPG_NARRATOR_MODEL": "x", "RPG_NARRATOR_TEMPERATURE": "hot"})
    with pytest.raises(ValidationError, match="temperature"):
        load_settings({"RPG_NARRATOR_MODEL": "x", "RPG_NARRATOR_TEMPERATURE": "9"})
    with pytest.raises(ValidationError, match="RPG_SERVER_PORT"):
        load_settings({"RPG_NARRATOR_MODEL": "x", "RPG_SERVER_PORT": "0"})
    with pytest.raises(ValidationError, match="RPG_NARRATOR_MODEL"):
        load_settings({"RPG_NARRATOR_MODEL": "  "})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/backend/unit/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: sillytavern_rpg_engine.config`

- [ ] **Step 3: Add dependencies and implement `config.py`**

`pyproject.toml` — replace the empty dependency list:

```toml
dependencies = [
    "fastapi>=0.115",
    "httpx>=0.27",
    "langgraph>=1.0,<2",
    "uvicorn>=0.30",
]
```

Then run `python -m pip install -e ".[dev]"` once so later tasks can import the packages.

`src/sillytavern_rpg_engine/config.py`:

```python
"""Environment-based settings for the server and the two model roles."""

from dataclasses import dataclass
import os

from .domain.errors import ValidationError

_DEFAULT_BASE_URL = "http://127.0.0.1:5000/v1"


@dataclass(frozen=True)
class ModelConfig:
    """Connection parameters for one OpenAI-compatible model endpoint."""

    base_url: str
    api_key: str
    model: str
    temperature: float
    timeout_seconds: float
    max_tokens: int


@dataclass(frozen=True)
class Settings:
    """Runtime configuration; ``critic`` is None in single-model mode."""

    database_path: str
    host: str
    port: int
    max_history_messages: int
    narrator: ModelConfig
    critic: ModelConfig | None


def _float(env: dict[str, str], key: str, default: float) -> float:
    raw = env.get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        raise ValidationError(f"{key} must be a number") from None


def _int(env: dict[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValidationError(f"{key} must be an integer") from None


def _model(
    env: dict[str, str], prefix: str, fallback: ModelConfig | None
) -> ModelConfig | None:
    model = env.get(f"{prefix}_MODEL", "").strip()
    if not model:
        if prefix == "RPG_NARRATOR":
            raise ValidationError("RPG_NARRATOR_MODEL must not be empty")
        return None
    base_url = env.get(f"{prefix}_BASE_URL") or (
        fallback.base_url if fallback else _DEFAULT_BASE_URL
    )
    api_key = env.get(f"{prefix}_API_KEY") or (fallback.api_key if fallback else "")
    temperature = _float(env, f"{prefix}_TEMPERATURE", 0.8)
    if not 0.0 <= temperature <= 2.0:
        raise ValidationError(f"{prefix} temperature must be within 0..2")
    timeout = _float(env, f"{prefix}_TIMEOUT_SECONDS", 120.0)
    if timeout <= 0:
        raise ValidationError(f"{prefix} timeout must be positive")
    max_tokens = _int(env, f"{prefix}_MAX_TOKENS", 1024)
    if max_tokens < 1:
        raise ValidationError(f"{prefix} max_tokens must be positive")
    return ModelConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=temperature,
        timeout_seconds=timeout,
        max_tokens=max_tokens,
    )


def load_settings(env: dict[str, str] | None = None) -> Settings:
    """Build settings from ``env`` (defaults to ``os.environ``)."""
    env = os.environ if env is None else env
    narrator = _model(env, "RPG_NARRATOR", None)
    port = _int(env, "RPG_SERVER_PORT", 8000)
    if not 1 <= port <= 65535:
        raise ValidationError("RPG_SERVER_PORT must be within 1..65535")
    max_history = _int(env, "RPG_MAX_HISTORY_MESSAGES", 40)
    if max_history < 1:
        raise ValidationError("RPG_MAX_HISTORY_MESSAGES must be positive")
    return Settings(
        database_path=env.get("RPG_DATABASE", "./rpg.sqlite3"),
        host=env.get("RPG_SERVER_HOST", "127.0.0.1"),
        port=port,
        max_history_messages=max_history,
        narrator=narrator,
        critic=_model(env, "RPG_CRITIC", narrator),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/backend/unit/test_config.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: add env settings and api dependencies" \
  "pyproject.toml" \
  "src/sillytavern_rpg_engine/config.py" \
  "tests/backend/unit/test_config.py"
```

---

### Task 2: LLM client contracts and scripted test double

**Files:**
- Create: `src/sillytavern_rpg_engine/llm/__init__.py`
- Create: `src/sillytavern_rpg_engine/llm/client.py`
- Create: `src/sillytavern_rpg_engine/llm/scripted.py`
- Test: `tests/backend/unit/test_llm_client.py`

**Interfaces:**
- Consumes: nothing (contracts only).
- Produces: `ChatMessage(role, content)`; `LLMResponse(content, prompt_tokens, completion_tokens, model)`; `LLMError`; `LLMClient` protocol (`chat(messages, *, max_tokens=None) -> LLMResponse`, `ping() -> bool`); `ScriptedLLMClient(responses)` with a public `requests` list. Every node and the API layer depend on these names.

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from sillytavern_rpg_engine.llm import (
    ChatMessage,
    LLMError,
    LLMResponse,
    ScriptedLLMClient,
)


def test_scripted_client_returns_queued_responses_and_records_requests():
    client = ScriptedLLMClient(["first", LLMResponse("second", model="m2")])
    one = client.chat([ChatMessage("user", "hi")])
    two = client.chat([ChatMessage("user", "again")], max_tokens=5)
    assert one.content == "first"
    assert two.content == "second" and two.model == "m2"
    assert client.requests[0][0].content == "hi"
    assert len(client.requests) == 2


def test_scripted_client_raises_queued_errors_then_exhaustion():
    client = ScriptedLLMClient([LLMError("boom")])
    with pytest.raises(LLMError, match="boom"):
        client.chat([ChatMessage("user", "hi")])
    with pytest.raises(LLMError, match="exhausted"):
        client.chat([ChatMessage("user", "hi")])


def test_scripted_client_ping():
    assert ScriptedLLMClient([]).ping() is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/backend/unit/test_llm_client.py -v`
Expected: FAIL — `ModuleNotFoundError: sillytavern_rpg_engine.llm`

- [ ] **Step 3: Implement the contracts**

`src/sillytavern_rpg_engine/llm/client.py`:

```python
"""LLM client contracts: message/response values, error, and protocol."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass(frozen=True)
class LLMResponse:
    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""


class LLMError(RuntimeError):
    """The model endpoint failed or returned a malformed body."""


class LLMClient(Protocol):
    """Minimal synchronous chat interface used by every graph node."""

    def chat(
        self, messages: list[ChatMessage], *, max_tokens: int | None = None
    ) -> LLMResponse: ...

    def ping(self) -> bool: ...
```

`src/sillytavern_rpg_engine/llm/scripted.py`:

```python
"""Deterministic LLM double: queued responses, recorded requests."""

from .client import ChatMessage, LLMError, LLMResponse


class ScriptedLLMClient:
    """Returns queued strings/LLMResponses in order; re-raises queued errors."""

    def __init__(self, responses: list[str | LLMResponse | Exception]):
        self._responses = list(responses)
        self.requests: list[list[ChatMessage]] = []

    def chat(
        self, messages: list[ChatMessage], *, max_tokens: int | None = None
    ) -> LLMResponse:
        self.requests.append(list(messages))
        if not self._responses:
            raise LLMError("scripted responses exhausted")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, str):
            return LLMResponse(content=item, model="scripted")
        return item

    def ping(self) -> bool:
        return True
```

`src/sillytavern_rpg_engine/llm/__init__.py`:

```python
"""LLM client contracts and implementations."""

from .client import ChatMessage, LLMClient, LLMError, LLMResponse
from .scripted import ScriptedLLMClient

__all__ = [
    "ChatMessage",
    "LLMClient",
    "LLMError",
    "LLMResponse",
    "ScriptedLLMClient",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/backend/unit/test_llm_client.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: add llm client contracts and scripted double" \
  "src/sillytavern_rpg_engine/llm/__init__.py" \
  "src/sillytavern_rpg_engine/llm/client.py" \
  "src/sillytavern_rpg_engine/llm/scripted.py" \
  "tests/backend/unit/test_llm_client.py"
```

---

### Task 3: OpenAI-compatible HTTP client

**Files:**
- Create: `src/sillytavern_rpg_engine/llm/openai.py`
- Test: `tests/backend/unit/test_openai_client.py`

**Interfaces:**
- Consumes: `config.ModelConfig`, `llm/client.py` contracts, `httpx`.
- Produces: `OpenAIChatClient(config, transport=None)` implementing `LLMClient` (`chat`, `ping`, plus `close()`). `llm/__init__.py` re-exports it.

- [ ] **Step 1: Write the failing tests**

```python
import httpx
import pytest

from sillytavern_rpg_engine.config import ModelConfig
from sillytavern_rpg_engine.llm.client import ChatMessage, LLMError
from sillytavern_rpg_engine.llm.openai import OpenAIChatClient

CONFIG = ModelConfig(
    base_url="http://upstream.test/v1", api_key="sekret", model="narr",
    temperature=0.8, timeout_seconds=5.0, max_tokens=128,
)


def _client(handler):
    return OpenAIChatClient(CONFIG, transport=httpx.MockTransport(handler))


def test_chat_maps_response_and_sends_auth():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["json"] = request.read()
        return httpx.Response(200, json={
            "model": "narr",
            "choices": [{"message": {"role": "assistant", "content": "你好"}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7},
        })

    client = _client(handler)
    response = client.chat([ChatMessage("user", "hi")])
    assert response.content == "你好"
    assert response.prompt_tokens == 11 and response.completion_tokens == 7
    assert seen["auth"] == "Bearer sekret"
    assert b'"stream": false' in seen["json"]


def test_http_error_and_malformed_body_raise_llmerror():
    client = _client(lambda request: httpx.Response(500, text="down"))
    with pytest.raises(LLMError, match="500"):
        client.chat([ChatMessage("user", "hi")])
    client = _client(lambda request: httpx.Response(200, json={"oops": True}))
    with pytest.raises(LLMError, match="malformed"):
        client.chat([ChatMessage("user", "hi")])


def test_ping():
    ok = _client(lambda request: httpx.Response(200, json={"data": []}))
    down = _client(lambda request: httpx.Response(503))
    assert ok.ping() is True
    assert down.ping() is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/backend/unit/test_openai_client.py -v`
Expected: FAIL — `ModuleNotFoundError: sillytavern_rpg_engine.llm.openai`

- [ ] **Step 3: Implement `openai.py`**

```python
"""Synchronous OpenAI-compatible chat client over httpx."""

import httpx

from ..config import ModelConfig
from .client import ChatMessage, LLMError, LLMResponse


class OpenAIChatClient:
    """POSTs chat completions to one configured endpoint; never streams."""

    def __init__(self, config: ModelConfig, transport: httpx.BaseTransport | None = None):
        self._config = config
        self._client = httpx.Client(
            base_url=config.base_url,
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=config.timeout_seconds,
            transport=transport,
        )

    def chat(
        self, messages: list[ChatMessage], *, max_tokens: int | None = None
    ) -> LLMResponse:
        payload = {
            "model": self._config.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": self._config.temperature,
            "max_tokens": max_tokens or self._config.max_tokens,
            "stream": False,
        }
        try:
            response = self._client.post("/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            raise LLMError(
                f"model endpoint returned {exc.response.status_code}"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMError(f"model endpoint request failed: {exc}") from exc
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("malformed chat completion body") from exc
        if not isinstance(content, str):
            raise LLMError("malformed chat completion body")
        usage = data.get("usage") or {}
        return LLMResponse(
            content=content,
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            model=str(data.get("model", self._config.model)),
        )

    def ping(self) -> bool:
        try:
            return self._client.get("/models").status_code == 200
        except httpx.HTTPError:
            return False

    def close(self) -> None:
        self._client.close()
```

Append to `src/sillytavern_rpg_engine/llm/__init__.py`:

```python
from .openai import OpenAIChatClient
```

and add `"OpenAIChatClient"` to `__all__`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/backend/unit/test_openai_client.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: add openai-compatible llm client" \
  "src/sillytavern_rpg_engine/llm/openai.py" \
  "src/sillytavern_rpg_engine/llm/__init__.py" \
  "tests/backend/unit/test_openai_client.py"
```

---

### Task 4: Request normalization and tracker-instruction stripping

**Files:**
- Create: `src/sillytavern_rpg_engine/orchestration/__init__.py` (empty docstring module)
- Create: `src/sillytavern_rpg_engine/orchestration/normalize.py`
- Test: `tests/backend/unit/test_normalize.py`

**Interfaces:**
- Consumes: `llm/client.py` (`ChatMessage`), `domain.errors.ValidationError`.
- Produces: `NormalizedRequest(campaign_id, branch_id, messages, player_text, removed_instructions, history_hash, raw)`; `normalize_request(payload: dict) -> NormalizedRequest`; `strip_tracker_blocks(content: str) -> str` (reused by `narrative.py` for history cleanup). The graph's first node calls `normalize_request`.

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from sillytavern_rpg_engine.domain.errors import ValidationError
from sillytavern_rpg_engine.orchestration.normalize import (
    normalize_request,
    strip_tracker_blocks,
)

TRACKER_MESSAGE = (
    "You must update the Tracker at the end of every reply. Output exactly one"
    " tracker JSON block:\n```json\n{\"userStats\": {\"stats\": []},"
    " \"infoBox\": {}}\n```"
)


def _payload(**overrides):
    base = {
        "campaign_id": "c1",
        "messages": [
            {"role": "system", "content": "Character card text."},
            {"role": "user", "content": "我走进炼金铺。"},
        ],
    }
    base.update(overrides)
    return base


def test_normalize_happy_path_computes_hash_and_player_text():
    request = normalize_request(_payload())
    assert request.campaign_id == "c1"
    assert request.branch_id == "main"
    assert request.player_text == "我走进炼金铺。"
    assert len(request.history_hash) == 64
    assert request.removed_instructions == ()
    assert request.raw["campaign_id"] == "c1"


def test_tracker_instruction_message_removed_but_real_text_kept():
    request = normalize_request(_payload(messages=[
        {"role": "system", "content": TRACKER_MESSAGE},
        {"role": "user", "content": "我走进炼金铺。"},
    ]))
    assert [m.role for m in request.messages] == ["user"]
    assert len(request.removed_instructions) == 1


def test_similar_words_without_structure_are_never_dropped():
    text = "我想查看 tracker 上记录的炼金术进度。"
    request = normalize_request(_payload(messages=[{"role": "user", "content": text}]))
    assert request.player_text == text
    assert request.removed_instructions == ()


def test_mixed_message_strips_only_tracker_block():
    mixed = "继续剧情。\n```json\n{\"userStats\": {}}\n```"
    request = normalize_request(_payload(messages=[{"role": "user", "content": mixed}]))
    assert request.player_text == "继续剧情。"


def test_missing_campaign_stream_and_empty_user_message_rejected():
    with pytest.raises(ValidationError, match="campaign_id"):
        normalize_request(_payload(campaign_id=""))
    with pytest.raises(ValidationError, match="stream"):
        normalize_request(_payload(stream=True))
    with pytest.raises(ValidationError, match="user message"):
        normalize_request(_payload(messages=[{"role": "system", "content": "x"}]))
    with pytest.raises(ValidationError, match="role"):
        normalize_request(_payload(messages=[{"role": "tool", "content": "x"}]))


def test_strip_tracker_blocks_leaves_other_code_blocks():
    content = "看这段代码:\n```python\nprint(1)\n```\n```json\n{\"infoBox\": {}}\n```"
    assert strip_tracker_blocks(content) == "看这段代码:\n```python\nprint(1)\n```"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/backend/unit/test_normalize.py -v`
Expected: FAIL — `ModuleNotFoundError: sillytavern_rpg_engine.orchestration`

- [ ] **Step 3: Implement `normalize.py`**

`src/sillytavern_rpg_engine/orchestration/__init__.py`:

```python
"""Turn orchestration: normalization, routing, generation, gating, graph."""
```

`src/sillytavern_rpg_engine/orchestration/normalize.py`:

```python
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
        raw=payload,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/backend/unit/test_normalize.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: add request normalization and tracker stripping" \
  "src/sillytavern_rpg_engine/orchestration/__init__.py" \
  "src/sillytavern_rpg_engine/orchestration/normalize.py" \
  "tests/backend/unit/test_normalize.py"
```

---

### Task 5: Deterministic intent router

**Files:**
- Create: `src/sillytavern_rpg_engine/orchestration/intent.py`
- Test: `tests/backend/unit/test_intent.py`

**Interfaces:**
- Consumes: `NormalizedRequest.player_text`.
- Produces: `Intent(StrEnum)` with `ACTION`, `QUERY`, `EXPLICIT_CHANGE`; `route_intent(player_text: str) -> Intent`. The graph's conditional edge routes on `intent.value`.

Design rule: the router is deliberately conservative — only unambiguous prefixes/patterns classify as QUERY or EXPLICIT_CHANGE; everything else is ACTION (the full pipeline). A misrouted query that becomes an action costs one narrative turn; a misrouted action that becomes a query would silently drop state changes, which is worse.

- [ ] **Step 1: Write the failing tests**

```python
from sillytavern_rpg_engine.orchestration.intent import Intent, route_intent


def test_explicit_change_patterns():
    assert route_intent("给艾琳新增技能炼金术,当前35") is Intent.EXPLICIT_CHANGE
    assert route_intent("把艾琳的信任调整为60") is Intent.EXPLICIT_CHANGE
    assert route_intent("新增人物艾琳:半精灵炼金术师") is Intent.EXPLICIT_CHANGE
    assert route_intent("set alchemy to 35") is Intent.EXPLICIT_CHANGE
    assert route_intent("确认提案 p-102") is Intent.EXPLICIT_CHANGE
    assert route_intent("reject abc123") is Intent.EXPLICIT_CHANGE


def test_query_patterns():
    assert route_intent("查询艾琳的属性") is Intent.QUERY
    assert route_intent("查看背包") is Intent.QUERY
    assert route_intent("status") is Intent.QUERY


def test_everything_else_is_action():
    assert route_intent("我走进炼金铺,打量四周。") is Intent.ACTION
    assert route_intent("艾琳的信任是多少?") is Intent.ACTION  # 剧情内提问
    assert route_intent("") is Intent.ACTION
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/backend/unit/test_intent.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `intent.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/backend/unit/test_intent.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: add deterministic intent router" \
  "src/sillytavern_rpg_engine/orchestration/intent.py" \
  "tests/backend/unit/test_intent.py"
```

---

### Task 6: Scene entity scan and reference resolution

**Files:**
- Create: `src/sillytavern_rpg_engine/orchestration/scene.py`
- Test: `tests/backend/unit/test_scene.py`

**Interfaces:**
- Consumes: `persistence.database.Database`, `domain.errors` (`NotFoundError`, `AmbiguousEntityError`).
- Produces: `SceneScan(entity_ids, names)` where `names` always maps to the entity's canonical display name (never the matched alias); `scan_scene(connection, campaign_id, text) -> SceneScan`; `resolve_reference(connection, campaign_id, ref) -> str` (entity id; by id first, then name/alias; raises on zero or many matches). `extraction.py` resolves every LLM-emitted entity reference through `resolve_reference`; `graph.py` uses `scan_scene` for retrieval scoping.

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from sillytavern_rpg_engine.domain.errors import (
    AmbiguousEntityError,
    NotFoundError,
)
from sillytavern_rpg_engine.domain.models import EntityKind
from sillytavern_rpg_engine.orchestration.scene import (
    resolve_reference,
    scan_scene,
)
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)


def _world(database):
    campaigns = CampaignService(database, id_factory=lambda: "cid",
                                clock=lambda: "2026-08-13T00:00:00Z")
    campaigns.create_campaign("c1", "测试")
    entities = EntityAttributeService(database, campaigns.mutation_engine)
    version = 0  # create_campaign leaves state_version 0
    for entity_id, name, aliases in (
        ("erin", "艾琳", ("小艾",)),
        ("silvermoon", "银月城", ()),
        ("erin-2", "艾琳·复制体", ()),
    ):
        entities.apply_explicit("c1", "main", version, CreateEntityOperation(
            entity_id=entity_id, kind=EntityKind.CHARACTER, name=name,
            aliases=aliases,
        ))
        version += 1
    return entities


def test_scan_finds_names_and_aliases_longest_first(database):
    _world(database)
    with database.connect() as connection:
        scan = scan_scene(connection, "c1", "小艾和艾琳·复制体在银月城碰面")
    assert scan.names == {
        "erin": "艾琳",
        "erin-2": "艾琳·复制体",
        "silvermoon": "银月城",
    }


def test_scan_without_matches_is_empty(database):
    _world(database)
    with database.connect() as connection:
        assert scan_scene(connection, "c1", "荒野").entity_ids == ()


def test_resolve_reference_by_id_name_alias(database):
    _world(database)
    with database.connect() as connection:
        assert resolve_reference(connection, "c1", "erin") == "erin"
        assert resolve_reference(connection, "c1", "艾琳") == "erin"
        assert resolve_reference(connection, "c1", "小艾") == "erin"
        with pytest.raises(NotFoundError):
            resolve_reference(connection, "c1", "不存在的人")


def test_resolve_reference_ambiguous_name_and_alias(database):
    campaigns = CampaignService(database, id_factory=lambda: "cid",
                                clock=lambda: "2026-08-13T00:00:00Z")
    campaigns.create_campaign("c1", "测试")
    entities = EntityAttributeService(database, campaigns.mutation_engine)
    entities.apply_explicit("c1", "main", 0, CreateEntityOperation(
        entity_id="a", kind=EntityKind.CHARACTER, name="星光",
    ))
    entities.apply_explicit("c1", "main", 1, CreateEntityOperation(
        entity_id="b", kind=EntityKind.CHARACTER, name="另一个人", aliases=("星光",),
    ))
    with database.connect() as connection:
        with pytest.raises(AmbiguousEntityError):
            resolve_reference(connection, "c1", "星光")
```

Note: `CreateEntityOperation(entity_id, kind, name, age_status=AgeStatus.UNKNOWN, aliases=())` — the test above uses this exact signature.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/backend/unit/test_scene.py -v`
Expected: FAIL — `ModuleNotFoundError: sillytavern_rpg_engine.orchestration.scene`

- [ ] **Step 3: Implement `scene.py`**

```python
"""Scene entity scanning and entity reference resolution."""

from dataclasses import dataclass
import sqlite3

from ..domain.errors import AmbiguousEntityError, NotFoundError
from ..services.entities import normalize_key


@dataclass(frozen=True)
class SceneScan:
    """Entities mentioned in the player text, in order of first mention."""

    entity_ids: tuple[str, ...]
    names: dict[str, str]


def scan_scene(
    connection: sqlite3.Connection, campaign_id: str, text: str
) -> SceneScan:
    """Substring-match entity names and aliases, longest first, no overlap."""
    names = connection.execute(
        "SELECT id, name FROM entities WHERE campaign_id = ?",
        (campaign_id,),
    ).fetchall()
    aliases = connection.execute(
        "SELECT entity_id, alias FROM entity_aliases WHERE campaign_id = ?",
        (campaign_id,),
    ).fetchall()
    canonical = {row["id"]: row["name"] for row in names}
    candidates = [
        (row["name"].casefold(), row["id"]) for row in names
    ] + [
        (row["alias"].casefold(), row["entity_id"]) for row in aliases
    ]
    candidates = [c for c in candidates if c[0]]
    candidates.sort(key=lambda c: -len(c[0]))
    haystack = text.casefold()
    claimed: list[tuple[int, int]] = []
    found: dict[str, int] = {}
    for needle, entity_id in candidates:
        start = haystack.find(needle)
        while start != -1:
            end = start + len(needle)
            if not any(s < end and start < e for s, e in claimed):
                claimed.append((start, end))
                if entity_id not in found:
                    found[entity_id] = start
                break
            start = haystack.find(needle, start + 1)
    ordered = sorted(found.items(), key=lambda item: item[1])
    return SceneScan(
        entity_ids=tuple(entity_id for entity_id, _ in ordered),
        names={entity_id: canonical[entity_id] for entity_id, _ in ordered},
    )


def resolve_reference(
    connection: sqlite3.Connection, campaign_id: str, ref: str
) -> str:
    """Resolve one LLM- or user-supplied reference to a unique entity id."""
    if not isinstance(ref, str) or not ref.strip():
        raise NotFoundError("empty entity reference")
    ref = ref.strip()
    row = connection.execute(
        "SELECT id FROM entities WHERE campaign_id = ? AND id = ?",
        (campaign_id, ref),
    ).fetchone()
    if row is not None:
        return row["id"]
    normalized = normalize_key(ref)
    rows = connection.execute(
        "SELECT id FROM entities WHERE campaign_id = ?"
        " AND (normalized_name = ? OR id IN ("
        " SELECT entity_id FROM entity_aliases"
        " WHERE campaign_id = ? AND normalized_alias = ?))",
        (campaign_id, normalized, campaign_id, normalized),
    ).fetchall()
    if not rows:
        raise NotFoundError(f"entity {ref!r} not found in campaign {campaign_id}")
    if len(rows) > 1:
        raise AmbiguousEntityError(
            f"entity reference {ref!r} is ambiguous in campaign {campaign_id}"
        )
    return rows[0]["id"]
```

Note: `scan_scene` matches with `casefold()` on raw text instead of `normalize_key`, because `normalize_key` rewrites non-alphanumeric runs to `_` and would break substring matching ("艾琳·复制体" normalizes to "艾琳_复制体", which never appears in prose). `resolve_reference` reuses `normalize_key` so name/alias matching is identical to `EntityService.resolve`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/backend/unit/test_scene.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: add scene scan and reference resolution" \
  "src/sillytavern_rpg_engine/orchestration/scene.py" \
  "tests/backend/unit/test_scene.py"
```

---

### Task 7: Migration 0005 — turn records

**Files:**
- Create: `src/sillytavern_rpg_engine/persistence/schema/0005_turns.sql`
- Create: `src/sillytavern_rpg_engine/services/turns.py`
- Test: `tests/backend/unit/test_turns.py`

**Interfaces:**
- Consumes: `persistence.database.Database`, `domain.errors.NotFoundError`.
- Produces: `Turn` dataclass; `TurnService(database, id_factory=None, clock=None)` with `record(campaign_id, branch_id, *, intent, player_text, response_text, history_hash, state_before_version, state_after_version, turn_id: str | None = None) -> Turn`, `latest(campaign_id, branch_id) -> Turn | None`, `get(turn_id) -> Turn`. The optional `turn_id` lets the graph mint the id at turn start so extraction and memory events reference it before the row exists. The graph's commit node is the only writer in this phase; Phase 6 reads `history_hash`/`parent_turn_id` for swipe recovery.

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from sillytavern_rpg_engine.domain.errors import NotFoundError
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.turns import TurnService


def _turns(database):
    campaigns = CampaignService(database, id_factory=lambda: "cid",
                                clock=lambda: "2026-08-13T00:00:00Z")
    campaigns.create_campaign("c1", "测试")
    ids = iter(f"turn-{n}" for n in range(1, 100))
    return TurnService(
        database,
        id_factory=lambda: next(ids),
        clock=lambda: "2026-08-13T01:00:00Z",
    )


def test_record_links_parent_and_reads_back(database):
    turns = _turns(database)
    first = turns.record(
        "c1", "main", intent="action", player_text="开始",
        response_text="世界展开。", history_hash="a" * 64,
        state_before_version=0, state_after_version=3,
    )
    second = turns.record(
        "c1", "main", intent="query", player_text="查询状态",
        response_text="……", history_hash="b" * 64,
        state_before_version=3, state_after_version=3,
    )
    assert first.parent_turn_id is None
    assert second.parent_turn_id == "turn-1"
    assert turns.latest("c1", "main").id == "turn-2"
    assert turns.get("turn-1").player_text == "开始"
    with pytest.raises(NotFoundError):
        turns.get("nope")


def test_record_validates_version_order_and_text(database):
    turns = _turns(database)
    with pytest.raises(Exception, match="state_after_version"):
        turns.record(
            "c1", "main", intent="action", player_text="x",
            response_text="y", history_hash="h",
            state_before_version=5, state_after_version=4,
        )
    with pytest.raises(Exception, match="player_text"):
        turns.record(
            "c1", "main", intent="action", player_text="  ",
            response_text="y", history_hash="h",
            state_before_version=0, state_after_version=0,
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/backend/unit/test_turns.py -v`
Expected: FAIL — migration 0005 missing / module missing

- [ ] **Step 3: Implement the migration and service**

`src/sillytavern_rpg_engine/persistence/schema/0005_turns.sql`:

```sql
CREATE TABLE turns (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    parent_turn_id TEXT REFERENCES turns(id),
    intent TEXT NOT NULL CHECK (intent IN ('action', 'query', 'explicit_change')),
    player_text TEXT NOT NULL,
    response_text TEXT NOT NULL,
    history_hash TEXT NOT NULL,
    removed_instructions_json TEXT NOT NULL DEFAULT '[]',
    state_before_version INTEGER NOT NULL CHECK (state_before_version >= 0),
    state_after_version INTEGER NOT NULL
        CHECK (state_after_version >= state_before_version),
    created_at TEXT NOT NULL
);

CREATE INDEX idx_turns_campaign_branch ON turns(campaign_id, branch_id, created_at);
CREATE INDEX idx_turns_history_hash ON turns(campaign_id, history_hash);
```

`src/sillytavern_rpg_engine/services/turns.py`:

```python
"""Turn records: one row per committed game turn (bookkeeping, not state)."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Callable
from uuid import uuid4

from ..domain.errors import NotFoundError, ValidationError
from ..persistence.database import Database


@dataclass(frozen=True)
class Turn:
    id: str
    campaign_id: str
    branch_id: str
    parent_turn_id: str | None
    intent: str
    player_text: str
    response_text: str
    history_hash: str
    removed_instructions: tuple[str, ...]
    state_before_version: int
    state_after_version: int
    created_at: str


def _to_turn(row) -> Turn:
    return Turn(
        id=row["id"],
        campaign_id=row["campaign_id"],
        branch_id=row["branch_id"],
        parent_turn_id=row["parent_turn_id"],
        intent=row["intent"],
        player_text=row["player_text"],
        response_text=row["response_text"],
        history_hash=row["history_hash"],
        removed_instructions=tuple(json.loads(row["removed_instructions_json"])),
        state_before_version=row["state_before_version"],
        state_after_version=row["state_after_version"],
        created_at=row["created_at"],
    )


class TurnService:
    """Append-only turn log; parentage follows the branch's latest turn."""

    def __init__(
        self,
        database: Database,
        id_factory: Callable[[], str] = lambda: uuid4().hex,
        clock: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(),
    ):
        self.database = database
        self.id_factory = id_factory
        self.clock = clock

    def record(
        self,
        campaign_id: str,
        branch_id: str,
        *,
        intent: str,
        player_text: str,
        response_text: str,
        history_hash: str,
        state_before_version: int,
        state_after_version: int,
        turn_id: str | None = None,
        removed_instructions: tuple[str, ...] = (),
    ) -> Turn:
        if not player_text.strip():
            raise ValidationError("player_text must not be empty")
        if state_after_version < state_before_version:
            raise ValidationError(
                "state_after_version must be >= state_before_version"
            )
        turn_id = turn_id or self.id_factory()
        now = self.clock()
        with self.database.transaction() as connection:
            parent = connection.execute(
                "SELECT id FROM turns WHERE campaign_id = ? AND branch_id = ?"
                " ORDER BY created_at DESC, id DESC LIMIT 1",
                (campaign_id, branch_id),
            ).fetchone()
            connection.execute(
                "INSERT INTO turns(id, campaign_id, branch_id, parent_turn_id,"
                " intent, player_text, response_text, history_hash,"
                " removed_instructions_json,"
                " state_before_version, state_after_version, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    turn_id, campaign_id, branch_id,
                    parent["id"] if parent else None,
                    intent, player_text, response_text, history_hash,
                    json.dumps(list(removed_instructions), ensure_ascii=False),
                    state_before_version, state_after_version, now,
                ),
            )
        return Turn(
            id=turn_id, campaign_id=campaign_id, branch_id=branch_id,
            parent_turn_id=parent["id"] if parent else None,
            intent=intent, player_text=player_text, response_text=response_text,
            history_hash=history_hash,
            removed_instructions=tuple(removed_instructions),
            state_before_version=state_before_version,
            state_after_version=state_after_version, created_at=now,
        )

    def latest(self, campaign_id: str, branch_id: str) -> Turn | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM turns WHERE campaign_id = ? AND branch_id = ?"
                " ORDER BY created_at DESC, id DESC LIMIT 1",
                (campaign_id, branch_id),
            ).fetchone()
        return _to_turn(row) if row else None

    def get(self, turn_id: str) -> Turn:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM turns WHERE id = ?", (turn_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"turn {turn_id} not found")
        return _to_turn(row)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/backend/unit/test_turns.py tests/backend/unit/test_migrations.py -v`
Expected: all pass (migration tests pick up 0005 automatically)

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: add turn records" \
  "src/sillytavern_rpg_engine/persistence/schema/0005_turns.sql" \
  "src/sillytavern_rpg_engine/services/turns.py" \
  "tests/backend/unit/test_turns.py"
```

---

### Task 8: Narrator prompt assembly and generation

**Files:**
- Create: `src/sillytavern_rpg_engine/orchestration/narrative.py`
- Test: `tests/backend/unit/test_narrative.py`

**Interfaces:**
- Consumes: `llm/client.py`, `normalize.strip_tracker_blocks`.
- Produces: `NARRATOR_SYSTEM_PROMPT`, `ANSWER_SYSTEM_PROMPT`; `build_messages(context, history, *, feedback=None, max_history=40, system_prompt=NARRATOR_SYSTEM_PROMPT) -> list[ChatMessage]`; `generate(client, context, history, *, feedback=None, max_history=40, system_prompt=...) -> LLMResponse`. Used by graph nodes `narrate`, `answer`, and by `critic.py` for the rewrite.

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/backend/unit/test_narrative.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `narrative.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/backend/unit/test_narrative.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: add narrator prompt assembly" \
  "src/sillytavern_rpg_engine/orchestration/narrative.py" \
  "tests/backend/unit/test_narrative.py"
```

---

### Task 9: LLM proposal extraction with one correction retry

**Files:**
- Create: `src/sillytavern_rpg_engine/orchestration/extraction.py`
- Test: `tests/backend/unit/test_extraction.py`

**Interfaces:**
- Consumes: `llm/client.py`, `services/proposals.py` (`OperationCodec`), `orchestration/scene.py` (`resolve_reference`), `domain/errors.py`.
- Produces: `ExtractedOperation(payload, reason)`; `ExtractionResult(operations, error, drops)`; `extract_operations(client, *, campaign_id, player_text, narrative, context, turn_id, connection, id_factory) -> ExtractionResult`. The gate (Task 10) consumes `ExtractedOperation.payload` via `OperationCodec.decode`.

Extraction-time repairs, all deterministic and logged in `drops`:
- `set_rules` operations are always dropped — rules enable/disable never comes from chat (Phase 3 invariant: readiness-gated `DndRulesService` only).
- `define_attribute.campaign_id` is force-set to the current campaign.
- Empty server-id fields (`fact_id`, `event_id`, `arc_id`) are filled by `id_factory`; `turn_id` fields (`turn_id`, `start_turn_id`, `end_turn_id`) are filled with the current turn id when null.
- Entity reference fields are resolved through `resolve_reference` (id → name → alias); `create_entity.entity_id` is generated when empty, never resolved. `record_memory_event.participant_entity_ids` / `location_entity_id` resolve element-wise.
- A per-operation `reason` key is popped before codec decode (the codec rejects unknown keys).
- Any operation that fails repair/validation is dropped with a note; the rest survive. Whole-response invalid JSON gets exactly one correction retry.

- [ ] **Step 1: Write the failing tests**

```python
import json

from sillytavern_rpg_engine.domain.models import EntityKind
from sillytavern_rpg_engine.llm import ScriptedLLMClient
from sillytavern_rpg_engine.orchestration.extraction import extract_operations
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)


def _world(database):
    campaigns = CampaignService(database, id_factory=lambda: "cid",
                                clock=lambda: "2026-08-13T00:00:00Z")
    campaigns.create_campaign("c1", "测试")
    entities = EntityAttributeService(database, campaigns.mutation_engine)
    entities.apply_explicit("c1", "main", 0, CreateEntityOperation(
        entity_id="erin", kind=EntityKind.CHARACTER, name="艾琳",
    ))
    ids = iter(f"gen-{n}" for n in range(1, 100))
    return lambda: next(ids)


def _extract(database, client, id_factory, narrative="剧情草稿"):
    with database.connect() as connection:
        return extract_operations(
            client,
            campaign_id="c1", player_text="玩家输入", narrative=narrative,
            context={"entities": []}, turn_id="turn-1",
            connection=connection, id_factory=id_factory,
        )


def test_valid_operations_are_repaired_and_decodable(database):
    id_factory = _world(database)
    client = ScriptedLLMClient([json.dumps({
        "operations": [
            {"kind": "assert_fact", "fact_id": "", "entity_id": "艾琳",
             "fact_type": "general", "fact_key": "职业", "content": "炼金术师",
             "importance": 3, "audiences": ["engine", "narrator"],
             "turn_id": None, "reason": "自我介绍"},
            {"kind": "set_rules", "mode": "dnd-2024", "enabled": True,
             "version": None, "custom_preset_id": None},
        ]
    }, ensure_ascii=False)])
    result = _extract(database, client, id_factory)
    assert result.error is None
    assert len(result.operations) == 1
    operation = result.operations[0]
    assert operation.payload["fact_id"] == "gen-1"
    assert operation.payload["entity_id"] == "erin"
    assert operation.payload["turn_id"] == "turn-1"
    assert operation.reason == "自我介绍"
    assert "set_rules" in result.drops[0]["error"]


def test_invalid_json_gets_one_correction_retry(database):
    id_factory = _world(database)
    client = ScriptedLLMClient([
        "这不是 JSON",
        json.dumps({"operations": []}),
    ])
    result = _extract(database, client, id_factory)
    assert result.error is None
    assert result.operations == ()
    assert len(client.requests) == 2
    assert "不是 JSON" in client.requests[1][-2].content  # 回显错误输出


def test_second_failure_yields_error_and_no_operations(database):
    id_factory = _world(database)
    client = ScriptedLLMClient(["坏", "还是坏"])
    result = _extract(database, client, id_factory)
    assert result.error is not None
    assert result.operations == ()


def test_unknown_entity_reference_drops_only_that_operation(database):
    id_factory = _world(database)
    client = ScriptedLLMClient([json.dumps({
        "operations": [
            {"kind": "set_attribute", "entity_id": "不存在", "attribute_key": "hp",
             "value": 1, "turn_id": None},
            {"kind": "set_attribute", "entity_id": "erin", "attribute_key": "hp",
             "value": 1, "turn_id": None},
        ]
    })])
    result = _extract(database, client, id_factory)
    assert len(result.operations) == 1
    assert result.operations[0].payload["entity_id"] == "erin"
    assert len(result.drops) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/backend/unit/test_extraction.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `extraction.py`**

```python
"""LLM change-proposal extraction: parse, repair, validate — never apply."""

from dataclasses import dataclass
import json
import sqlite3
from typing import Any, Callable

from ..domain.errors import ValidationError
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
            except ValidationError as exc:
                drops.append({"operation": raw, "error": str(exc)})
        return ExtractionResult(tuple(operations), None, tuple(drops))
    return ExtractionResult((), last_error, ())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/backend/unit/test_extraction.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: add llm proposal extraction" \
  "src/sillytavern_rpg_engine/orchestration/extraction.py" \
  "tests/backend/unit/test_extraction.py"
```

---

### Task 10: Authorization gate and proposal commands

**Files:**
- Create: `src/sillytavern_rpg_engine/orchestration/gate.py`
- Test: `tests/backend/unit/test_gate.py`

**Interfaces:**
- Consumes: `services/mutations.py` (`MutationEngine`, `MutationRequest`), `services/proposals.py` (`ProposalService`, `OperationCodec`), `orchestration/intent.py` (`Intent`), `orchestration/extraction.py` (`ExtractedOperation`), `persistence/repositories.py` (`CampaignRepository`).
- Produces: `GateResult(applied, pending, dropped, message)`; `parse_proposal_command(text) -> tuple[str, str] | None`; `run_proposal_command(database, proposal_service, command, proposal_id) -> GateResult`; `run_gate(database, mutation_engine, proposal_service, campaign_id, branch_id, intent, operations) -> GateResult`. Graph nodes `gate_explicit` / `gate_action` call these.

Gate semantics:
- EXPLICIT_CHANGE: decode + apply each operation immediately through `MutationEngine` (`source="user-command"`, `event_type="explicit-state-change"`), re-reading the campaign version before each apply so sequential operations chain. Domain errors drop that operation with the error message; earlier applied operations stay applied (each was its own complete transaction).
- ACTION: every operation becomes a Pending proposal via `ProposalService.create` (no version change, no state change).
- `确认提案 <id>` / `approve <id>` / `拒绝提案 <id>` / `reject <id>` are parsed deterministically and executed via `ProposalService.approve` / `.reject`; approval re-checks the base version (existing Phase 1 logic).

- [ ] **Step 1: Write the failing tests**

```python
from sillytavern_rpg_engine.domain.models import EntityKind
from sillytavern_rpg_engine.orchestration.extraction import ExtractedOperation
from sillytavern_rpg_engine.orchestration.gate import (
    parse_proposal_command,
    run_gate,
    run_proposal_command,
)
from sillytavern_rpg_engine.orchestration.intent import Intent
from sillytavern_rpg_engine.services.attributes import DefineAttributeOperation
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)
from sillytavern_rpg_engine.services.proposals import ProposalService


def _world(database):
    campaigns = CampaignService(database, id_factory=lambda: "cid",
                                clock=lambda: "2026-08-13T00:00:00Z")
    campaigns.create_campaign("c1", "测试")
    entities = EntityAttributeService(database, campaigns.mutation_engine)
    entities.apply_explicit("c1", "main", 0, CreateEntityOperation(
        entity_id="erin", kind=EntityKind.CHARACTER, name="艾琳",
    ))
    proposals = ProposalService(database, campaigns.mutation_engine,
                                id_factory=lambda: "prop-1")
    return campaigns, entities, proposals


def _define_alchemy():
    from sillytavern_rpg_engine.domain.models import (
        AttributeDefinition, AttributeType, Audience, DisplayType,
    )
    return DefineAttributeOperation(AttributeDefinition(
        campaign_id="c1", key="alchemy", label="炼金术", category="skill",
        value_type=AttributeType.NUMBER, display=DisplayType.BAR,
        audiences=frozenset(Audience), minimum=0, maximum=100,
    ))


def test_parse_proposal_command():
    assert parse_proposal_command("确认提案 p-102") == ("approve", "p-102")
    assert parse_proposal_command("reject abc") == ("reject", "abc")
    assert parse_proposal_command("继续剧情") is None


def test_explicit_operations_apply_immediately(database):
    campaigns, entities, proposals = _world(database)
    entities.apply_explicit("c1", "main", 1, _define_alchemy())
    result = run_gate(
        database, campaigns.mutation_engine, proposals, "c1", "main",
        Intent.EXPLICIT_CHANGE,
        (ExtractedOperation(
            {"kind": "set_attribute", "entity_id": "erin",
             "attribute_key": "alchemy", "value": 35, "turn_id": None},
            "玩家指令",
        ),),
    )
    assert len(result.applied) == 1 and not result.pending and not result.dropped
    assert "已应用" in result.message
    with database.connect() as connection:
        row = connection.execute(
            "SELECT value_json FROM attribute_values"
            " WHERE entity_id = 'erin' AND attribute_key = 'alchemy'"
        ).fetchone()
        assert row["value_json"] == "35"


def test_action_operations_become_pending_proposals(database):
    campaigns, entities, proposals = _world(database)
    result = run_gate(
        database, campaigns.mutation_engine, proposals, "c1", "main",
        Intent.ACTION,
        (ExtractedOperation(
            {"kind": "set_attribute", "entity_id": "erin",
             "attribute_key": "alchemy", "value": 40, "turn_id": None},
            "剧情推断",
        ),),
    )
    assert not result.applied and len(result.pending) == 1
    assert result.pending[0]["proposal_id"] == "prop-1"
    assert "待确认" in result.message


def test_invalid_operation_is_dropped_with_error(database):
    campaigns, entities, proposals = _world(database)
    result = run_gate(
        database, campaigns.mutation_engine, proposals, "c1", "main",
        Intent.EXPLICIT_CHANGE,
        (ExtractedOperation({"kind": "set_attribute", "entity_id": "erin",
                             "attribute_key": "alchemy", "value": "abc",
                             "turn_id": None}, ""),
        ),
    )
    assert not result.applied and len(result.dropped) == 1
    assert "拒绝" in result.message


`run_proposal_command` converts domain errors into a `GateResult` message instead of raising, so the graph never crashes on a bad command:

```python
def test_proposal_command_approve_and_reject(database):
    campaigns, entities, proposals = _world(database)
    proposals.create("c1", "main", {
        "kind": "set_attribute", "entity_id": "erin", "attribute_key": "alchemy",
        "value": 40, "turn_id": None,
    }, "剧情推断")
    rejected = run_proposal_command(database, proposals, "reject", "prop-1")
    assert "已拒绝" in rejected.message
    again = run_proposal_command(database, proposals, "approve", "prop-1")
    assert "not pending" in again.message
    missing = run_proposal_command(database, proposals, "approve", "nope")
    assert "not found" in missing.message
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/backend/unit/test_gate.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `gate.py`**

```python
"""Authorization gate: explicit commands apply, inferences become proposals."""

from dataclasses import dataclass
import re
from typing import Any

from ..domain.errors import DomainError
from ..persistence.database import Database
from ..persistence.repositories import CampaignRepository
from ..services.mutations import MutationEngine, MutationRequest
from ..services.proposals import OperationCodec, ProposalService
from .extraction import ExtractedOperation
from .intent import Intent

_PROPOSAL_COMMAND = re.compile(
    r"^(?:确认提案|approve)\s+(\S+)$|^(?:拒绝提案|reject)\s+(\S+)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GateResult:
    applied: tuple[dict[str, Any], ...]
    pending: tuple[dict[str, Any], ...]
    dropped: tuple[dict[str, Any], ...]
    message: str


def parse_proposal_command(text: str) -> tuple[str, str] | None:
    """Deterministic proposal confirmation: (\"approve\"|\"reject\", id)."""
    match = _PROPOSAL_COMMAND.match(text.strip())
    if not match:
        return None
    if match.group(1):
        return ("approve", match.group(1))
    return ("reject", match.group(2))


def _current_version(database: Database, campaign_id: str) -> int:
    with database.connect() as connection:
        return CampaignRepository().require(connection, campaign_id).state_version


def _summarize(applied, pending, dropped) -> str:
    parts = []
    if applied:
        parts.append(f"已应用 {len(applied)} 项变更。")
    if pending:
        parts.append(f"记录 {len(pending)} 项待确认提案。")
    if dropped:
        reasons = "; ".join(d["error"] for d in dropped)
        parts.append(f"拒绝 {len(dropped)} 项变更:{reasons}")
    return "".join(parts)


def run_proposal_command(
    database: Database,
    proposal_service: ProposalService,
    command: str,
    proposal_id: str,
) -> GateResult:
    """Execute one deterministic proposal approve/reject command."""
    try:
        if command == "reject":
            proposal_service.reject(proposal_id)
            return GateResult((), (), (), f"已拒绝提案 {proposal_id}。")
        proposal_service.approve(
            proposal_id, expected_version=_current_version(
                database, proposal_service.get(proposal_id).campaign_id
            ),
        )
        return GateResult(
            ({"kind": "approve_proposal", "proposal_id": proposal_id},), (), (),
            f"提案 {proposal_id} 已确认并应用。",
        )
    except DomainError as exc:
        return GateResult((), (), (), str(exc))


def run_gate(
    database: Database,
    mutation_engine: MutationEngine,
    proposal_service: ProposalService,
    campaign_id: str,
    branch_id: str,
    intent: Intent,
    operations: tuple[ExtractedOperation, ...],
) -> GateResult:
    """Apply explicit operations; park inferred ones as Pending proposals."""
    applied: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for extracted in operations:
        try:
            operation = OperationCodec.decode(extracted.payload)
        except DomainError as exc:
            dropped.append({"operation": extracted.payload, "error": str(exc)})
            continue
        if intent is Intent.ACTION:
            proposal = proposal_service.create(
                campaign_id, branch_id, extracted.payload,
                extracted.reason or "剧情推断",
            )
            pending.append({
                "proposal_id": proposal.id,
                "kind": extracted.payload["kind"],
                "operation": extracted.payload,
                "reason": extracted.reason,
            })
            continue
        try:
            mutation_engine.apply(MutationRequest(
                campaign_id=campaign_id,
                branch_id=branch_id,
                expected_version=_current_version(database, campaign_id),
                source="user-command",
                event_type="explicit-state-change",
                operation=operation,
            ))
            applied.append({
                "kind": extracted.payload["kind"],
                "operation": extracted.payload,
            })
        except DomainError as exc:
            dropped.append({"operation": extracted.payload, "error": str(exc)})
    return GateResult(
        tuple(applied), tuple(pending), tuple(dropped),
        _summarize(applied, pending, dropped),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/backend/unit/test_gate.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: add authorization gate and proposal commands" \
  "src/sillytavern_rpg_engine/orchestration/gate.py" \
  "tests/backend/unit/test_gate.py"
```

---

### Task 11: Consistency critic with one narrative rewrite

**Files:**
- Create: `src/sillytavern_rpg_engine/orchestration/critic.py`
- Test: `tests/backend/unit/test_critic.py`

**Interfaces:**
- Consumes: `llm/client.py`, `narrative.generate`, `gate.GateResult`.
- Produces: `CriticVerdict(consistent, issues, raw)`; `CriticOutcome(narrative, verdict, rewritten)`; `run_critic(*, critic, narrator, draft, context, history, gate, max_history=40) -> CriticOutcome`. The graph's `critic` node is the only caller.

Critic rules: the critic sees the draft plus the gate's applied/pending/dropped JSON and the pre-gate state context (the applied list carries the post-gate truth, so pre-gate context is sufficient). Invalid verdict JSON is fail-open (treated as consistent) and kept in `raw` for diagnostics. An inconsistent verdict triggers exactly one narrator rewrite with the issues as feedback; the rewrite is used unconditionally — the critic never edits state, proposals, or dice.

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/backend/unit/test_critic.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `critic.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/backend/unit/test_critic.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: add consistency critic" \
  "src/sillytavern_rpg_engine/orchestration/critic.py" \
  "tests/backend/unit/test_critic.py"
```

---

### Task 12: Tracker presenter and pending-proposal listing

**Files:**
- Modify: `src/sillytavern_rpg_engine/services/proposals.py` (add `ProposalService.pending`)
- Create: `src/sillytavern_rpg_engine/orchestration/presenter.py`
- Test: `tests/backend/unit/test_presenter.py`

**Interfaces:**
- Consumes: `persistence` repositories, `services/proposals.py`, `domain/models.py` (`Audience`, `EntityKind`).
- Produces: `ProposalService.pending(campaign_id, branch_id) -> list[Proposal]`; `TrackerPresenter(database)` with `build(campaign_id, branch_id) -> dict` and `render(campaign_id, branch_id) -> str` (fenced ```json block). The graph's `respond` node calls `render`.

Tracker contract (spec §12.2): root keys `rules` (mode/enabled/version, plus `custom_preset_id` only when set), `userStats` (empty scaffold — the compat fork fills display), `infoBox` ({}), `characters` (one entry per character-kind entity with player_ui-visible dynamic attributes), plus the extra root key `pending_proposals` (the compat fork preserves unknown root keys; Phase 5 renders them).

- [ ] **Step 1: Write the failing tests**

```python
import json

from sillytavern_rpg_engine.domain.models import (
    AttributeDefinition,
    AttributeType,
    Audience,
    DisplayType,
    EntityKind,
)
from sillytavern_rpg_engine.orchestration.presenter import TrackerPresenter
from sillytavern_rpg_engine.services.attributes import (
    DefineAttributeOperation,
    SetAttributeOperation,
)
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)
from sillytavern_rpg_engine.services.proposals import ProposalService


def _world(database):
    campaigns = CampaignService(database, id_factory=lambda: "cid",
                                clock=lambda: "2026-08-13T00:00:00Z")
    campaigns.create_campaign("c1", "测试")
    attributes = EntityAttributeService(database, campaigns.mutation_engine)
    attributes.apply_explicit("c1", "main", 0, CreateEntityOperation(
        entity_id="erin", kind=EntityKind.CHARACTER, name="艾琳",
    ))
    attributes.apply_explicit("c1", "main", 1, CreateEntityOperation(
        entity_id="silvermoon", kind=EntityKind.LOCATION, name="银月城",
    ))
    return campaigns, attributes


def _define(key, audiences):
    return DefineAttributeOperation(AttributeDefinition(
        campaign_id="c1", key=key, label=f"标签{key}", category="skill",
        value_type=AttributeType.NUMBER, display=DisplayType.BAR,
        audiences=frozenset(audiences), minimum=0, maximum=100,
    ))


def test_build_contains_rules_characters_and_visible_attributes_only(database):
    campaigns, attributes = _world(database)
    attributes.apply_explicit("c1", "main", 2, _define(
        "alchemy", {Audience.ENGINE, Audience.NARRATOR, Audience.PLAYER_UI},
    ))
    attributes.apply_explicit("c1", "main", 3, _define(
        "secret", {Audience.ENGINE},
    ))
    tracker = TrackerPresenter(database).build("c1", "main")
    assert tracker["rules"] == {"mode": "narrative", "enabled": False,
                                "version": None}
    assert tracker["userStats"]["attributes"] == []
    assert [c["name"] for c in tracker["characters"]] == ["艾琳"]
    keys = [a["key"] for a in tracker["characters"][0]["attributes"]]
    assert keys == []  # 尚无值
    assert tracker["pending_proposals"] == []


def test_values_rendered_and_hidden_keys_never_leak(database):
    campaigns, attributes = _world(database)
    attributes.apply_explicit("c1", "main", 2, _define(
        "alchemy", {Audience.PLAYER_UI},
    ))
    attributes.apply_explicit("c1", "main", 3, _define(
        "secret", {Audience.ENGINE},
    ))
    attributes.apply_explicit("c1", "main", 4, SetAttributeOperation(
        "erin", "alchemy", 35, None,
    ))
    attributes.apply_explicit("c1", "main", 5, SetAttributeOperation(
        "erin", "secret", 99, None,
    ))
    tracker = TrackerPresenter(database).build("c1", "main")
    attributes_out = tracker["characters"][0]["attributes"]
    assert [a["key"] for a in attributes_out] == ["alchemy"]
    assert attributes_out[0]["value"] == 35
    assert attributes_out[0]["max"] == 100
    assert "secret" not in json.dumps(tracker, ensure_ascii=False)


def test_pending_proposals_listed_and_render_wraps_json(database):
    campaigns, attributes = _world(database)
    proposals = ProposalService(database, campaigns.mutation_engine,
                                id_factory=lambda: "prop-1")
    proposals.create("c1", "main", {
        "kind": "set_attribute", "entity_id": "erin", "attribute_key": "alchemy",
        "value": 40, "turn_id": None,
    }, "剧情推断")
    presenter = TrackerPresenter(database)
    tracker = presenter.build("c1", "main")
    assert [p["id"] for p in tracker["pending_proposals"]] == ["prop-1"]
    rendered = presenter.render("c1", "main")
    assert rendered.startswith("```json\n") and rendered.endswith("\n```")
    json.loads(rendered.removeprefix("```json\n").removesuffix("\n```"))
```

Note: `attributes.py` defines only operations; `EntityAttributeService` (from `services/entities.py`) applies both entity and attribute operations via `apply_explicit`, as the test shows.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/backend/unit/test_presenter.py -v`
Expected: FAIL — `ProposalService.pending` missing / module missing

- [ ] **Step 3: Implement**

Append to `ProposalService` in `src/sillytavern_rpg_engine/services/proposals.py`:

```python
    def pending(self, campaign_id: str, branch_id: str) -> list[Proposal]:
        """List Pending proposals for one branch, oldest first."""
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT id, campaign_id, branch_id, base_state_version,"
                " operation_json, reason, status, created_at, resolved_at"
                " FROM pending_proposals"
                " WHERE campaign_id = ? AND branch_id = ? AND status = 'pending'"
                " ORDER BY created_at, id",
                (campaign_id, branch_id),
            ).fetchall()
        return [_to_proposal(row) for row in rows]
```

Create `src/sillytavern_rpg_engine/orchestration/presenter.py`:

```python
"""Tracker JSON presentation built from committed, player-visible state."""

import json
from typing import Any

from ..domain.models import Audience, EntityKind
from ..persistence.database import Database
from ..persistence.repositories import CampaignRepository
from ..services.mutations import MutationEngine
from ..services.proposals import ProposalService


class TrackerPresenter:
    """Render the spec §12.2 Tracker payload; read-only, audience-filtered."""

    def __init__(self, database: Database):
        self.database = database
        self.campaign_repository = CampaignRepository()
        self.proposals = ProposalService(database, MutationEngine(database))

    def build(self, campaign_id: str, branch_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            campaign = self.campaign_repository.require(connection, campaign_id)
            definitions = connection.execute(
                "SELECT key, label, category, value_type, display, maximum,"
                " audiences_json FROM attribute_definitions"
                " WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchall()
            values = connection.execute(
                "SELECT entity_id, attribute_key, value_json"
                " FROM attribute_values WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchall()
            entities = connection.execute(
                "SELECT id, kind, name FROM entities WHERE campaign_id = ?"
                " ORDER BY normalized_name",
                (campaign_id,),
            ).fetchall()
        visible = {
            row["key"]: row for row in definitions
            if Audience.PLAYER_UI.value in json.loads(row["audiences_json"])
        }
        by_entity: dict[str, list] = {}
        for row in values:
            if row["attribute_key"] in visible:
                by_entity.setdefault(row["entity_id"], []).append(row)
        characters = []
        for entity in entities:
            if entity["kind"] != EntityKind.CHARACTER.value:
                continue
            attributes = []
            for value in sorted(
                by_entity.get(entity["id"], []),
                key=lambda row: row["attribute_key"],
            ):
                definition = visible[value["attribute_key"]]
                attributes.append({
                    "key": definition["key"],
                    "label": definition["label"],
                    "category": definition["category"],
                    "type": definition["value_type"],
                    "value": json.loads(value["value_json"]),
                    "max": definition["maximum"],
                    "display": definition["display"],
                })
            characters.append({
                "name": entity["name"],
                "details": {},
                "relationship": {},
                "attributes": attributes,
            })
        rules = {
            "mode": campaign.rules.mode.value,
            "enabled": campaign.rules.enabled,
            "version": campaign.rules.version,
        }
        if campaign.rules.custom_preset_id is not None:
            rules["custom_preset_id"] = campaign.rules.custom_preset_id
        return {
            "rules": rules,
            "userStats": {
                "stats": [], "status": {}, "skills": [],
                "inventory": {}, "quests": {}, "attributes": [],
            },
            "infoBox": {},
            "characters": characters,
            "pending_proposals": [
                {
                    "id": proposal.id,
                    "operation": proposal.operation,
                    "reason": proposal.reason,
                    "base_state_version": proposal.base_state_version,
                }
                for proposal in self.proposals.pending(campaign_id, branch_id)
            ],
        }

    def render(self, campaign_id: str, branch_id: str) -> str:
        """The single fenced Tracker block appended to every response."""
        return (
            "```json\n"
            + json.dumps(
                self.build(campaign_id, branch_id), ensure_ascii=False, indent=2
            )
            + "\n```"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/backend/unit/test_presenter.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: add tracker presenter and pending listing" \
  "src/sillytavern_rpg_engine/services/proposals.py" \
  "src/sillytavern_rpg_engine/orchestration/presenter.py" \
  "tests/backend/unit/test_presenter.py"
```

---

### Task 13: LangGraph turn pipeline and TurnRunner

**Files:**
- Modify: `src/sillytavern_rpg_engine/services/memory_events.py` (add `record_transcript`)
- Create: `src/sillytavern_rpg_engine/orchestration/graph.py`
- Test: `tests/backend/unit/test_graph.py`

**Interfaces:**
- Consumes: every earlier task — `normalize_request`, `route_intent`/`Intent`, `scan_scene`, `RetrievalService`/`RetrievalQuery`, `narrative.generate` (+ prompts), `extract_operations`, `run_gate`/`run_proposal_command`/`parse_proposal_command`/`GateResult`, `run_critic`, `TrackerPresenter`, `TurnService`, `MemoryEventService`/`MemoryEventType`, `MutationEngine`, `ProposalService`, `Settings`, `LLMClient`.
- Produces: `TurnServices` dataclass; `default_services(database, settings, narrator, critic=None) -> TurnServices`; `TurnState(TypedDict)`; `build_graph(services)`; `TurnRunner(services).run(payload: dict) -> dict` (OpenAI chat completion body). The API layer (Task 14) calls only `TurnRunner.run`.

Graph topology (all edges deterministic; the only conditional is intent after retrieval):

```text
START → normalize → route → scene → retrieve ─┬─ query → answer ─────────────┐
                                              ├─ explicit_change → gate_explicit → commit ─┤
                                              └─ action → narrate → extract → gate_action → critic → commit ─┤
                                                                                                             ↓
                                                                            respond → END
```

- `normalize` also mints `turn_id` (via `TurnService.id_factory`) and reads `state_before_version`.
- `commit` is a no-op for query turns. For action turns it records two transcript memory events (`玩家:…` and `叙事:…`, type SCENE, importance 3, all audiences, scene participants, `source="turn"`) via the new `MemoryEventService.record_transcript`, then the turn row. For explicit turns it records only the turn row (no memory noise from system confirmations).
- Transcript events MUST NOT bump `state_version`: they are bookkeeping like the turns row. If they went through `MutationEngine`, every action turn would bump the version twice and every Pending proposal would be stale before the player could confirm it (`ProposalService.approve` requires `current == base_state_version`). `record_transcript` writes the event + FTS row + participants in one plain transaction stamped with the current version; the turn row is its audit record.
- Extraction failure in the action path yields zero operations and an `extraction_error` note in the response — state is never committed on invalid LLM output.
- The response `usage` object is zeros; token accounting is not part of the authoritative contract.

- [ ] **Step 1: Write the failing tests**

```python
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
    campaigns = CampaignService(database, id_factory=lambda: "cid",
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/backend/unit/test_graph.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `record_transcript` and `graph.py`**

First, extend `src/sillytavern_rpg_engine/services/memory_events.py`. In `MemoryEventService.__init__`, also store the clock (`self.clock = clock` — it is currently only forwarded to the internally built `MutationEngine`). Then add:

```python
    def record_transcript(
        self,
        campaign_id: str,
        branch_id: str,
        *,
        type: MemoryEventType,
        content: str,
        importance: int,
        audiences: frozenset[Audience],
        participants: tuple[str, ...] = (),
        turn_id: str | None = None,
    ) -> str:
        """Append a turn transcript event WITHOUT bumping the state version.

        Transcripts are bookkeeping (like the turns row): a version bump per
        turn would invalidate every Pending proposal before the player can
        confirm it. The turns row is the audit record for the transcript.
        """
        if not content.strip():
            raise ValidationError("memory event content must not be empty")
        validate_importance(importance)
        validate_audiences(audiences)
        event_id = self.id_factory()
        with self.database.transaction() as connection:
            campaign = self.campaign_repository.require(connection, campaign_id)
            self.branch_repository.require(connection, campaign_id, branch_id)
            for entity_id in participants:
                if connection.execute(
                    "SELECT id FROM entities WHERE id = ? AND campaign_id = ?",
                    (entity_id, campaign_id),
                ).fetchone() is None:
                    raise NotFoundError(
                        f"entity {entity_id!r} not found in campaign"
                        f" {campaign_id}"
                    )
            connection.execute(
                "INSERT INTO memory_events(id, campaign_id, branch_id, turn_id,"
                " event_type, content, importance, audiences_json,"
                " location_entity_id, source, state_version, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 'turn', ?, ?)",
                (
                    event_id, campaign_id, branch_id, turn_id, type.value,
                    content, importance,
                    dump_json(sorted(a.value for a in audiences)),
                    campaign.state_version, self.clock(),
                ),
            )
            connection.execute(
                "INSERT INTO memory_events_fts(rowid, content)"
                " SELECT rowid, content FROM memory_events WHERE id = ?",
                (event_id,),
            )
            for entity_id in dict.fromkeys(participants):
                connection.execute(
                    "INSERT INTO memory_event_participants(campaign_id,"
                    " event_id, entity_id) VALUES (?, ?, ?)",
                    (campaign_id, event_id, entity_id),
                )
        return event_id
```

This requires `MemoryEventService` to hold `campaign_repository`/`branch_repository` attributes — add in `__init__`:

```python
        self.campaign_repository = CampaignRepository()
        self.branch_repository = BranchRepository()
```

with the import line changed to `from ..persistence.repositories import BranchRepository, CampaignRepository, dump_json` (`dump_json`, `validate_importance`, `validate_audiences`, `ValidationError`, `NotFoundError`, `Audience`, `MemoryEventType` are already imported in `memory_events.py` — reuse them).

Then create `src/sillytavern_rpg_engine/orchestration/graph.py`:

```python
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
from .normalize import NormalizedRequest, normalize_request
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


def build_graph(services: TurnServices):
    database = services.database
    campaigns = CampaignRepository()

    def _version(campaign_id: str) -> int:
        with database.connect() as connection:
            return campaigns.require(connection, campaign_id).state_version

    def node_normalize(state: TurnState) -> dict[str, Any]:
        request = normalize_request(state["raw"])
        return {
            "request": request,
            "turn_id": services.turns.id_factory(),
            "state_before_version": _version(request.campaign_id),
        }

    def node_route(state: TurnState) -> dict[str, Any]:
        return {"intent": route_intent(state["request"].player_text).value}

    def node_scene(state: TurnState) -> dict[str, Any]:
        request = state["request"]
        with database.connect() as connection:
            scan = scan_scene(
                connection, request.campaign_id, request.player_text
            )
        return {"scene_entity_ids": scan.entity_ids}

    def node_retrieve(state: TurnState) -> dict[str, Any]:
        request = state["request"]
        context = services.retrieval.assemble(RetrievalQuery(
            campaign_id=request.campaign_id,
            branch_id=request.branch_id,
            audiences=frozenset({Audience.ENGINE, Audience.NARRATOR}),
            text=request.player_text,
            scene_entity_ids=state["scene_entity_ids"],
        ))
        return {"context": context}

    def node_answer(state: TurnState) -> dict[str, Any]:
        request = state["request"]
        response = generate(
            services.narrator, state["context"], request.messages,
            max_history=services.settings.max_history_messages,
            system_prompt=ANSWER_SYSTEM_PROMPT,
        )
        return {"narrative": response.content.strip()}

    def node_narrate(state: TurnState) -> dict[str, Any]:
        request = state["request"]
        response = generate(
            services.narrator, state["context"], request.messages,
            max_history=services.settings.max_history_messages,
        )
        return {"narrative": response.content.strip()}

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

    def _store_gate(result: GateResult) -> dict[str, Any]:
        return {
            "applied": result.applied,
            "pending": result.pending,
            "dropped": result.dropped,
            "gate_message": result.message,
        }

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
            request.campaign_id, request.branch_id,
            Intent.EXPLICIT_CHANGE, extraction.operations,
        ))

    def node_gate_action(state: TurnState) -> dict[str, Any]:
        request = state["request"]
        return _store_gate(run_gate(
            database, services.mutation_engine, services.proposals,
            request.campaign_id, request.branch_id,
            Intent.ACTION, state.get("operations", ()),
        ))

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

    def node_commit(state: TurnState) -> dict[str, Any]:
        request = state["request"]
        intent = state["intent"]
        if intent == Intent.QUERY.value:
            return {}
        response_text = state.get("narrative") or state.get("gate_message", "")
        if intent == Intent.ACTION.value:
            audiences = frozenset(Audience)
            participants = state.get("scene_entity_ids", ())
            services.memory.record_transcript(
                request.campaign_id, request.branch_id,
                type=MemoryEventType.SCENE,
                content=f"玩家:{request.player_text}",
                importance=3, audiences=audiences, participants=participants,
                turn_id=state["turn_id"],
            )
            services.memory.record_transcript(
                request.campaign_id, request.branch_id,
                type=MemoryEventType.SCENE,
                content=f"叙事:{response_text}",
                importance=3, audiences=audiences, participants=participants,
                turn_id=state["turn_id"],
            )
        after_version = _version(request.campaign_id)
        services.turns.record(
            request.campaign_id, request.branch_id,
            intent=intent,
            player_text=request.player_text,
            response_text=response_text,
            history_hash=request.history_hash,
            state_before_version=state["state_before_version"],
            state_after_version=after_version,
            turn_id=state["turn_id"],
            removed_instructions=request.removed_instructions,
        )
        return {}

    def node_respond(state: TurnState) -> dict[str, Any]:
        request = state["request"]
        tracker = TrackerPresenter(database).render(
            request.campaign_id, request.branch_id
        )
        parts = []
        if state.get("narrative"):
            parts.append(state["narrative"])
        if state.get("gate_message"):
            parts.append(state["gate_message"])
        if state.get("extraction_error"):
            parts.append(f"(状态变更解析失败:{state['extraction_error']})")
        content = "\n\n".join(parts) + "\n\n" + tracker
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

    def _route_after_retrieve(state: TurnState) -> str:
        return state["intent"]

    builder = StateGraph(TurnState)
    builder.add_node("normalize", node_normalize)
    builder.add_node("route", node_route)
    builder.add_node("scene", node_scene)
    builder.add_node("retrieve", node_retrieve)
    builder.add_node("answer", node_answer)
    builder.add_node("narrate", node_narrate)
    builder.add_node("extract", node_extract)
    builder.add_node("gate_explicit", node_gate_explicit)
    builder.add_node("gate_action", node_gate_action)
    builder.add_node("critic", node_critic)
    builder.add_node("commit", node_commit)
    builder.add_node("respond", node_respond)
    builder.add_edge(START, "normalize")
    builder.add_edge("normalize", "route")
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/backend/unit/test_graph.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: add langgraph turn pipeline" \
  "src/sillytavern_rpg_engine/orchestration/graph.py" \
  "tests/backend/unit/test_graph.py"
```

---

### Task 14: FastAPI app, serve command, and end-to-end integration

**Files:**
- Create: `src/sillytavern_rpg_engine/server/__init__.py` (empty docstring module)
- Create: `src/sillytavern_rpg_engine/server/app.py`
- Modify: `src/sillytavern_rpg_engine/cli.py` (add `serve`)
- Modify: `src/sillytavern_rpg_engine/services/campaign_export.py` (`_build_payload` → public `build_payload`; keep the private name as an alias so existing callers don't break)
- Modify: `README.md` (document the server, env vars, SillyTavern wiring)
- Test: `tests/backend/unit/test_api.py`
- Test: `tests/backend/integration/test_phase4_flow.py`

**Interfaces:**
- Consumes: `TurnRunner`/`default_services` (Task 13), `load_settings`, `OpenAIChatClient`, `CampaignExporter`, `MigrationRunner`, domain errors.
- Produces: `create_app(settings, database, narrator, critic=None) -> FastAPI`; CLI `serve --database PATH [--host H] [--port P]`. This is the deliverable SillyTavern points at (`http://127.0.0.1:8000/v1`).

API contract:
- `GET /v1/models` → `{"object": "list", "data": [{"id": <narrator model>, "object": "model", "created": 0, "owned_by": "rpg-engine"}, ...critic if configured]}`.
- `POST /v1/chat/completions` → OpenAI chat completion body from `TurnRunner.run`. Request JSON is read as a raw object (extra SillyTavern fields tolerated).
- `GET /health` → `{"status": "ok"|"degraded", "schema_version": int, "integrity": "ok"|error, "models": {"narrator": "configured", "critic": "configured"|"disabled"}}`; `?deep=1` additionally pings both upstreams (`"up"|"down"`).
- `GET /admin/campaigns/{campaign_id}/export` → the export payload JSON (no API keys by Phase 1 design).
- Error mapping (OpenAI error envelope `{"error": {"message", "type", "code"}}`): `ValidationError` → 400 `invalid_request_error`; `NotFoundError` → 404; `StaleStateError`/`AmbiguousEntityError` → 409; `LLMError` → 502 `upstream_error`; any other `DomainError` → 400; unexpected `Exception` → 500 `internal_error` (no traceback leak in the body).

- [ ] **Step 1: Write the failing API tests**

`tests/backend/unit/test_api.py`:

```python
import json

from fastapi.testclient import TestClient

from sillytavern_rpg_engine.config import ModelConfig, Settings
from sillytavern_rpg_engine.llm import ScriptedLLMClient
from sillytavern_rpg_engine.server.app import create_app
from sillytavern_rpg_engine.services.campaigns import CampaignService

NARRATOR_CFG = ModelConfig("http://x/v1", "", "narrator-model", 0.8, 5.0, 512)


def _client(database, narrator):
    settings = Settings(
        database_path=":memory:", host="127.0.0.1", port=8000,
        max_history_messages=40, narrator=NARRATOR_CFG, critic=None,
    )
    return TestClient(create_app(settings, database, narrator))


def _campaign(database):
    campaigns = CampaignService(database, id_factory=lambda: "cid",
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
    client = _client(database, ScriptedLLMClient([]))
    response = client.get("/admin/campaigns/c1/export")
    assert response.status_code == 200
    assert response.json()["campaign"]["id"] == "c1"
    missing = client.get("/admin/campaigns/nope/export")
    assert missing.status_code == 404


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/backend/unit/test_api.py -v`
Expected: FAIL — `ModuleNotFoundError: sillytavern_rpg_engine.server`

- [ ] **Step 3: Implement the app, export tweak, and CLI command**

`src/sillytavern_rpg_engine/server/__init__.py`:

```python
"""HTTP adapter: FastAPI app exposing the OpenAI-compatible API."""
```

`src/sillytavern_rpg_engine/server/app.py`:

```python
"""FastAPI app: OpenAI-compatible endpoints over the turn pipeline."""

import sqlite3
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..config import Settings
from ..domain.errors import (
    AmbiguousEntityError,
    DomainError,
    NotFoundError,
    StaleStateError,
    ValidationError,
)
from ..llm.client import LLMClient, LLMError
from ..orchestration.graph import TurnRunner, default_services
from ..persistence.database import Database
from ..services.campaign_export import CampaignExporter

_STATUS = {
    ValidationError: (400, "invalid_request_error"),
    NotFoundError: (404, "not_found"),
    StaleStateError: (409, "stale_state"),
    AmbiguousEntityError: (409, "ambiguous_entity"),
    LLMError: (502, "upstream_error"),
}


def _error_body(exc: Exception, type_: str) -> dict[str, Any]:
    return {"error": {"message": str(exc), "type": type_, "code": None}}


def create_app(
    settings: Settings,
    database: Database,
    narrator: LLMClient,
    critic: LLMClient | None = None,
    *,
    degraded: bool = False,
) -> FastAPI:
    """Build the API around a wired turn runner; no module-level state.

    ``degraded=True`` is the read-only diagnostic mode for failed schema
    migrations: health and export answer, chat returns 503.
    """
    app = FastAPI(title="SillyTavern RPG Engine", version="0.1.0")
    runner = None if degraded else TurnRunner(
        default_services(database, settings, narrator, critic)
    )
    exporter = CampaignExporter(database)

    @app.exception_handler(DomainError)
    async def _domain_error(request: Request, exc: DomainError):
        status, type_ = _STATUS.get(type(exc), (400, "invalid_request_error"))
        return JSONResponse(_error_body(exc, type_), status_code=status)

    @app.exception_handler(LLMError)
    async def _llm_error(request: Request, exc: LLMError):
        return JSONResponse(_error_body(exc, "upstream_error"), status_code=502)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        return JSONResponse(
            _error_body(RuntimeError("internal error"), "internal_error"),
            status_code=500,
        )

    @app.get("/v1/models")
    def models() -> dict[str, Any]:
        data = [{
            "id": settings.narrator.model, "object": "model",
            "created": 0, "owned_by": "rpg-engine",
        }]
        if settings.critic is not None:
            data.append({
                "id": settings.critic.model, "object": "model",
                "created": 0, "owned_by": "rpg-engine",
            })
        return {"object": "list", "data": data}

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> dict[str, Any]:
        if runner is None:
            return JSONResponse(
                _error_body(
                    RuntimeError("engine is in read-only diagnostic mode"),
                    "degraded_mode",
                ),
                status_code=503,
            )
        return runner.run(await request.json())

    @app.get("/health")
    def health(deep: int = 0) -> dict[str, Any]:
        try:
            with database.connect() as connection:
                integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
                version = connection.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone()[0]
        except sqlite3.Error as exc:
            return JSONResponse(
                {"status": "degraded", "integrity": str(exc)}, status_code=503
            )
        models: dict[str, str] = {
            "narrator": "configured",
            "critic": "configured" if settings.critic is not None else "disabled",
        }
        if deep:
            models["narrator"] = "up" if narrator.ping() else "down"
            if critic is not None:
                models["critic"] = "up" if critic.ping() else "down"
        return {
            "status": "degraded" if (degraded or integrity != "ok") else "ok",
            "integrity": integrity,
            "schema_version": version,
            "models": models,
        }

    @app.get("/admin/campaigns/{campaign_id}/export")
    def admin_export(campaign_id: str) -> dict[str, Any]:
        return exporter.build_payload(campaign_id)

    return app
```

`src/sillytavern_rpg_engine/services/campaign_export.py` — rename `_build_payload` to `build_payload` (public) and update the internal caller in `export()`; keep behavior identical:

```python
    def export(self, campaign_id: str, path: Path) -> Path:
        payload = self.build_payload(campaign_id)
        # ... rest unchanged ...

    def build_payload(self, campaign_id: str) -> dict[str, Any]:
        """Assemble the full export document for one campaign."""
        # ... body identical to the previous _build_payload ...
```

`src/sillytavern_rpg_engine/cli.py` — add the serve subcommand inside `_build_parser()`:

```python
    serve = subparsers.add_parser(
        "serve", help="run the OpenAI-compatible API server"
    )
    _add_database_argument(serve)
    serve.add_argument("--host", default=None, help="bind host (env RPG_SERVER_HOST)")
    serve.add_argument("--port", type=int, default=None,
                       help="bind port (env RPG_SERVER_PORT)")
    serve.set_defaults(handler=_run_serve)
```

and the handler:

```python
def _run_serve(args: argparse.Namespace) -> int:
    try:
        settings = load_settings()
    except DomainError as exc:
        print(f"serve failed: {exc}", file=sys.stderr)
        return 1
    database = Database(args.database)
    degraded = False
    try:
        MigrationRunner(database).apply()
    except sqlite3.Error as exc:
        print(
            f"serve: migration failed ({exc}); starting in read-only"
            " diagnostic mode",
            file=sys.stderr,
        )
        degraded = True
    narrator = OpenAIChatClient(settings.narrator)
    critic = OpenAIChatClient(settings.critic) if settings.critic else None
    app = create_app(settings, database, narrator, critic, degraded=degraded)
    import uvicorn
    uvicorn.run(
        app,
        host=args.host or settings.host,
        port=args.port or settings.port,
    )
    return 0
```

with new imports at the top of `cli.py`:

```python
from .config import load_settings
from .llm.openai import OpenAIChatClient
from .server.app import create_app
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/backend/unit/test_api.py -v`
Expected: 5 passed

- [ ] **Step 5: Write the integration test**

`tests/backend/integration/test_phase4_flow.py` — a full session over the HTTP boundary: create world via services, then drive chat turns through `TestClient`: action turn with an inferred proposal, proposal confirmation, explicit change, read-only query, and a tracker-instruction-laden request that must survive cleaning.

```python
import json

from fastapi.testclient import TestClient

from sillytavern_rpg_engine.config import ModelConfig, Settings
from sillytavern_rpg_engine.domain.models import (
    AttributeDefinition,
    AttributeType,
    Audience,
    DisplayType,
    EntityKind,
)
from sillytavern_rpg_engine.llm import ScriptedLLMClient
from sillytavern_rpg_engine.server.app import create_app
from sillytavern_rpg_engine.services.attributes import DefineAttributeOperation
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)

NARRATOR_CFG = ModelConfig("http://x/v1", "", "narrator-model", 0.8, 5.0, 512)

TRACKER_INSTRUCTION = (
    "You must update the Tracker in every reply.\n"
    "```json\n{\"userStats\": {\"stats\": []}, \"infoBox\": {}}\n```"
)


def test_phase4_full_flow(database):
    campaigns = CampaignService(database, id_factory=lambda: "cid",
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
    narrator = ScriptedLLMClient([
        "你推门而入,艾琳正在蒸馏药剂。",                    # turn 1 narrate
        json.dumps({"operations": [{                        # turn 1 extract
            "kind": "set_attribute", "entity_id": "erin",
            "attribute_key": "alchemy", "value": 36, "turn_id": None,
            "reason": "艾琳在练习",
        }]}),
        json.dumps({"operations": [{                        # turn 3 extract
            "kind": "set_attribute", "entity_id": "erin",
            "attribute_key": "alchemy", "value": 35, "turn_id": None,
        }]}),
        "艾琳的炼金术是 35,最高 100。",                     # turn 4 answer
    ])
    settings = Settings(
        database_path=":memory:", host="127.0.0.1", port=8000,
        max_history_messages=40, narrator=NARRATOR_CFG, critic=None,
    )
    client = TestClient(create_app(settings, database, narrator))

    def chat(text, **extra):
        payload = {"campaign_id": "c1",
                   "messages": [{"role": "system", "content": TRACKER_INSTRUCTION},
                                {"role": "user", "content": text}]}
        payload.update(extra)
        response = client.post("/v1/chat/completions", json=payload)
        assert response.status_code == 200
        return response.json()["choices"][0]["message"]["content"]

    # Turn 1: action -> narrative + one pending proposal + tracker block.
    content = chat("我走进炼金铺。")
    assert "你推门而入" in content and "待确认" in content
    assert "```json" in content
    with database.connect() as connection:
        proposal_id = connection.execute(
            "SELECT id FROM pending_proposals WHERE status = 'pending'"
        ).fetchone()["id"]

    # Turn 2: confirm the proposal -> applied.
    content = chat(f"确认提案 {proposal_id}")
    assert "已确认并应用" in content
    with database.connect() as connection:
        assert connection.execute(
            "SELECT value_json FROM attribute_values"
            " WHERE entity_id = 'erin' AND attribute_key = 'alchemy'"
        ).fetchone()["value_json"] == "36"

    # Turn 3: explicit change -> applied immediately.
    content = chat("把艾琳的炼金术调整为35")
    assert "已应用 1 项变更" in content
    with database.connect() as connection:
        assert connection.execute(
            "SELECT value_json FROM attribute_values"
            " WHERE entity_id = 'erin' AND attribute_key = 'alchemy'"
        ).fetchone()["value_json"] == "35"

    # Turn 4: read-only query -> answer, no new turn row beyond turns 1-3.
    with database.connect() as connection:
        before = connection.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
    content = chat("查询艾琳的炼金术")
    assert "35" in content
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == before
        # Tracker instructions were stripped, never stored as memory.
        contents = " ".join(
            row["content"] for row in connection.execute(
                "SELECT content FROM memory_events"
            ).fetchall()
        )
        assert "userStats" not in contents

    # Turn 5: unknown campaign -> 404 OpenAI envelope.
    response = client.post("/v1/chat/completions", json={
        "campaign_id": "ghost",
        "messages": [{"role": "user", "content": "x"}],
    })
    assert response.status_code == 404
    assert response.json()["error"]["type"] == "not_found"
```

- [ ] **Step 6: Run the integration test**

Run: `python -m pytest tests/backend/integration/test_phase4_flow.py -v`
Expected: 1 passed

- [ ] **Step 7: Update README and run the full suite + guard**

Add to `README.md` (new section after the existing usage docs):

```markdown
## OpenAI-compatible API server

Install with API dependencies and start the server:

```bash
python -m pip install -e ".[dev]"
python -m sillytavern_rpg_engine serve --database ./rpg.sqlite3
```

SillyTavern → API Connection → Custom OpenAI Compatible:

- Base URL: `http://127.0.0.1:8000/v1`
- Custom Body: `{"campaign_id": "campaign-001"}`

Environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `RPG_NARRATOR_MODEL` | (required) | Narrator model id |
| `RPG_NARRATOR_BASE_URL` | `http://127.0.0.1:5000/v1` | Narrator endpoint |
| `RPG_NARRATOR_API_KEY` | empty | Narrator key |
| `RPG_NARRATOR_TEMPERATURE` | `0.8` | Sampling temperature |
| `RPG_NARRATOR_TIMEOUT_SECONDS` | `120` | Upstream timeout |
| `RPG_NARRATOR_MAX_TOKENS` | `1024` | Reply cap |
| `RPG_CRITIC_MODEL` | unset | Critic model id; unset = single-model mode |
| `RPG_CRITIC_BASE_URL` / `RPG_CRITIC_API_KEY` | inherit narrator | Critic endpoint |
| `RPG_CRITIC_TEMPERATURE` / `RPG_CRITIC_TIMEOUT_SECONDS` / `RPG_CRITIC_MAX_TOKENS` | `0.8` / `120` / `1024` | Critic sampling |
| `RPG_SERVER_HOST` / `RPG_SERVER_PORT` | `127.0.0.1` / `8000` | Bind address |
| `RPG_DATABASE` | `./rpg.sqlite3` | Default database path |
| `RPG_MAX_HISTORY_MESSAGES` | `40` | Prompt history cap |

Endpoints: `GET /v1/models`, `POST /v1/chat/completions` (non-streaming),
`GET /health` (`?deep=1` pings upstreams), `GET /admin/campaigns/{id}/export`.

Chat commands: `确认提案 <id>` / `reject <id>` manage pending proposals;
`查询…` is read-only; explicit changes (`把X调整为Y`) apply immediately.
```

Run: `python -m pytest tests/backend -q`
Expected: all pass
Run: `python .harness/scripts/guard.py src/sillytavern_rpg_engine tests/backend`
Expected: no findings

- [ ] **Step 8: Commit**

```bash
bash .harness/scripts/committer "feat: add fastapi server and serve command" \
  "src/sillytavern_rpg_engine/server/__init__.py" \
  "src/sillytavern_rpg_engine/server/app.py" \
  "src/sillytavern_rpg_engine/services/campaign_export.py" \
  "src/sillytavern_rpg_engine/cli.py" \
  "tests/backend/unit/test_api.py" \
  "tests/backend/integration/test_phase4_flow.py" \
  "README.md"
```

---

## Exit Criteria

- `python -m pytest tests/backend -q` passes, including `test_phase4_flow.py`.
- `python .harness/scripts/guard.py src/sillytavern_rpg_engine tests/backend` reports no findings.
- `python -m sillytavern_rpg_engine serve --database ./rpg.sqlite3` boots; `curl http://127.0.0.1:8000/v1/models` answers; a SillyTavern Custom OpenAI Compatible profile pointed at `http://127.0.0.1:8000/v1` with body `{"campaign_id": ...}` completes a full turn (narrative + one Tracker JSON block).
- Invariants hold end-to-end: no LLM output touches state without `OperationCodec` validation; explicit changes apply immediately, inferences are Pending-only; invalid structured output never commits; the critic rewrites text only; `stream: true` is rejected; tracker instructions are stripped with an audit trail; query turns write nothing.
