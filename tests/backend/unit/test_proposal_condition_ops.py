import pytest

from sillytavern_rpg_engine.domain.errors import ValidationError
from sillytavern_rpg_engine.services.proposals import OperationCodec


def test_condition_kinds_decode():
    applied = OperationCodec.decode({
        "kind": "apply_condition", "entity_id": "pc1", "condition": "prone",
        "level": 1, "source": "topple",
    })
    assert applied.condition.value == "prone"
    removed = OperationCodec.decode({
        "kind": "remove_condition", "entity_id": "pc1", "condition": "prone",
        "level": 1,
    })
    assert removed.condition.value == "prone"


def test_random_operation_kinds_are_rejected():
    for kind in ("roll_check", "attack", "cast_spell", "death_save",
                 "multiattack", "start_combat"):
        with pytest.raises(ValidationError, match="unsupported"):
            OperationCodec.decode({"kind": kind})


def test_condition_payload_rejects_extra_keys_and_bad_condition():
    with pytest.raises(ValidationError, match="extra keys"):
        OperationCodec.decode({
            "kind": "apply_condition", "entity_id": "pc1", "condition": "prone",
            "level": 1, "source": "x", "hack": True,
        })
    with pytest.raises(ValidationError, match="condition"):
        OperationCodec.decode({
            "kind": "apply_condition", "entity_id": "pc1",
            "condition": "on_fire", "level": 1, "source": "x",
        })
