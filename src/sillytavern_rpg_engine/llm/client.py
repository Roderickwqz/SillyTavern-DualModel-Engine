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
