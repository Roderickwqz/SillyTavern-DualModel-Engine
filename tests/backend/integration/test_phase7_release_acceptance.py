"""Phase 7 §17.4 release acceptance (automated subset).

Maps the §17.4 items a pure backend run can verify without a manual
SillyTavern host:
- #4 hidden attributes never leak into the player Tracker response;
- #9 a Recorder Tracker parse failure surfaces the stale banner and
  persists no authoritative state (no version bump, no audit event, no
  projection change);
- #13 a default narrative campaign never produces dice rolls or combat
  encounters, while a dnd-2024 campaign does;
- #3 export -> fresh-DB import restores identical authoritative state
  (state_version, entity attribute values, projection);
- #11 recovery import appends audit events only: pre-import rows are
  byte-identical before and after.
"""

import json
import re

from sillytavern_rpg_engine.config import load_settings
from sillytavern_rpg_engine.domain.dice import SequenceDiceRoller
from sillytavern_rpg_engine.domain.models import (
    AttributeDefinition,
    AttributeType,
    Audience,
    DisplayType,
    EntityKind,
)
from sillytavern_rpg_engine.llm.scripted import ScriptedLLMClient
from sillytavern_rpg_engine.orchestration.graph import TurnRunner, default_services
from sillytavern_rpg_engine.orchestration.normalize import strip_tracker_blocks
from sillytavern_rpg_engine.persistence.database import Database
from sillytavern_rpg_engine.persistence.migrations import MigrationRunner
from sillytavern_rpg_engine.services.attributes import (
    DefineAttributeOperation,
    SetAttributeOperation,
)
from sillytavern_rpg_engine.services.campaign_export import CampaignExporter
from sillytavern_rpg_engine.services.campaign_import import CampaignImporter
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.dice import DiceService
from sillytavern_rpg_engine.services.dnd_pack import DndRulesService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)
from sillytavern_rpg_engine.services.projection import ProjectionService

_JSON_FENCE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)


def _tracker_json(content: str) -> dict:
    block = _JSON_FENCE.search(content)
    assert block is not None, f"no Tracker block in response: {content!r}"
    return json.loads(block.group(1))


def _set_attribute(entity_id: str, value: int) -> str:
    return (
        f'{{"kind": "set_attribute", "entity_id": "{entity_id}",'
        f' "attribute_key": "alchemy", "value": {value}, "turn_id": null}}'
    )


def _play_turn(database, responses, player_text, *, campaign_id="c1",
               history=None):
    settings = load_settings({"RPG_NARRATOR_MODEL": "narrator-model"})
    narrator = ScriptedLLMClient(responses)
    runner = TurnRunner(default_services(database, settings, narrator))
    history = [] if history is None else history
    history.append({"role": "user", "content": player_text})
    result = runner.run({
        "campaign_id": campaign_id,
        "messages": list(history),
    })
    content = result["choices"][0]["message"]["content"]
    history.append({
        "role": "assistant",
        "content": strip_tracker_blocks(content),
    })
    return content


def _fresh_database(tmp_path, name):
    value = Database(tmp_path / name)
    MigrationRunner(value, clock=lambda: "2026-08-16T00:00:00Z").apply()
    return value


def _world(database, campaign_id="c1"):
    ids = iter(f"evt-{campaign_id}-{i}" for i in range(1000))
    campaigns = CampaignService(
        database, id_factory=ids.__next__, clock=lambda: "2026-08-13T00:00:00Z"
    )
    campaigns.create_campaign(campaign_id, "测试")
    attrs = EntityAttributeService(database, campaigns.mutation_engine)
    attrs.apply_explicit(campaign_id, "main", 0, CreateEntityOperation(
        "erin", EntityKind.CHARACTER, "艾琳",
    ))
    attrs.apply_explicit(campaign_id, "main", 1, DefineAttributeOperation(
        AttributeDefinition(
            campaign_id=campaign_id, key="alchemy", label="炼金", category="skill",
            value_type=AttributeType.NUMBER, display=DisplayType.BAR,
            audiences=frozenset({Audience.PLAYER_UI}), minimum=0, maximum=100,
        ),
    ))
    return attrs


