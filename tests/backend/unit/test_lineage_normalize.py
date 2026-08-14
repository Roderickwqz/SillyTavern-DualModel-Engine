from sillytavern_rpg_engine.llm.client import ChatMessage
from sillytavern_rpg_engine.orchestration.normalize import (
    lineage_hash_after,
    lineage_hash_before,
    normalize_request,
    response_hash,
)


def test_lineage_hashes_exclude_tracker_blocks():
    msgs = (
        ChatMessage("user", "你好"),
        ChatMessage("assistant", "回复\n```json\n{\"userStats\":{}}\n```"),
        ChatMessage("user", "继续"),
    )
    before = lineage_hash_before(msgs)
    after = lineage_hash_after(
        msgs[:-1],
        "回复\n```json\n{\"userStats\":{}}\n```",
    )
    assert len(before) == 64
    assert len(after) == 64
    assert before != after
    assert response_hash("回复\n```json\n{\"userStats\":{}}\n```") == response_hash("回复")


def test_normalize_request_includes_lineage_hash_before():
    request = normalize_request({
        "campaign_id": "c1",
        "messages": [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
        ],
    })
    assert request.lineage_hash_before == lineage_hash_before(request.messages)
