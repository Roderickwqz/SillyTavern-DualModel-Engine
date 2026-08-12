import json

import pytest

from sillytavern_rpg_engine.domain.dice import DiceFormula, SequenceDiceRoller
from sillytavern_rpg_engine.domain.dnd import (
    Condition,
    DND2024_RULES_VERSION,
    MasteryProperty,
    SpellProfile,
    WeaponProfile,
    WeaponProperty,
)
from sillytavern_rpg_engine.domain.errors import ValidationError
from sillytavern_rpg_engine.domain.models import EntityKind
from sillytavern_rpg_engine.services.attacks import AttackService, AttackSpec
from sillytavern_rpg_engine.services.attributes import SetAttributeOperation
from sillytavern_rpg_engine.services.campaign_export import CampaignExporter
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.combat import CombatService, CombatantEntry
from sillytavern_rpg_engine.services.conditions import ConditionService, condition_map
from sillytavern_rpg_engine.services.damage import DamageService
from sillytavern_rpg_engine.services.dice import DiceService
from sillytavern_rpg_engine.services.dnd_pack import DndRulesService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)
from sillytavern_rpg_engine.services.rests import RestService
from sillytavern_rpg_engine.services.snapshots import SnapshotBuilder
from sillytavern_rpg_engine.services.spells import SpellService

SWORD = WeaponProfile(
    key="longsword", damage=DiceFormula(1, 8), damage_type="slashing",
    properties=frozenset(), mastery=MasteryProperty.SAP,
    attack_ability="str", range_normal=5,
)
FIREBOLT = SpellProfile(
    key="fire-bolt", level=0, attack=True, save_ability=None,
    damage=DiceFormula(1, 10), damage_type="fire", healing=None,
    concentration=False, duration_rounds=None, ability="int",
)

CHARACTER = {
    "ability_str": 16, "ability_dex": 14, "ability_con": 13,
    "ability_int": 10, "ability_wis": 12, "ability_cha": 8,
    "proficiency_bonus": 2, "proficiencies": ["longsword"],
    "armor_class": 15, "hp_max": 20, "hp_current": 20, "hp_temp": 0,
    "speed": 30, "character_level": 1,
    "spell_slots_max": [0] * 9, "spell_slots_current": [0] * 9,
    "hit_die": 10, "hit_dice_total": 1, "hit_dice_current": 1,
    "death_saves_success": 0, "death_saves_failure": 0,
    "is_dead": False, "is_stable": False, "resistances": [],
    "vulnerabilities": [], "immunities": [], "condition_immunities": [],
    "weapon_masteries": ["longsword"],
}


def _services(database):
    ids = iter(f"e{n}" for n in range(1, 5000))
    clock = lambda: "2026-08-12T00:00:00Z"
    make = lambda cls: cls(database, id_factory=lambda: next(ids), clock=clock)
    campaigns = make(CampaignService)
    dnd = make(DndRulesService)
    entities = EntityAttributeService(database, dnd.mutation_engine)
    return {
        "campaigns": campaigns, "dnd": dnd, "entities": entities,
        "dice": make(DiceService), "combat": make(CombatService),
        "attacks": make(AttackService), "damage": make(DamageService),
        "conditions": make(ConditionService), "spells": make(SpellService),
        "rests": make(RestService),
    }


def _fill(services, entity_id, version, **overrides):
    values = {**CHARACTER, **overrides}
    for key, value in values.items():
        services["entities"].apply_explicit(
            "c1", "main", version, SetAttributeOperation(entity_id, key, value)
        )
        version += 1
    return version


