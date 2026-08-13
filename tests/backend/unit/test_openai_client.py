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
    assert b'"stream":false' in seen["json"]


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
