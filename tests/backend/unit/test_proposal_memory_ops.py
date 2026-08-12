import pytest

from sillytavern_rpg_engine.domain.errors import ValidationError
from sillytavern_rpg_engine.domain.memory import (
    FactType,
    MemoryEventType,
    SummaryScope,
    TraitTier,
)
from sillytavern_rpg_engine.domain.models import Audience
from sillytavern_rpg_engine.services.arcs import CloseArcOperation, OpenArcOperation
from sillytavern_rpg_engine.services.facts import AssertFactOperation
from sillytavern_rpg_engine.services.memory_events import RecordMemoryEventOperation
from sillytavern_rpg_engine.services.proposals import OperationCodec
from sillytavern_rpg_engine.services.relationships import SetRelationshipOperation
from sillytavern_rpg_engine.services.summaries import UpsertSummaryOperation
from sillytavern_rpg_engine.services.traits import RecordTraitEventOperation


def test_codec_decodes_all_memory_kinds():
    fact = OperationCodec.decode({
        "kind": "assert_fact", "fact_id": "f-1", "entity_id": "erin",
        "fact_type": "identity", "fact_key": "home", "content": "银月城",
        "importance": 3, "audiences": ["engine"], "turn_id": None,
    })
    assert isinstance(fact, AssertFactOperation)
    assert fact.fact_type is FactType.IDENTITY
    event = OperationCodec.decode({
        "kind": "record_memory_event", "event_id": "e-1",
        "event_type": "scene", "content": "艾琳路过集市", "importance": 2,
        "audiences": ["engine", "player_ui"], "participant_entity_ids": ["erin"],
        "location_entity_id": None, "turn_id": None, "source": "narrative_development",
    })
    assert isinstance(event, RecordMemoryEventOperation)
    assert event.event_type is MemoryEventType.SCENE
    trait = OperationCodec.decode({
        "kind": "record_trait_event", "event_id": "t-1", "entity_id": "erin",
        "trait_key": "openness", "tier": "important", "delta": 5,
        "cause": "主动探索", "turn_id": None, "source": "narrative_development",
    })
    assert isinstance(trait, RecordTraitEventOperation)
    assert trait.tier is TraitTier.IMPORTANT
    relation = OperationCodec.decode({
        "kind": "set_relationship", "from_entity_id": "erin",
        "to_entity_id": "borin", "dimension": "trust", "value": 0.6,
        "audiences": None, "turn_id": None,
    })
    assert isinstance(relation, SetRelationshipOperation)
    summary = OperationCodec.decode({
        "kind": "upsert_summary", "scope": "character", "scope_key": "erin",
        "content": "谨慎的炼金术师", "audiences": ["engine"],
        "source_event_ids": ["e-1"],
    })
    assert isinstance(summary, UpsertSummaryOperation)
    assert summary.scope is SummaryScope.CHARACTER
    opened = OperationCodec.decode({
        "kind": "open_arc", "arc_id": "a-1", "entity_id": "erin",
        "dimension": "openness", "label": "主动探索", "summary": "开始",
        "source_event_ids": ["t-1"], "start_turn_id": "turn-3",
    })
    assert isinstance(opened, OpenArcOperation)
    closed = OperationCodec.decode({
        "kind": "close_arc", "arc_id": "a-1", "end_turn_id": "turn-9",
        "summary": None,
    })
    assert isinstance(closed, CloseArcOperation)


def test_codec_rejects_unknown_keys_and_bad_enums():
    with pytest.raises(ValidationError, match="extra keys"):
        OperationCodec.decode({
            "kind": "assert_fact", "fact_id": "f-1", "entity_id": "erin",
            "fact_type": "identity", "fact_key": "home", "content": "银月城",
            "importance": 3, "audiences": ["engine"], "turn_id": None,
            "hack": "drop table",
        })
    with pytest.raises(ValidationError, match="invalid"):
        OperationCodec.decode({
            "kind": "record_trait_event", "event_id": "t-1", "entity_id": "erin",
            "trait_key": "openness", "tier": "cosmic", "delta": 5,
            "cause": "x", "turn_id": None, "source": "narrative_development",
        })


def test_codec_rejects_phase2_missing_keys_wrong_types_and_unknown_kind():
    with pytest.raises(ValidationError, match="missing keys"):
        OperationCodec.decode({
            "kind": "assert_fact", "entity_id": "erin",
            "fact_type": "identity", "fact_key": "home", "content": "银月城",
            "importance": 3, "audiences": ["engine"], "turn_id": None,
        })
    with pytest.raises(ValidationError, match="audiences must be a list"):
        OperationCodec.decode({
            "kind": "assert_fact", "fact_id": "f-1", "entity_id": "erin",
            "fact_type": "identity", "fact_key": "home", "content": "银月城",
            "importance": 3, "audiences": "engine", "turn_id": None,
        })
    with pytest.raises(ValidationError, match="unsupported operation kind"):
        OperationCodec.decode({"kind": "bogus", "fact_id": "f-1"})