def test_release_hidden_attributes_never_in_tracker(database):
    """§17.4 #4: a real turn's Tracker response must never carry an
    ENGINE-only (hidden) attribute, while the visible one renders."""
    ids = iter(f"evt-{i}" for i in range(1000))
    campaigns = CampaignService(
        database, id_factory=ids.__next__, clock=lambda: "2026-08-13T00:00:00Z"
    )
    campaigns.create_campaign("c1", "测试")
    attrs = EntityAttributeService(database, campaigns.mutation_engine)
    attrs.apply_explicit("c1", "main", 0, CreateEntityOperation(
        "erin", EntityKind.CHARACTER, "艾琳",
    ))
    attrs.apply_explicit("c1", "main", 1, CreateEntityOperation(
        "player", EntityKind.CHARACTER, "旅人",
    ))
    attrs.apply_explicit("c1", "main", 2, DefineAttributeOperation(
        AttributeDefinition(
            campaign_id="c1", key="alchemy", label="炼金", category="skill",
            value_type=AttributeType.NUMBER, display=DisplayType.BAR,
            audiences=frozenset({Audience.PLAYER_UI, Audience.NARRATOR}),
            minimum=0, maximum=100,
        ),
    ))
    attrs.apply_explicit("c1", "main", 3, DefineAttributeOperation(
        AttributeDefinition(
            campaign_id="c1", key="secret", label="秘密", category="skill",
            value_type=AttributeType.NUMBER, display=DisplayType.BAR,
            audiences=frozenset({Audience.ENGINE}),
        ),
    ))
    attrs.apply_explicit("c1", "main", 4, SetAttributeOperation(
        "erin", "alchemy", 35, None,
    ))
    attrs.apply_explicit("c1", "main", 5, SetAttributeOperation(
        "erin", "secret", 99, None,
    ))

    content = _play_turn(database, ["叙述一", json.dumps({"operations": []})],
                         "我走进炼金铺。")
    tracker = _tracker_json(content)
    assert tracker["state_version"] == 6
    attributes_out = tracker["characters"][0]["attributes"]
    assert [a["key"] for a in attributes_out] == ["alchemy"]
    assert attributes_out[0]["value"] == 35
    assert "secret" not in json.dumps(tracker, ensure_ascii=False)

    projection = ProjectionService(database).for_audience(
        "c1", "main", Audience.PLAYER_UI
    )
    erin_projection = next(
        e for e in projection["entities"] if e["id"] == "erin"
    )
    assert erin_projection["attributes"] == [{"key": "alchemy", "value": 35}]
    assert "secret" not in json.dumps(projection, ensure_ascii=False)


def test_release_tracker_parse_failure_does_not_mutate_db(database):
    """§17.4 #9: unparseable Recorder output shows the failure banner and
    persists no authoritative mutation (version, audit, projection intact)."""
    attrs = _world(database)
    attrs.apply_explicit("c1", "main", 2, SetAttributeOperation(
        "erin", "alchemy", 35, None,
    ))
    before = ProjectionService(database).for_audience(
        "c1", "main", Audience.PLAYER_UI
    )
    with database.connect() as connection:
        version_before = connection.execute(
            "SELECT state_version FROM campaigns WHERE id = 'c1'"
        ).fetchone()["state_version"]
        audit_before = connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE campaign_id = 'c1'"
        ).fetchone()[0]

    content = _play_turn(
        database,
        ["叙述一", "这不是合法的 Tracker JSON", "依旧不是合法 JSON"],
        "我继续探索地牢。",
    )
    assert "状态变更解析失败" in content

    after = ProjectionService(database).for_audience(
        "c1", "main", Audience.PLAYER_UI
    )
    assert after == before
    with database.connect() as connection:
        version_after = connection.execute(
            "SELECT state_version FROM campaigns WHERE id = 'c1'"
        ).fetchone()["state_version"]
        audit_after = connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE campaign_id = 'c1'"
        ).fetchone()[0]
        values_after = connection.execute(
            "SELECT COUNT(*) FROM attribute_values WHERE campaign_id = 'c1'"
        ).fetchone()[0]
        proposals_after = connection.execute(
            "SELECT COUNT(*) FROM pending_proposals WHERE campaign_id = 'c1'"
        ).fetchone()[0]
    assert version_after == version_before == 3
    assert audit_after == audit_before == 4
    assert values_after == 1
    assert proposals_after == 0


