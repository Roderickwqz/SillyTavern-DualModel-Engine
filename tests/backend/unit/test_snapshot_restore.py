import pytest

from sillytavern_rpg_engine.domain.dice import SequenceDiceRoller
from sillytavern_rpg_engine.domain.dnd import DND2024_RULES_VERSION
from sillytavern_rpg_engine.domain.errors import NotFoundError
from sillytavern_rpg_engine.domain.memory import MemoryEventType
from sillytavern_rpg_engine.domain.models import (
    AttributeDefinition,
    AttributeType,
    Audience,
    CampaignRules,
    DisplayType,
    EntityKind,
    RulesMode,
)
from sillytavern_rpg_engine.persistence.repositories import SnapshotRepository
from sillytavern_rpg_engine.services.attributes import (
    DefineAttributeOperation,
    SetAttributeOperation,
)
from sillytavern_rpg_engine.services.branches import BranchService
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.combat import CombatService, CombatantEntry
from sillytavern_rpg_engine.services.dnd_pack import DndRulesService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)
from sillytavern_rpg_engine.services.memory_events import MemoryEventService
from sillytavern_rpg_engine.services.snapshot_restore import SnapshotRestoreService
from sillytavern_rpg_engine.services.snapshots import SnapshotBuilder
from sillytavern_rpg_engine.services.turns import TurnService


def _seed_snapshot(database, campaign_id, branch_id, state_version):
    """Replace the auto-stored snapshot at state_version with a fresh build."""
    with database.connect() as connection:
        snapshot = SnapshotBuilder().build(connection, campaign_id, branch_id)
        connection.execute(
            "DELETE FROM state_snapshots WHERE campaign_id = ? AND branch_id = ?"
            " AND state_version = ?",
            (campaign_id, branch_id, state_version),
        )
        SnapshotRepository().insert(
            connection, campaign_id, branch_id, state_version, snapshot,
            "2026-08-14T00:00:00Z",
        )
    return snapshot


def test_restore_reverts_live_state_to_snapshot(database):
    ids = iter(f"e-{i}" for i in range(30))
    campaigns = CampaignService(
        database,
        id_factory=ids.__next__,
        clock=lambda: "2026-08-14T00:00:00Z",
    )
    campaigns.create_campaign("c1", "Test")
    attrs = EntityAttributeService(database, campaigns.mutation_engine)
    attrs.apply_explicit("c1", "main", 0, CreateEntityOperation(
        "erin", EntityKind.CHARACTER, "艾琳",
    ))
    attrs.apply_explicit("c1", "main", 1, DefineAttributeOperation(AttributeDefinition(
        campaign_id="c1", key="alchemy", label="炼金", category="skill",
        value_type=AttributeType.NUMBER, display=DisplayType.BAR,
        audiences=frozenset({Audience.PLAYER_UI}), minimum=0, maximum=100,
    )))
    attrs.apply_explicit("c1", "main", 2, SetAttributeOperation(
        "erin", "alchemy", 10, None,
    ))
    snapshot = _seed_snapshot(database, "c1", "main", 3)
    assert snapshot["campaign"]["state_version"] == 3
    attrs.apply_explicit("c1", "main", 3, SetAttributeOperation(
        "erin", "alchemy", 99, None,
    ))
    restored = SnapshotRestoreService(database).restore("c1", "main", 3)
    assert restored == 3
    with database.connect() as connection:
        value = connection.execute(
            "SELECT value_json FROM attribute_values"
            " WHERE entity_id = 'erin' AND attribute_key = 'alchemy'"
        ).fetchone()["value_json"]
        state_version = connection.execute(
            "SELECT state_version FROM campaigns WHERE id = 'c1'"
        ).fetchone()["state_version"]
    assert value == "10"
    assert state_version == 3


