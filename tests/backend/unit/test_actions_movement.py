import json

import pytest

from sillytavern_rpg_engine.domain.dice import SequenceDiceRoller
from sillytavern_rpg_engine.domain.dnd import DND2024_RULES_VERSION, ActionType, Condition
from sillytavern_rpg_engine.domain.errors import ValidationError
from sillytavern_rpg_engine.domain.models import CampaignRules, EntityKind, RulesMode
from sillytavern_rpg_engine.services.attributes import SetAttributeOperation
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.combat import (
    CombatService,
    CombatantEntry,
    MovementMode,
)
from sillytavern_rpg_engine.services.conditions import ConditionService
from sillytavern_rpg_engine.services.dnd_pack import DndRulesService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)


def _world(database):
    ids = iter(f"e{n}" for n in range(1, 1000))
    clock = lambda: "2026-08-12T00:00:00Z"
    campaigns = CampaignService(database, id_factory=lambda: next(ids), clock=clock)
    dnd = DndRulesService(database, id_factory=lambda: next(ids), clock=clock)
    combat = CombatService(database, id_factory=lambda: next(ids), clock=clock)
    conditions = ConditionService(database, id_factory=lambda: next(ids), clock=clock)
    entities = EntityAttributeService(database, dnd.mutation_engine)
    campaigns.create_campaign("c1", "Dungeon")
    dnd.seed_pack("c1", 0)
    version = 1
    for entity_id, name in (("pc1", "Aria"), ("orc", "Orc")):
        entities.apply_explicit("c1", "main", version, CreateEntityOperation(
            entity_id=entity_id, kind=EntityKind.CHARACTER, name=name,
        ))
        version += 1
        for key, value in (
            ("ability_dex", 10), ("ability_str", 14), ("speed", 30),
            ("proficiency_bonus", 2), ("proficiencies", ["stealth"]),
            ("condition_immunities", []),
        ):
            entities.apply_explicit(
                "c1", "main", version, SetAttributeOperation(entity_id, key, value)
            )
            version += 1
    campaigns.set_rules(
        "c1",
        version,
        CampaignRules(
            mode=RulesMode.DND_2024,
            enabled=True,
            version=DND2024_RULES_VERSION,
        ),
    )
    return combat, conditions, version + 1


def _start(combat, version):
    # pc1: 15 (+0), orc: 10 (+0) -> pc1 first
    return combat.start(
        "c1", expected_version=version,
        roller=SequenceDiceRoller([15, 10]),
        entries=(CombatantEntry("pc1"), CombatantEntry("orc")),
    )


def _row(connection, entity_id):
    return connection.execute(
        "SELECT c.* FROM combatants c"
        " JOIN combat_encounters e ON c.encounter_id = e.id"
        " WHERE c.entity_id = ? AND e.status = 'active'",
        (entity_id,),
    ).fetchone()


def test_action_budget_dash_and_turn_reset(database):
    combat, _, version = _world(database)
    result = _start(combat, version)
    version = result.state_version
    result = combat.use_action("c1", version, "pc1", ActionType.DASH)
    with database.connect() as connection:
        row = _row(connection, "pc1")
        assert row["action_used"] == 1
        assert row["movement_total"] == 60
    with pytest.raises(ValidationError, match="action"):
        combat.use_action("c1", result.state_version, "pc1", ActionType.DODGE)
    with pytest.raises(ValidationError, match="active"):
        combat.use_action("c1", result.state_version, "orc", ActionType.DODGE)
    result = combat.advance_turn("c1", result.state_version, SequenceDiceRoller([]))
    result = combat.advance_turn("c1", result.state_version, SequenceDiceRoller([]))
    with database.connect() as connection:
        row = _row(connection, "pc1")
        assert row["action_used"] == 0
        assert row["movement_total"] == 30
        encounter = connection.execute(
            "SELECT round_number, active_index FROM combat_encounters"
        ).fetchone()
        assert (encounter["round_number"], encounter["active_index"]) == (2, 0)