def test_release_narrative_campaign_has_no_dice_rolls(database):
    """§17.4 #13: a default narrative campaign never settles D&D rolls or
    opens combat, while a dnd-2024 campaign does."""
    ids = iter(f"evt-{i}" for i in range(1000))
    campaigns = CampaignService(
        database, id_factory=ids.__next__, clock=lambda: "2026-08-12T00:00:00Z"
    )
    campaigns.create_campaign("c1", "Story")
    assert campaigns.get_campaign("c1").rules.mode.value == "narrative"
    history = []
    for text in ("我继续探索地牢。", "我打开石门。"):
        _play_turn(database, ["叙述", json.dumps({"operations": []})], text,
                   history=history)

    dnd_ids = iter(f"dnd-{i}" for i in range(1000))
    clock = lambda: "2026-08-12T00:00:00Z"
    dnd = DndRulesService(database, id_factory=dnd_ids.__next__, clock=clock)
    dice = DiceService(database, id_factory=dnd_ids.__next__, clock=clock)
    entities = EntityAttributeService(database, dnd.mutation_engine)
    campaigns.create_campaign("c2", "Dungeon")
    dnd.seed_pack("c2", 0)
    entities.apply_explicit("c2", "main", 1, CreateEntityOperation(
        "pc1", EntityKind.CHARACTER, "Aria",
    ))
    values = {
        "ability_str": 16, "ability_dex": 14, "ability_con": 13,
        "ability_int": 10, "ability_wis": 12, "ability_cha": 8,
        "proficiency_bonus": 2, "proficiencies": ["athletics"],
        "armor_class": 16, "hp_max": 12, "hp_current": 12, "hp_temp": 0,
        "speed": 30, "character_level": 1,
        "spell_slots_max": [0] * 9, "spell_slots_current": [0] * 9,
        "hit_die": 10, "hit_dice_total": 1, "hit_dice_current": 1,
        "death_saves_success": 0, "death_saves_failure": 0,
        "is_dead": False, "is_stable": False,
        "resistances": [], "vulnerabilities": [], "immunities": [],
        "condition_immunities": [], "weapon_masteries": [],
    }
    version = 2
    for key, value in values.items():
        entities.apply_explicit(
            "c2", "main", version, SetAttributeOperation("pc1", key, value)
        )
        version += 1
    dnd.enable("c2", version)
    dice.check(
        "c2", expected_version=version + 1, roller=SequenceDiceRoller([14]),
        entity_id="pc1", ability="str", skill="athletics", dc=15,
        purpose="force the gate",
    )

    with database.connect() as connection:
        narrative_rolls = connection.execute(
            "SELECT COUNT(*) FROM dice_rolls WHERE campaign_id = 'c1'"
        ).fetchone()[0]
        narrative_combat = connection.execute(
            "SELECT COUNT(*) FROM combat_encounters WHERE campaign_id = 'c1'"
        ).fetchone()[0]
        dnd_rolls = connection.execute(
            "SELECT COUNT(*) FROM dice_rolls WHERE campaign_id = 'c2'"
        ).fetchone()[0]
    assert narrative_rolls == 0
    assert narrative_combat == 0
    assert dnd_rolls == 1