def test_restore_removes_events_newer_than_snapshot(database):
    ids = iter(f"e-{i}" for i in range(30))
    campaigns = CampaignService(
        database,
        id_factory=ids.__next__,
        clock=lambda: "2026-08-14T00:00:00Z",
    )
    campaigns.create_campaign("c1", "Test")
    attrs = EntityAttributeService(database, campaigns.mutation_engine)
    attrs.apply_explicit("c1", "main", 0, CreateEntityOperation(
        "erin", EntityKind.CHARACTER, "艾琳",
    ))
    memory = MemoryEventService(
        database,
        mutation_engine=campaigns.mutation_engine,
        id_factory=iter(f"m-{i}" for i in range(10)).__next__,
    )
    memory.record(
        "c1", "main", 1,
        type=MemoryEventType.GENERAL, content="old memory", importance=2,
        audiences=frozenset({Audience.PLAYER_UI}), participants=("erin",),
        location_entity_id="erin",
    )
    _seed_snapshot(database, "c1", "main", 2)
    memory.record(
        "c1", "main", 2,
        type=MemoryEventType.GENERAL, content="new memory", importance=3,
        audiences=frozenset({Audience.PLAYER_UI}), participants=("erin",),
    )
    restored = SnapshotRestoreService(database).restore("c1", "main", 2)
    assert restored == 2
    with database.connect() as connection:
        events = connection.execute(
            "SELECT content, location_entity_id FROM memory_events"
            " WHERE campaign_id = 'c1' AND branch_id = 'main'"
        ).fetchall()
        fts_count = connection.execute(
            "SELECT COUNT(*) FROM memory_events_fts"
        ).fetchone()[0]
        participants = connection.execute(
            "SELECT COUNT(*) FROM memory_event_participants"
        ).fetchone()[0]
        state_version = connection.execute(
            "SELECT state_version FROM campaigns WHERE id = 'c1'"
        ).fetchone()[0]
    assert [row["content"] for row in events] == ["old memory"]
    assert [row["location_entity_id"] for row in events] == ["erin"]
    assert fts_count == 1
    assert participants == 1
    assert state_version == 2


def test_restore_rebuilds_active_combat(database):
    ids = iter(f"e-{i}" for i in range(1000))
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
        "c1", version,
        CampaignRules(
            mode=RulesMode.DND_2024,
            enabled=True,
            version=DND2024_RULES_VERSION,
        ),
    )
    started = combat.start(
        "c1", expected_version=version + 1,
        roller=SequenceDiceRoller([11, 16]),
        entries=(CombatantEntry("pc1"), CombatantEntry("orc")),
    )
    _seed_snapshot(database, "c1", "main", started.state_version)
    combat.advance_turn("c1", started.state_version, SequenceDiceRoller([]))
    restored = SnapshotRestoreService(database).restore(
        "c1", "main", started.state_version
    )
    assert restored == started.state_version
    with database.connect() as connection:
        encounter = connection.execute(
            "SELECT round_number, active_index FROM combat_encounters"
            " WHERE campaign_id = 'c1' AND branch_id = 'main'"
        ).fetchone()
        rows = connection.execute(
            "SELECT entity_id, initiative FROM combatants"
            " ORDER BY initiative DESC, entity_id"
        ).fetchall()
    assert (encounter["round_number"], encounter["active_index"]) == (1, 0)
    assert [(row["entity_id"], row["initiative"]) for row in rows] == [
        ("orc", 17),
        ("pc1", 13),
    ]


def test_restore_missing_snapshot_raises_not_found(database):
    campaigns = CampaignService(
        database,
        id_factory=iter(range(100)).__next__,
        clock=lambda: "2026-08-14T00:00:00Z",
    )
    campaigns.create_campaign("c1", "Test")
    with pytest.raises(NotFoundError, match="not found"):
        SnapshotRestoreService(database).restore("c1", "main", 7)


def test_restore_missing_campaign_raises_not_found(database):
    with pytest.raises(NotFoundError, match="campaign"):
        SnapshotRestoreService(database).restore("nope", "main", 0)


def test_restore_missing_branch_raises_not_found(database):
    campaigns = CampaignService(
        database,
        id_factory=iter(range(100)).__next__,
        clock=lambda: "2026-08-14T00:00:00Z",
    )
    campaigns.create_campaign("c1", "Test")
    with pytest.raises(NotFoundError, match="branch"):
        SnapshotRestoreService(database).restore("c1", "no-such-branch", 0)


def test_restore_falls_back_to_ancestor_branch_snapshot(database):
    ids = iter(f"e-{i}" for i in range(30))
    campaigns = CampaignService(
        database,
        id_factory=ids.__next__,
        clock=lambda: "2026-08-14T00:00:00Z",
    )
    campaigns.create_campaign("c1", "Test")
    attrs = EntityAttributeService(database, campaigns.mutation_engine)
    attrs.apply_explicit("c1", "main", 0, CreateEntityOperation(
        "erin", EntityKind.CHARACTER, "艾琳",
    ))
    attrs.apply_explicit("c1", "main", 1, DefineAttributeOperation(AttributeDefinition(
        campaign_id="c1", key="alchemy", label="炼金", category="skill",
        value_type=AttributeType.NUMBER, display=DisplayType.BAR,
        audiences=frozenset({Audience.PLAYER_UI}), minimum=0, maximum=100,
    )))
    attrs.apply_explicit("c1", "main", 2, SetAttributeOperation(
        "erin", "alchemy", 10, None,
    ))
    _seed_snapshot(database, "c1", "main", 2)
    attrs.apply_explicit("c1", "main", 3, SetAttributeOperation(
        "erin", "alchemy", 99, None,
    ))
    turns = TurnService(database, id_factory=lambda: "turn-fork")
    turn = turns.record(
        "c1", "main", intent="action", player_text="hi",
        response_text="ok", history_hash="h",
        state_before_version=2, state_after_version=2,
    )
    child = BranchService(database).fork(
        "c1", "main", turn.id, new_branch_id="branch-swipe-1"
    )
    restored = SnapshotRestoreService(database).restore("c1", child, 2)
    assert restored == 2
    with database.connect() as connection:
        value = connection.execute(
            "SELECT value_json FROM attribute_values"
            " WHERE entity_id = 'erin' AND attribute_key = 'alchemy'"
        ).fetchone()["value_json"]
        copied = connection.execute(
            "SELECT 1 FROM state_snapshots WHERE campaign_id = 'c1'"
            " AND branch_id = ? AND state_version = 2",
            (child,),
        ).fetchone()
        head = connection.execute(
            "SELECT state_version FROM branch_heads WHERE campaign_id = 'c1'"
            " AND branch_id = ?",
            (child,),
        ).fetchone()
    assert value == "10"
    assert copied is not None
    assert head["state_version"] == 2


