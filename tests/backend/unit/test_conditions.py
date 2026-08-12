import pytest

from sillytavern_rpg_engine.domain.dnd import DND2024_RULES_VERSION, Condition
from sillytavern_rpg_engine.domain.errors import NotFoundError, ValidationError
from sillytavern_rpg_engine.domain.models import CampaignRules, EntityKind, RulesMode
from sillytavern_rpg_engine.services.attributes import SetAttributeOperation
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.conditions import (
    ConditionService,
    condition_map,
)
from sillytavern_rpg_engine.services.dnd_pack import DndRulesService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)


def _service(database):
    ids = iter(f"e{n}" for n in range(1, 1000))
    clock = lambda: "2026-08-12T00:00:00Z"
    campaigns = CampaignService(database, id_factory=lambda: next(ids), clock=clock)
    dnd = DndRulesService(database, id_factory=lambda: next(ids), clock=clock)
    conditions = ConditionService(database, id_factory=lambda: next(ids), clock=clock)
    entities = EntityAttributeService(database, dnd.mutation_engine)
    campaigns.create_campaign("c1", "Dungeon")
    dnd.seed_pack("c1", 0)
    campaigns.set_rules(
        "c1",
        1,
        CampaignRules(
            mode=RulesMode.DND_2024,
            enabled=True,
            version=DND2024_RULES_VERSION,
        ),
    )
    entities.apply_explicit("c1", "main", 2, CreateEntityOperation(
        entity_id="pc1", kind=EntityKind.CHARACTER, name="Aria",
    ))
    entities.apply_explicit("c1", "main", 3, SetAttributeOperation(
        "pc1", "condition_immunities", ["poisoned"]
    ))
    return conditions, entities


def test_apply_and_remove_condition_roundtrip(database):
    conditions, _ = _service(database)
    conditions.apply("c1", 4, "pc1", Condition.PRONE, source="topple")
    conditions.apply("c1", 5, "pc1", Condition.PRONE, source="again")
    conditions.apply("c1", 6, "pc1", Condition.BLINDED, source="spell")
    with database.connect() as connection:
        assert condition_map(connection, "c1", "pc1") == {
            Condition.PRONE: 1,
            Condition.BLINDED: 1,
        }
    conditions.remove("c1", 7, "pc1", Condition.PRONE)
    with database.connect() as connection:
        assert condition_map(connection, "c1", "pc1") == {Condition.BLINDED: 1}
    with pytest.raises(NotFoundError):
        conditions.remove("c1", 8, "pc1", Condition.PRONE)


def test_exhaustion_accumulates_and_kills_at_six(database):
    conditions, entities = _service(database)
    conditions.apply("c1", 4, "pc1", Condition.EXHAUSTION, level=2, source="forced march")
    conditions.apply("c1", 5, "pc1", Condition.EXHAUSTION, level=3, source="cold")
    with database.connect() as connection:
        assert condition_map(connection, "c1", "pc1") == {Condition.EXHAUSTION: 5}
    conditions.remove("c1", 6, "pc1", Condition.EXHAUSTION)
    with database.connect() as connection:
        assert condition_map(connection, "c1", "pc1") == {Condition.EXHAUSTION: 4}
    result = conditions.apply("c1", 7, "pc1", Condition.EXHAUSTION, level=2, source="overexertion")
    assert result.snapshot["entities"][0]["attributes"]
    with database.connect() as connection:
        row = connection.execute(
            "SELECT value_json FROM attribute_values"
            " WHERE entity_id = 'pc1' AND attribute_key = 'is_dead'"
        ).fetchone()
        assert row is not None and row["value_json"] == "true"


def test_condition_immunity_rejects_application(database):
    conditions, _ = _service(database)
    with pytest.raises(ValidationError, match="immune"):
        conditions.apply("c1", 4, "pc1", Condition.POISONED, source="trap")