def test_hide_rolls_stealth_and_ready_stores_action(database):
    combat, _, version = _world(database)
    result = _start(combat, version)
    result = combat.use_action(
        "c1", result.state_version, "pc1", ActionType.HIDE,
        roller=SequenceDiceRoller([16]),
    )
    with database.connect() as connection:
        assert _row(connection, "pc1")["hidden"] == 1
        roll = connection.execute(
            "SELECT dc, success FROM dice_rolls WHERE purpose LIKE 'hide%'"
        ).fetchone()
        assert (roll["dc"], roll["success"]) == (15, 1)
    result = combat.advance_turn("c1", result.state_version, SequenceDiceRoller([]))
    result = combat.advance_turn("c1", result.state_version, SequenceDiceRoller([]))
    result = combat.use_action(
        "c1", result.state_version, "pc1", ActionType.READY,
        readied={"trigger": "orc moves closer", "action": "attack"},
    )
    with database.connect() as connection:
        assert json.loads(_row(connection, "pc1")["readied_action_json"]) == {
            "trigger": "orc moves closer",
            "action": "attack",
        }


def test_help_grants_advantage_and_reaction_budget(database):
    combat, _, version = _world(database)
    result = _start(combat, version)
    result = combat.use_action(
        "c1", result.state_version, "pc1", ActionType.HELP,
        help_ally="orc", help_target="pc1",
    )
    with database.connect() as connection:
        grants = json.loads(_row(connection, "orc")["help_grants_json"])
        assert grants == [{"ally": "orc", "target": "pc1"}]
    result = combat.advance_turn("c1", result.state_version, SequenceDiceRoller([]))
    result = combat.use_reaction("c1", result.state_version, "orc", "opportunity attack")
    with pytest.raises(ValidationError, match="reaction"):
        combat.use_reaction("c1", result.state_version, "orc", "again")
    result = combat.advance_turn("c1", result.state_version, SequenceDiceRoller([]))
    result = combat.use_reaction("c1", result.state_version, "pc1", "shield")


def test_voluntary_movement_requires_active_combatant(database):
    combat, _, version = _world(database)
    result = _start(combat, version)
    with pytest.raises(ValidationError, match="active"):
        combat.move("c1", result.state_version, "orc", feet=10, mode=MovementMode.WALK)
    result = combat.move("c1", result.state_version, "orc", feet=10, mode=MovementMode.FORCED)
    with database.connect() as connection:
        assert _row(connection, "orc")["movement_used"] == 0


def test_movement_costs_and_budget(database):
    combat, _, version = _world(database)
    result = _start(combat, version)
    result = combat.move("c1", result.state_version, "pc1", feet=10, mode=MovementMode.WALK)
    result = combat.move("c1", result.state_version, "pc1", feet=5, mode=MovementMode.DIFFICULT)
    with pytest.raises(ValidationError, match="movement"):
        combat.move("c1", result.state_version, "pc1", feet=11, mode=MovementMode.WALK)
    result = combat.move("c1", result.state_version, "pc1", feet=90, mode=MovementMode.FORCED)
    with database.connect() as connection:
        assert _row(connection, "pc1")["movement_used"] == 20


def test_exhaustion_slows_and_grapple_stops_movement_reset(database):
    combat, conditions, version = _world(database)
    conditions.apply("c1", version, "pc1", Condition.EXHAUSTION, level=2, source="march")
    result = _start(combat, version + 1)
    with database.connect() as connection:
        assert _row(connection, "pc1")["movement_total"] == 20
    combat.end("c1", result.state_version)
    conditions.apply("c1", result.state_version + 1, "pc1", Condition.GRAPPLED, source="orc")
    result = _start(combat, result.state_version + 2)
    with database.connect() as connection:
        assert _row(connection, "pc1")["movement_total"] == 0
