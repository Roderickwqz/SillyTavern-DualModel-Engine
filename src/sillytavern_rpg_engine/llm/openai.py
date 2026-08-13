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