def test_restore_without_ancestor_snapshot_still_raises(database):
    ids = iter(f"e-{i}" for i in range(30))
    campaigns = CampaignService(
        database,
        id_factory=ids.__next__,
        clock=lambda: "2026-08-14T00:00:00Z",
    )
    campaigns.create_campaign("c1", "Test")
    turns = TurnService(database, id_factory=lambda: "turn-fork")
    turn = turns.record(
        "c1", "main", intent="action", player_text="hi",
        response_text="ok", history_hash="h",
        state_before_version=0, state_after_version=0,
    )
    child = BranchService(database).fork(
        "c1", "main", turn.id, new_branch_id="branch-swipe-1"
    )
    with pytest.raises(NotFoundError, match="not found"):
        SnapshotRestoreService(database).restore("c1", child, 7)


def test_restore_prunes_stale_snapshots_and_allows_continue(database):
    ids = iter(f"e-{i}" for i in range(30))
    campaigns = CampaignService(
        database,
        id_factory=ids.__next__,
        clock=lambda: "2026-08-14T00:00:00Z",
    )
    campaigns.create_campaign("c1", "Test")
    attrs = EntityAttributeService(database, campaigns.mutation_engine)
    attrs.apply_explicit("c1", "main", 0, CreateEntityOperation(
        "erin", EntityKind.CHARACTER, "艾琳",
    ))
    attrs.apply_explicit("c1", "main", 1, DefineAttributeOperation(AttributeDefinition(
        campaign_id="c1", key="alchemy", label="炼金", category="skill",
        value_type=AttributeType.NUMBER, display=DisplayType.BAR,
        audiences=frozenset({Audience.PLAYER_UI}), minimum=0, maximum=100,
    )))
    attrs.apply_explicit("c1", "main", 2, SetAttributeOperation(
        "erin", "alchemy", 10, None,
    ))
    attrs.apply_explicit("c1", "main", 3, SetAttributeOperation(
        "erin", "alchemy", 99, None,
    ))
    restored = SnapshotRestoreService(
        database, clock=lambda: "2026-08-14T00:00:00Z",
    ).restore("c1", "main", 3)
    assert restored == 3
    with database.connect() as connection:
        stale = connection.execute(
            "SELECT COUNT(*) FROM state_snapshots WHERE campaign_id = 'c1'"
            " AND branch_id = 'main' AND state_version > 3"
        ).fetchone()[0]
        head = connection.execute(
            "SELECT state_version, latest_turn_id FROM branch_heads"
            " WHERE campaign_id = 'c1' AND branch_id = 'main'"
        ).fetchone()
        value = connection.execute(
            "SELECT value_json FROM attribute_values"
            " WHERE entity_id = 'erin' AND attribute_key = 'alchemy'"
        ).fetchone()["value_json"]
    assert stale == 0
    assert head["state_version"] == 3
    assert head["latest_turn_id"] is None
    assert value == "10"
    attrs.apply_explicit("c1", "main", 3, SetAttributeOperation(
        "erin", "alchemy", 42, None,
    ))
    with database.connect() as connection:
        value = connection.execute(
            "SELECT value_json FROM attribute_values"
            " WHERE entity_id = 'erin' AND attribute_key = 'alchemy'"
        ).fetchone()["value_json"]
        state_version = connection.execute(
            "SELECT state_version FROM campaigns WHERE id = 'c1'"
        ).fetchone()["state_version"]
        snapshot_count = connection.execute(
            "SELECT COUNT(*) FROM state_snapshots WHERE campaign_id = 'c1'"
            " AND branch_id = 'main' AND state_version = 4"
        ).fetchone()[0]
    assert value == "42"
    assert state_version == 4
    assert snapshot_count == 1
