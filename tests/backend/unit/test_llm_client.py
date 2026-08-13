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
