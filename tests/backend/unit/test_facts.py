import pytest

from sillytavern_rpg_engine.domain.errors import NotFoundError, ValidationError
from sillytavern_rpg_engine.domain.memory import FactType
from sillytavern_rpg_engine.domain.models import AgeStatus, Audience, EntityKind
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)
from sillytavern_rpg_engine.services.facts import AssertFactOperation, FactService
from sillytavern_rpg_engine.services.projection import ProjectionService


@pytest.fixture
def seeded(database):
    campaigns = CampaignService(
        database,
        id_factory=iter(f"fact-event-{i}" for i in range(20)).__next__,
        clock=lambda: "2026-08-10T00:00:00Z",
    )
    campaigns.create_campaign("c1", "Campaign")
    EntityAttributeService(database, campaigns.mutation_engine).apply_explicit(
        "c1", "main", 0,
        CreateEntityOperation("erin", EntityKind.CHARACTER, "艾琳", AgeStatus.ADULT, ()),
    )
    return campaigns


def test_assert_fact_supersedes_without_deleting(database, seeded):
    state = EntityAttributeService(database, seeded.mutation_engine)
    state.apply_explicit("c1", "main", 1, AssertFactOperation(
        fact_id="f-1", entity_id="erin", fact_type=FactType.GENERAL,
        fact_key="home", content="住在银月城", importance=2,
        audiences=frozenset({Audience.ENGINE, Audience.PLAYER_UI}),
        turn_id="turn-1",
    ))
    result = state.apply_explicit("c1", "main", 2, AssertFactOperation(
        fact_id="f-2", entity_id="erin", fact_type=FactType.GENERAL,
        fact_key=" Home ", content="搬到凌云窟", importance=3,
        audiences=frozenset({Audience.ENGINE, Audience.PLAYER_UI}),
        turn_id="turn-2",
    ))
    facts = FactService(database)
    current = facts.current("c1", "erin")
    assert [fact.content for fact in current] == ["搬到凌云窟"]
    assert current[0].valid_from == 3
    history = facts.history("erin", "home")
    assert [fact.content for fact in history] == ["住在银月城", "搬到凌云窟"]
    assert history[0].valid_until == 3
    assert history[0].superseded_by == "f-2"
    assert result.snapshot["facts"][0]["content"] == "搬到凌云窟"


def test_fact_validation_and_projection_filtering(database, seeded):
    state = EntityAttributeService(database, seeded.mutation_engine)
    with pytest.raises(ValidationError, match="empty"):
        state.apply_explicit("c1", "main", 1, AssertFactOperation(
            fact_id="f-x", entity_id="erin", fact_type=FactType.GENERAL,
            fact_key="home", content="  ", importance=2,
            audiences=frozenset({Audience.ENGINE}),
        ))
    with pytest.raises(NotFoundError):
        state.apply_explicit("c1", "main", 1, AssertFactOperation(
            fact_id="f-y", entity_id="nobody", fact_type=FactType.GENERAL,
            fact_key="home", content="鬼魂", importance=2,
            audiences=frozenset({Audience.ENGINE}),
        ))
    state.apply_explicit("c1", "main", 1, AssertFactOperation(
        fact_id="f-3", entity_id="erin", fact_type=FactType.IDENTITY,
        fact_key="true_role", content="王国密探", importance=5,
        audiences=frozenset({Audience.ENGINE}),
    ))
    player = ProjectionService(database).for_audience("c1", "main", Audience.PLAYER_UI)
    engine = ProjectionService(database).for_audience("c1", "main", Audience.ENGINE)
    assert "王国密探" not in str(player)
    assert engine["entities"][0]["facts"][0]["content"] == "王国密探"