def test_phase3_full_flow(database, tmp_path):
    s = _services(database)
    s["campaigns"].create_campaign("c1", "Dungeon")
    s["campaigns"].create_campaign("c2", "Story")
    s["dnd"].seed_pack("c1", 0)
    s["entities"].apply_explicit("c1", "main", 1, CreateEntityOperation(
        entity_id="pc1", kind=EntityKind.CHARACTER, name="Aria",
    ))
    version = _fill(s, "pc1", 2)
    # readiness gate
    assert s["dnd"].readiness("c1").ready
    result = s["dnd"].enable("c1", version)
    assert result.snapshot["campaign"]["rules"]["version"] == DND2024_RULES_VERSION
    version = result.state_version
    # narrative campaign rejects D&D operations
    with pytest.raises(ValidationError, match="enabled"):
        s["dice"].check("c2", 0, SequenceDiceRoller([10]), "pc1", "str",
                        None, 10, "lore")
    # standalone check
    result = s["dice"].check("c1", version, SequenceDiceRoller([14]), "pc1",
                             "str", "athletics", 15, "force the gate")
    version = result.state_version
    # combat: pc1 (18+2) before orc
    s["entities"].apply_explicit("c1", "main", version, CreateEntityOperation(
        entity_id="orc", kind=EntityKind.CHARACTER, name="Orc",
    ))
    version = _fill(s, "orc", version + 1, ability_dex=10, armor_class=13)
    result = s["combat"].start(
        "c1", version, SequenceDiceRoller([18, 5, 3]),
        (CombatantEntry("pc1"), CombatantEntry("orc", surprised=True)),
    )
    version = result.state_version
    # pc1 attacks with sap; orc's turn is disadvantaged
    result = s["attacks"].attack(
        "c1", version, "pc1", AttackSpec(target_id="orc", weapon=SWORD),
        roller=SequenceDiceRoller([15, 6]),
    )
    version = result.state_version
    assert result.snapshot["combat"]["active_entity_id"] == "pc1"
    # cantrip attack next turn after advancing twice
    result = s["combat"].advance_turn("c1", version, SequenceDiceRoller([]))
    version = result.state_version
    result = s["combat"].advance_turn("c1", version, SequenceDiceRoller([]))
    version = result.state_version
    result = s["spells"].cast(
        "c1", version, "pc1", FIREBOLT, slot_level=None, target_ids=("orc",),
        roller=SequenceDiceRoller([16, 5]),
    )
    version = result.state_version
    # poison via condition service; end combat; short rest
    result = s["conditions"].apply(
        "c1", version, "pc1", Condition.POISONED, source="trap"
    )
    version = result.state_version
    result = s["combat"].end("c1", version)
    version = result.state_version
    result = s["rests"].long_rest("c1", version, "pc1")
    version = result.state_version
    # snapshot + export carry the new sections; rolls are immutable and pinned
    with database.connect() as connection:
        snapshot = SnapshotBuilder().build(connection, "c1", "main")
        assert snapshot["combat"] is None
        assert {
            c["condition"] for c in snapshot["conditions"]
        } == {"poisoned"}
        rolls = connection.execute(
            "SELECT COUNT(*) AS n, MIN(rules_version) AS v FROM dice_rolls"
        ).fetchone()
        assert rolls["n"] >= 5 and rolls["v"] == "srd-5.2.1"
    exported = CampaignExporter(database).export("c1", tmp_path / "c1.json")
    verify = CampaignExporter(database).verify_export(exported)
    assert verify["ok"], verify["errors"]
    payload = json.loads(exported.read_text(encoding="utf-8"))
    assert payload["dice_rolls"] and payload["combat_encounters"]
    # disabling preserves the state and requires no confirmation now
    result = s["campaigns"].disable_rules("c1", version)
    assert result.snapshot["campaign"]["rules"]["enabled"] is False
    with pytest.raises(ValidationError, match="enabled"):
        s["dice"].check("c1", result.state_version, SequenceDiceRoller([1]),
                        "pc1", "str", None, 10, "lore")


def test_version_pinning_rejects_mismatched_rules_version(database):
    s = _services(database)
    s["campaigns"].create_campaign("c1", "Dungeon")
    s["dnd"].seed_pack("c1", 0)
    result = s["dnd"].enable("c1", 1)  # no characters -> ready
    with database.transaction() as connection:
        connection.execute(
            "UPDATE campaigns SET rules_version = 'srd-9.9' WHERE id = 'c1'"
        )
    with pytest.raises(ValidationError, match="version"):
        s["dice"].check("c1", result.state_version, SequenceDiceRoller([1]),
                        "pc1", "str", None, 10, "lore")
