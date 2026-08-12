import pytest

from sillytavern_rpg_engine.domain.dice import SequenceDiceRoller
from sillytavern_rpg_engine.domain.dnd import DND2024_RULES_VERSION
from sillytavern_rpg_engine.domain.errors import NotFoundError, ValidationError
from sillytavern_rpg_engine.domain.models import CampaignRules, EntityKind, RulesMode
from sillytavern_rpg_engine.services.attributes import SetAttributeOperation
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.combat import CombatService, CombatantEntry
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
    entities = EntityAttributeService(database, dnd.mutation_engine)
    campaigns.create_campaign("c1", "Dungeon")
    dnd.seed_pack("c1", 0)
    version = 1
    for entity_id, name, dex, speed in (
        ("pc1", "Aria", 14, 30),
        ("orc", "Orc", 12, 30),
    ):
        entities.apply_explicit("c1", "main", version, CreateEntityOperation(
            entity_id=entity_id, kind=EntityKind.CHARACTER, name=name,
        ))
        version += 1
        for key, value in (("ability_dex", dex), ("speed", speed)):
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
    return combat, entities, version + 1


def test_start_rolls_initiative_sorts_and_blocks_second_active(database):
    combat, _, version = _world(database)
    # pc1 rolls 11 (+2) = 13; orc rolls 16 (+1) = 17 -> orc first
    result = combat.start(
        "c1", expected_version=version,
        roller=SequenceDiceRoller([11, 16]),
        entries=(CombatantEntry("pc1"), CombatantEntry("orc")),
    )
    with database.connect() as connection:
        encounter = connection.execute(
            "SELECT round_number, active_index, status FROM combat_encounters"
        ).fetchone()
        assert (encounter["round_number"], encounter["active_index"]) == (1, 0)
        order = connection.execute(
            "SELECT entity_id, initiative FROM combatants ORDER BY initiative DESC"
        ).fetchall()
        assert [(r["entity_id"], r["initiative"]) for r in order] == [
            ("orc", 17), ("pc1", 13),
        ]
    with pytest.raises(ValidationError, match="already active"):
        combat.start(
            "c1", expected_version=result.state_version,
            roller=SequenceDiceRoller([1, 1]),
            entries=(CombatantEntry("pc1"), CombatantEntry("orc")),
        )


def test_surprised_rolls_with_disadvantage_and_ties_break_on_entity_id(database):
    combat, _, version = _world(database)
    # pc1 surprised: 2d20 keep lower -> (10, 18) keeps 10 (+2) = 12
    # orc: 12 (+1) = 13; tie-break not needed here
    combat.start(
        "c1", expected_version=version,
        roller=SequenceDiceRoller([10, 18, 12]),
        entries=(CombatantEntry("pc1", surprised=True), CombatantEntry("orc")),
    )
    with database.connect() as connection:
        order = connection.execute(
            "SELECT entity_id FROM combatants ORDER BY initiative DESC, entity_id"
        ).fetchall()
        assert [row["entity_id"] for row in order] == ["orc", "pc1"]
        faces = connection.execute(
            "SELECT faces_json FROM dice_rolls WHERE roller_entity_id = 'pc1'"
        ).fetchone()
        assert faces["faces_json"] == "[18, 10]"


def test_equal_initiative_totals_tie_break_on_entity_id(database):
    combat, _, version = _world(database)
    # pc1: 10 (+2) = 12; orc: 11 (+1) = 12 -> tie on total, orc before pc1
    combat.start(
        "c1", expected_version=version,
        roller=SequenceDiceRoller([10, 11]),
        entries=(CombatantEntry("pc1"), CombatantEntry("orc")),
    )
    with database.connect() as connection:
        order = connection.execute(
            "SELECT entity_id, initiative FROM combatants ORDER BY initiative DESC, entity_id"
        ).fetchall()
        assert [(r["entity_id"], r["initiative"]) for r in order] == [
            ("orc", 12), ("pc1", 12),
        ]


def test_add_and_end_combat(database):
    combat, entities, version = _world(database)
    result = combat.start(
        "c1", expected_version=version,
        roller=SequenceDiceRoller([11, 16]),
        entries=(CombatantEntry("pc1"), CombatantEntry("orc")),
    )
    version = result.state_version
    entities.apply_explicit("c1", "main", version, CreateEntityOperation(
        entity_id="goblin", kind=EntityKind.CHARACTER, name="Goblin",
    ))
    version += 1
    for key, value in (("ability_dex", 16), ("speed", 30)):
        entities.apply_explicit(
            "c1", "main", version, SetAttributeOperation("goblin", key, value)
        )
        version += 1
    result = combat.add_combatant(
        "c1", expected_version=version,
        roller=SequenceDiceRoller([20]), entry=CombatantEntry("goblin"),
    )
    with database.connect() as connection:
        row = connection.execute(
            "SELECT initiative FROM combatants WHERE entity_id = 'goblin'"
        ).fetchone()
        assert row["initiative"] == 23
    ended = combat.end("c1", expected_version=result.state_version)
    with database.connect() as connection:
        row = connection.execute(
            "SELECT status, ended_state_version FROM combat_encounters"
        ).fetchone()
        assert row["status"] == "ended"
        assert row["ended_state_version"] == ended.state_version
    with pytest.raises(NotFoundError, match="active"):
        combat.end("c1", expected_version=ended.state_version)
