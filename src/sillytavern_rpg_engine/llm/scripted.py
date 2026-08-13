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
