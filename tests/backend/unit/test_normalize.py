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
