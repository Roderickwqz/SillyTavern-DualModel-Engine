import pytest

from sillytavern_rpg_engine.domain.errors import NotFoundError, ValidationError
from sillytavern_rpg_engine.domain.models import AgeStatus, Audience, EntityKind
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)
from sillytavern_rpg_engine.services.projection import ProjectionService
from sillytavern_rpg_engine.services.relationships import (
    RelationshipService,
    SetRelationshipOperation,
)


@pytest.fixture
def seeded(database):
    campaigns = CampaignService(
        database,
        id_factory=iter(f"rel-{i}" for i in range(10)).__next__,
        clock=lambda: "2026-08-10T00:00:00Z",
    )
    campaigns.create_campaign("c1", "Campaign")
    state = EntityAttributeService(database, campaigns.mutation_engine)
    state.apply_explicit("c1", "main", 0, CreateEntityOperation(
        "erin", EntityKind.CHARACTER, "艾琳", AgeStatus.ADULT, ()))
    state.apply_explicit("c1", "main", 1, CreateEntityOperation(
        "borin", EntityKind.CHARACTER, "博林", AgeStatus.ADULT, ()))
    return state


def test_relationship_defaults_hidden_and_visible_to_engine(database, seeded):
    result = seeded.apply_explicit("c1", "main", 2, SetRelationshipOperation(
        from_entity_id="erin", to_entity_id="borin",
        dimension=" Trust ", value=0.65,
    ))
    assert result.snapshot["relationships"][0]["dimension"] == "trust"
    service = RelationshipService(database)
    relation = service.between("erin", "borin")[0]
    assert relation.value == 0.65
    assert relation.audiences == frozenset({Audience.ENGINE, Audience.NPC_AGENT})
    player = ProjectionService(database).for_audience("c1", "main", Audience.PLAYER_UI)
    assert "trust" not in str(player)
    engine = ProjectionService(database).for_audience("c1", "main", Audience.ENGINE)
    erin = [e for e in engine["entities"] if e["id"] == "erin"][0]
    assert erin["relationships"] == [
        {"to": "borin", "dimension": "trust", "value": 0.65}
    ]


def test_relationship_validation(database, seeded):
    with pytest.raises(ValidationError, match="self"):
        seeded.apply_explicit("c1", "main", 2, SetRelationshipOperation(
            "erin", "erin", "trust", 0.5))
    with pytest.raises(NotFoundError):
        seeded.apply_explicit("c1", "main", 2, SetRelationshipOperation(
            "erin", "ghost", "trust", 0.5))
    with pytest.raises(ValidationError, match="JSON"):
        seeded.apply_explicit("c1", "main", 2, SetRelationshipOperation(
            "erin", "borin", "trust", object()))