def _authoritative_fields(database, campaign_id="c1"):
    with database.connect() as connection:
        return {
            "state_version": connection.execute(
                "SELECT state_version FROM campaigns WHERE id = ?",
                (campaign_id,),
            ).fetchone()["state_version"],
            "memory_events": connection.execute(
                "SELECT COUNT(*) FROM memory_events WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()[0],
            "branch_heads": [
                (row["branch_id"], row["state_version"])
                for row in connection.execute(
                    "SELECT branch_id, state_version FROM branch_heads"
                    " WHERE campaign_id = ? ORDER BY branch_id",
                    (campaign_id,),
                )
            ],
        }


def _seed_exported_campaign(database):
    ids = iter(f"src-{i}" for i in range(1000))
    campaigns = CampaignService(
        database, id_factory=ids.__next__, clock=lambda: "2026-08-16T00:00:00Z"
    )
    campaigns.create_campaign("c1", "Roundtrip")
    attrs = EntityAttributeService(database, campaigns.mutation_engine)
    attrs.apply_explicit("c1", "main", 0, CreateEntityOperation(
        "erin", EntityKind.CHARACTER, "艾琳",
    ))
    attrs.apply_explicit("c1", "main", 1, DefineAttributeOperation(
        AttributeDefinition(
            campaign_id="c1", key="alchemy", label="炼金", category="skill",
            value_type=AttributeType.NUMBER, display=DisplayType.BAR,
            audiences=frozenset({Audience.PLAYER_UI}), minimum=0, maximum=100,
        ),
    ))
    settings = load_settings({"RPG_NARRATOR_MODEL": "narrator-model"})
    narrator = ScriptedLLMClient([
        json.dumps({"operations": [json.loads(_set_attribute("erin", 40))]}),
        "叙述二", json.dumps({"operations": []}),
    ])
    runner = TurnRunner(default_services(database, settings, narrator))
    history = []
    for text in ("把艾琳的炼金术调整为40", "我让艾琳继续探索地牢。"):
        history.append({"role": "user", "content": text})
        result = runner.run({"campaign_id": "c1", "messages": list(history)})
        history.append({
            "role": "assistant",
            "content": strip_tracker_blocks(
                result["choices"][0]["message"]["content"]
            ),
        })


def test_release_export_restart_import_state_match(tmp_path):
    """§17.4 #3: export -> fresh-DB import restores identical authoritative
    state (state_version, entity attribute values, projection)."""
    source = _fresh_database(tmp_path, "source.db")
    _seed_exported_campaign(source)

    export_path = tmp_path / "c1.json"
    CampaignExporter(source).export("c1", export_path)

    target = _fresh_database(tmp_path, "target.db")
    assert CampaignImporter(target).import_file(export_path) == "c1"

    source_projection = ProjectionService(source).for_audience(
        "c1", "main", Audience.PLAYER_UI
    )
    target_projection = ProjectionService(target).for_audience(
        "c1", "main", Audience.PLAYER_UI
    )
    assert target_projection == source_projection
    assert target_projection["state_version"] == 3
    assert target_projection["entities"][0]["attributes"] == [
        {"key": "alchemy", "value": 40}
    ]
    assert _authoritative_fields(target) == _authoritative_fields(source)


def test_release_audit_append_only_on_import(tmp_path):
    """§17.4 #11 (partial): recovery import appends audit events; pre-import
    rows (including another campaign's) stay byte-identical."""
    source = _fresh_database(tmp_path, "source.db")
    _seed_exported_campaign(source)
    export_path = tmp_path / "c1.json"
    CampaignExporter(source).export("c1", export_path)
    export_audit = json.loads(
        export_path.read_text(encoding="utf-8")
    )["audit_events"]
    assert len(export_audit) == 4  # campaign-created + 3 explicit-state-change

    target = _fresh_database(tmp_path, "target.db")
    tgt_ids = iter(f"tgt-{i}" for i in range(1000))
    tgt_campaigns = CampaignService(
        target, id_factory=tgt_ids.__next__, clock=lambda: "2026-08-16T00:00:00Z"
    )
    tgt_campaigns.create_campaign("c2", "Other")
    tgt_attrs = EntityAttributeService(target, tgt_campaigns.mutation_engine)
    tgt_attrs.apply_explicit("c2", "main", 0, CreateEntityOperation(
        "aria", EntityKind.CHARACTER, "Aria",
    ))
    tgt_attrs.apply_explicit("c2", "main", 1, DefineAttributeOperation(
        AttributeDefinition(
            campaign_id="c2", key="alchemy", label="炼金", category="skill",
            value_type=AttributeType.NUMBER, display=DisplayType.BAR,
            audiences=frozenset({Audience.PLAYER_UI}), minimum=0, maximum=100,
        ),
    ))
    settings = load_settings({"RPG_NARRATOR_MODEL": "narrator-model"})
    tgt_narrator = ScriptedLLMClient([
        json.dumps({"operations": [json.loads(_set_attribute("aria", 40))]})
    ])
    TurnRunner(default_services(target, settings, tgt_narrator)).run({
        "campaign_id": "c2",
        "messages": [{"role": "user", "content": "把艾琳的炼金术调整为40"}],
    })

    def _audit_table(database):
        with database.connect() as connection:
            rows = connection.execute(
                "SELECT id, campaign_id, branch_id, state_version, event_type,"
                " source, payload_json, created_at FROM audit_events ORDER BY id"
            ).fetchall()
        return {row["id"]: tuple(row) for row in rows}

    before = _audit_table(target)
    assert [row for row in before.values() if row[1] == "c2"]

    CampaignImporter(target).import_file(export_path)
    after = _audit_table(target)

    assert all(after[row_id] == row for row_id, row in before.items())
    c2_after = {row_id: row for row_id, row in after.items() if row[1] == "c2"}
    c2_before = {row_id: row for row_id, row in before.items() if row[1] == "c2"}
    assert c2_after == c2_before

    new_ids = set(after) - set(before)
    recovery_ids = [
        row_id for row_id in new_ids if after[row_id][4] == "recovery_import"
    ]
    assert len(recovery_ids) == 1
    assert after[recovery_ids[0]][5] == "import"
    imported_ids = {row["id"] for row in export_audit}
    assert new_ids == imported_ids | set(recovery_ids)
    for row in export_audit:
        stored = after[row["id"]]
        assert stored[4] == row["event_type"]
        assert stored[5] == row["source"]
        assert json.loads(stored[6]) == row["payload"]