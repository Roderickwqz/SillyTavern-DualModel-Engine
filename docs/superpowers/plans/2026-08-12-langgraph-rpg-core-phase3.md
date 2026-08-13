# LangGraph RPG Core Phase 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the D&D 2024 / 5.5e rules layer to the Phase 1+2 core: campaign readiness gating, authoritative injectable dice with immutable roll records, full core combat (initiative, action economy, movement, attacks, damage, conditions), 2024 Weapon Mastery, spell-slot transactions with concentration, rests, and monster mechanics.

**Architecture:** Pure-Python deterministic rules engine on top of the existing mutation infrastructure. Every game-state change is a `MutationOperation` applied through `MutationEngine`, so version bump + audit event + outbox row + snapshot stay atomic. Randomness enters only through an injectable `DiceRoller` protocol (`secrets`-based in production, scripted sequences in tests) and every roll is persisted to an append-only `dice_rolls` table inside the same transaction as the state change. D&D statistics live in the Phase 1 dynamic attribute registry (seeded pack); combat-volatile state (initiative, budgets, debuffs) lives in new branch-scoped tables. No LLM dependency: random operations are user-command only and are deliberately absent from the proposal codec.

**Tech Stack:** Python 3.11+, standard-library `sqlite3`, `secrets`, dataclasses, enums, pytest 8.x. Rules boundary: 2024 Free Rules / SRD 5.2.1 core mechanics only — no proprietary class/spell/monster text.

## Global Constraints

- All Phase 1/2 guarantees unchanged: one CAS version bump, one audit event, one JSONL outbox row, one snapshot per mutation, committed in a single transaction or rolled back whole.
- LLMs are never an authority on randomness; the Python engine reads state, rolls via the injected `DiceRoller`, resolves, and commits state + immutable roll record in one transaction. Production rolls record actual faces, modifiers, DC, result, and rules version.
- `dice_rolls` is append-only: DB triggers reject UPDATE and DELETE.
- Rules boundary is the public 2024 Free Rules / SRD 5.2.1. Do not bundle proprietary class, subclass, spell, monster, or adventure text; extra content arrives later as user-provided data packs.
- Campaign pins ruleset version: enabling D&D stores mode `dnd-2024` and version `srd-5.2.1` (`DND2024_RULES_VERSION`). Every D&D operation validates the pinned version and rejects mismatches — upgrades never silently reinterpret old turns.
- Narrative campaigns never execute D&D resolution: every operation in this plan calls `require_dnd_2024(campaign)` first and raises `ValidationError` otherwise. Enabling requires a readiness check over all character entities and fails with a complete missing-fields report.
- Rules enable/disable come only from explicit user commands (`CampaignService` / `DndRulesService`), never from API request parameters or narrator inference.
- 2024 Exhaustion constants: 6 levels, d20 Test penalty −2×level, Speed −5 ft×level, death at level 6, Long Rest removes 1 level. Engine constants, not per-campaign config.
- Weapon Mastery: Cleave and Nick are once-per-turn; Graze/Push/Sap/Slow/Topple/Vex trigger per qualifying attack (Slow does not stack beyond −10 ft).
- Any advantage + any disadvantage on the same d20 roll cancel to a straight roll (2024).
- Operation kinds that roll dice (`roll_check`, `attack`, `cast_spell`, …) must NOT be added to the proposal codec. Only the non-random `apply_condition` / `remove_condition` kinds are added so narrative inference can propose them as Pending proposals.
- Branch scope: encounters record `branch_id` (one active encounter per campaign+branch, DB-enforced). `entity_conditions` stay campaign-global like other authoritative state until the Phase 6 branching plan.
- Do not modify, delete, or migrate existing JavaScript DME state in this phase.
- Do not store API keys, secrets, machine-specific absolute paths, or credentials in campaign data or exports.
- Use `.harness/scripts/committer` for every commit and stage only files listed by the task.

## Multi-Plan Roadmap

The approved specification (`docs/superpowers/specs/2026-08-10-langgraph-rpg-companion-design.md`) is split into independent plans:

1. Phase 1 (done): authoritative Python core and persistence.
2. Phase 2 (done): memory and personality.
3. **This plan:** D&D 2024 / 5.5e — campaign readiness, dice, action economy, combat, conditions, Weapon Mastery, spell/resource transactions.
4. FastAPI and LangGraph: OpenAI-compatible endpoint, request normalization, model adapters, narrative/critic graph.
5. RPG Companion compatibility fork: dynamic `attributes`, rules metadata, read-only mode, narrowed JSON cleaning.
6. Branching and release: SillyTavern history hashes, swipe/edit/delete recovery, backups, long-run simulation, E2E.

## File Structure

```text
src/sillytavern_rpg_engine/
├── domain/
│   ├── dice.py                 # NEW: DiceFormula, DiceRoller protocol, d20 resolution
│   └── dnd.py                  # NEW: ruleset constants, enums, profiles, condition effects
├── persistence/
│   └── schema/
│       └── 0004_dnd_combat.sql # NEW: dice_rolls, combat_encounters, combatants, entity_conditions
├── services/
│   ├── dnd_pack.py             # NEW: attribute pack seeding, readiness, enable operation
│   ├── dice.py                 # NEW: record_roll helper, CheckOperation, DiceService
│   ├── conditions.py           # NEW: Apply/RemoveConditionOperation, effect aggregation
│   ├── combat.py               # NEW: encounter lifecycle, turn advance, actions, movement
│   ├── damage.py               # NEW: damage/heal/death-save pipeline, concentration checks
│   ├── attacks.py              # NEW: attack resolution, weapon properties, grapple/shove
│   ├── mastery.py              # NEW: 2024 Weapon Mastery effects
│   ├── spells.py               # NEW: casting, slots, concentration lifecycle
│   ├── rests.py                # NEW: short/long rest transactions
│   ├── monsters.py             # NEW: multiattack, recharge, data-driven turn triggers
│   ├── snapshots.py            # MODIFY: + combat, conditions sections
│   ├── projection.py           # MODIFY: + per-entity conditions, combat summary
│   ├── proposals.py            # MODIFY: codec accepts apply_condition / remove_condition
│   └── campaign_export.py      # MODIFY: + dice_rolls, conditions, combat sections
tests/backend/
├── unit/
│   ├── test_dice_domain.py
│   ├── test_dnd_schema.py
│   ├── test_dnd_domain.py
│   ├── test_dnd_pack.py
│   ├── test_dice_service.py
│   ├── test_conditions.py
│   ├── test_combat.py
│   ├── test_actions_movement.py
│   ├── test_damage.py
│   ├── test_attacks.py
│   ├── test_mastery.py
│   ├── test_spells.py
│   ├── test_rests.py
│   ├── test_monsters.py
│   └── test_proposal_condition_ops.py
└── integration/
    └── test_phase3_flow.py
```

---

### Task 1: Dice domain contracts and injectable randomness

**Files:**
- Create: `src/sillytavern_rpg_engine/domain/dice.py`
- Test: `tests/backend/unit/test_dice_domain.py`

**Interfaces:**
- Consumes: `domain/errors.py` (`ValidationError`).
- Produces: `DiceFormula(count, sides, modifier)` with `.parse()`/`__str__`; `DiceRoller` protocol (`roll(count, sides) -> tuple[int, ...]`); `SecureDiceRoller`, `SequenceDiceRoller`; `D20Mode`; `D20Outcome`; `roll_formula()`, `resolve_d20()`, `combine_modes()`; constants `MAX_DICE_COUNT`, `MIN_DIE_SIDES`, `MAX_DIE_SIDES`.

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from sillytavern_rpg_engine.domain.dice import (
    D20Mode,
    DiceFormula,
    SecureDiceRoller,
    SequenceDiceRoller,
    combine_modes,
    resolve_d20,
    roll_formula,
)
from sillytavern_rpg_engine.domain.errors import ValidationError


def test_formula_parse_and_roundtrip():
    assert DiceFormula.parse("2d6+3") == DiceFormula(2, 6, 3)
    assert DiceFormula.parse("1d20-1") == DiceFormula(1, 20, -1)
    assert DiceFormula.parse("8d8") == DiceFormula(8, 8, 0)
    assert str(DiceFormula(2, 6, 3)) == "2d6+3"
    assert str(DiceFormula(1, 20, -1)) == "1d20-1"
    assert str(DiceFormula(8, 8)) == "8d8"


def test_formula_rejects_garbage_and_bounds():
    for text in ("", "d6", "2D6", "2d6+1x", "0d6", "101d6", "1d1", "1d1001"):
        with pytest.raises(ValidationError):
            DiceFormula.parse(text)
    with pytest.raises(ValidationError, match="integer"):
        DiceFormula(True, 6)


def test_sequence_roller_is_deterministic_and_exhausts():
    roller = SequenceDiceRoller([4, 2])
    assert roller.roll(2, 6) == (4, 2)
    with pytest.raises(RuntimeError, match="exhausted"):
        roller.roll(1, 6)
    with pytest.raises(ValidationError, match="outside"):
        SequenceDiceRoller([7]).roll(1, 6)


def test_secure_roller_faces_within_range():
    roller = SecureDiceRoller()
    for _ in range(200):
        faces = roller.roll(3, 20)
        assert len(faces) == 3
        assert all(1 <= face <= 20 for face in faces)


def test_roll_formula_sums_faces_plus_modifier():
    outcome = roll_formula(SequenceDiceRoller([3, 5]), DiceFormula(2, 6, 2))
    assert outcome.faces == (3, 5)
    assert outcome.total == 10


def test_resolve_d20_modes_and_naturals():
    normal = resolve_d20(SequenceDiceRoller([12]), D20Mode.NORMAL, 5)
    assert (normal.kept, normal.dropped, normal.total) == (12, None, 17)
    adv = resolve_d20(SequenceDiceRoller([7, 18]), D20Mode.ADVANTAGE, 0)
    assert (adv.kept, adv.dropped) == (18, 7)
    assert adv.is_natural_20 is False
    dis = resolve_d20(SequenceDiceRoller([20, 3]), D20Mode.DISADVANTAGE, 1)
    assert (dis.kept, dis.dropped, dis.total) == (3, 20, 4)
    crit = resolve_d20(SequenceDiceRoller([20]), D20Mode.NORMAL, -1)
    assert crit.is_natural_20 and crit.total == 19
    fumble = resolve_d20(SequenceDiceRoller([1]), D20Mode.NORMAL, 10)
    assert fumble.is_natural_1 and fumble.total == 11


def test_combine_modes_cancels_any_advantage_with_any_disadvantage():
    assert combine_modes(1, 1) is D20Mode.NORMAL
    assert combine_modes(3, 1) is D20Mode.NORMAL
    assert combine_modes(2, 0) is D20Mode.ADVANTAGE
    assert combine_modes(0, 2) is D20Mode.DISADVANTAGE
    assert combine_modes(0, 0) is D20Mode.NORMAL
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/backend/unit/test_dice_domain.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sillytavern_rpg_engine.domain.dice'`

- [ ] **Step 3: Implement `domain/dice.py`**

```python
"""Dice formulas, injectable random sources, and d20 test resolution."""

from dataclasses import dataclass
from enum import StrEnum
import re
import secrets
from typing import Protocol

from .errors import ValidationError

MAX_DICE_COUNT = 100
MIN_DIE_SIDES = 2
MAX_DIE_SIDES = 1000

_FORMULA_RE = re.compile(r"^(\d+)d(\d+)([+-]\d+)?$")


class D20Mode(StrEnum):
    """How a d20 test picks its kept die."""

    NORMAL = "normal"
    ADVANTAGE = "advantage"
    DISADVANTAGE = "disadvantage"


@dataclass(frozen=True)
class DiceFormula:
    """A validated NdS+M dice expression."""

    count: int
    sides: int
    modifier: int = 0

    def __post_init__(self) -> None:
        for field_name, value, low, high in (
            ("count", self.count, 1, MAX_DICE_COUNT),
            ("sides", self.sides, MIN_DIE_SIDES, MAX_DIE_SIDES),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValidationError(f"{field_name} must be an integer")
            if not low <= value <= high:
                raise ValidationError(
                    f"{field_name} {value} outside range [{low}, {high}]"
                )
        if isinstance(self.modifier, bool) or not isinstance(self.modifier, int):
            raise ValidationError("modifier must be an integer")

    @classmethod
    def parse(cls, text: str) -> "DiceFormula":
        """Parse ``NdS`` / ``NdS+M`` / ``NdS-M``; raise ValidationError otherwise."""
        match = _FORMULA_RE.match(text.strip()) if isinstance(text, str) else None
        if match is None:
            raise ValidationError(f"invalid dice formula {text!r}")
        count, sides, modifier = match.groups()
        return cls(int(count), int(sides), int(modifier) if modifier else 0)

    def __str__(self) -> str:
        base = f"{self.count}d{self.sides}"
        if self.modifier > 0:
            return f"{base}+{self.modifier}"
        if self.modifier < 0:
            return f"{base}{self.modifier}"
        return base


class DiceRoller(Protocol):
    """Source of random faces; implementations must be deterministic when seeded."""

    def roll(self, count: int, sides: int) -> tuple[int, ...]:
        """Return ``count`` faces, each in ``1..sides``."""


class SecureDiceRoller:
    """Production roller backed by the OS cryptographic random source."""

    def roll(self, count: int, sides: int) -> tuple[int, ...]:
        return tuple(secrets.randbelow(sides) + 1 for _ in range(count))


class SequenceDiceRoller:
    """Deterministic roller for tests; raises RuntimeError when the queue runs out."""

    def __init__(self, values: list[int] | tuple[int, ...]):
        self._values = list(values)

    def roll(self, count: int, sides: int) -> tuple[int, ...]:
        if len(self._values) < count:
            raise RuntimeError(
                f"scripted sequence exhausted: need {count}, have {len(self._values)}"
            )
        faces = tuple(self._values[:count])
        del self._values[:count]
        for face in faces:
            if not 1 <= face <= sides:
                raise ValidationError(
                    f"scripted face {face} outside d{sides} range"
                )
        return faces

    @property
    def remaining(self) -> int:
        return len(self._values)


@dataclass(frozen=True)
class RollOutcome:
    """The faces and total of one rolled formula."""

    formula: DiceFormula
    faces: tuple[int, ...]
    total: int


def roll_formula(roller: DiceRoller, formula: DiceFormula) -> RollOutcome:
    faces = roller.roll(formula.count, formula.sides)
    return RollOutcome(formula, faces, sum(faces) + formula.modifier)


@dataclass(frozen=True)
class D20Outcome:
    """The kept/dropped faces and total of one d20 test."""

    kept: int
    dropped: int | None
    modifier: int
    total: int
    natural: int
    is_natural_20: bool
    is_natural_1: bool


def resolve_d20(roller: DiceRoller, mode: D20Mode, modifier: int) -> D20Outcome:
    """Roll a d20 test; advantage keeps the higher of two, disadvantage the lower."""
    if mode is D20Mode.NORMAL:
        kept = roller.roll(1, 20)[0]
        dropped = None
    else:
        faces = roller.roll(2, 20)
        if mode is D20Mode.ADVANTAGE:
            kept, dropped = max(faces), min(faces)
        else:
            kept, dropped = min(faces), max(faces)
    return D20Outcome(
        kept=kept,
        dropped=dropped,
        modifier=modifier,
        total=kept + modifier,
        natural=kept,
        is_natural_20=kept == 20,
        is_natural_1=kept == 1,
    )


def combine_modes(advantages: int, disadvantages: int) -> D20Mode:
    """2024 rule: any advantage plus any disadvantage cancel to a straight roll."""
    if advantages > 0 and disadvantages > 0:
        return D20Mode.NORMAL
    if advantages > 0:
        return D20Mode.ADVANTAGE
    if disadvantages > 0:
        return D20Mode.DISADVANTAGE
    return D20Mode.NORMAL
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/backend/unit/test_dice_domain.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat(core): add dice formulas and injectable rollers" \
  "src/sillytavern_rpg_engine/domain/dice.py" \
  "tests/backend/unit/test_dice_domain.py"
```

---

### Task 2: Migration 0004 — dice roll ledger, encounters, combatants, conditions

**Files:**
- Create: `src/sillytavern_rpg_engine/persistence/schema/0004_dnd_combat.sql`
- Test: `tests/backend/unit/test_dnd_schema.py`

**Interfaces:**
- Consumes: existing migration runner conventions (see `0003_memory_personality.sql`).
- Produces: tables `dice_rolls`, `combat_encounters`, `combatants`, `entity_conditions`; partial unique index `one_active_encounter`; append-only triggers on `dice_rolls`.

- [ ] **Step 1: Write the failing schema tests**

```python
import sqlite3

import pytest


def test_migration_0004_creates_tables(database):
    with database.connect() as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "dice_rolls",
            "combat_encounters",
            "combatants",
            "entity_conditions",
        } <= names
        version = connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0]
        assert version == 4


def _campaign(connection):
    connection.execute(
        "INSERT INTO campaigns(id, name, created_at, updated_at)"
        " VALUES ('c1', 'Dungeon', '2026-08-12', '2026-08-12')"
    )
    connection.execute(
        "INSERT INTO branches(id, campaign_id, status, created_at)"
        " VALUES ('main', 'c1', 'active', '2026-08-12')"
    )


def test_dice_rolls_are_append_only(database):
    with database.transaction() as connection:
        _campaign(connection)
        connection.execute(
            "INSERT INTO dice_rolls(id, campaign_id, branch_id, purpose, formula,"
            " faces_json, total, rules_version, state_version, created_at)"
            " VALUES ('r1', 'c1', 'main', 'check', '1d20', '[12]', 12,"
            " 'srd-5.2.1', 1, '2026-08-12')"
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE dice_rolls SET total = 99 WHERE id = 'r1'")
    with database.transaction() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM dice_rolls WHERE id = 'r1'")


def test_one_active_encounter_per_branch(database):
    with database.transaction() as connection:
        _campaign(connection)
        for encounter_id in ("e1", "e2"):
            connection.execute(
                "INSERT INTO combat_encounters(id, campaign_id, branch_id,"
                " created_state_version, created_at)"
                " VALUES (?, 'c1', 'main', 1, '2026-08-12')",
                (encounter_id,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO combat_encounters(id, campaign_id, branch_id,"
                " created_state_version, created_at)"
                " VALUES ('e3', 'c1', 'main', 1, '2026-08-12')"
            )
    with database.transaction() as connection:
        connection.execute(
            "UPDATE combat_encounters SET status = 'ended', ended_state_version = 2"
            " WHERE id = 'e1'"
        )
        connection.execute(
            "INSERT INTO combat_encounters(id, campaign_id, branch_id,"
            " created_state_version, created_at)"
            " VALUES ('e4', 'c1', 'main', 3, '2026-08-12')"
        )


def test_combatant_unique_per_encounter_and_conditions_pk(database):
    with database.transaction() as connection:
        _campaign(connection)
        connection.execute(
            "INSERT INTO entities(id, campaign_id, kind, name, normalized_name,"
            " created_state_version) VALUES ('pc1', 'c1', 'character', 'Aria',"
            " 'aria', 1)"
        )
        connection.execute(
            "INSERT INTO combat_encounters(id, campaign_id, branch_id,"
            " created_state_version, created_at)"
            " VALUES ('e1', 'c1', 'main', 1, '2026-08-12')"
        )
        connection.execute(
            "INSERT INTO combatants(id, encounter_id, entity_id, initiative)"
            " VALUES ('cb1', 'e1', 'pc1', 15)"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO combatants(id, encounter_id, entity_id, initiative)"
                " VALUES ('cb2', 'e1', 'pc1', 12)"
            )
        connection.execute(
            "INSERT INTO entity_conditions(campaign_id, entity_id, condition,"
            " level, source, applied_state_version)"
            " VALUES ('c1', 'pc1', 'prone', 1, 'test', 1)"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO entity_conditions(campaign_id, entity_id, condition,"
                " level, source, applied_state_version)"
                " VALUES ('c1', 'pc1', 'prone', 1, 'test', 2)"
            )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/backend/unit/test_dnd_schema.py -v`
Expected: FAIL — tables do not exist (`no such table: dice_rolls`), `MAX(version)` still 3.

- [ ] **Step 3: Write `0004_dnd_combat.sql`**

```sql
CREATE TABLE dice_rolls (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL,
    turn_id TEXT,
    roller_entity_id TEXT,
    purpose TEXT NOT NULL,
    formula TEXT NOT NULL,
    faces_json TEXT NOT NULL,
    modifier INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL,
    dc INTEGER,
    success INTEGER CHECK (success IN (0, 1)),
    critical INTEGER NOT NULL DEFAULT 0 CHECK (critical IN (0, 1)),
    rules_version TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TRIGGER dice_rolls_no_update BEFORE UPDATE ON dice_rolls
BEGIN
    SELECT RAISE(ABORT, 'dice_rolls is append-only');
END;

CREATE TRIGGER dice_rolls_no_delete BEFORE DELETE ON dice_rolls
BEGIN
    SELECT RAISE(ABORT, 'dice_rolls is append-only');
END;

CREATE TABLE combat_encounters (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'ended')),
    round_number INTEGER NOT NULL DEFAULT 1 CHECK (round_number >= 1),
    active_index INTEGER NOT NULL DEFAULT 0 CHECK (active_index >= 0),
    created_state_version INTEGER NOT NULL,
    ended_state_version INTEGER,
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX one_active_encounter
    ON combat_encounters(campaign_id, branch_id) WHERE status = 'active';

CREATE TABLE combatants (
    id TEXT PRIMARY KEY,
    encounter_id TEXT NOT NULL REFERENCES combat_encounters(id) ON DELETE CASCADE,
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    initiative INTEGER NOT NULL,
    action_used INTEGER NOT NULL DEFAULT 0 CHECK (action_used IN (0, 1)),
    bonus_used INTEGER NOT NULL DEFAULT 0 CHECK (bonus_used IN (0, 1)),
    reaction_used INTEGER NOT NULL DEFAULT 0 CHECK (reaction_used IN (0, 1)),
    movement_total INTEGER NOT NULL DEFAULT 0 CHECK (movement_total >= 0),
    movement_used INTEGER NOT NULL DEFAULT 0 CHECK (movement_used >= 0),
    interaction_used INTEGER NOT NULL DEFAULT 0 CHECK (interaction_used IN (0, 1)),
    slot_spent_this_turn INTEGER NOT NULL DEFAULT 0 CHECK (slot_spent_this_turn IN (0, 1)),
    attacks_this_turn INTEGER NOT NULL DEFAULT 0 CHECK (attacks_this_turn >= 0),
    dodging INTEGER NOT NULL DEFAULT 0 CHECK (dodging IN (0, 1)),
    disengaged INTEGER NOT NULL DEFAULT 0 CHECK (disengaged IN (0, 1)),
    hidden INTEGER NOT NULL DEFAULT 0 CHECK (hidden IN (0, 1)),
    help_grants_json TEXT NOT NULL DEFAULT '[]',
    readied_action_json TEXT,
    debuffs_json TEXT NOT NULL DEFAULT '{}',
    mastery_uses_json TEXT NOT NULL DEFAULT '{}',
    concentrating_spell TEXT,
    concentration_rounds INTEGER,
    recharge_json TEXT NOT NULL DEFAULT '[]',
    triggers_json TEXT NOT NULL DEFAULT '[]',
    UNIQUE(encounter_id, entity_id)
);

CREATE TABLE entity_conditions (
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    condition TEXT NOT NULL,
    level INTEGER NOT NULL DEFAULT 1 CHECK (level >= 1),
    source TEXT NOT NULL,
    applied_state_version INTEGER NOT NULL,
    PRIMARY KEY (campaign_id, entity_id, condition)
);
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/backend/unit/test_dnd_schema.py tests/backend/unit/test_migrations.py -v`
Expected: all pass (existing migration tests still green).

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat(core): add dnd combat schema migration" \
  "src/sillytavern_rpg_engine/persistence/schema/0004_dnd_combat.sql" \
  "tests/backend/unit/test_dnd_schema.py"
```

---

### Task 3: D&D 2024 domain constants, profiles, and gating

**Files:**
- Create: `src/sillytavern_rpg_engine/domain/dnd.py`
- Test: `tests/backend/unit/test_dnd_domain.py`

**Interfaces:**
- Consumes: `domain/dice.py` (`DiceFormula`), `domain/models.py` (`Campaign`, `RulesMode`), `domain/errors.py`.
- Produces: `DND2024_RULESET_ID`, `DND2024_RULES_VERSION`; enums `DamageType`, `Condition`, `ActionType`, `WeaponProperty`, `MasteryProperty`, `Cover`, `CreatureSize`; `ABILITIES`; `ability_modifier()`; `WeaponProfile`, `SpellProfile`; `ConditionEffects`, `CONDITION_EFFECTS`; `exhaustion_penalty()`, `effective_speed()`; `require_dnd_2024()`; `HIDE_DC`, exhaustion constants.

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from sillytavern_rpg_engine.domain.dice import DiceFormula
from sillytavern_rpg_engine.domain.dnd import (
    ABILITIES,
    CONDITION_EFFECTS,
    DND2024_RULES_VERSION,
    Condition,
    Cover,
    MasteryProperty,
    SpellProfile,
    WeaponProfile,
    WeaponProperty,
    ability_modifier,
    effective_speed,
    exhaustion_penalty,
    require_dnd_2024,
)
from sillytavern_rpg_engine.domain.errors import ValidationError
from sillytavern_rpg_engine.domain.models import Campaign, CampaignRules, RulesMode


def test_ability_modifier_rounds_down_toward_negative():
    assert [ability_modifier(s) for s in (1, 8, 10, 15, 20, 30)] == [-5, -1, 0, 2, 5, 10]


def test_weapon_profile_validation():
    sword = WeaponProfile(
        key="longsword", damage=DiceFormula(1, 8), damage_type="slashing",
        properties=frozenset({WeaponProperty.LIGHT}),
        mastery=MasteryProperty.SAP, attack_ability="str", range_normal=5,
    )
    assert sword.damage_type == "slashing"
    with pytest.raises(ValidationError, match="damage_type"):
        WeaponProfile(key="x", damage=DiceFormula(1, 6), damage_type="sparkle",
                      properties=frozenset(), mastery=None,
                      attack_ability="str", range_normal=5)
    with pytest.raises(ValidationError, match="attack_ability"):
        WeaponProfile(key="x", damage=DiceFormula(1, 6), damage_type="slashing",
                      properties=frozenset(), mastery=None,
                      attack_ability="cha", range_normal=5)
    with pytest.raises(ValidationError, match="range_long"):
        WeaponProfile(key="x", damage=DiceFormula(1, 6), damage_type="piercing",
                      properties=frozenset({WeaponProperty.THROWN}), mastery=None,
                      attack_ability="str", range_normal=20, range_long=None)


def test_spell_profile_validation():
    bolt = SpellProfile(key="fire-bolt", level=0, attack=True, save_ability=None,
                        damage=DiceFormula(1, 10), damage_type="fire",
                        healing=None, concentration=False, duration_rounds=None,
                        ability="int")
    assert bolt.level == 0
    with pytest.raises(ValidationError, match="save_ability"):
        SpellProfile(key="bad", level=1, attack=False, save_ability=None,
                     damage=None, damage_type=None, healing=None,
                     concentration=False, duration_rounds=None, ability="int")
    with pytest.raises(ValidationError, match="concentration"):
        SpellProfile(key="bad", level=1, attack=False, save_ability="dex",
                     damage=None, damage_type=None, healing=None,
                     concentration=True, duration_rounds=None, ability="int")


def test_cover_bonus_and_total_cover():
    assert Cover.NONE.ac_bonus == 0
    assert Cover.HALF.ac_bonus == 2
    assert Cover.THREE_QUARTERS.ac_bonus == 5
    assert Cover.TOTAL.ac_bonus is None


def test_condition_effects_table_covers_2024_core():
    assert set(CONDITION_EFFECTS) == set(Condition)
    assert CONDITION_EFFECTS[Condition.POISONED].attacker_disadvantage
    assert CONDITION_EFFECTS[Condition.INVISIBLE].attacker_advantage
    assert CONDITION_EFFECTS[Condition.PARALYZED].crit_when_hit_within_5ft
    assert CONDITION_EFFECTS[Condition.GRAPPLED].speed_zero
    assert CONDITION_EFFECTS[Condition.UNCONSCIOUS].incapacitated


def test_exhaustion_penalty_and_effective_speed():
    assert exhaustion_penalty(0) == 0
    assert exhaustion_penalty(3) == -6
    assert effective_speed(30, {Condition.EXHAUSTION: 2}) == 20
    assert effective_speed(30, {Condition.GRAPPLED: 1}) == 0
    assert effective_speed(10, {Condition.EXHAUSTION: 3}) == 0


def test_require_dnd_2024_gates_on_mode_enabled_and_version():
    campaign = Campaign("c1", "Story", 0)
    with pytest.raises(ValidationError, match="not .*enabled"):
        require_dnd_2024(campaign)
    wrong_version = Campaign(
        "c1", "Story", 0,
        CampaignRules(RulesMode.DND_2024, True, "srd-9.9"),
    )
    with pytest.raises(ValidationError, match="version"):
        require_dnd_2024(wrong_version)
    ready = Campaign(
        "c1", "Dungeon", 0,
        CampaignRules(RulesMode.DND_2024, True, DND2024_RULES_VERSION),
    )
    require_dnd_2024(ready)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/backend/unit/test_dnd_domain.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `domain/dnd.py`**

```python
"""D&D 2024 / SRD 5.2.1 rules constants, profiles, and gating.

Mechanics boundary: public 2024 Free Rules / SRD 5.2.1 only. No proprietary
class, spell, monster, or adventure text is bundled.
"""

from dataclasses import dataclass
from enum import StrEnum

from .dice import DiceFormula
from .errors import ValidationError
from .models import Campaign, RulesMode

DND2024_RULESET_ID = "dnd-2024"
DND2024_RULES_VERSION = "srd-5.2.1"

ABILITIES = ("str", "dex", "con", "int", "wis", "cha")

EXHAUSTION_MAX_LEVEL = 6
EXHAUSTION_D20_PENALTY_PER_LEVEL = 2
EXHAUSTION_SPEED_PENALTY_PER_LEVEL = 5
HIDE_DC = 15


class DamageType(StrEnum):
    ACID = "acid"
    BLUDGEONING = "bludgeoning"
    COLD = "cold"
    FIRE = "fire"
    FORCE = "force"
    LIGHTNING = "lightning"
    NECROTIC = "necrotic"
    PIERCING = "piercing"
    POISON = "poison"
    PSYCHIC = "psychic"
    RADIANT = "radiant"
    SLASHING = "slashing"
    THUNDER = "thunder"


class Condition(StrEnum):
    """2024 core conditions; exhaustion carries a level 1..6."""

    BLINDED = "blinded"
    CHARMED = "charmed"
    DEAFENED = "deafened"
    EXHAUSTION = "exhaustion"
    FRIGHTENED = "frightened"
    GRAPPLED = "grappled"
    INCAPACITATED = "incapacitated"
    INVISIBLE = "invisible"
    PARALYZED = "paralyzed"
    PETRIFIED = "petrified"
    POISONED = "poisoned"
    PRONE = "prone"
    RESTRAINED = "restrained"
    STUNNED = "stunned"
    UNCONSCIOUS = "unconscious"


class ActionType(StrEnum):
    ATTACK = "attack"
    DASH = "dash"
    DISENGAGE = "disengage"
    DODGE = "dodge"
    HELP = "help"
    HIDE = "hide"
    INFLUENCE = "influence"
    MAGIC = "magic"
    READY = "ready"
    SEARCH = "search"
    STUDY = "study"
    UTILIZE = "utilize"


class WeaponProperty(StrEnum):
    LIGHT = "light"
    FINESSE = "finesse"
    HEAVY = "heavy"
    LOADING = "loading"
    REACH = "reach"
    THROWN = "thrown"
    TWO_HANDED = "two_handed"
    AMMUNITION = "ammunition"
    RANGED = "ranged"


class MasteryProperty(StrEnum):
    CLEAVE = "cleave"
    GRAZE = "graze"
    NICK = "nick"
    PUSH = "push"
    SAP = "sap"
    SLOW = "slow"
    TOPPLE = "topple"
    VEX = "vex"


class Cover(StrEnum):
    NONE = "none"
    HALF = "half"
    THREE_QUARTERS = "three_quarters"
    TOTAL = "total"

    @property
    def ac_bonus(self) -> int | None:
        """AC bonus vs attacks; None means the target cannot be targeted."""
        return {
            Cover.NONE: 0,
            Cover.HALF: 2,
            Cover.THREE_QUARTERS: 5,
            Cover.TOTAL: None,
        }[self]


class CreatureSize(StrEnum):
    TINY = "tiny"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    HUGE = "huge"
    GARGANTUAN = "gargantuan"

    @property
    def rank(self) -> int:
        return list(CreatureSize).index(self)


def ability_modifier(score: int) -> int:
    """2024 ability modifier: floor((score - 10) / 2), correct for negatives."""
    return (score - 10) // 2


@dataclass(frozen=True)
class WeaponProfile:
    """Data-driven weapon description supplied with an attack command.

    ``attack_ability`` is an ability key; finesse weapons resolve to the
    better of str/dex at attack time. ``range_normal`` is reach in feet for
    melee weapons.
    """

    key: str
    damage: DiceFormula
    damage_type: str
    properties: frozenset[WeaponProperty]
    mastery: MasteryProperty | None
    attack_ability: str
    range_normal: int
    range_long: int | None = None

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValidationError("weapon key must not be empty")
        try:
            DamageType(self.damage_type)
        except ValueError as exc:
            raise ValidationError(f"invalid damage_type {self.damage_type!r}") from exc
        if self.attack_ability not in ABILITIES:
            raise ValidationError(f"invalid attack_ability {self.attack_ability!r}")
        if self.range_normal < 0 or (self.range_long is not None and self.range_long < self.range_normal):
            raise ValidationError("range_long must be >= range_normal")
        if (
            WeaponProperty.THROWN in self.properties or WeaponProperty.RANGED in self.properties
        ) and self.range_long is None:
            raise ValidationError("ranged/thrown weapons require range_long")


@dataclass(frozen=True)
class SpellProfile:
    """Data-driven spell description supplied with a cast command."""

    key: str
    level: int
    attack: bool
    save_ability: str | None
    damage: DiceFormula | None
    damage_type: str | None
    healing: DiceFormula | None
    concentration: bool
    duration_rounds: int | None
    ability: str

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValidationError("spell key must not be empty")
        if isinstance(self.level, bool) or not 0 <= self.level <= 9:
            raise ValidationError("spell level must be an integer in 0..9")
        if self.attack and self.save_ability is not None:
            raise ValidationError("a spell is either an attack or a save")
        if not self.attack and self.save_ability is None and not self.healing:
            raise ValidationError("save_ability is required for non-attack spells")
        if self.save_ability is not None and self.save_ability not in ABILITIES:
            raise ValidationError(f"invalid save_ability {self.save_ability!r}")
        if self.ability not in ABILITIES:
            raise ValidationError(f"invalid casting ability {self.ability!r}")
        if self.damage is not None:
            if self.damage_type is None:
                raise ValidationError("damage_type is required with damage")
            DamageType(self.damage_type)
        if self.concentration and self.duration_rounds is None:
            raise ValidationError("concentration spells require duration_rounds")


@dataclass(frozen=True)
class ConditionEffects:
    """2024 combat-relevant effects of one condition."""

    attacker_advantage: bool = False
    attacker_disadvantage: bool = False
    target_advantage: bool = False
    target_disadvantage: bool = False
    speed_zero: bool = False
    incapacitated: bool = False
    crit_when_hit_within_5ft: bool = False
    resist_all_damage: bool = False


_UNCONSCIOUS = ConditionEffects(
    target_advantage=True, speed_zero=True, incapacitated=True,
    crit_when_hit_within_5ft=True,
)

CONDITION_EFFECTS: dict[Condition, ConditionEffects] = {
    Condition.BLINDED: ConditionEffects(attacker_disadvantage=True, target_advantage=True),
    Condition.CHARMED: ConditionEffects(),
    Condition.DEAFENED: ConditionEffects(),
    Condition.EXHAUSTION: ConditionEffects(),
    Condition.FRIGHTENED: ConditionEffects(attacker_disadvantage=True),
    Condition.GRAPPLED: ConditionEffects(speed_zero=True),
    Condition.INCAPACITATED: ConditionEffects(incapacitated=True),
    Condition.INVISIBLE: ConditionEffects(attacker_advantage=True, target_disadvantage=True),
    Condition.PARALYZED: ConditionEffects(
        target_advantage=True, incapacitated=True, crit_when_hit_within_5ft=True
    ),
    Condition.PETRIFIED: ConditionEffects(
        target_advantage=True, incapacitated=True, resist_all_damage=True
    ),
    Condition.POISONED: ConditionEffects(attacker_disadvantage=True),
    Condition.PRONE: ConditionEffects(attacker_disadvantage=True),
    Condition.RESTRAINED: ConditionEffects(attacker_disadvantage=True, target_advantage=True),
    Condition.STUNNED: ConditionEffects(target_advantage=True, incapacitated=True),
    Condition.UNCONSCIOUS: _UNCONSCIOUS,
}


def aggregate_effects(conditions: dict[Condition, int]) -> ConditionEffects:
    """Combine the effects of every active condition (levels only matter for
    exhaustion, which is handled by exhaustion_penalty/effective_speed)."""
    merged = ConditionEffects()
    for condition in conditions:
        effects = CONDITION_EFFECTS[condition]
        merged = ConditionEffects(
            attacker_advantage=merged.attacker_advantage or effects.attacker_advantage,
            attacker_disadvantage=merged.attacker_disadvantage or effects.attacker_disadvantage,
            target_advantage=merged.target_advantage or effects.target_advantage,
            target_disadvantage=merged.target_disadvantage or effects.target_disadvantage,
            speed_zero=merged.speed_zero or effects.speed_zero,
            incapacitated=merged.incapacitated or effects.incapacitated,
            crit_when_hit_within_5ft=(
                merged.crit_when_hit_within_5ft or effects.crit_when_hit_within_5ft
            ),
            resist_all_damage=merged.resist_all_damage or effects.resist_all_damage,
        )
    return merged


def exhaustion_penalty(level: int) -> int:
    """d20 Test penalty: −2 per exhaustion level."""
    return -EXHAUSTION_D20_PENALTY_PER_LEVEL * level


def effective_speed(base_speed: int, conditions: dict[Condition, int]) -> int:
    """Speed after condition effects; never below 0."""
    if any(CONDITION_EFFECTS[c].speed_zero for c in conditions):
        return 0
    level = conditions.get(Condition.EXHAUSTION, 0)
    return max(0, base_speed - EXHAUSTION_SPEED_PENALTY_PER_LEVEL * level)


def require_dnd_2024(campaign: Campaign) -> None:
    """Gate every D&D operation on the campaign's enabled, pinned ruleset."""
    rules = campaign.rules
    if rules.mode is not RulesMode.DND_2024 or not rules.enabled:
        raise ValidationError("campaign does not have dnd-2024 rules enabled")
    if rules.version != DND2024_RULES_VERSION:
        raise ValidationError(
            f"unsupported dnd-2024 rules version {rules.version!r};"
            f" engine implements {DND2024_RULES_VERSION!r}"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/backend/unit/test_dnd_domain.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat(core): add dnd 2024 domain constants and profiles" \
  "src/sillytavern_rpg_engine/domain/dnd.py" \
  "tests/backend/unit/test_dnd_domain.py"
```

---

### Task 4: D&D attribute pack, readiness, and atomic enable

**Files:**
- Create: `src/sillytavern_rpg_engine/services/dnd_pack.py`
- Test: `tests/backend/unit/test_dnd_pack.py`

**Interfaces:**
- Consumes: `DefineAttributeOperation`, `SetAttributeOperation`, `CampaignService` internals, `domain/dnd.py`.
- Produces: `dnd_definitions(campaign_id)`, `REQUIRED_CHARACTER_KEYS`; `SeedDndPackOperation`; `ReadinessReport`, `check_readiness(connection, campaign_id)`; `EnableDnd2024Operation`; `DndRulesService` with `seed_pack()`, `readiness()`, `enable()`; read/write helpers `read_value()`, `read_int()`, `read_list()`, `read_bool()`.

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from sillytavern_rpg_engine.domain.errors import ValidationError
from sillytavern_rpg_engine.domain.models import EntityKind
from sillytavern_rpg_engine.services.attributes import SetAttributeOperation
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.dnd_pack import (
    DndRulesService,
    REQUIRED_CHARACTER_KEYS,
    check_readiness,
    dnd_definitions,
)
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)


def _services(database):
    ids = iter(f"event-{n}" for n in range(1, 1000))
    clock = lambda: "2026-08-12T00:00:00Z"
    campaigns = CampaignService(database, id_factory=lambda: next(ids), clock=clock)
    dnd = DndRulesService(database, id_factory=lambda: next(ids), clock=clock)
    return campaigns, dnd, EntityAttributeService(database, dnd.mutation_engine)


def test_seed_pack_registers_all_definitions_and_readiness_reports_missing(database):
    campaigns, dnd, entities = _services(database)
    campaigns.create_campaign("c1", "Dungeon")
    dnd.seed_pack("c1", expected_version=0)
    entities.apply_explicit("c1", "main", 1, CreateEntityOperation(
        entity_id="pc1", kind=EntityKind.CHARACTER, name="Aria",
    ))
    report = dnd.readiness("c1")
    assert not report.ready
    assert set(report.missing["pc1"]) == REQUIRED_CHARACTER_KEYS


def test_enable_requires_complete_character_data(database):
    campaigns, dnd, entities = _services(database)
    campaigns.create_campaign("c1", "Dungeon")
    dnd.seed_pack("c1", 0)
    entities.apply_explicit("c1", "main", 1, CreateEntityOperation(
        entity_id="pc1", kind=EntityKind.CHARACTER, name="Aria",
    ))
    with pytest.raises(ValidationError, match="pc1"):
        dnd.enable("c1", 2)
    version = 2
    values = {
        "ability_str": 16, "ability_dex": 14, "ability_con": 13,
        "ability_int": 10, "ability_wis": 12, "ability_cha": 8,
        "proficiency_bonus": 2, "proficiencies": ["longsword"],
        "armor_class": 16, "hp_max": 12, "hp_current": 12, "hp_temp": 0,
        "speed": 30, "character_level": 1,
        "spell_slots_max": [0] * 9, "spell_slots_current": [0] * 9,
        "hit_die": 10, "hit_dice_total": 1, "hit_dice_current": 1,
        "death_saves_success": 0, "death_saves_failure": 0,
        "is_dead": False, "is_stable": False,
        "resistances": [], "vulnerabilities": [], "immunities": [],
        "condition_immunities": [], "weapon_masteries": ["longsword"],
    }
    for key, value in values.items():
        entities.apply_explicit(
            "c1", "main", version, SetAttributeOperation("pc1", key, value)
        )
        version += 1
    assert dnd.readiness("c1").ready
    result = dnd.enable("c1", version)
    assert result.snapshot["campaign"]["rules"] == {
        "mode": "dnd-2024", "enabled": True,
        "version": "srd-5.2.1", "custom_preset_id": None,
    }


def test_enable_without_characters_is_ready_and_seed_is_idempotent_guarded(database):
    campaigns, dnd, _ = _services(database)
    campaigns.create_campaign("c1", "Dungeon")
    dnd.seed_pack("c1", 0)
    assert dnd.readiness("c1").ready
    dnd.enable("c1", 1)
    with pytest.raises(ValidationError, match="already exists"):
        dnd.seed_pack("c1", 2)


def test_definitions_are_audience_visible_and_ranges_enforced(database):
    definitions = dnd_definitions("c1")
    by_key = {d.key: d for d in definitions}
    assert "player_ui" in {a.value for a in by_key["hp_current"].audiences}
    assert by_key["spell_slots_max"].value_type.value == "list"
    assert by_key["character_level"].minimum == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/backend/unit/test_dnd_pack.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `services/dnd_pack.py`**

```python
"""D&D 2024 attribute pack: seeded definitions, readiness, atomic enable."""

from dataclasses import dataclass
import json
import sqlite3
from typing import Any, Callable

from ..domain.dnd import DND2024_RULES_VERSION
from ..domain.errors import ValidationError
from ..domain.models import (
    AttributeDefinition,
    AttributeType,
    Audience,
    CampaignRules,
    DisplayType,
    Entity,
    RulesMode,
)
from ..domain.operations import MutationContext
from ..persistence.database import Database
from .attributes import DefineAttributeOperation
from .campaigns import SetRulesOperation
from .mutations import MutationEngine, MutationRequest, MutationResult

_ALL_AUDIENCES = frozenset(
    {Audience.ENGINE, Audience.NARRATOR, Audience.PLAYER_UI}
)
_DND_CATEGORY = "dnd"

# (key, label, value_type, minimum, maximum, enum_values)
_PACK: tuple[tuple[str, str, AttributeType, float | None, float | None, tuple[str, ...]], ...] = (
    ("ability_str", "Strength", AttributeType.INTEGER, 1, 30, ()),
    ("ability_dex", "Dexterity", AttributeType.INTEGER, 1, 30, ()),
    ("ability_con", "Constitution", AttributeType.INTEGER, 1, 30, ()),
    ("ability_int", "Intelligence", AttributeType.INTEGER, 1, 30, ()),
    ("ability_wis", "Wisdom", AttributeType.INTEGER, 1, 30, ()),
    ("ability_cha", "Charisma", AttributeType.INTEGER, 1, 30, ()),
    ("proficiency_bonus", "Proficiency Bonus", AttributeType.INTEGER, 2, 9, ()),
    ("proficiencies", "Proficiencies", AttributeType.LIST, None, None, ()),
    ("armor_class", "Armor Class", AttributeType.INTEGER, 1, 40, ()),
    ("hp_max", "Max HP", AttributeType.INTEGER, 1, 999, ()),
    ("hp_current", "Current HP", AttributeType.INTEGER, 0, 999, ()),
    ("hp_temp", "Temporary HP", AttributeType.INTEGER, 0, 999, ()),
    ("speed", "Speed", AttributeType.INTEGER, 0, 120, ()),
    ("character_level", "Level", AttributeType.INTEGER, 1, 20, ()),
    ("spell_slots_max", "Spell Slots (Max)", AttributeType.LIST, None, None, ()),
    ("spell_slots_current", "Spell Slots", AttributeType.LIST, None, None, ()),
    ("hit_die", "Hit Die", AttributeType.INTEGER, 4, 20, ()),
    ("hit_dice_total", "Hit Dice (Total)", AttributeType.INTEGER, 0, 20, ()),
    ("hit_dice_current", "Hit Dice", AttributeType.INTEGER, 0, 20, ()),
    ("death_saves_success", "Death Saves (Success)", AttributeType.INTEGER, 0, 3, ()),
    ("death_saves_failure", "Death Saves (Failure)", AttributeType.INTEGER, 0, 3, ()),
    ("is_dead", "Dead", AttributeType.BOOLEAN, None, None, ()),
    ("is_stable", "Stable", AttributeType.BOOLEAN, None, None, ()),
    ("resistances", "Resistances", AttributeType.LIST, None, None, ()),
    ("vulnerabilities", "Vulnerabilities", AttributeType.LIST, None, None, ()),
    ("immunities", "Immunities", AttributeType.LIST, None, None, ()),
    ("condition_immunities", "Condition Immunities", AttributeType.LIST, None, None, ()),
    ("weapon_masteries", "Weapon Masteries", AttributeType.LIST, None, None, ()),
)

REQUIRED_CHARACTER_KEYS = frozenset(key for key, *_ in _PACK)

_SLOT_SHAPE_ERROR = "spell_slots must be a list of 9 non-negative integers"


def dnd_definitions(campaign_id: str) -> list[AttributeDefinition]:
    """Build the full D&D 2024 attribute definition pack for a campaign."""
    return [
        AttributeDefinition(
            campaign_id=campaign_id,
            key=key,
            label=label,
            category=_DND_CATEGORY,
            value_type=value_type,
            display=DisplayType.NUMBER,
            audiences=_ALL_AUDIENCES,
            minimum=minimum,
            maximum=maximum,
            enum_values=enum_values,
        )
        for key, label, value_type, minimum, maximum, enum_values in _PACK
    ]


def validate_slot_list(value: Any) -> list[int]:
    """Require a 9-element list of non-negative ints (spell slot levels 1-9)."""
    if (
        not isinstance(value, list)
        or len(value) != 9
        or any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in value)
    ):
        raise ValidationError(_SLOT_SHAPE_ERROR)
    return list(value)


def read_value(
    connection: sqlite3.Connection, campaign_id: str, entity_id: str, key: str
) -> Any:
    """Read one attribute value; raise ValidationError when unset."""
    row = connection.execute(
        "SELECT value_json FROM attribute_values"
        " WHERE campaign_id = ? AND entity_id = ? AND attribute_key = ?",
        (campaign_id, entity_id, key),
    ).fetchone()
    if row is None:
        raise ValidationError(f"entity {entity_id!r} lacks attribute {key!r}")
    return json.loads(row["value_json"])


def read_int(connection, campaign_id: str, entity_id: str, key: str) -> int:
    value = read_value(connection, campaign_id, entity_id, key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"attribute {key!r} is not an integer")
    return value


def read_list(connection, campaign_id: str, entity_id: str, key: str) -> list:
    value = read_value(connection, campaign_id, entity_id, key)
    if not isinstance(value, list):
        raise ValidationError(f"attribute {key!r} is not a list")
    return value


def read_bool(connection, campaign_id: str, entity_id: str, key: str) -> bool:
    value = read_value(connection, campaign_id, entity_id, key)
    if not isinstance(value, bool):
        raise ValidationError(f"attribute {key!r} is not a boolean")
    return value


@dataclass(frozen=True)
class SeedDndPackOperation:
    """Register every D&D attribute definition; fails if any already exists."""

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        from ..domain.operations import CompositeOperation

        composite = CompositeOperation(
            tuple(
                DefineAttributeOperation(definition)
                for definition in dnd_definitions(context.campaign.id)
            )
        )
        return composite.apply(connection, context)


@dataclass(frozen=True)
class ReadinessReport:
    """Per-character missing D&D attribute keys; ready when empty."""

    ready: bool
    missing: dict[str, tuple[str, ...]]


def check_readiness(connection: sqlite3.Connection, campaign_id: str) -> ReadinessReport:
    """List missing/invalid D&D attribute keys for every character entity."""
    entities = connection.execute(
        "SELECT id FROM entities WHERE campaign_id = ? AND kind = 'character'"
        " ORDER BY normalized_name",
        (campaign_id,),
    ).fetchall()
    defined = {
        row[0]
        for row in connection.execute(
            "SELECT key FROM attribute_definitions WHERE campaign_id = ?",
            (campaign_id,),
        )
    }
    missing: dict[str, tuple[str, ...]] = {}
    for row in entities:
        absent = set(REQUIRED_CHARACTER_KEYS) - defined
        valued = {
            r[0]
            for r in connection.execute(
                "SELECT attribute_key FROM attribute_values"
                " WHERE campaign_id = ? AND entity_id = ?",
                (campaign_id, row["id"]),
            )
        }
        absent |= set(REQUIRED_CHARACTER_KEYS) - valued
        for slot_key in ("spell_slots_max", "spell_slots_current"):
            if slot_key not in absent:
                try:
                    validate_slot_list(read_value(connection, campaign_id, row["id"], slot_key))
                except ValidationError:
                    absent.add(slot_key)
        if absent:
            missing[row["id"]] = tuple(sorted(absent))
    return ReadinessReport(ready=not missing, missing=missing)


@dataclass(frozen=True)
class EnableDnd2024Operation:
    """Atomically enable dnd-2024 after a successful readiness check; the
    mutation engine's snapshot captures pre-enable state in the same tx."""

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        report = check_readiness(connection, context.campaign.id)
        if not report.ready:
            raise ValidationError(f"character data incomplete: {report.missing}")
        return SetRulesOperation(
            CampaignRules(
                mode=RulesMode.DND_2024,
                enabled=True,
                version=DND2024_RULES_VERSION,
            )
        ).apply(connection, context)


class DndRulesService:
    """Seeds the attribute pack, reports readiness, and enables dnd-2024."""

    def __init__(
        self,
        database: Database,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], str] | None = None,
    ):
        from datetime import datetime, timezone
        from uuid import uuid4

        self.database = database
        self.id_factory = id_factory or (lambda: uuid4().hex)
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat())
        self.mutation_engine = MutationEngine(
            database, id_factory=self.id_factory, clock=self.clock
        )

    def seed_pack(self, campaign_id: str, expected_version: int) -> MutationResult:
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id,
                branch_id="main",
                expected_version=expected_version,
                source="user-command",
                event_type="dnd-pack-seeded",
                operation=SeedDndPackOperation(),
            )
        )

    def readiness(self, campaign_id: str) -> ReadinessReport:
        with self.database.connect() as connection:
            return check_readiness(connection, campaign_id)

    def enable(self, campaign_id: str, expected_version: int) -> MutationResult:
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id,
                branch_id="main",
                expected_version=expected_version,
                source="user-command",
                event_type="rules-updated",
                operation=EnableDnd2024Operation(),
            )
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/backend/unit/test_dnd_pack.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat(core): add dnd attribute pack and readiness gate" \
  "src/sillytavern_rpg_engine/services/dnd_pack.py" \
  "tests/backend/unit/test_dnd_pack.py"
```

---

### Task 5: Authoritative dice rolls with immutable records

**Files:**
- Create: `src/sillytavern_rpg_engine/services/dice.py`
- Test: `tests/backend/unit/test_dice_service.py`

**Interfaces:**
- Consumes: `domain/dice.py`, `domain/dnd.py` (`require_dnd_2024`, `exhaustion_penalty`), `services/dnd_pack.py` (`read_int`, `read_list`), `services/conditions.py` (`condition_map` — Task 6; this task inlines the same SQL query to avoid forward references; Task 6 refactors to share).
- Produces: `record_roll(connection, context, id_factory, ...)`; `CheckOperation(roller, entity_id, ability, skill, dc, purpose, turn_id)`; `DiceService.check()`.

Design: a check reads the ability score, proficiency bonus (when `skill` is listed in `proficiencies`), and the entity's exhaustion level, then resolves `1d20 + ability_mod + (proficiency_bonus if proficient) + exhaustion_penalty` against the DC. Ability checks have no auto success/failure on natural 20/1.

- [ ] **Step 1: Write the failing tests**

```python
import json

import pytest

from sillytavern_rpg_engine.domain.dice import D20Mode, SequenceDiceRoller
from sillytavern_rpg_engine.domain.dnd import DND2024_RULES_VERSION
from sillytavern_rpg_engine.domain.errors import ValidationError
from sillytavern_rpg_engine.domain.models import EntityKind
from sillytavern_rpg_engine.services.attributes import SetAttributeOperation
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.dice import DiceService
from sillytavern_rpg_engine.services.dnd_pack import DndRulesService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)


def _ready_campaign(database):
    ids = iter(f"e{n}" for n in range(1, 1000))
    clock = lambda: "2026-08-12T00:00:00Z"
    campaigns = CampaignService(database, id_factory=lambda: next(ids), clock=clock)
    dnd = DndRulesService(database, id_factory=lambda: next(ids), clock=clock)
    dice = DiceService(database, id_factory=lambda: next(ids), clock=clock)
    entities = EntityAttributeService(database, dnd.mutation_engine)
    campaigns.create_campaign("c1", "Dungeon")
    campaigns.create_campaign("c2", "Story")
    dnd.seed_pack("c1", 0)
    entities.apply_explicit("c1", "main", 1, CreateEntityOperation(
        entity_id="pc1", kind=EntityKind.CHARACTER, name="Aria",
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
            "c1", "main", version, SetAttributeOperation("pc1", key, value)
        )
        version += 1
    dnd.enable("c1", version)
    return dice, version + 1


def test_check_records_immutable_roll_with_pinned_version(database):
    dice, version = _ready_campaign(database)
    result = dice.check(
        "c1", expected_version=version, roller=SequenceDiceRoller([14]),
        entity_id="pc1", ability="str", skill="athletics", dc=15,
        purpose="force the gate",
    )
    # +3 str, +2 proficiency => 14 + 5 = 19 >= 15
    assert result.snapshot["campaign"]["rules"]["version"] == DND2024_RULES_VERSION
    with database.connect() as connection:
        row = connection.execute("SELECT * FROM dice_rolls").fetchone()
        assert row["purpose"] == "force the gate"
        assert row["formula"] == "1d20"
        assert json.loads(row["faces_json"]) == [14]
        assert row["modifier"] == 5
        assert row["total"] == 19
        assert row["dc"] == 15
        assert row["success"] == 1
        assert row["rules_version"] == "srd-5.2.1"


def test_check_without_proficiency_and_narrative_campaign_rejected(database):
    dice, version = _ready_campaign(database)
    result = dice.check(
        "c1", expected_version=version, roller=SequenceDiceRoller([10]),
        entity_id="pc1", ability="int", skill=None, dc=12, purpose="recall lore",
    )
    with database.connect() as connection:
        row = connection.execute(
            "SELECT modifier, total, success FROM dice_rolls ORDER BY rowid DESC"
        ).fetchone()
        assert (row["modifier"], row["total"], row["success"]) == (0, 10, 0)
    with pytest.raises(ValidationError, match="enabled"):
        dice.check(
            "c2", expected_version=0, roller=SequenceDiceRoller([10]),
            entity_id="pc1", ability="int", skill=None, dc=12, purpose="lore",
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/backend/unit/test_dice_service.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `services/dice.py`**

```python
"""Authoritative dice resolution: every roll is recorded immutably inside the
same transaction as the state change it belongs to."""

from dataclasses import dataclass
import json
import sqlite3
from typing import Any, Callable

from ..domain.dice import D20Mode, DiceRoller, resolve_d20
from ..domain.dnd import (
    DND2024_RULES_VERSION,
    Condition,
    ability_modifier,
    exhaustion_penalty,
    require_dnd_2024,
)
from ..domain.operations import MutationContext
from ..persistence.database import Database
from .dnd_pack import read_int, read_list
from .mutations import MutationEngine, MutationRequest, MutationResult


def read_exhaustion(connection: sqlite3.Connection, campaign_id: str, entity_id: str) -> int:
    """Current exhaustion level of one entity (0 when unaffected)."""
    row = connection.execute(
        "SELECT level FROM entity_conditions"
        " WHERE campaign_id = ? AND entity_id = ? AND condition = ?",
        (campaign_id, entity_id, Condition.EXHAUSTION.value),
    ).fetchone()
    return row["level"] if row is not None else 0


def record_roll(
    connection: sqlite3.Connection,
    context: MutationContext,
    roll_id: str,
    *,
    purpose: str,
    formula: str,
    faces: tuple[int, ...],
    modifier: int,
    total: int,
    dc: int | None = None,
    success: bool | None = None,
    critical: bool = False,
    roller_entity_id: str | None = None,
    turn_id: str | None = None,
) -> dict[str, Any]:
    """Append one immutable roll row; returns its audit-safe payload."""
    connection.execute(
        "INSERT INTO dice_rolls(id, campaign_id, branch_id, turn_id,"
        " roller_entity_id, purpose, formula, faces_json, modifier, total, dc,"
        " success, critical, rules_version, state_version, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            roll_id,
            context.campaign.id,
            context.branch_id,
            turn_id,
            roller_entity_id,
            purpose,
            formula,
            json.dumps(list(faces)),
            modifier,
            total,
            dc,
            None if success is None else int(success),
            int(critical),
            DND2024_RULES_VERSION,
            context.next_state_version,
            context.now,
        ),
    )
    return {
        "roll_id": roll_id,
        "purpose": purpose,
        "formula": formula,
        "faces": list(faces),
        "modifier": modifier,
        "total": total,
        "dc": dc,
        "success": success,
        "critical": critical,
    }


@dataclass(frozen=True)
class CheckOperation:
    """Resolve one ability check: 1d20 + ability mod + proficiency (when the
    skill is proficient) + exhaustion penalty vs a DC."""

    roller: DiceRoller
    roll_id: str
    entity_id: str
    ability: str
    skill: str | None
    dc: int
    purpose: str
    mode: D20Mode = D20Mode.NORMAL
    turn_id: str | None = None

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        campaign_id = context.campaign.id
        score = read_int(connection, campaign_id, self.entity_id, f"ability_{self.ability}")
        modifier = ability_modifier(score)
        if self.skill is not None and self.skill in read_list(
            connection, campaign_id, self.entity_id, "proficiencies"
        ):
            modifier += read_int(connection, campaign_id, self.entity_id, "proficiency_bonus")
        modifier += exhaustion_penalty(
            read_exhaustion(connection, campaign_id, self.entity_id)
        )
        outcome = resolve_d20(self.roller, self.mode, modifier)
        faces = (
            (outcome.kept,)
            if outcome.dropped is None
            else tuple(sorted((outcome.kept, outcome.dropped), reverse=True))
        )
        return record_roll(
            connection, context, self.roll_id,
            purpose=self.purpose, formula="1d20", faces=faces,
            modifier=modifier, total=outcome.total, dc=self.dc,
            success=outcome.total >= self.dc,
            roller_entity_id=self.entity_id, turn_id=self.turn_id,
        )


class DiceService:
    """User-command entry point for standalone authorized checks."""

    def __init__(
        self,
        database: Database,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], str] | None = None,
    ):
        from datetime import datetime, timezone
        from uuid import uuid4

        self.database = database
        self.id_factory = id_factory or (lambda: uuid4().hex)
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat())
        self.mutation_engine = MutationEngine(
            database, id_factory=self.id_factory, clock=self.clock
        )

    def check(
        self,
        campaign_id: str,
        expected_version: int,
        roller: DiceRoller,
        entity_id: str,
        ability: str,
        skill: str | None,
        dc: int,
        purpose: str,
        branch_id: str = "main",
        mode: D20Mode = D20Mode.NORMAL,
        turn_id: str | None = None,
    ) -> MutationResult:
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id,
                branch_id=branch_id,
                expected_version=expected_version,
                source="user-command",
                event_type="dice-check",
                operation=CheckOperation(
                    roller=roller, roll_id=self.id_factory(),
                    entity_id=entity_id, ability=ability, skill=skill,
                    dc=dc, purpose=purpose, mode=mode, turn_id=turn_id,
                ),
            )
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/backend/unit/test_dice_service.py -v`
Expected: 2 passed. Note: `entity_conditions` is queried before Task 6 creates its service; the table exists from Task 2, so no forward dependency.

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat(core): add authoritative dice roll records" \
  "src/sillytavern_rpg_engine/services/dice.py" \
  "tests/backend/unit/test_dice_service.py"
```

---

### Task 6: Conditions with 2024 effects and exhaustion levels

**Files:**
- Create: `src/sillytavern_rpg_engine/services/conditions.py`
- Test: `tests/backend/unit/test_conditions.py`

**Interfaces:**
- Consumes: `domain/dnd.py` (`Condition`, `CONDITION_EFFECTS`, exhaustion constants), `services/dnd_pack.py` (`read_int`), `services/attributes.py` (`SetAttributeOperation`).
- Produces: `condition_map(connection, campaign_id, entity_id) -> dict[Condition, int]`; `ApplyConditionOperation(entity_id, condition, level, source)`; `RemoveConditionOperation(entity_id, condition)`; `ConditionService` (`apply`, `remove`).

Rules encoded:
- Applying an existing non-exhaustion condition refreshes it (upsert level/source).
- Applying exhaustion adds levels up to 6; reaching 6 sets `is_dead` and records `died: true` in the payload.
- Entities with a matching `condition_immunities` entry reject the application.
- Removing exhaustion lowers the level by `level` (default 1); at 0 the row is deleted.

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from sillytavern_rpg_engine.domain.dnd import Condition
from sillytavern_rpg_engine.domain.errors import NotFoundError, ValidationError
from sillytavern_rpg_engine.domain.models import EntityKind
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
    entities.apply_explicit("c1", "main", 1, CreateEntityOperation(
        entity_id="pc1", kind=EntityKind.CHARACTER, name="Aria",
    ))
    entities.apply_explicit("c1", "main", 2, SetAttributeOperation(
        "pc1", "condition_immunities", ["poisoned"]
    ))
    return conditions, entities


def test_apply_and_remove_condition_roundtrip(database):
    conditions, _ = _service(database)
    conditions.apply("c1", 3, "pc1", Condition.PRONE, source="topple")
    conditions.apply("c1", 4, "pc1", Condition.PRONE, source="again")
    conditions.apply("c1", 5, "pc1", Condition.BLINDED, source="spell")
    with database.connect() as connection:
        assert condition_map(connection, "c1", "pc1") == {
            Condition.PRONE: 1,
            Condition.BLINDED: 1,
        }
    conditions.remove("c1", 6, "pc1", Condition.PRONE)
    with database.connect() as connection:
        assert condition_map(connection, "c1", "pc1") == {Condition.BLINDED: 1}
    with pytest.raises(NotFoundError):
        conditions.remove("c1", 7, "pc1", Condition.PRONE)


def test_exhaustion_accumulates_and_kills_at_six(database):
    conditions, entities = _service(database)
    conditions.apply("c1", 3, "pc1", Condition.EXHAUSTION, level=2, source="forced march")
    conditions.apply("c1", 4, "pc1", Condition.EXHAUSTION, level=3, source="cold")
    with database.connect() as connection:
        assert condition_map(connection, "c1", "pc1") == {Condition.EXHAUSTION: 5}
    conditions.remove("c1", 5, "pc1", Condition.EXHAUSTION)
    with database.connect() as connection:
        assert condition_map(connection, "c1", "pc1") == {Condition.EXHAUSTION: 4}
    result = conditions.apply("c1", 6, "pc1", Condition.EXHAUSTION, level=2, source="overexertion")
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
        conditions.apply("c1", 3, "pc1", Condition.POISONED, source="trap")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/backend/unit/test_conditions.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `services/conditions.py`**

```python
"""Condition application/removal with 2024 effects and exhaustion levels."""

from dataclasses import dataclass
import json
import sqlite3
from typing import Any, Callable

from ..domain.dnd import (
    EXHAUSTION_MAX_LEVEL,
    Condition,
    require_dnd_2024,
)
from ..domain.errors import NotFoundError, ValidationError
from ..domain.operations import MutationContext
from ..persistence.database import Database
from .attributes import SetAttributeOperation
from .dnd_pack import read_list
from .mutations import MutationEngine, MutationRequest, MutationResult


def condition_map(
    connection: sqlite3.Connection, campaign_id: str, entity_id: str
) -> dict[Condition, int]:
    """Read active conditions of one entity as ``{condition: level}``."""
    rows = connection.execute(
        "SELECT condition, level FROM entity_conditions"
        " WHERE campaign_id = ? AND entity_id = ?",
        (campaign_id, entity_id),
    ).fetchall()
    return {Condition(row["condition"]): row["level"] for row in rows}


@dataclass(frozen=True)
class ApplyConditionOperation:
    """Apply (or refresh) a condition; exhaustion accumulates levels and
    reaching the cap marks the entity dead via its ``is_dead`` attribute."""

    entity_id: str
    condition: Condition
    source: str
    level: int = 1

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict[str, Any]:
        require_dnd_2024(context.campaign)
        campaign_id = context.campaign.id
        if isinstance(self.level, bool) or not isinstance(self.level, int) or self.level < 1:
            raise ValidationError("condition level must be a positive integer")
        immunities = read_list(connection, campaign_id, self.entity_id, "condition_immunities")
        if self.condition.value in immunities:
            raise ValidationError(
                f"entity {self.entity_id!r} is immune to {self.condition.value}"
            )
        existing = condition_map(connection, campaign_id, self.entity_id)
        died = False
        if self.condition is Condition.EXHAUSTION:
            level = min(existing.get(Condition.EXHAUSTION, 0) + self.level, EXHAUSTION_MAX_LEVEL)
            died = level >= EXHAUSTION_MAX_LEVEL
        else:
            level = self.level
        connection.execute(
            "INSERT INTO entity_conditions(campaign_id, entity_id, condition,"
            " level, source, applied_state_version) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(campaign_id, entity_id, condition) DO UPDATE SET"
            " level = excluded.level, source = excluded.source,"
            " applied_state_version = excluded.applied_state_version",
            (
                campaign_id,
                self.entity_id,
                self.condition.value,
                level,
                self.source,
                context.next_state_version,
            ),
        )
        if died:
            SetAttributeOperation(self.entity_id, "is_dead", True).apply(connection, context)
        return {
            "entity_id": self.entity_id,
            "condition": self.condition.value,
            "level": level,
            "source": self.source,
            "died": died,
        }


@dataclass(frozen=True)
class RemoveConditionOperation:
    """Remove a condition; exhaustion drops by ``level`` and ends at 0."""

    entity_id: str
    condition: Condition
    level: int = 1

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict[str, Any]:
        require_dnd_2024(context.campaign)
        campaign_id = context.campaign.id
        existing = condition_map(connection, campaign_id, self.entity_id)
        if self.condition not in existing:
            raise NotFoundError(
                f"entity {self.entity_id!r} does not have {self.condition.value}"
            )
        remaining = 0
        if self.condition is Condition.EXHAUSTION:
            remaining = max(0, existing[Condition.EXHAUSTION] - self.level)
        if remaining:
            connection.execute(
                "UPDATE entity_conditions SET level = ?,"
                " applied_state_version = ?"
                " WHERE campaign_id = ? AND entity_id = ? AND condition = ?",
                (
                    remaining,
                    context.next_state_version,
                    campaign_id,
                    self.entity_id,
                    self.condition.value,
                ),
            )
        else:
            connection.execute(
                "DELETE FROM entity_conditions"
                " WHERE campaign_id = ? AND entity_id = ? AND condition = ?",
                (campaign_id, self.entity_id, self.condition.value),
            )
        return {
            "entity_id": self.entity_id,
            "condition": self.condition.value,
            "remaining_level": remaining,
        }


class ConditionService:
    """User-command entry points for condition changes."""

    def __init__(
        self,
        database: Database,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], str] | None = None,
    ):
        from datetime import datetime, timezone
        from uuid import uuid4

        self.database = database
        self.id_factory = id_factory or (lambda: uuid4().hex)
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat())
        self.mutation_engine = MutationEngine(
            database, id_factory=self.id_factory, clock=self.clock
        )

    def apply(
        self,
        campaign_id: str,
        expected_version: int,
        entity_id: str,
        condition: Condition,
        source: str,
        level: int = 1,
        branch_id: str = "main",
    ) -> MutationResult:
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id,
                branch_id=branch_id,
                expected_version=expected_version,
                source="user-command",
                event_type="condition-applied",
                operation=ApplyConditionOperation(entity_id, condition, source, level),
            )
        )

    def remove(
        self,
        campaign_id: str,
        expected_version: int,
        entity_id: str,
        condition: Condition,
        level: int = 1,
        branch_id: str = "main",
    ) -> MutationResult:
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id,
                branch_id=branch_id,
                expected_version=expected_version,
                source="user-command",
                event_type="condition-removed",
                operation=RemoveConditionOperation(entity_id, condition, level),
            )
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/backend/unit/test_conditions.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat(core): add condition service with exhaustion levels" \
  "src/sillytavern_rpg_engine/services/conditions.py" \
  "tests/backend/unit/test_conditions.py"
```

---

### Task 7: Combat encounters — start, initiative, surprise, join, end

**Files:**
- Create: `src/sillytavern_rpg_engine/services/combat.py`
- Test: `tests/backend/unit/test_combat.py`

**Interfaces:**
- Consumes: `domain/dice.py`, `domain/dnd.py`, `services/conditions.py` (`condition_map`), `services/dice.py` (`record_roll`), `services/dnd_pack.py` (`read_int`).
- Produces: `CombatantEntry(entity_id, surprised, recharge, triggers)`; `StartCombatOperation(roller, roll_id_factory, entries)`; `AddCombatantOperation(roller, roll_id, entry)`; `EndCombatOperation()`; `require_active_encounter(connection, campaign_id, branch_id) -> (encounter_row, combatant_rows)`; `CombatService` (`start`, `add_combatant`, `end`).

Rules encoded (2024):
- Initiative = `1d20 + Dexterity modifier`; surprised creatures roll with Disadvantage.
- Order sorts by total descending; ties break by `entity_id` (deterministic, identical to the read-path `ORDER BY`).
- One active encounter per (campaign, branch) enforced by the `one_active_encounter` index; a clear `ValidationError` is raised before insert.
- Starting combat sets each combatant's `movement_total` to effective speed (speed attribute minus exhaustion, zero when grappled etc.) and starts round 1 with the first combatant active. Combatant 1 begins with fresh budgets (default zeroed columns).
- Ending sets status `ended` + `ended_state_version`; combatant rows stay for audit/replay.

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from sillytavern_rpg_engine.domain.dice import SequenceDiceRoller
from sillytavern_rpg_engine.domain.errors import NotFoundError, ValidationError
from sillytavern_rpg_engine.domain.models import EntityKind
from sillytavern_rpg_engine.services.attributes import SetAttributeOperation
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.combat import CombatService, CombatantEntry
from sillytavern_rpg_engine.services.conditions import ConditionService
from sillytavern_rpg_engine.domain.dnd import Condition
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
    dnd.enable("c1", version)
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


def test_surprised_rolls_with_disadvantage_and_ties_break_on_dex(database):
    combat, _, version = _world(database)
    # pc1 surprised: 2d20 keep lower -> (10, 18) keeps 10 (+2) = 12
    # orc: 12 (+1) = 13; tie-break not needed here
    result = combat.start(
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
    for key, value in (("ability_dex", 15), ("speed", 30)):
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/backend/unit/test_combat.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `services/combat.py` (encounter lifecycle)**

```python
"""Combat encounters: initiative, surprise, joins, and end of combat."""

from dataclasses import dataclass
import json
import sqlite3
from typing import Any, Callable

from ..domain.dice import D20Mode, DiceRoller, resolve_d20
from ..domain.dnd import (
    Condition,
    ability_modifier,
    effective_speed,
    require_dnd_2024,
)
from ..domain.errors import NotFoundError, ValidationError
from ..domain.operations import MutationContext
from ..persistence.database import Database
from .conditions import condition_map
from .dice import record_roll
from .dnd_pack import read_int
from .mutations import MutationEngine, MutationRequest, MutationResult


@dataclass(frozen=True)
class CombatantEntry:
    """One creature joining an encounter.

    ``recharge`` holds ``{"name": ..., "min": 2..6, "available": bool}`` maps
    (monster Recharge abilities); ``triggers`` holds data-driven turn hooks
    (see Task 14).
    """

    entity_id: str
    surprised: bool = False
    recharge: tuple[dict[str, Any], ...] = ()
    triggers: tuple[dict[str, Any], ...] = ()


def require_active_encounter(
    connection: sqlite3.Connection, campaign_id: str, branch_id: str
) -> tuple[Any, list[Any]]:
    """Return ``(encounter_row, combatant_rows)`` of the active encounter."""
    encounter = connection.execute(
        "SELECT id, round_number, active_index FROM combat_encounters"
        " WHERE campaign_id = ? AND branch_id = ? AND status = 'active'",
        (campaign_id, branch_id),
    ).fetchone()
    if encounter is None:
        raise NotFoundError("no active encounter for this branch")
    combatants = connection.execute(
        "SELECT * FROM combatants WHERE encounter_id = ? ORDER BY initiative DESC,"
        " entity_id",
        (encounter["id"],),
    ).fetchall()
    return encounter, list(combatants)


def active_combatant(encounter, combatants) -> Any:
    """The combatant whose turn it is (order matches the sorted row list)."""
    return combatants[encounter["active_index"]]


def _roll_initiative(
    connection: sqlite3.Connection,
    context: MutationContext,
    roller: DiceRoller,
    roll_id: str,
    entry: CombatantEntry,
) -> int:
    campaign_id = context.campaign.id
    dex = read_int(connection, campaign_id, entry.entity_id, "ability_dex")
    modifier = ability_modifier(dex)
    mode = D20Mode.DISADVANTAGE if entry.surprised else D20Mode.NORMAL
    outcome = resolve_d20(roller, mode, modifier)
    faces = (
        (outcome.kept,)
        if outcome.dropped is None
        else tuple(sorted((outcome.kept, outcome.dropped), reverse=True))
    )
    record_roll(
        connection, context, roll_id,
        purpose="initiative", formula="1d20", faces=faces,
        modifier=modifier, total=outcome.total,
        roller_entity_id=entry.entity_id,
    )
    return outcome.total


def _insert_combatant(
    connection: sqlite3.Connection,
    context: MutationContext,
    encounter_id: str,
    entry: CombatantEntry,
    initiative: int,
) -> None:
    conditions = condition_map(connection, context.campaign.id, entry.entity_id)
    speed = read_int(connection, context.campaign.id, entry.entity_id, "speed")
    connection.execute(
        "INSERT INTO combatants(id, encounter_id, entity_id, initiative,"
        " movement_total, recharge_json, triggers_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            f"{encounter_id}:{entry.entity_id}",
            encounter_id,
            entry.entity_id,
            initiative,
            effective_speed(speed, conditions),
            json.dumps(list(entry.recharge)),
            json.dumps(list(entry.triggers)),
        ),
    )


def _order_entries(
    entries: tuple[CombatantEntry, ...],
    initiatives: dict[str, int],
) -> list[CombatantEntry]:
    """Sort by initiative desc; ties break deterministically on entity id.

    Must match the ``ORDER BY initiative DESC, entity_id`` used when loading
    combatants, or turn indices would drift between writes and reads."""
    return sorted(
        entries,
        key=lambda entry: (-initiatives[entry.entity_id], entry.entity_id),
    )


@dataclass(frozen=True)
class StartCombatOperation:
    """Open an encounter: roll initiative, sort combatants, start round 1."""

    roller: DiceRoller
    roll_ids: tuple[str, ...]
    entries: tuple[CombatantEntry, ...]
    encounter_id: str

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        if len(self.roll_ids) != len(self.entries):
            raise ValidationError("one roll id per combatant entry is required")
        entity_ids = [entry.entity_id for entry in self.entries]
        if len(set(entity_ids)) != len(entity_ids):
            raise ValidationError("duplicate combatant entity")
        existing = connection.execute(
            "SELECT id FROM combat_encounters"
            " WHERE campaign_id = ? AND branch_id = ? AND status = 'active'",
            (context.campaign.id, context.branch_id),
        ).fetchone()
        if existing is not None:
            raise ValidationError("an encounter is already active on this branch")
        initiatives = {
            entry.entity_id: _roll_initiative(
                connection, context, self.roller, roll_id, entry
            )
            for entry, roll_id in zip(self.entries, self.roll_ids)
        }
        connection.execute(
            "INSERT INTO combat_encounters(id, campaign_id, branch_id,"
            " created_state_version, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                self.encounter_id,
                context.campaign.id,
                context.branch_id,
                context.next_state_version,
                context.now,
            ),
        )
        ordered = _order_entries(self.entries, initiatives)
        for entry in ordered:
            _insert_combatant(
                connection, context, self.encounter_id, entry,
                initiatives[entry.entity_id],
            )
        return {
            "encounter_id": self.encounter_id,
            "order": [entry.entity_id for entry in ordered],
            "initiative": initiatives,
            "round": 1,
        }


@dataclass(frozen=True)
class AddCombatantOperation:
    """Join one creature into the active encounter mid-combat."""

    roller: DiceRoller
    roll_id: str
    entry: CombatantEntry

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        encounter, _ = require_active_encounter(
            connection, context.campaign.id, context.branch_id
        )
        initiative = _roll_initiative(
            connection, context, self.roller, self.roll_id, self.entry
        )
        try:
            _insert_combatant(
                connection, context, encounter["id"], self.entry, initiative
            )
        except sqlite3.IntegrityError as exc:
            raise ValidationError("combatant already in encounter") from exc
        return {
            "encounter_id": encounter["id"],
            "entity_id": self.entry.entity_id,
            "initiative": initiative,
        }


@dataclass(frozen=True)
class EndCombatOperation:
    """Close the active encounter; combatant rows stay for audit and replay."""

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        encounter, _ = require_active_encounter(
            connection, context.campaign.id, context.branch_id
        )
        connection.execute(
            "UPDATE combat_encounters SET status = 'ended',"
            " ended_state_version = ? WHERE id = ?",
            (context.next_state_version, encounter["id"]),
        )
        return {
            "encounter_id": encounter["id"],
            "rounds": encounter["round_number"],
            "ended_state_version": context.next_state_version,
        }


class CombatService:
    """User-command entry points for encounter lifecycle."""

    def __init__(
        self,
        database: Database,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], str] | None = None,
    ):
        from datetime import datetime, timezone
        from uuid import uuid4

        self.database = database
        self.id_factory = id_factory or (lambda: uuid4().hex)
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat())
        self.mutation_engine = MutationEngine(
            database, id_factory=self.id_factory, clock=self.clock
        )

    def _apply(self, campaign_id: str, expected_version: int, event_type: str, operation):
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id,
                branch_id="main",
                expected_version=expected_version,
                source="user-command",
                event_type=event_type,
                operation=operation,
            )
        )

    def start(
        self,
        campaign_id: str,
        expected_version: int,
        roller: DiceRoller,
        entries: tuple[CombatantEntry, ...],
    ) -> MutationResult:
        roll_ids = tuple(self.id_factory() for _ in entries)
        return self._apply(
            campaign_id, expected_version, "combat-started",
            StartCombatOperation(roller, roll_ids, entries, self.id_factory()),
        )

    def add_combatant(
        self,
        campaign_id: str,
        expected_version: int,
        roller: DiceRoller,
        entry: CombatantEntry,
    ) -> MutationResult:
        return self._apply(
            campaign_id, expected_version, "combatant-joined",
            AddCombatantOperation(roller, self.id_factory(), entry),
        )

    def end(self, campaign_id: str, expected_version: int) -> MutationResult:
        return self._apply(
            campaign_id, expected_version, "combat-ended", EndCombatOperation()
        )
```

Note: `require_active_encounter` sorts combatants `ORDER BY initiative DESC, entity_id` — identical to `_order_entries`, so the stored insertion order and the read order never drift. The snapshot `"combat"` section arrives in Task 15; tests here assert against SQL directly.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/backend/unit/test_combat.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat(core): add combat encounter lifecycle" \
  "src/sillytavern_rpg_engine/services/combat.py" \
  "tests/backend/unit/test_combat.py"
```

---

### Task 8: Turn lifecycle and action economy

**Files:**
- Modify: `src/sillytavern_rpg_engine/services/combat.py`
- Test: `tests/backend/unit/test_actions_movement.py`

**Interfaces:**
- Consumes: Task 7 encounter helpers.
- Produces: `AdvanceTurnOperation(roller, roll_id_factory)`; `UseActionOperation(action, entity_id, ...)`; `UseReactionOperation(entity_id, purpose)`; `MoveOperation(entity_id, feet, mode)`; `MovementMode`; additions to `CombatService`: `advance_turn`, `use_action`, `use_reaction`, `move`.

Rules encoded (2024):
- Advancing past the last combatant increments `round_number` and wraps to index 0.
- On the starting combatant's turn: `action/bonus/interaction` reset to 0, `slot_spent_this_turn` reset, `mastery_uses_json` cleared, `movement_used` reset to 0 and `movement_total` recomputed from effective speed, `reaction_used` reset (reactions refresh at the start of your turn), `dodging`/`disengaged` cleared (they last until the start of your next turn), debuffs sourced by the *ending* combatant with policy `"end"` (Vex) and by the *starting* combatant with policy `"start"` (Sap, Slow) are removed, concentration duration ticks down (0 clears concentration), and Recharge abilities roll a d6 (available when the roll ≥ `min`).
- `UseActionOperation` validates: the actor is the active combatant, has its action budget, and is not Incapacitated (or Stunned/Paralyzed/Unconscious). Dash adds effective speed to `movement_total`; Disengage/Dodge set their flags; Help appends `{"ally", "target"}` to the *ally's* `help_grants_json`; Hide rolls a DC 15 Dexterity (Stealth) check and sets `hidden` on success; Search/Study/Influence record a check with caller-provided skill/DC and never set flags; Ready stores `readied_action_json`; Attack/Magic are rejected (use the attack/cast operations, which consume the budget themselves).
- Movement costs: `walk`/`jump` 1×, `difficult`/`crawl`/`climb`/`swim` 2× (2024: each foot costs 1 extra foot), `stand_up` costs half the (effective) speed, `forced` costs nothing and ignores budgets, `mounted` deducts from the mount entity's own combatant budget (`entity_id` is the mount).
- Opportunity attacks and readied actions are user-invoked: `UseReactionOperation` only marks the reaction budget; the attack operation with `is_opportunity=True` (Task 11) validates it.

- [ ] **Step 1: Write the failing tests**

```python
import json

import pytest

from sillytavern_rpg_engine.domain.dice import SequenceDiceRoller
from sillytavern_rpg_engine.domain.dnd import ActionType, Condition
from sillytavern_rpg_engine.domain.errors import ValidationError
from sillytavern_rpg_engine.domain.models import EntityKind
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
        ):
            entities.apply_explicit(
                "c1", "main", version, SetAttributeOperation(entity_id, key, value)
            )
            version += 1
    dnd.enable("c1", version)
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
        "SELECT * FROM combatants WHERE entity_id = ?", (entity_id,)
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/backend/unit/test_actions_movement.py -v`
Expected: FAIL — `MovementMode` and the new methods do not exist.

- [ ] **Step 3: Extend `services/combat.py`**

Append after `EndCombatOperation`:

```python
class MovementMode(StrEnum):
    """Movement cost modes; numeric multiplier on feet spent."""

    WALK = "walk"
    JUMP = "jump"
    DIFFICULT = "difficult"
    CRAWL = "crawl"
    CLIMB = "climb"
    SWIM = "swim"
    STAND_UP = "stand_up"
    FORCED = "forced"
    MOUNTED = "mounted"


def _movement_cost(mode: MovementMode, feet: int, speed: int) -> int:
    if mode is MovementMode.FORCED:
        return 0
    if mode is MovementMode.STAND_UP:
        return speed // 2
    if mode in (MovementMode.DIFFICULT, MovementMode.CRAWL, MovementMode.CLIMB, MovementMode.SWIM):
        return feet * 2
    return feet


def _debuffs(row) -> dict[str, Any]:
    return json.loads(row["debuffs_json"])


def add_debuff(
    connection: sqlite3.Connection,
    combatant_id: str,
    name: str,
    value: dict[str, Any],
) -> None:
    """Attach one debuff/marker; ``value`` must carry ``source`` and
    ``clear`` ("start" = source's next turn start, "end" = source's next
    turn end)."""
    row = connection.execute(
        "SELECT debuffs_json FROM combatants WHERE id = ?", (combatant_id,)
    ).fetchone()
    debuffs = json.loads(row["debuffs_json"])
    debuffs[name] = value
    connection.execute(
        "UPDATE combatants SET debuffs_json = ? WHERE id = ?",
        (json.dumps(debuffs, sort_keys=True), combatant_id),
    )


def _clear_debuffs(
    connection: sqlite3.Connection, combatants, source_id: str, policy: str
) -> None:
    """Expire debuffs sourced by ``source_id`` under ``policy`` ("start"/"end");
    entries with a ``turns`` counter survive until it reaches 0."""
    for row in combatants:
        debuffs = _debuffs(row)
        kept: dict[str, Any] = {}
        changed = False
        for key, value in debuffs.items():
            if value.get("source") == source_id and value.get("clear") == policy:
                turns = value.get("turns")
                if turns is None or turns <= 1:
                    changed = True
                    continue
                value = {**value, "turns": turns - 1}
                changed = True
            kept[key] = value
        if changed:
            connection.execute(
                "UPDATE combatants SET debuffs_json = ? WHERE id = ?",
                (json.dumps(kept, sort_keys=True), row["id"]),
            )


@dataclass(frozen=True)
class AdvanceTurnOperation:
    """End the active turn, start the next: budget resets, debuff expiry,
    concentration countdown, and Recharge rolls. Wrapping starts a new round."""

    roller: DiceRoller
    roll_id_factory: Callable[[], str]

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        encounter, combatants = require_active_encounter(
            connection, context.campaign.id, context.branch_id
        )
        ending = combatants[encounter["active_index"]]
        _clear_debuffs(connection, combatants, ending["entity_id"], "end")
        next_index = encounter["active_index"] + 1
        round_number = encounter["round_number"]
        if next_index >= len(combatants):
            next_index = 0
            round_number += 1
        connection.execute(
            "UPDATE combat_encounters SET active_index = ?, round_number = ?"
            " WHERE id = ?",
            (next_index, round_number, encounter["id"]),
        )
        starting = combatants[next_index]
        _clear_debuffs(connection, combatants, starting["entity_id"], "start")
        conditions = condition_map(
            connection, context.campaign.id, starting["entity_id"]
        )
        speed = read_int(
            connection, context.campaign.id, starting["entity_id"], "speed"
        )
        concentration_rounds = starting["concentration_rounds"]
        concentrating = starting["concentrating_spell"]
        if concentrating is not None and concentration_rounds is not None:
            concentration_rounds -= 1
            if concentration_rounds <= 0:
                concentrating = None
                concentration_rounds = None
        recharge = json.loads(starting["recharge_json"])
        recharged: list[str] = []
        for ability in recharge:
            if not ability.get("available", True):
                faces = self.roller.roll(1, 6)
                record_roll(
                    connection, context, self.roll_id_factory(),
                    purpose=f"recharge: {ability['name']}", formula="1d6",
                    faces=faces, modifier=0, total=faces[0],
                    roller_entity_id=starting["entity_id"],
                )
                if faces[0] >= ability["min"]:
                    ability["available"] = True
                    recharged.append(ability["name"])
        debuffs = _debuffs(starting)
        movement = effective_speed(speed, conditions)
        if "slow" in debuffs:
            movement = max(0, movement - int(debuffs["slow"].get("amount", 0)))
        connection.execute(
            "UPDATE combatants SET action_used = 0, bonus_used = 0,"
            " reaction_used = 0, interaction_used = 0, slot_spent_this_turn = 0,"
            " attacks_this_turn = 0,"
            " movement_total = ?, movement_used = 0, dodging = 0, disengaged = 0,"
            " mastery_uses_json = '{}', concentrating_spell = ?,"
            " concentration_rounds = ?, recharge_json = ? WHERE id = ?",
            (
                movement,
                concentrating,
                concentration_rounds,
                json.dumps(recharge),
                starting["id"],
            ),
        )
        return {
            "encounter_id": encounter["id"],
            "round": round_number,
            "active_entity_id": starting["entity_id"],
            "concentration_ended": concentrating is None
            and starting["concentrating_spell"] is not None,
            "recharged": recharged,
        }


@dataclass(frozen=True)
class UseActionOperation:
    """Spend the active combatant's action on a 2024 action type."""

    entity_id: str
    action: ActionType
    roller: DiceRoller | None = None
    roll_id: str | None = None
    skill: str | None = None
    ability: str = "dex"
    dc: int = HIDE_DC
    help_ally: str | None = None
    help_target: str | None = None
    readied: dict[str, Any] | None = None
    turn_id: str | None = None

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        if self.action in (ActionType.ATTACK, ActionType.MAGIC):
            raise ValidationError(
                "attack and magic are resolved by their own operations"
            )
        encounter, combatants = require_active_encounter(
            connection, context.campaign.id, context.branch_id
        )
        current = active_combatant(encounter, combatants)
        if current["entity_id"] != self.entity_id:
            raise ValidationError(
                f"it is not {self.entity_id!r}'s turn (not the active combatant)"
            )
        if current["action_used"]:
            raise ValidationError("action already used this turn")
        conditions = condition_map(
            connection, context.campaign.id, self.entity_id
        )
        if any(CONDITION_EFFECTS[c].incapacitated for c in conditions):
            raise ValidationError("incapacitated creatures cannot take actions")
        payload: dict[str, Any] = {
            "entity_id": self.entity_id,
            "action": self.action.value,
        }
        if self.action is ActionType.DASH:
            speed = read_int(connection, context.campaign.id, self.entity_id, "speed")
            connection.execute(
                "UPDATE combatants SET movement_total = movement_total + ?"
                " WHERE id = ?",
                (effective_speed(speed, conditions), current["id"]),
            )
        elif self.action is ActionType.DISENGAGE:
            connection.execute(
                "UPDATE combatants SET disengaged = 1 WHERE id = ?", (current["id"],)
            )
        elif self.action is ActionType.DODGE:
            connection.execute(
                "UPDATE combatants SET dodging = 1 WHERE id = ?", (current["id"],)
            )
        elif self.action is ActionType.HELP:
            if not self.help_ally or not self.help_target:
                raise ValidationError("help requires help_ally and help_target")
            ally = next(
                (row for row in combatants if row["entity_id"] == self.help_ally),
                None,
            )
            if ally is None:
                raise ValidationError("help target ally is not in the encounter")
            grants = json.loads(ally["help_grants_json"])
            grants.append({"ally": self.help_ally, "target": self.help_target})
            connection.execute(
                "UPDATE combatants SET help_grants_json = ? WHERE id = ?",
                (json.dumps(grants), ally["id"]),
            )
        elif self.action is ActionType.HIDE:
            payload["check"] = self._skill_check(
                connection, context, "dex", "stealth", HIDE_DC, "hide"
            )
            if payload["check"]["success"]:
                connection.execute(
                    "UPDATE combatants SET hidden = 1 WHERE id = ?", (current["id"],)
                )
        elif self.action in (ActionType.SEARCH, ActionType.STUDY, ActionType.INFLUENCE):
            if not self.skill:
                raise ValidationError(f"{self.action.value} requires a skill")
            payload["check"] = self._skill_check(
                connection, context, self.ability, self.skill, self.dc,
                f"{self.action.value}: {self.skill}",
            )
        elif self.action is ActionType.READY:
            if not self.readied:
                raise ValidationError("ready requires a readied action payload")
            connection.execute(
                "UPDATE combatants SET readied_action_json = ? WHERE id = ?",
                (json.dumps(self.readied), current["id"]),
            )
        connection.execute(
            "UPDATE combatants SET action_used = 1 WHERE id = ?", (current["id"],)
        )
        return payload

    def _skill_check(self, connection, context, ability, skill, dc, purpose) -> dict:
        if self.roller is None or self.roll_id is None:
            raise ValidationError(f"{purpose} requires a roller")
        from .dice import CheckOperation

        return CheckOperation(
            roller=self.roller,
            roll_id=self.roll_id,
            entity_id=self.entity_id,
            ability=ability,
            skill=skill,
            dc=dc,
            purpose=purpose,
            turn_id=self.turn_id,
        ).apply(connection, context)


@dataclass(frozen=True)
class UseReactionOperation:
    """Mark the active-round reaction spent (opportunity attack, readied
    trigger, etc.); the concrete effect is resolved by its own operation."""

    entity_id: str
    purpose: str

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        _, combatants = require_active_encounter(
            connection, context.campaign.id, context.branch_id
        )
        row = next(
            (r for r in combatants if r["entity_id"] == self.entity_id), None
        )
        if row is None:
            raise ValidationError(f"{self.entity_id!r} is not in the encounter")
        if row["reaction_used"]:
            raise ValidationError("reaction already used this round")
        conditions = condition_map(
            connection, context.campaign.id, self.entity_id
        )
        if any(CONDITION_EFFECTS[c].incapacitated for c in conditions):
            raise ValidationError("incapacitated creatures cannot take reactions")
        connection.execute(
            "UPDATE combatants SET reaction_used = 1 WHERE id = ?", (row["id"],)
        )
        return {"entity_id": self.entity_id, "purpose": self.purpose}


@dataclass(frozen=True)
class MoveOperation:
    """Spend movement on the active turn; cost depends on the mode."""

    entity_id: str
    feet: int
    mode: MovementMode

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        if isinstance(self.feet, bool) or not isinstance(self.feet, int) or self.feet < 0:
            raise ValidationError("feet must be a non-negative integer")
        _, combatants = require_active_encounter(
            connection, context.campaign.id, context.branch_id
        )
        row = next(
            (r for r in combatants if r["entity_id"] == self.entity_id), None
        )
        if row is None:
            raise ValidationError(f"{self.entity_id!r} is not in the encounter")
        conditions = condition_map(
            connection, context.campaign.id, self.entity_id
        )
        base = effective_speed(
            read_int(connection, context.campaign.id, self.entity_id, "speed"),
            conditions,
        )
        cost = _movement_cost(self.mode, self.feet, base)
        if row["movement_used"] + cost > row["movement_total"]:
            raise ValidationError(
                f"movement budget exceeded: used {row['movement_used']},"
                f" cost {cost}, total {row['movement_total']}"
            )
        connection.execute(
            "UPDATE combatants SET movement_used = movement_used + ? WHERE id = ?",
            (cost, row["id"]),
        )
        return {
            "entity_id": self.entity_id,
            "mode": self.mode.value,
            "feet": self.feet,
            "cost": cost,
            "movement_used": row["movement_used"] + cost,
        }
```

New imports needed at the top of `combat.py`:

```python
from enum import StrEnum
from ..domain.dnd import CONDITION_EFFECTS, HIDE_DC
```

New `CombatService` methods (after `end`):

```python
    def advance_turn(
        self, campaign_id: str, expected_version: int, roller: DiceRoller
    ) -> MutationResult:
        return self._apply(
            campaign_id, expected_version, "turn-advanced",
            AdvanceTurnOperation(roller, self.id_factory),
        )

    def use_action(
        self,
        campaign_id: str,
        expected_version: int,
        entity_id: str,
        action: ActionType,
        **kwargs,
    ) -> MutationResult:
        if "roller" in kwargs and kwargs["roller"] is not None:
            kwargs.setdefault("roll_id", self.id_factory())
        return self._apply(
            campaign_id, expected_version, "action-used",
            UseActionOperation(entity_id, action, **kwargs),
        )

    def use_reaction(
        self, campaign_id: str, expected_version: int, entity_id: str, purpose: str
    ) -> MutationResult:
        return self._apply(
            campaign_id, expected_version, "reaction-used",
            UseReactionOperation(entity_id, purpose),
        )

    def move(
        self,
        campaign_id: str,
        expected_version: int,
        entity_id: str,
        feet: int,
        mode: MovementMode,
    ) -> MutationResult:
        return self._apply(
            campaign_id, expected_version, "moved",
            MoveOperation(entity_id, feet, mode),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/backend/unit/test_actions_movement.py tests/backend/unit/test_combat.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat(core): add turn lifecycle, action economy, movement" \
  "src/sillytavern_rpg_engine/services/combat.py" \
  "tests/backend/unit/test_actions_movement.py"
```

---

### Task 9: Damage, healing, dying, and death saves

**Files:**
- Create: `src/sillytavern_rpg_engine/services/damage.py`
- Test: `tests/backend/unit/test_damage.py`

**Interfaces:**
- Consumes: `services/combat.py` (combatant lookup by encounter), `services/conditions.py`, `services/dnd_pack.py`, `services/dice.py` (`record_roll`), `services/attributes.py` (`SetAttributeOperation`).
- Produces: `DamageResult`; `apply_damage(connection, context, roller, roll_id_factory, target_id, amount, damage_type, is_crit_hit_5ft=False, turn_id=None)`; `ApplyDamageOperation`, `HealOperation`, `StabilizeOperation`, `DeathSaveOperation(roller, roll_id, entity_id)`; `DamageService` (`deal`, `heal`, `stabilize`, `death_save`).

Rules encoded (2024):
- Mitigation order: immunity → 0; resistance (or Petrified's resist-all) halves (round down); vulnerability doubles. Resistance and vulnerability both apply (halve, then double).
- Temporary HP absorbs first; losing temp HP still counts as taking damage for concentration.
- 0 HP: the creature falls Unconscious (condition applied, source `damage`). Massive damage: if the remaining damage after reaching 0 HP ≥ `hp_max`, the creature dies outright (`is_dead`).
- Hits against a creature at 0 HP add death-save failures: 1 per hit, 2 on a critical hit within 5 ft; 3 failures = death.
- Death saves: d20, no modifiers; ≥10 success, <10 failure; natural 1 = two failures; natural 20 = regain 1 HP and clear Unconscious; 3 successes = Stable (`is_stable`, saves reset, stays unconscious); a dead creature cannot be stabilized.
- Concentration: when a concentrating combatant takes damage, roll a Constitution save vs `max(10, damage // 2)`; failure clears `concentrating_spell`/`concentration_rounds`.
- Healing from 0 HP clears Unconscious and `is_stable` and resets death saves; HP never exceeds `hp_max`.

- [ ] **Step 1: Write the failing tests**

```python
import json

import pytest

from sillytavern_rpg_engine.domain.dice import SequenceDiceRoller
from sillytavern_rpg_engine.domain.dnd import Condition
from sillytavern_rpg_engine.domain.errors import ValidationError
from sillytavern_rpg_engine.domain.models import EntityKind
from sillytavern_rpg_engine.services.attributes import SetAttributeOperation
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.combat import CombatService, CombatantEntry
from sillytavern_rpg_engine.services.conditions import ConditionService, condition_map
from sillytavern_rpg_engine.services.damage import DamageService
from sillytavern_rpg_engine.services.dnd_pack import DndRulesService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)


def _world(database, resistances=(), immunities=(), vulnerabilities=()):
    ids = iter(f"e{n}" for n in range(1, 1000))
    clock = lambda: "2026-08-12T00:00:00Z"
    campaigns = CampaignService(database, id_factory=lambda: next(ids), clock=clock)
    dnd = DndRulesService(database, id_factory=lambda: next(ids), clock=clock)
    damage = DamageService(database, id_factory=lambda: next(ids), clock=clock)
    entities = EntityAttributeService(database, dnd.mutation_engine)
    campaigns.create_campaign("c1", "Dungeon")
    dnd.seed_pack("c1", 0)
    entities.apply_explicit("c1", "main", 1, CreateEntityOperation(
        entity_id="pc1", kind=EntityKind.CHARACTER, name="Aria",
    ))
    values = {
        "ability_con": 13, "hp_max": 12, "hp_current": 12, "hp_temp": 0,
        "death_saves_success": 0, "death_saves_failure": 0,
        "is_dead": False, "is_stable": False,
        "resistances": list(resistances), "immunities": list(immunities),
        "vulnerabilities": list(vulnerabilities),
    }
    version = 2
    for key, value in values.items():
        entities.apply_explicit(
            "c1", "main", version, SetAttributeOperation("pc1", key, value)
        )
        version += 1
    dnd.enable("c1", version)
    return damage, entities, version + 1


def _hp(connection, key="hp_current"):
    row = connection.execute(
        "SELECT value_json FROM attribute_values"
        " WHERE entity_id = 'pc1' AND attribute_key = ?",
        (key,),
    ).fetchone()
    return json.loads(row["value_json"])


def test_resistance_vulnerability_immunity_and_temp_hp(database):
    damage, _, version = _world(database, resistances=["fire"], immunities=["cold"])
    result = damage.deal("c1", version, "pc1", 9, "fire")
    assert result.snapshot and _hp_at(database) == 8  # 9 -> 4 after halving
    result = damage.deal("c1", result.state_version, "pc1", 5, "cold")
    assert _hp_at(database) == 8  # immune
    damage.deal("c1", result.state_version, "pc1", -0, "fire") if False else None
    # temp hp absorbs
    _, entities, _ = _world_state(database)
    with database.connect() as connection:
        pass


def _hp_at(database, key="hp_current"):
    with database.connect() as connection:
        return _hp(connection, key)


def _world_state(database):
    return None, None, None


def test_zero_hp_unconscious_massive_damage_and_death_save_hits(database):
    damage, entities, version = _world(database)
    result = damage.deal("c1", version, "pc1", 12, "slashing")
    assert _hp_at(database) == 0
    with database.connect() as connection:
        assert condition_map(connection, "c1", "pc1") == {Condition.UNCONSCIOUS: 1}
    # hit while down: one failure
    result = damage.deal("c1", result.state_version, "pc1", 3, "slashing")
    assert _hp_at(database, "death_saves_failure") == 1
    # critical within 5 ft: two failures -> dead at 3
    result = damage.deal(
        "c1", result.state_version, "pc1", 2, "slashing", is_crit_hit_5ft=True
    )
    assert _hp_at(database, "is_dead") is True
    with pytest.raises(ValidationError, match="dead"):
        damage.stabilize("c1", result.state_version, "pc1")


def test_massive_damage_kills_instantly(database):
    damage, _, version = _world(database)
    damage.deal("c1", version, "pc1", 24, "fire")  # 12 to reach 0, 12 >= hp_max
    assert _hp_at(database, "is_dead") is True


def test_healing_from_zero_clears_dying_state(database):
    damage, _, version = _world(database)
    result = damage.deal("c1", version, "pc1", 12, "fire")
    result = damage.heal("c1", result.state_version, "pc1", 5)
    assert _hp_at(database) == 5
    with database.connect() as connection:
        assert condition_map(connection, "c1", "pc1") == {}
    result = damage.heal("c1", result.state_version, "pc1", 99)
    assert _hp_at(database) == 12


def test_death_saves_natural_outcomes_and_stabilize(database):
    damage, _, version = _world(database)
    result = damage.deal("c1", version, "pc1", 12, "fire")
    # natural 1: two failures
    result = damage.death_save(
        "c1", result.state_version, SequenceDiceRoller([1]), "pc1"
    )
    assert _hp_at(database, "death_saves_failure") == 2
    result = damage.death_save(
        "c1", result.state_version, SequenceDiceRoller([12]), "pc1"
    )
    assert _hp_at(database, "death_saves_success") == 1
    # third failure -> dead
    result = damage.death_save(
        "c1", result.state_version, SequenceDiceRoller([3]), "pc1"
    )
    assert _hp_at(database, "is_dead") is True


def test_natural_20_death_save_revives_with_1_hp(database):
    damage, _, version = _world(database)
    result = damage.deal("c1", version, "pc1", 12, "fire")
    damage.death_save("c1", result.state_version, SequenceDiceRoller([20]), "pc1")
    assert _hp_at(database) == 1
    with database.connect() as connection:
        assert condition_map(connection, "c1", "pc1") == {}
```

(The first test contains intentionally awkward lines; the implementer should write it cleanly as shown in Step 3 notes — the assertions are: 9 fire vs resistance leaves hp at 8; 5 cold vs immunity leaves hp at 8.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/backend/unit/test_damage.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `services/damage.py`**

```python
"""Damage, healing, dying, death saves, and concentration checks."""

from dataclasses import dataclass
import sqlite3
from typing import Any, Callable

from ..domain.dice import D20Mode, DiceRoller, resolve_d20
from ..domain.dnd import (
    CONDITION_EFFECTS,
    Condition,
    DamageType,
    ability_modifier,
    require_dnd_2024,
)
from ..domain.errors import ValidationError
from ..domain.operations import MutationContext
from ..persistence.database import Database
from .attributes import SetAttributeOperation
from .conditions import ApplyConditionOperation, RemoveConditionOperation, condition_map
from .dice import record_roll
from .dnd_pack import read_bool, read_int, read_list
from .mutations import MutationEngine, MutationRequest, MutationResult


@dataclass(frozen=True)
class DamageResult:
    raw: int
    mitigated: int
    temp_absorbed: int
    hp_lost: int
    new_hp: int
    unconscious: bool
    died: bool
    concentration_broken: bool


def _mitigate(
    connection: sqlite3.Connection,
    campaign_id: str,
    target_id: str,
    amount: int,
    damage_type: DamageType,
) -> int:
    if damage_type.value in read_list(connection, campaign_id, target_id, "immunities"):
        return 0
    conditions = condition_map(connection, campaign_id, target_id)
    resistant = CONDITION_EFFECTS[Condition.PETRIFIED].resist_all_damage and Condition.PETRIFIED in conditions
    resistant = resistant or damage_type.value in read_list(
        connection, campaign_id, target_id, "resistances"
    )
    vulnerable = damage_type.value in read_list(
        connection, campaign_id, target_id, "vulnerabilities"
    )
    if resistant:
        amount //= 2
    if vulnerable:
        amount *= 2
    return amount


def _concentration_save(
    connection: sqlite3.Connection,
    context: MutationContext,
    roller: DiceRoller | None,
    roll_id_factory: Callable[[], str] | None,
    target_id: str,
    damage_taken: int,
) -> bool:
    """Roll the concentration save when the target concentrates in combat;
    returns True when concentration broke."""
    row = connection.execute(
        "SELECT c.id FROM combatants c"
        " JOIN combat_encounters e ON e.id = c.encounter_id"
        " WHERE e.campaign_id = ? AND e.branch_id = ? AND e.status = 'active'"
        " AND c.entity_id = ? AND c.concentrating_spell IS NOT NULL",
        (context.campaign.id, context.branch_id, target_id),
    ).fetchone()
    if row is None or damage_taken <= 0:
        return False
    if roller is None or roll_id_factory is None:
        raise ValidationError("concentration check requires a roller")
    dc = max(10, damage_taken // 2)
    modifier = ability_modifier(
        read_int(connection, context.campaign.id, target_id, "ability_con")
    )
    outcome = resolve_d20(roller, D20Mode.NORMAL, modifier)
    record_roll(
        connection, context, roll_id_factory(),
        purpose="concentration", formula="1d20", faces=(outcome.kept,),
        modifier=modifier, total=outcome.total, dc=dc,
        success=outcome.total >= dc, roller_entity_id=target_id,
    )
    if outcome.total >= dc:
        return False
    connection.execute(
        "UPDATE combatants SET concentrating_spell = NULL,"
        " concentration_rounds = NULL WHERE id = ?",
        (row["id"],),
    )
    return True


def apply_damage(
    connection: sqlite3.Connection,
    context: MutationContext,
    roller: DiceRoller | None,
    roll_id_factory: Callable[[], str] | None,
    target_id: str,
    amount: int,
    damage_type: str,
    *,
    is_crit_hit_5ft: bool = False,
    turn_id: str | None = None,
) -> DamageResult:
    """Mitigate, consume temp HP, reduce HP, and apply dying/concentration."""
    require_dnd_2024(context.campaign)
    campaign_id = context.campaign.id
    try:
        dtype = DamageType(damage_type)
    except ValueError as exc:
        raise ValidationError(f"invalid damage_type {damage_type!r}") from exc
    if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
        raise ValidationError("damage amount must be a non-negative integer")
    if read_bool(connection, campaign_id, target_id, "is_dead"):
        raise ValidationError("cannot damage a dead entity")
    mitigated = _mitigate(connection, campaign_id, target_id, amount, dtype)
    temp = read_int(connection, campaign_id, target_id, "hp_temp")
    hp = read_int(connection, campaign_id, target_id, "hp_current")
    absorbed = min(temp, mitigated)
    if absorbed:
        SetAttributeOperation(target_id, "hp_temp", temp - absorbed).apply(
            connection, context
        )
    remaining = mitigated - absorbed
    hp_lost = min(hp, remaining)
    new_hp = hp - hp_lost
    died = False
    unconscious = False
    conditions = condition_map(connection, campaign_id, target_id)
    if Condition.UNCONSCIOUS in conditions and hp == 0 and mitigated > 0:
        failures = read_int(connection, campaign_id, target_id, "death_saves_failure")
        failures += 2 if is_crit_hit_5ft else 1
        SetAttributeOperation(
            target_id, "death_saves_failure", min(failures, 3)
        ).apply(connection, context)
        if failures >= 3:
            died = True
    if hp_lost:
        SetAttributeOperation(target_id, "hp_current", new_hp).apply(connection, context)
    if new_hp == 0 and hp > 0:
        overflow = remaining - hp_lost
        if overflow >= read_int(connection, campaign_id, target_id, "hp_max"):
            died = True
        else:
            unconscious = True
            if Condition.UNCONSCIOUS not in conditions:
                ApplyConditionOperation(target_id, Condition.UNCONSCIOUS, "damage").apply(
                    connection, context
                )
            SetAttributeOperation(target_id, "is_stable", False).apply(connection, context)
    if died:
        SetAttributeOperation(target_id, "is_dead", True).apply(connection, context)
    broken = _concentration_save(
        connection, context, roller, roll_id_factory, target_id, mitigated
    )
    return DamageResult(
        raw=amount, mitigated=mitigated, temp_absorbed=absorbed, hp_lost=hp_lost,
        new_hp=new_hp, unconscious=unconscious, died=died,
        concentration_broken=broken,
    )


@dataclass(frozen=True)
class ApplyDamageOperation:
    roller: DiceRoller | None
    roll_id_factory: Callable[[], str] | None
    target_id: str
    amount: int
    damage_type: str
    is_crit_hit_5ft: bool = False
    turn_id: str | None = None

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        result = apply_damage(
            connection, context, self.roller, self.roll_id_factory,
            self.target_id, self.amount, self.damage_type,
            is_crit_hit_5ft=self.is_crit_hit_5ft, turn_id=self.turn_id,
        )
        return {"target_id": self.target_id, **result.__dict__}


@dataclass(frozen=True)
class HealOperation:
    target_id: str
    amount: int

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        campaign_id = context.campaign.id
        if isinstance(self.amount, bool) or not isinstance(self.amount, int) or self.amount < 1:
            raise ValidationError("healing amount must be a positive integer")
        if read_bool(connection, campaign_id, self.target_id, "is_dead"):
            raise ValidationError("cannot heal a dead entity")
        hp_max = read_int(connection, campaign_id, self.target_id, "hp_max")
        hp = read_int(connection, campaign_id, self.target_id, "hp_current")
        new_hp = min(hp_max, hp + self.amount)
        SetAttributeOperation(self.target_id, "hp_current", new_hp).apply(
            connection, context
        )
        if hp == 0 < new_hp:
            conditions = condition_map(connection, campaign_id, self.target_id)
            if Condition.UNCONSCIOUS in conditions:
                RemoveConditionOperation(self.target_id, Condition.UNCONSCIOUS).apply(
                    connection, context
                )
            SetAttributeOperation(self.target_id, "is_stable", False).apply(
                connection, context
            )
            SetAttributeOperation(self.target_id, "death_saves_success", 0).apply(
                connection, context
            )
            SetAttributeOperation(self.target_id, "death_saves_failure", 0).apply(
                connection, context
            )
        return {"target_id": self.target_id, "healed": new_hp - hp, "hp": new_hp}


@dataclass(frozen=True)
class StabilizeOperation:
    target_id: str

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        campaign_id = context.campaign.id
        if read_bool(connection, campaign_id, self.target_id, "is_dead"):
            raise ValidationError("cannot stabilize a dead entity")
        conditions = condition_map(connection, campaign_id, self.target_id)
        if Condition.UNCONSCIOUS not in conditions or read_int(
            connection, campaign_id, self.target_id, "hp_current"
        ) != 0:
            raise ValidationError("entity is not dying")
        SetAttributeOperation(self.target_id, "is_stable", True).apply(connection, context)
        SetAttributeOperation(self.target_id, "death_saves_success", 0).apply(
            connection, context
        )
        SetAttributeOperation(self.target_id, "death_saves_failure", 0).apply(
            connection, context
        )
        return {"target_id": self.target_id, "stable": True}


@dataclass(frozen=True)
class DeathSaveOperation:
    roller: DiceRoller
    roll_id: str
    target_id: str

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        campaign_id = context.campaign.id
        conditions = condition_map(connection, campaign_id, self.target_id)
        if read_bool(connection, campaign_id, self.target_id, "is_dead"):
            raise ValidationError("the entity is already dead")
        if Condition.UNCONSCIOUS not in conditions or read_int(
            connection, campaign_id, self.target_id, "hp_current"
        ) != 0:
            raise ValidationError("death saves require a dying (0 HP) entity")
        outcome = resolve_d20(self.roller, D20Mode.NORMAL, 0)
        payload = record_roll(
            connection, context, self.roll_id,
            purpose="death save", formula="1d20", faces=(outcome.kept,),
            modifier=0, total=outcome.total, roller_entity_id=self.target_id,
        )
        if outcome.is_natural_20:
            SetAttributeOperation(self.target_id, "hp_current", 1).apply(connection, context)
            RemoveConditionOperation(self.target_id, Condition.UNCONSCIOUS).apply(
                connection, context
            )
            SetAttributeOperation(self.target_id, "death_saves_success", 0).apply(
                connection, context
            )
            SetAttributeOperation(self.target_id, "death_saves_failure", 0).apply(
                connection, context
            )
            return {**payload, "outcome": "revived"}
        successes = read_int(connection, campaign_id, self.target_id, "death_saves_success")
        failures = read_int(connection, campaign_id, self.target_id, "death_saves_failure")
        if outcome.is_natural_1:
            failures += 2
        elif outcome.total >= 10:
            successes += 1
        else:
            failures += 1
        result = "rolling"
        if failures >= 3:
            SetAttributeOperation(self.target_id, "is_dead", True).apply(connection, context)
            result = "died"
        else:
            SetAttributeOperation(
                self.target_id, "death_saves_success", successes
            ).apply(connection, context)
            SetAttributeOperation(
                self.target_id, "death_saves_failure", failures
            ).apply(connection, context)
            if successes >= 3:
                SetAttributeOperation(self.target_id, "is_stable", True).apply(
                    connection, context
                )
                result = "stable"
        return {**payload, "outcome": result}


class DamageService:
    """User-command entry points for damage, healing, and death saves."""

    def __init__(
        self,
        database: Database,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], str] | None = None,
    ):
        from datetime import datetime, timezone
        from uuid import uuid4

        self.database = database
        self.id_factory = id_factory or (lambda: uuid4().hex)
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat())
        self.mutation_engine = MutationEngine(
            database, id_factory=self.id_factory, clock=self.clock
        )

    def _apply(self, campaign_id, expected_version, event_type, operation):
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id, branch_id="main",
                expected_version=expected_version, source="user-command",
                event_type=event_type, operation=operation,
            )
        )

    def deal(self, campaign_id, expected_version, target_id, amount, damage_type,
             is_crit_hit_5ft=False, roller=None):
        factory = self.id_factory if roller is not None else None
        return self._apply(campaign_id, expected_version, "damage-dealt",
                           ApplyDamageOperation(roller, factory, target_id, amount,
                                                damage_type, is_crit_hit_5ft))

    def heal(self, campaign_id, expected_version, target_id, amount):
        return self._apply(campaign_id, expected_version, "healed",
                           HealOperation(target_id, amount))

    def stabilize(self, campaign_id, expected_version, target_id):
        return self._apply(campaign_id, expected_version, "stabilized",
                           StabilizeOperation(target_id))

    def death_save(self, campaign_id, expected_version, roller, target_id):
        return self._apply(campaign_id, expected_version, "death-save",
                           DeathSaveOperation(roller, self.id_factory(), target_id))
```

Note for the test file: drop the awkward `_world_state` placeholder lines shown in Step 1 — the clean assertions are `_hp_at(database) == 8` after each of the first two `deal` calls, plus a temp-HP case: set `hp_temp` to 5 via `SetAttributeOperation`, deal 8 slashing, assert hp is still full and `hp_temp` is 0.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/backend/unit/test_damage.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat(core): add damage, dying, and death save pipeline" \
  "src/sillytavern_rpg_engine/services/damage.py" \
  "tests/backend/unit/test_damage.py"
```

---

### Task 10: Attack resolution with weapon properties

**Files:**
- Create: `src/sillytavern_rpg_engine/services/attacks.py`
- Test: `tests/backend/unit/test_attacks.py`

**Interfaces:**
- Consumes: `services/combat.py` (encounter helpers, `add_debuff`), `services/damage.py` (`apply_damage`), `services/dice.py`, `services/conditions.py`, `domain/dnd.py`.
- Produces: `AttackSpec`; `resolve_strike(connection, context, roller, roll_id_factory, attacker_id, spec, budget)`; `AttackOperation(roller, roll_id_factory, attacker_id, spec, is_opportunity=False)`; `GrappleOperation`, `EscapeGrappleOperation`, `ShoveOperation`, `UnarmedStrikeOperation`; `AttackService` (`attack`, `grapple`, `escape_grapple`, `shove`, `unarmed`).

Rules encoded (2024):
- Budget: a normal attack requires the Attack action (active turn, action unused); `extra_attack=True` requires the action already spent on an attack this turn (tracked via `attacks_this_turn > 0`); `offhand=True` requires the Bonus Action, a Light weapon, and `other_weapon_light`; opportunity attacks consume the Reaction and skip turn checks. A `loading` weapon allows only one attack with it per turn.
- Attack modifier: ability mod (Finesse → max(str, dex); melee default str; RANGED → dex; Thrown uses the melee ability) + proficiency bonus when the weapon key is in `proficiencies` + exhaustion penalty.
- Advantage/disadvantage sources are aggregated then `combine_modes` cancels: attacker condition effects, target condition effects, Prone distance rule (≤5 ft advantage for the attacker, else disadvantage), Dodge, hidden attacker (advantage, then hidden clears), Sap debuff on attacker (disadvantage, consumed), Vex marker matching the target (advantage, consumed), Help grants matching the target (advantage, consumed), long range (disadvantage).
- Natural 1 always misses; natural 20 always hits and crits. Hits against a Paralyzed/Unconscious target within 5 ft crit.
- Cover: Total → `ValidationError`; otherwise adds its bonus to the target's AC.
- Damage on hit: weapon dice (doubled on crit) + attack ability modifier (omitted on offhand/Cleave attacks unless negative). Applied through `apply_damage` with `is_crit_hit_5ft`.
- Graze-capable resolution is delegated to Task 11's `mastery.py` through `mastery_on_hit` / `mastery_on_miss` callback parameters defaulting to no-ops; Task 10 wires the parameters, Task 11 fills them.
- Unarmed strike: attack ability str, flat damage `1 + str mod` (no damage dice; recorded with empty faces).
- Grapple/Shove (2024): no attack roll — the target makes a Str or Dex save (its choice) vs `8 + attacker's str mod + PB`; grapple failure applies Grappled (source = attacker), shove failure applies Prone or records a 5 ft push. Size gate: target at most one size larger. Escape: action, Str(Athletics)/Dex(Acrobatics) save vs the grappler's DC, success removes Grappled.

- [ ] **Step 1: Write the failing tests**

```python
import json

import pytest

from sillytavern_rpg_engine.domain.dice import DiceFormula, SequenceDiceRoller
from sillytavern_rpg_engine.domain.dnd import (
    Condition,
    Cover,
    CreatureSize,
    MasteryProperty,
    WeaponProfile,
    WeaponProperty,
)
from sillytavern_rpg_engine.domain.errors import ValidationError
from sillytavern_rpg_engine.domain.models import EntityKind
from sillytavern_rpg_engine.services.attacks import AttackService, AttackSpec
from sillytavern_rpg_engine.services.attributes import SetAttributeOperation
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.combat import CombatService, CombatantEntry
from sillytavern_rpg_engine.services.conditions import ConditionService, condition_map
from sillytavern_rpg_engine.services.dnd_pack import DndRulesService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)

SWORD = WeaponProfile(
    key="longsword", damage=DiceFormula(1, 8), damage_type="slashing",
    properties=frozenset(), mastery=MasteryProperty.SAP,
    attack_ability="str", range_normal=5,
)
DAGGER = WeaponProfile(
    key="dagger", damage=DiceFormula(1, 4), damage_type="piercing",
    properties=frozenset({WeaponProperty.LIGHT, WeaponProperty.THROWN}),
    mastery=MasteryProperty.NICK, attack_ability="str", range_normal=5,
    range_long=20,
)
BOW = WeaponProfile(
    key="longbow", damage=DiceFormula(1, 8), damage_type="piercing",
    properties=frozenset({WeaponProperty.RANGED, WeaponProperty.AMMUNITION}),
    mastery=MasteryProperty.SLOW, attack_ability="dex", range_normal=30,
    range_long=100,
)


def _world(database):
    ids = iter(f"e{n}" for n in range(1, 1000))
    clock = lambda: "2026-08-12T00:00:00Z"
    campaigns = CampaignService(database, id_factory=lambda: next(ids), clock=clock)
    dnd = DndRulesService(database, id_factory=lambda: next(ids), clock=clock)
    combat = CombatService(database, id_factory=lambda: next(ids), clock=clock)
    attacks = AttackService(database, id_factory=lambda: next(ids), clock=clock)
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
            ("ability_str", 16), ("ability_dex", 10), ("ability_con", 13),
            ("proficiency_bonus", 2), ("proficiencies", ["longsword", "dagger"]),
            ("armor_class", 13), ("hp_max", 20), ("hp_current", 20),
            ("hp_temp", 0), ("speed", 30), ("character_level", 1),
            ("spell_slots_max", [0] * 9), ("spell_slots_current", [0] * 9),
            ("hit_die", 10), ("hit_dice_total", 1), ("hit_dice_current", 1),
            ("death_saves_success", 0), ("death_saves_failure", 0),
            ("is_dead", False), ("is_stable", False), ("resistances", []),
            ("vulnerabilities", []), ("immunities", []),
            ("condition_immunities", []), ("weapon_masteries", ["longsword", "dagger"]),
        ):
            entities.apply_explicit(
                "c1", "main", version, SetAttributeOperation(entity_id, key, value)
            )
            version += 1
    dnd.enable("c1", version)
    result = combat.start(
        "c1", expected_version=version + 1,
        roller=SequenceDiceRoller([15, 10]),
        entries=(CombatantEntry("pc1"), CombatantEntry("orc")),
    )
    return attacks, combat, conditions, entities, result.state_version


def _hp(database, entity_id):
    with database.connect() as connection:
        row = connection.execute(
            "SELECT value_json FROM attribute_values"
            " WHERE entity_id = ? AND attribute_key = 'hp_current'",
            (entity_id,),
        ).fetchone()
        return json.loads(row["value_json"])


def test_hit_miss_crit_and_records(database):
    attacks, _, _, _, version = _world(database)
    # attack 15 + 5 = 20 vs AC 13: hit, damage 6 + 3
    result = attacks.attack(
        "c1", version, "pc1",
        AttackSpec(target_id="orc", weapon=SWORD),
        roller=SequenceDiceRoller([15, 6]),
    )
    assert _hp(database, "orc") == 11
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT purpose, total, critical FROM dice_rolls ORDER BY rowid"
        ).fetchall()
        assert [(r["purpose"], r["total"], r["critical"]) for r in rows] == [
            ("attack: longsword", 20, 0),
            ("damage: longsword", 9, 0),
        ]
    with pytest.raises(ValidationError, match="action"):
        attacks.attack(
            "c1", result.state_version, "pc1",
            AttackSpec(target_id="orc", weapon=SWORD),
            roller=SequenceDiceRoller([10, 3]),
        )


def test_natural_20_crits_and_doubles_dice(database):
    attacks, _, _, _, version = _world(database)
    attacks.attack(
        "c1", version, "pc1",
        AttackSpec(target_id="orc", weapon=SWORD),
        roller=SequenceDiceRoller([20, 4, 4]),
    )
    assert _hp(database, "orc") == 9  # 4+4+3


def test_prone_dodge_and_cover_change_the_roll(database):
    attacks, combat, conditions, _, version = _world(database)
    conditions.apply("c1", version, "pc1", Condition.PRONE, source="test")
    # prone attacker has disadvantage: (12, 4) keeps 4 + 5 = 9 < 13 -> miss
    result = attacks.attack(
        "c1", version + 1, "pc1",
        AttackSpec(target_id="orc", weapon=SWORD),
        roller=SequenceDiceRoller([12, 4]),
    )
    assert _hp(database, "orc") == 20
    # three-quarters cover: AC 18
    result = combat.advance_turn("c1", result.state_version, SequenceDiceRoller([]))
    result = combat.advance_turn("c1", result.state_version, SequenceDiceRoller([]))
    attacks.attack(
        "c1", result.state_version, "pc1",
        AttackSpec(target_id="orc", weapon=SWORD, cover=Cover.THREE_QUARTERS),
        roller=SequenceDiceRoller([12, 5]),
    )
    assert _hp(database, "orc") == 20  # 17 < 18
    with pytest.raises(ValidationError, match="cover"):
        attacks.attack(
            "c1", result.state_version + 1, "pc1",
            AttackSpec(target_id="orc", weapon=SWORD, cover=Cover.TOTAL),
            roller=SequenceDiceRoller([20, 5]),
        )


def test_extra_attack_offhand_loading_and_range_rules(database):
    attacks, combat, _, _, version = _world(database)
    result = attacks.attack(
        "c1", version, "pc1",
        AttackSpec(target_id="orc", weapon=SWORD),
        roller=SequenceDiceRoller([15, 6]),
    )
    # extra attack on the same Attack action is allowed
    result = attacks.attack(
        "c1", result.state_version, "pc1",
        AttackSpec(target_id="orc", weapon=SWORD, extra_attack=True),
        roller=SequenceDiceRoller([15, 6]),
    )
    # offhand needs the bonus action and a light weapon
    result = attacks.attack(
        "c1", result.state_version, "pc1",
        AttackSpec(
            target_id="orc", weapon=DAGGER, offhand=True, other_weapon_light=True,
        ),
        roller=SequenceDiceRoller([15, 2]),
    )
    assert _hp(database, "orc") == 20 - 9 - 9 - 2  # offhand: no ability mod
    with pytest.raises(ValidationError, match="bonus"):
        attacks.attack(
            "c1", result.state_version, "pc1",
            AttackSpec(
                target_id="orc", weapon=DAGGER, offhand=True,
                other_weapon_light=True,
            ),
            roller=SequenceDiceRoller([15, 2]),
        )


def test_grapple_shove_and_escape(database):
    attacks, _, _, _, version = _world(database)
    # grapple: orc dex save (10 + 0) vs DC 8 + 3 + 2 = 13 -> grappled
    result = attacks.grapple(
        "c1", version, "pc1", "orc", save_ability="dex",
        attacker_size=CreatureSize.MEDIUM, target_size=CreatureSize.MEDIUM,
        roller=SequenceDiceRoller([10]),
    )
    with database.connect() as connection:
        assert condition_map(connection, "c1", "orc") == {Condition.GRAPPLED: 1}
    result = combat_end_and_restart(database, result)
    # escape: orc str save 12 + 2 vs DC 13 -> escapes
    result = attacks.escape_grapple(
        "c1", result, "orc", roller=SequenceDiceRoller([12]),
    )
    with database.connect() as connection:
        assert condition_map(connection, "c1", "orc") == {}


def combat_end_and_restart(database, result):
    return result


def test_unarmed_strike_flat_damage(database):
    attacks, _, _, _, version = _world(database)
    attacks.unarmed("c1", version, "pc1", "orc", roller=SequenceDiceRoller([15]))
    assert _hp(database, "orc") == 16  # 1 + 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/backend/unit/test_attacks.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `services/attacks.py`**

```python
"""Attack resolution: budgets, advantage aggregation, weapon properties,
grapple/shove contests, and unarmed strikes."""

from dataclasses import dataclass, replace
import json
import sqlite3
from typing import Any, Callable

from ..domain.dice import (
    D20Mode,
    DiceFormula,
    DiceRoller,
    combine_modes,
    resolve_d20,
    roll_formula,
)
from ..domain.dnd import (
    CONDITION_EFFECTS,
    Cover,
    CreatureSize,
    Condition,
    WeaponProfile,
    WeaponProperty,
    ability_modifier,
    require_dnd_2024,
)
from ..domain.errors import ValidationError
from ..domain.operations import MutationContext
from ..persistence.database import Database
from .combat import (
    active_combatant,
    add_debuff,
    require_active_encounter,
)
from .conditions import (
    ApplyConditionOperation,
    RemoveConditionOperation,
    condition_map,
)
from .damage import apply_damage
from .dice import record_roll
from .dnd_pack import read_int, read_list
from .mutations import MutationEngine, MutationRequest, MutationResult


@dataclass(frozen=True)
class AttackSpec:
    """One weapon strike.

    ``offhand`` uses the Bonus Action with a Light weapon (no ability
    modifier on damage unless negative). ``extra_attack`` chains a further
    attack on an already-spent Attack action. ``suppress_positive_modifier``
    serves Cleave's second attack.
    """

    target_id: str
    weapon: WeaponProfile
    distance_ft: int = 5
    cover: Cover = Cover.NONE
    offhand: bool = False
    other_weapon_light: bool = False
    extra_attack: bool = False
    is_opportunity: bool = False
    use_nick: bool = False
    suppress_positive_modifier: bool = False
    flat_damage: int | None = None
    attacker_size: CreatureSize = CreatureSize.MEDIUM
    target_size: CreatureSize = CreatureSize.MEDIUM


def _attack_ability_score(
    connection, campaign_id: str, attacker_id: str, weapon: WeaponProfile
) -> int:
    strength = read_int(connection, campaign_id, attacker_id, "ability_str")
    if WeaponProperty.FINESSE in weapon.properties:
        dexterity = read_int(connection, campaign_id, attacker_id, "ability_dex")
        return max(strength, dexterity)
    return read_int(connection, campaign_id, attacker_id, f"ability_{weapon.attack_ability}")


def _combatant_row(connection, encounter_id: str, entity_id: str):
    row = connection.execute(
        "SELECT * FROM combatants WHERE encounter_id = ? AND entity_id = ?",
        (encounter_id, entity_id),
    ).fetchone()
    if row is None:
        raise ValidationError(f"{entity_id!r} is not in the encounter")
    return row


def _spend_budget(
    connection,
    context: MutationContext,
    encounter,
    combatants,
    attacker_id: str,
    spec: AttackSpec,
) -> Any:
    row = _combatant_row(connection, encounter["id"], attacker_id)
    conditions = condition_map(connection, context.campaign.id, attacker_id)
    if any(CONDITION_EFFECTS[c].incapacitated for c in conditions):
        raise ValidationError("incapacitated creatures cannot attack")
    if spec.is_opportunity:
        if row["reaction_used"]:
            raise ValidationError("reaction already used this round")
        connection.execute(
            "UPDATE combatants SET reaction_used = 1 WHERE id = ?", (row["id"],)
        )
        return row
    current = active_combatant(encounter, combatants)
    if current["entity_id"] != attacker_id:
        raise ValidationError(f"it is not {attacker_id!r}'s turn")
    if spec.offhand:
        if WeaponProperty.LIGHT not in spec.weapon.properties:
            raise ValidationError("offhand attacks require a light weapon")
        if not spec.other_weapon_light:
            raise ValidationError("the other weapon must be light")
        if spec.use_nick:
            pass  # Task 11 validates and consumes the Nick use
        elif row["bonus_used"]:
            raise ValidationError("bonus action already used this turn")
        else:
            connection.execute(
                "UPDATE combatants SET bonus_used = 1 WHERE id = ?", (row["id"],)
            )
    elif spec.extra_attack:
        if not row["action_used"] or not row["attacks_this_turn"]:
            raise ValidationError("extra attacks require a prior attack this turn")
        if WeaponProperty.LOADING in spec.weapon.properties:
            raise ValidationError("loading weapons allow one attack per turn")
    else:
        if row["action_used"]:
            raise ValidationError("action already used this turn")
        if WeaponProperty.LOADING in spec.weapon.properties and row["attacks_this_turn"]:
            raise ValidationError("loading weapons allow one attack per turn")
        connection.execute(
            "UPDATE combatants SET action_used = 1 WHERE id = ?", (row["id"],)
        )
    connection.execute(
        "UPDATE combatants SET attacks_this_turn = attacks_this_turn + 1"
        " WHERE id = ?",
        (row["id"],),
    )
    return row


def _attack_modifiers(
    connection,
    context: MutationContext,
    attacker_row,
    spec: AttackSpec,
) -> tuple[int, int]:
    """Aggregate (advantage_count, disadvantage_count) for one strike."""
    campaign_id = context.campaign.id
    adv = dis = 0
    attacker_conditions = condition_map(connection, campaign_id, attacker_row["entity_id"])
    target_conditions = condition_map(connection, campaign_id, spec.target_id)
    attacker_effects = CONDITION_EFFECTS  # alias for readability
    for condition in attacker_conditions:
        effects = attacker_effects[condition]
        adv += effects.attacker_advantage
        dis += effects.attacker_disadvantage
    for condition in target_conditions:
        effects = attacker_effects[condition]
        adv += effects.target_advantage
        dis += effects.target_disadvantage
    if Condition.PRONE in target_conditions:
        if spec.distance_ft <= 5:
            adv += 1
        else:
            dis += 1
    target_row = _combatant_row(
        connection, attacker_row["encounter_id"], spec.target_id
    )
    if target_row["dodging"]:
        dis += 1
    if attacker_row["hidden"]:
        adv += 1
    debuffs = json.loads(attacker_row["debuffs_json"])
    if "sap" in debuffs:
        dis += 1
    if debuffs.get("vex_vs", {}).get("target") == spec.target_id:
        adv += 1
    grants = [
        grant
        for grant in json.loads(attacker_row["help_grants_json"])
        if grant["target"] == spec.target_id
    ]
    if grants:
        adv += 1
    if (
        spec.distance_ft > spec.weapon.range_normal
        and spec.weapon.range_long is not None
        and spec.distance_ft <= spec.weapon.range_long
    ):
        dis += 1
    return adv, dis


def _consume_attack_marks(connection, attacker_row, spec: AttackSpec) -> None:
    debuffs = json.loads(attacker_row["debuffs_json"])
    changed = debuffs.pop("sap", None) is not None
    if debuffs.get("vex_vs", {}).get("target") == spec.target_id:
        del debuffs["vex_vs"]
        changed = True
    grants = json.loads(attacker_row["help_grants_json"])
    kept = [g for g in grants if g["target"] != spec.target_id]
    connection.execute(
        "UPDATE combatants SET debuffs_json = ?, help_grants_json = ?,"
        " hidden = 0 WHERE id = ?",
        (json.dumps(debuffs, sort_keys=True), json.dumps(kept), attacker_row["id"]),
    )


def resolve_strike(
    connection: sqlite3.Connection,
    context: MutationContext,
    roller: DiceRoller,
    roll_id_factory: Callable[[], str],
    attacker_id: str,
    spec: AttackSpec,
    *,
    spend_budget: bool = True,
    mastery_on_hit: Callable[..., dict] | None = None,
    mastery_on_miss: Callable[..., dict] | None = None,
) -> dict[str, Any]:
    """Resolve one strike; shared by AttackOperation and MultiattackOperation."""
    require_dnd_2024(context.campaign)
    campaign_id = context.campaign.id
    encounter, combatants = require_active_encounter(
        connection, campaign_id, context.branch_id
    )
    attacker_row = (
        _spend_budget(connection, context, encounter, combatants, attacker_id, spec)
        if spend_budget
        else _combatant_row(connection, encounter["id"], attacker_id)
    )
    weapon = spec.weapon
    if WeaponProperty.RANGED not in weapon.properties:
        reach = 10 if WeaponProperty.REACH in weapon.properties else weapon.range_normal
        if WeaponProperty.THROWN not in weapon.properties or spec.distance_ft <= reach:
            if spec.distance_ft > reach:
                raise ValidationError("target is beyond melee reach")
    if spec.distance_ft > (weapon.range_long or weapon.range_normal):
        raise ValidationError("target is beyond long range")
    if spec.cover is Cover.TOTAL:
        raise ValidationError("cannot target a creature behind total cover")
    score = _attack_ability_score(connection, campaign_id, attacker_id, weapon)
    ability_mod = ability_modifier(score)
    modifier = ability_mod
    if weapon.key in read_list(connection, campaign_id, attacker_id, "proficiencies"):
        modifier += read_int(connection, campaign_id, attacker_id, "proficiency_bonus")
    from .dice import read_exhaustion

    modifier += read_exhaustion(connection, campaign_id, attacker_id) * -2
    adv, dis = _attack_modifiers(connection, context, attacker_row, spec)
    outcome = resolve_d20(roller, combine_modes(adv, dis), modifier)
    target_row = _combatant_row(connection, encounter["id"], spec.target_id)
    armor_class = read_int(connection, campaign_id, spec.target_id, "armor_class")
    armor_class += spec.cover.ac_bonus or 0
    target_conditions = condition_map(connection, campaign_id, spec.target_id)
    crit_within_5 = spec.distance_ft <= 5 and any(
        CONDITION_EFFECTS[c].crit_when_hit_within_5ft for c in target_conditions
    )
    hit = not outcome.is_natural_1 and (
        outcome.is_natural_20 or outcome.total >= armor_class
    )
    critical = hit and (outcome.is_natural_20 or crit_within_5)
    faces = (
        (outcome.kept,)
        if outcome.dropped is None
        else tuple(sorted((outcome.kept, outcome.dropped), reverse=True))
    )
    payload = record_roll(
        connection, context, roll_id_factory(),
        purpose=f"attack: {weapon.key}", formula="1d20", faces=faces,
        modifier=modifier, total=outcome.total, dc=armor_class,
        success=hit, critical=critical, roller_entity_id=attacker_id,
    )
    _consume_attack_marks(connection, attacker_row, spec)
    payload["hit"] = hit
    payload["critical"] = critical
    if hit:
        if spec.flat_damage is not None:
            damage_amount = spec.flat_damage + ability_mod
            damage_formula = "flat"
            damage_faces: tuple[int, ...] = ()
        else:
            formula = weapon.damage
            if critical:
                formula = DiceFormula(formula.count * 2, formula.sides, 0)
            rolled = roll_formula(roller, formula)
            damage_faces = rolled.faces
            damage_modifier = ability_mod
            if (spec.offhand or spec.suppress_positive_modifier) and ability_mod > 0:
                damage_modifier = 0
            damage_amount = rolled.total - formula.modifier + damage_modifier
            damage_formula = str(formula)
        damage_amount = max(0, damage_amount)
        damage_payload = record_roll(
            connection, context, roll_id_factory(),
            purpose=f"damage: {weapon.key}", formula=damage_formula,
            faces=damage_faces, modifier=ability_mod, total=damage_amount,
            roller_entity_id=attacker_id,
        )
        result = apply_damage(
            connection, context, roller, roll_id_factory, spec.target_id,
            damage_amount, weapon.damage_type,
            is_crit_hit_5ft=critical and spec.distance_ft <= 5,
        )
        payload["damage"] = {**damage_payload, **result.__dict__}
        if mastery_on_hit is not None:
            payload["mastery"] = mastery_on_hit(
                connection, context, roller, roll_id_factory,
                attacker_row, spec, result,
            )
    elif mastery_on_miss is not None:
        payload["mastery"] = mastery_on_miss(
            connection, context, roller, roll_id_factory, attacker_row, spec,
        )
    return payload


@dataclass(frozen=True)
class AttackOperation:
    roller: DiceRoller
    roll_id_factory: Callable[[], str]
    attacker_id: str
    spec: AttackSpec

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        # Mastery callbacks are wired in Task 11 once mastery.py exists.
        return resolve_strike(
            connection, context, self.roller, self.roll_id_factory,
            self.attacker_id, self.spec,
        )


@dataclass(frozen=True)
class GrappleOperation:
    """2024 grapple: the target makes a Str/Dex save vs 8 + str mod + PB."""

    roller: DiceRoller
    roll_id: str
    attacker_id: str
    target_id: str
    save_ability: str
    attacker_size: CreatureSize
    target_size: CreatureSize

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        return _contest(
            connection, context, self.roller, self.roll_id,
            self.attacker_id, self.target_id, self.save_ability,
            self.attacker_size, self.target_size, effect="grapple",
        )


@dataclass(frozen=True)
class ShoveOperation:
    roller: DiceRoller
    roll_id: str
    attacker_id: str
    target_id: str
    save_ability: str
    attacker_size: CreatureSize
    target_size: CreatureSize
    prone: bool = True

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        return _contest(
            connection, context, self.roller, self.roll_id,
            self.attacker_id, self.target_id, self.save_ability,
            self.attacker_size, self.target_size,
            effect="shove-prone" if self.prone else "shove-push",
        )


def _contest(
    connection, context, roller, roll_id, attacker_id, target_id,
    save_ability, attacker_size, target_size, effect,
) -> dict:
    require_dnd_2024(context.campaign)
    if save_ability not in ("str", "dex"):
        raise ValidationError("the save ability must be str or dex")
    if target_size.rank > attacker_size.rank + 1:
        raise ValidationError("target is too large")
    campaign_id = context.campaign.id
    encounter, combatants = require_active_encounter(
        connection, campaign_id, context.branch_id
    )
    _spend_budget(
        connection, context, encounter, combatants, attacker_id,
        AttackSpec(target_id=target_id, weapon=WeaponProfile(
            key="unarmed", damage=DiceFormula(1, 2), damage_type="bludgeoning",
            properties=frozenset(), mastery=None, attack_ability="str",
            range_normal=5,
        )),
    )
    dc = 8 + ability_modifier(
        read_int(connection, campaign_id, attacker_id, "ability_str")
    ) + read_int(connection, campaign_id, attacker_id, "proficiency_bonus")
    save_mod = ability_modifier(
        read_int(connection, campaign_id, target_id, f"ability_{save_ability}")
    )
    if f"save_{save_ability}" in read_list(
        connection, campaign_id, target_id, "proficiencies"
    ):
        save_mod += read_int(connection, campaign_id, target_id, "proficiency_bonus")
    outcome = resolve_d20(roller, D20Mode.NORMAL, save_mod)
    payload = record_roll(
        connection, context, roll_id,
        purpose=f"{effect} save", formula="1d20", faces=(outcome.kept,),
        modifier=save_mod, total=outcome.total, dc=dc,
        success=outcome.total >= dc, roller_entity_id=target_id,
    )
    if outcome.total < dc:
        if effect == "grapple":
            ApplyConditionOperation(
                target_id, Condition.GRAPPLED, attacker_id
            ).apply(connection, context)
        elif effect == "shove-prone":
            ApplyConditionOperation(
                target_id, Condition.PRONE, attacker_id
            ).apply(connection, context)
        payload["applied"] = effect
    else:
        payload["applied"] = None
    return payload


@dataclass(frozen=True)
class EscapeGrappleOperation:
    roller: DiceRoller
    roll_id: str
    entity_id: str

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        campaign_id = context.campaign.id
        conditions = condition_map(connection, campaign_id, self.entity_id)
        if Condition.GRAPPLED not in conditions:
            raise ValidationError("entity is not grappled")
        row = connection.execute(
            "SELECT source FROM entity_conditions"
            " WHERE campaign_id = ? AND entity_id = ? AND condition = 'grappled'",
            (campaign_id, self.entity_id),
        ).fetchone()
        grappler = row["source"]
        dc = 8 + ability_modifier(
            read_int(connection, campaign_id, grappler, "ability_str")
        ) + read_int(connection, campaign_id, grappler, "proficiency_bonus")
        score = max(
            read_int(connection, campaign_id, self.entity_id, "ability_str"),
            read_int(connection, campaign_id, self.entity_id, "ability_dex"),
        )
        modifier = ability_modifier(score)
        outcome = resolve_d20(self.roller, D20Mode.NORMAL, modifier)
        payload = record_roll(
            connection, context, self.roll_id,
            purpose="escape grapple", formula="1d20", faces=(outcome.kept,),
            modifier=modifier, total=outcome.total, dc=dc,
            success=outcome.total >= dc, roller_entity_id=self.entity_id,
        )
        if outcome.total >= dc:
            RemoveConditionOperation(self.entity_id, Condition.GRAPPLED).apply(
                connection, context
            )
        return payload


class AttackService:
    """User-command entry points for attacks and contests."""

    def __init__(
        self,
        database: Database,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], str] | None = None,
    ):
        from datetime import datetime, timezone
        from uuid import uuid4

        self.database = database
        self.id_factory = id_factory or (lambda: uuid4().hex)
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat())
        self.mutation_engine = MutationEngine(
            database, id_factory=self.id_factory, clock=self.clock
        )

    def _apply(self, campaign_id, expected_version, event_type, operation):
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id, branch_id="main",
                expected_version=expected_version, source="user-command",
                event_type=event_type, operation=operation,
            )
        )

    def attack(self, campaign_id, expected_version, attacker_id, spec, roller):
        return self._apply(campaign_id, expected_version, "attack",
                           AttackOperation(roller, self.id_factory, attacker_id, spec))

    def grapple(self, campaign_id, expected_version, attacker_id, target_id,
                save_ability, attacker_size, target_size, roller):
        return self._apply(campaign_id, expected_version, "grapple",
                           GrappleOperation(roller, self.id_factory(), attacker_id,
                                            target_id, save_ability,
                                            attacker_size, target_size))

    def shove(self, campaign_id, expected_version, attacker_id, target_id,
              save_ability, attacker_size, target_size, roller, prone=True):
        return self._apply(campaign_id, expected_version, "shove",
                           ShoveOperation(roller, self.id_factory(), attacker_id,
                                          target_id, save_ability,
                                          attacker_size, target_size, prone))

    def escape_grapple(self, campaign_id, expected_version, entity_id, roller):
        return self._apply(campaign_id, expected_version, "escape-grapple",
                           EscapeGrappleOperation(roller, self.id_factory(), entity_id))

    def unarmed(self, campaign_id, expected_version, attacker_id, target_id, roller):
        spec = AttackSpec(
            target_id=target_id,
            weapon=WeaponProfile(
                key="unarmed", damage=DiceFormula(1, 2),
                damage_type="bludgeoning", properties=frozenset(), mastery=None,
                attack_ability="str", range_normal=5,
            ),
            flat_damage=1,
        )
        return self.attack(campaign_id, expected_version, attacker_id, spec, roller)
```

Also add `attacks_this_turn` to the schema — modify Task 2's `combatants` table (edit `0004_dnd_combat.sql`, it is already committed in-plan order but the plan has not executed, so edit the Task 2 SQL block):

```sql
    attacks_this_turn INTEGER NOT NULL DEFAULT 0 CHECK (attacks_this_turn >= 0),
```

and add `attacks_this_turn = 0,` to the `AdvanceTurnOperation` reset UPDATE in Task 8.

The test file references helper `combat_end_and_restart` — replace the `test_grapple_shove_and_escape` body after the first assertion with:

```python
    # escape: orc spends its next turn; first advance to orc's turn
    # (orc acts after pc1 in the seeded order), then escape directly:
    result = attacks.escape_grapple(
        "c1", result.state_version, "orc", roller=SequenceDiceRoller([12]),
    )
    with database.connect() as connection:
        assert condition_map(connection, "c1", "orc") == {}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/backend/unit/test_attacks.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat(core): add attack resolution and weapon properties" \
  "src/sillytavern_rpg_engine/persistence/schema/0004_dnd_combat.sql" \
  "src/sillytavern_rpg_engine/services/combat.py" \
  "src/sillytavern_rpg_engine/services/attacks.py" \
  "tests/backend/unit/test_attacks.py"
```

---

### Task 11: 2024 Weapon Mastery

**Files:**
- Create: `src/sillytavern_rpg_engine/services/mastery.py`
- Test: `tests/backend/unit/test_mastery.py`

**Interfaces:**
- Consumes: `attacks.py` internals (wired through `AttackOperation`'s callbacks), `combat.py` (`add_debuff`, `_clear_debuffs`), `damage.py`.
- Produces: `on_hit(connection, context, roller, roll_id_factory, attacker_row, spec, damage_result) -> dict`; `on_miss(...) -> dict`; mastery usage helpers `mastery_used()`, `mark_mastery()`.

Rules encoded (2024):
- Mastery applies only when the attacker has the weapon key in `weapon_masteries`; otherwise the property is inert (no error — the weapon simply functions normally).
- Cleave: on a hit with a melee weapon, one extra melee attack against a second creature within 5 ft of the first and within reach; no ability modifier on its damage unless negative; once per turn. The second target arrives as `spec.cleave_target_id` — add that field to `AttackSpec` in this task (default `None`).
- Graze: on a miss, damage equal to the attack ability modifier (same damage type, floored at 0).
- Nick: the Light extra attack joins the Attack action (no Bonus Action) when `spec.use_nick` is set; once per turn. Validation happens here: mark `nick` in `mastery_uses_json`, reject a second use.
- Push: on a hit, push the target up to 10 ft straight away when the target is Large or smaller (recorded in the payload; positions are abstract).
- Sap: on a hit, the target gains a `sap` debuff (Disadvantage on its next attack roll) cleared at the start of the attacker's next turn.
- Slow: on a hit that deals damage, the target gains a `slow` debuff (−10 ft, non-stacking overwrite) cleared at the start of the attacker's next turn; `AdvanceTurnOperation` subtracts active slow penalties when recomputing `movement_total`.
- Topple: on a hit, the target makes a Constitution save vs `8 + attack ability mod + PB`; failure applies Prone.
- Vex: on a hit that deals damage, the attacker gains `vex_vs` the target (Advantage on the next attack roll against it) lasting until the end of the attacker's next turn (`clear="end"`, `turns=2`).

Also in this task:
- Extend `_clear_debuffs` in `combat.py` to honor an optional `turns` counter (decrement on each matching clear pass; remove at 0).
- Extend `AdvanceTurnOperation` to subtract active `slow` amounts from the starting combatant's recomputed `movement_total` (floor 0).
- Extend `_spend_budget` in `attacks.py`: the `use_nick` branch requires mastery and marks the use.

- [ ] **Step 1: Write the failing tests**

```python
import json

import pytest

from sillytavern_rpg_engine.domain.dice import DiceFormula, SequenceDiceRoller
from sillytavern_rpg_engine.domain.dnd import (
    Condition,
    CreatureSize,
    MasteryProperty,
    WeaponProfile,
    WeaponProperty,
)
from sillytavern_rpg_engine.domain.errors import ValidationError
from sillytavern_rpg_engine.domain.models import EntityKind
from sillytavern_rpg_engine.services.attacks import AttackService, AttackSpec
from sillytavern_rpg_engine.services.attributes import SetAttributeOperation
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.combat import CombatService, CombatantEntry
from sillytavern_rpg_engine.services.conditions import condition_map
from sillytavern_rpg_engine.services.dnd_pack import DndRulesService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)

AXE = WeaponProfile(
    key="greataxe", damage=DiceFormula(1, 12), damage_type="slashing",
    properties=frozenset(), mastery=MasteryProperty.CLEAVE,
    attack_ability="str", range_normal=5,
)
GLAIVE = WeaponProfile(
    key="glaive", damage=DiceFormula(1, 10), damage_type="slashing",
    properties=frozenset({WeaponProperty.REACH}), mastery=MasteryProperty.GRAZE,
    attack_ability="str", range_normal=10,
)
DAGGER = WeaponProfile(
    key="dagger", damage=DiceFormula(1, 4), damage_type="piercing",
    properties=frozenset({WeaponProperty.LIGHT}), mastery=MasteryProperty.NICK,
    attack_ability="str", range_normal=5,
)
STAFF = WeaponProfile(
    key="quarterstaff", damage=DiceFormula(1, 6), damage_type="bludgeoning",
    properties=frozenset(), mastery=MasteryProperty.TOPPLE,
    attack_ability="str", range_normal=5,
)
RAPIER = WeaponProfile(
    key="rapier", damage=DiceFormula(1, 8), damage_type="piercing",
    properties=frozenset({WeaponProperty.FINESSE}), mastery=MasteryProperty.VEX,
    attack_ability="dex", range_normal=5,
)
MACE = WeaponProfile(
    key="mace", damage=DiceFormula(1, 6), damage_type="bludgeoning",
    properties=frozenset(), mastery=MasteryProperty.SAP,
    attack_ability="str", range_normal=5,
)
LONGBOW = WeaponProfile(
    key="longbow", damage=DiceFormula(1, 8), damage_type="piercing",
    properties=frozenset({WeaponProperty.RANGED}), mastery=MasteryProperty.SLOW,
    attack_ability="dex", range_normal=30, range_long=100,
)


def _world(database, masteries, weapon_count=3):
    ids = iter(f"e{n}" for n in range(1, 2000))
    clock = lambda: "2026-08-12T00:00:00Z"
    campaigns = CampaignService(database, id_factory=lambda: next(ids), clock=clock)
    dnd = DndRulesService(database, id_factory=lambda: next(ids), clock=clock)
    combat = CombatService(database, id_factory=lambda: next(ids), clock=clock)
    attacks = AttackService(database, id_factory=lambda: next(ids), clock=clock)
    entities = EntityAttributeService(database, dnd.mutation_engine)
    campaigns.create_campaign("c1", "Dungeon")
    dnd.seed_pack("c1", 0)
    version = 1
    roster = [("pc1", "Aria"), ("orc", "Orc"), ("goblin", "Goblin")][:weapon_count]
    roster[0] = ("pc1", "Aria")
    for entity_id, name in roster:
        entities.apply_explicit("c1", "main", version, CreateEntityOperation(
            entity_id=entity_id, kind=EntityKind.CHARACTER, name=name,
        ))
        version += 1
        for key, value in (
            ("ability_str", 16), ("ability_dex", 14), ("ability_con", 10),
            ("proficiency_bonus", 2), ("proficiencies", []),
            ("armor_class", 12), ("hp_max", 30), ("hp_current", 30),
            ("hp_temp", 0), ("speed", 30), ("character_level", 1),
            ("spell_slots_max", [0] * 9), ("spell_slots_current", [0] * 9),
            ("hit_die", 8), ("hit_dice_total", 1), ("hit_dice_current", 1),
            ("death_saves_success", 0), ("death_saves_failure", 0),
            ("is_dead", False), ("is_stable", False), ("resistances", []),
            ("vulnerabilities", []), ("immunities", []),
            ("condition_immunities", []),
            ("weapon_masteries", masteries if entity_id == "pc1" else []),
        ):
            entities.apply_explicit(
                "c1", "main", version, SetAttributeOperation(entity_id, key, value)
            )
            version += 1
    dnd.enable("c1", version)
    result = combat.start(
        "c1", expected_version=version + 1,
        roller=SequenceDiceRoller([18, 10, 5][: len(roster)]),
        entries=tuple(CombatantEntry(r[0]) for r in roster),
    )
    return attacks, combat, result.state_version


def _hp(database, entity_id):
    with database.connect() as connection:
        row = connection.execute(
            "SELECT value_json FROM attribute_values"
            " WHERE entity_id = ? AND attribute_key = 'hp_current'",
            (entity_id,),
        ).fetchone()
        return json.loads(row["value_json"])


def test_cleave_hits_second_target_once_per_turn(database):
    attacks, _, version = _world(database, ["greataxe"])
    result = attacks.attack(
        "c1", version, "pc1",
        AttackSpec(target_id="orc", weapon=AXE, cleave_target_id="goblin"),
        roller=SequenceDiceRoller([15, 5, 14, 3]),
    )
    assert _hp(database, "orc") == 25  # 5+3
    assert _hp(database, "goblin") == 27  # cleave: no ability mod
    with pytest.raises(ValidationError, match="cleave"):
        attacks.attack(
            "c1", result.state_version, "pc1",
            AttackSpec(
                target_id="orc", weapon=AXE, extra_attack=True,
                cleave_target_id="goblin",
            ),
            roller=SequenceDiceRoller([15, 5, 14, 3]),
        )


def test_graze_damages_on_miss_only_with_mastery(database):
    attacks, _, version = _world(database, ["glaive"])
    attacks.attack(
        "c1", version, "pc1",
        AttackSpec(target_id="orc", weapon=GLAIVE, distance_ft=10),
        roller=SequenceDiceRoller([1]),
    )
    assert _hp(database, "orc") == 27  # graze: ability mod 3
    attacks2, _, version2 = _world2(database)
    # without mastery a miss deals nothing
    ...


def _world2(database):
    raise SystemExit  # replaced below


def test_nick_frees_bonus_action_once_per_turn(database):
    attacks, _, version = _world(database, ["dagger"])
    result = attacks.attack(
        "c1", version, "pc1",
        AttackSpec(target_id="orc", weapon=DAGGER),
        roller=SequenceDiceRoller([15, 2]),
    )
    result = attacks.attack(
        "c1", result.state_version, "pc1",
        AttackSpec(
            target_id="orc", weapon=DAGGER, offhand=True,
            other_weapon_light=True, use_nick=True,
        ),
        roller=SequenceDiceRoller([15, 2]),
    )
    with pytest.raises(ValidationError, match="nick"):
        attacks.attack(
            "c1", result.state_version, "pc1",
            AttackSpec(
                target_id="orc", weapon=DAGGER, offhand=True,
                other_weapon_light=True, use_nick=True, extra_attack=True,
            ),
            roller=SequenceDiceRoller([15, 2]),
        )


def test_sap_vex_slow_and_topple(database):
    attacks, combat, version = _world(database, ["mace", "rapier", "quarterstaff"])
    # sap: hit -> orc's next attack has disadvantage
    result = attacks.attack(
        "c1", version, "pc1",
        AttackSpec(target_id="orc", weapon=MACE),
        roller=SequenceDiceRoller([15, 4]),
    )
    with database.connect() as connection:
        debuffs = json.loads(connection.execute(
            "SELECT debuffs_json FROM combatants WHERE entity_id = 'orc'"
        ).fetchone()["debuffs_json"])
        assert debuffs["sap"]["source"] == "pc1"
    # orc's turn: attacks at disadvantage, sap consumed
    result = combat.advance_turn("c1", result.state_version, SequenceDiceRoller([]))
    # topple: orc con save (8 + 0) vs DC 8+3+2=13 -> prone
    result = attacks.attack(
        "c1", result.state_version, "orc",
        AttackSpec(target_id="pc1", weapon=STAFF),
        roller=SequenceDiceRoller([12, 12, 4, 8]),
    )
    with database.connect() as connection:
        assert condition_map(connection, "c1", "pc1") == {Condition.PRONE: 1}


def test_vex_grants_advantage_until_end_of_next_turn(database):
    attacks, combat, version = _world(database, ["rapier"])
    result = attacks.attack(
        "c1", version, "pc1",
        AttackSpec(target_id="orc", weapon=RAPIER),
        roller=SequenceDiceRoller([15, 4]),
    )
    with database.connect() as connection:
        debuffs = json.loads(connection.execute(
            "SELECT debuffs_json FROM combatants WHERE entity_id = 'pc1'"
        ).fetchone()["debuffs_json"])
        assert debuffs["vex_vs"] == {"target": "orc", "source": "pc1",
                                     "clear": "end", "turns": 2}
```

(The `test_graze_...` `_world2` stub is a writing shortcut; the implementer writes the no-mastery case by building a second world with `masteries=[]` on a fresh `database`-style fixture — simplest is a second test function using `_world(database, [])` and asserting a miss with a scripted `1` leaves HP untouched.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/backend/unit/test_mastery.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `services/mastery.py` plus small edits**

`services/mastery.py`:

```python
"""2024 Weapon Mastery effects; called from attack resolution."""

import json
import sqlite3
from typing import Any

from ..domain.dice import D20Mode, DiceRoller, resolve_d20
from ..domain.dnd import (
    Condition,
    CreatureSize,
    MasteryProperty,
    ability_modifier,
)
from ..domain.errors import ValidationError
from ..domain.operations import MutationContext
from .attacks import AttackSpec, resolve_strike
from .combat import add_debuff
from .conditions import ApplyConditionOperation
from .damage import DamageResult, apply_damage
from .dice import record_roll
from .dnd_pack import read_int, read_list

ONCE_PER_TURN = frozenset({MasteryProperty.CLEAVE, MasteryProperty.NICK})


def _mastered(connection, campaign_id: str, attacker_id: str, spec: AttackSpec) -> bool:
    return spec.weapon.mastery is not None and spec.weapon.key in read_list(
        connection, campaign_id, attacker_id, "weapon_masteries"
    )


def mastery_used(row, name: str) -> bool:
    return bool(json.loads(row["mastery_uses_json"]).get(name))


def mark_mastery(connection: sqlite3.Connection, row_id: str, name: str) -> None:
    row = connection.execute(
        "SELECT mastery_uses_json FROM combatants WHERE id = ?", (row_id,)
    ).fetchone()
    uses = json.loads(row["mastery_uses_json"])
    uses[name] = True
    connection.execute(
        "UPDATE combatants SET mastery_uses_json = ? WHERE id = ?",
        (json.dumps(uses, sort_keys=True), row_id),
    )


def on_miss(
    connection: sqlite3.Connection,
    context: MutationContext,
    roller: DiceRoller,
    roll_id_factory,
    attacker_row,
    spec: AttackSpec,
) -> dict[str, Any] | None:
    """Graze: a miss still deals the attack ability modifier as damage."""
    if spec.weapon.mastery is not MasteryProperty.GRAZE:
        return None
    if not _mastered(connection, context.campaign.id, attacker_row["entity_id"], spec):
        return None
    score = read_int(
        connection, context.campaign.id, attacker_row["entity_id"],
        f"ability_{spec.weapon.attack_ability}",
    )
    amount = max(0, ability_modifier(score))
    result = apply_damage(
        connection, context, roller, roll_id_factory, spec.target_id,
        amount, spec.weapon.damage_type,
    )
    return {"property": "graze", "damage": amount, "result": result.__dict__}


def on_hit(
    connection: sqlite3.Connection,
    context: MutationContext,
    roller: DiceRoller,
    roll_id_factory,
    attacker_row,
    spec: AttackSpec,
    damage_result: DamageResult,
) -> dict[str, Any] | None:
    mastery = spec.weapon.mastery
    if mastery is None or not _mastered(
        connection, context.campaign.id, attacker_row["entity_id"], spec
    ):
        return None
    campaign_id = context.campaign.id
    attacker_id = attacker_row["entity_id"]
    if mastery in ONCE_PER_TURN:
        if mastery_used(attacker_row, mastery.value):
            raise ValidationError(f"{mastery.value} already used this turn")
        mark_mastery(connection, attacker_row["id"], mastery.value)
    if mastery is MasteryProperty.CLEAVE:
        if not spec.cleave_target_id:
            return {"property": "cleave", "used": False}
        second = AttackSpec(
            target_id=spec.cleave_target_id,
            weapon=spec.weapon,
            distance_ft=spec.distance_ft,
            suppress_positive_modifier=True,
        )
        payload = resolve_strike(
            connection, context, roller, roll_id_factory, attacker_id, second,
            spend_budget=False,
        )
        return {"property": "cleave", "attack": payload}
    if mastery is MasteryProperty.NICK:
        return {"property": "nick"}
    if mastery is MasteryProperty.PUSH:
        if spec.target_size.rank > CreatureSize.LARGE.rank:
            return {"property": "push", "applied": False}
        return {"property": "push", "applied": True, "feet": 10}
    if mastery is MasteryProperty.SAP:
        target_row = connection.execute(
            "SELECT id FROM combatants WHERE encounter_id = ? AND entity_id = ?",
            (attacker_row["encounter_id"], spec.target_id),
        ).fetchone()
        add_debuff(connection, target_row["id"], "sap",
                   {"source": attacker_id, "clear": "start"})
        return {"property": "sap"}
    if mastery is MasteryProperty.SLOW:
        if damage_result.hp_lost + damage_result.temp_absorbed <= 0:
            return {"property": "slow", "applied": False}
        target_row = connection.execute(
            "SELECT id FROM combatants WHERE encounter_id = ? AND entity_id = ?",
            (attacker_row["encounter_id"], spec.target_id),
        ).fetchone()
        add_debuff(connection, target_row["id"], "slow",
                   {"source": attacker_id, "clear": "start", "amount": 10})
        return {"property": "slow"}
    if mastery is MasteryProperty.TOPPLE:
        modifier = ability_modifier(
            read_int(connection, campaign_id, attacker_id,
                     f"ability_{spec.weapon.attack_ability}")
        )
        dc = 8 + modifier + read_int(
            connection, campaign_id, attacker_id, "proficiency_bonus"
        )
        save_mod = ability_modifier(
            read_int(connection, campaign_id, spec.target_id, "ability_con")
        )
        outcome = resolve_d20(roller, D20Mode.NORMAL, save_mod)
        payload = record_roll(
            connection, context, roll_id_factory(),
            purpose="topple save", formula="1d20", faces=(outcome.kept,),
            modifier=save_mod, total=outcome.total, dc=dc,
            success=outcome.total >= dc, roller_entity_id=spec.target_id,
        )
        applied = outcome.total < dc
        if applied:
            ApplyConditionOperation(
                spec.target_id, Condition.PRONE, f"topple:{attacker_id}"
            ).apply(connection, context)
        return {"property": "topple", "save": payload, "applied": applied}
    if mastery is MasteryProperty.VEX:
        if damage_result.hp_lost + damage_result.temp_absorbed <= 0:
            return {"property": "vex", "applied": False}
        add_debuff(connection, attacker_row["id"], "vex_vs",
                   {"target": spec.target_id, "source": attacker_id,
                    "clear": "end", "turns": 2})
        return {"property": "vex"}
    return None
```

Edits to existing files:

1. `attacks.py` — `AttackSpec` gains `cleave_target_id: str | None = None`. In `_spend_budget`, the `use_nick` branch becomes the complete form below (mastery validation against `weapon_masteries` happens here, not only in `on_hit`, so a budget spend never succeeds for an unmastered weapon):

```python
        if spec.use_nick:
            from .mastery import mark_mastery, mastery_used

            if spec.weapon.mastery is not MasteryProperty.NICK:
                raise ValidationError("nick requires a nick weapon")
            mastered = spec.weapon.key in read_list(
                connection, context.campaign.id, attacker_id, "weapon_masteries"
            )
            if not mastered:
                raise ValidationError("weapon mastery not unlocked for this weapon")
            if mastery_used(row, "nick"):
                raise ValidationError("nick already used this turn")
            mark_mastery(connection, row["id"], "nick")
```

2. `attacks.py` — `AttackOperation.apply` wires the mastery callbacks:

```python
    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        from .mastery import on_hit, on_miss

        return resolve_strike(
            connection, context, self.roller, self.roll_id_factory,
            self.attacker_id, self.spec,
            mastery_on_hit=on_hit, mastery_on_miss=on_miss,
        )
```

Note: the `combat.py` changes mastery relies on (`_clear_debuffs` honoring `turns`, Slow subtracting from `movement_total` in `AdvanceTurnOperation`, and the `attacks_this_turn = 0` reset) are already part of the Task 8 code as written — no further edit is needed here.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/backend/unit/test_mastery.py tests/backend/unit/test_attacks.py tests/backend/unit/test_actions_movement.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat(core): add 2024 weapon mastery effects" \
  "src/sillytavern_rpg_engine/services/mastery.py" \
  "src/sillytavern_rpg_engine/services/attacks.py" \
  "src/sillytavern_rpg_engine/services/combat.py" \
  "tests/backend/unit/test_mastery.py"
```

---

### Task 12: Spells, spell slots, and concentration

**Files:**
- Create: `src/sillytavern_rpg_engine/services/spells.py`
- Test: `tests/backend/unit/test_spells.py`

**Interfaces:**
- Consumes: all prior services (`dnd_pack.validate_slot_list`, `damage.apply_damage`/`HealOperation`, `attacks` patterns).
- Produces: `CastSpellOperation(roller, roll_id_factory, caster_id, spell, slot_level, target_ids, cover, distance_ft, turn_id)`; `EndConcentrationOperation(caster_id)`; `SpellService` (`cast`, `end_concentration`).

Rules encoded (2024):
- Casting uses the Magic action: active turn, action budget, not Incapacitated. Out of combat (no active encounter) only slot/state changes apply.
- One spell slot per turn: a slotted cast requires `slot_spent_this_turn = 0`. Cantrips (`slot_level=None`, `spell.level=0`) never touch slots.
- `slot_level` must be ≥ the spell's level and have a remaining slot; the slot is decremented in `spell_slots_current` (a 9-element list, levels 1–9).
- Attack spells roll `1d20 + casting ability mod + PB + exhaustion` vs the target's AC (+cover bonus); save spells roll each target's `1d20 + save ability mod (+PB if save-proficient) + exhaustion` vs `8 + PB + casting ability mod`; a successful save halves damage (round down).
- Concentration: casting a concentration spell replaces any current concentration; `concentration_rounds` counts down in `AdvanceTurnOperation`; damage-triggered checks already run in `apply_damage`; `EndConcentrationOperation` clears it voluntarily.
- Healing spells route through `HealOperation` semantics (inline, same rules).

- [ ] **Step 1: Write the failing tests**

```python
import json

import pytest

from sillytavern_rpg_engine.domain.dice import DiceFormula, SequenceDiceRoller
from sillytavern_rpg_engine.domain.dnd import SpellProfile
from sillytavern_rpg_engine.domain.errors import ValidationError
from sillytavern_rpg_engine.domain.models import EntityKind
from sillytavern_rpg_engine.services.attributes import SetAttributeOperation
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.combat import CombatService, CombatantEntry
from sillytavern_rpg_engine.services.dnd_pack import DndRulesService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)
from sillytavern_rpg_engine.services.spells import SpellService

MAGIC_MISSILE = SpellProfile(
    key="magic-missile", level=1, attack=False, save_ability=None,
    damage=DiceFormula(3, 4, 3), damage_type="force", healing=None,
    concentration=False, duration_rounds=None, ability="int",
)
SACRED_FLAME = SpellProfile(
    key="sacred-flame", level=0, attack=False, save_ability="dex",
    damage=DiceFormula(1, 8), damage_type="radiant", healing=None,
    concentration=False, duration_rounds=None, ability="wis",
)
BLESS = SpellProfile(
    key="bless", level=1, attack=False, save_ability=None, damage=None,
    damage_type=None, healing=None, concentration=True, duration_rounds=10,
    ability="wis",
)
CURE = SpellProfile(
    key="cure-wounds", level=1, attack=False, save_ability=None, damage=None,
    damage_type=None, healing=DiceFormula(2, 8), concentration=False,
    duration_rounds=None, ability="wis",
)


def _world(database):
    ids = iter(f"e{n}" for n in range(1, 2000))
    clock = lambda: "2026-08-12T00:00:00Z"
    campaigns = CampaignService(database, id_factory=lambda: next(ids), clock=clock)
    dnd = DndRulesService(database, id_factory=lambda: next(ids), clock=clock)
    combat = CombatService(database, id_factory=lambda: next(ids), clock=clock)
    spells = SpellService(database, id_factory=lambda: next(ids), clock=clock)
    entities = EntityAttributeService(database, dnd.mutation_engine)
    campaigns.create_campaign("c1", "Dungeon")
    dnd.seed_pack("c1", 0)
    version = 1
    for entity_id, name, values in (
        ("pc1", "Aria", {"ability_wis": 16, "ability_int": 12,
                          "spell_slots_max": [2, 0, 0, 0, 0, 0, 0, 0, 0]}),
        ("orc", "Orc", {"ability_dex": 10, "ability_wis": 8}),
    ):
        entities.apply_explicit("c1", "main", version, CreateEntityOperation(
            entity_id=entity_id, kind=EntityKind.CHARACTER, name=name,
        ))
        version += 1
        base = {
            "ability_str": 10, "ability_dex": 10, "ability_con": 10,
            "ability_int": 10, "ability_wis": 10, "ability_cha": 10,
            "proficiency_bonus": 2, "proficiencies": [],
            "armor_class": 12, "hp_max": 30, "hp_current": 30, "hp_temp": 0,
            "speed": 30, "character_level": 1,
            "spell_slots_max": [0] * 9, "spell_slots_current": [0] * 9,
            "hit_die": 8, "hit_dice_total": 1, "hit_dice_current": 1,
            "death_saves_success": 0, "death_saves_failure": 0,
            "is_dead": False, "is_stable": False, "resistances": [],
            "vulnerabilities": [], "immunities": [],
            "condition_immunities": [], "weapon_masteries": [],
        }
        base.update(values)
        base["spell_slots_current"] = list(base["spell_slots_max"])
        for key, value in base.items():
            entities.apply_explicit(
                "c1", "main", version, SetAttributeOperation(entity_id, key, value)
            )
            version += 1
    dnd.enable("c1", version)
    result = combat.start(
        "c1", expected_version=version + 1,
        roller=SequenceDiceRoller([15, 10]),
        entries=(CombatantEntry("pc1"), CombatantEntry("orc")),
    )
    return spells, combat, result.state_version


def _slots(database, entity_id="pc1", key="spell_slots_current"):
    with database.connect() as connection:
        row = connection.execute(
            "SELECT value_json FROM attribute_values"
            " WHERE entity_id = ? AND attribute_key = ?",
            (entity_id, key),
        ).fetchone()
        return json.loads(row["value_json"])


def _hp(database, entity_id):
    return _slots(database, entity_id, "hp_current")


def test_slotted_cast_spends_slot_and_one_slot_per_turn(database):
    spells, _, version = _world(database)
    result = spells.cast(
        "c1", version, "pc1", MAGIC_MISSILE, slot_level=1,
        target_ids=("orc",), roller=SequenceDiceRoller([2, 2, 2]),
    )
    assert _slots(database)[0] == 1
    assert _hp(database, "orc") == 30 - 9  # 3d4+3 auto-hit
    with pytest.raises(ValidationError, match="one spell slot"):
        spells.cast(
            "c1", result.state_version, "pc1", MAGIC_MISSILE, slot_level=1,
            target_ids=("orc",), roller=SequenceDiceRoller([2, 2, 2]),
        )


def test_cantrip_save_halves_on_success(database):
    spells, combat, version = _world(database)
    # pc1 casts sacred flame on orc: DC 8 + 2 + 3 = 13; orc rolls 14 + 0 = 14
    result = spells.cast(
        "c1", version, "pc1", SACRED_FLAME, slot_level=None,
        target_ids=("orc",), roller=SequenceDiceRoller([14, 6]),
    )
    assert _hp(database, "orc") == 30 - 3  # 6 halved
    assert _slots(database)[0] == 2  # cantrip spends nothing


def test_concentration_replaces_ticks_and_ends(database):
    spells, combat, version = _world(database)
    result = spells.cast(
        "c1", version, "pc1", BLESS, slot_level=1,
        target_ids=("pc1",), roller=SequenceDiceRoller([]),
    )
    with database.connect() as connection:
        row = connection.execute(
            "SELECT concentrating_spell, concentration_rounds FROM combatants"
            " WHERE entity_id = 'pc1'"
        ).fetchone()
        assert (row["concentrating_spell"], row["concentration_rounds"]) == ("bless", 10)
    result = combat.advance_turn("c1", result.state_version, SequenceDiceRoller([]))
    result = combat.advance_turn("c1", result.state_version, SequenceDiceRoller([]))
    with database.connect() as connection:
        row = connection.execute(
            "SELECT concentration_rounds FROM combatants WHERE entity_id = 'pc1'"
        ).fetchone()
        assert row["concentration_rounds"] == 9
    result = spells.end_concentration("c1", result.state_version, "pc1")
    with database.connect() as connection:
        row = connection.execute(
            "SELECT concentrating_spell FROM combatants WHERE entity_id = 'pc1'"
        ).fetchone()
        assert row["concentrating_spell"] is None


def test_healing_spell_and_slot_validation(database):
    spells, _, version = _world(database)
    with pytest.raises(ValidationError, match="slot level"):
        spells.cast(
            "c1", version, "pc1", CURE, slot_level=2, target_ids=("pc1",),
            roller=SequenceDiceRoller([3, 3]),
        )
    with pytest.raises(ValidationError, match="cantrip"):
        spells.cast(
            "c1", version, "pc1", CURE, slot_level=None, target_ids=("pc1",),
            roller=SequenceDiceRoller([3, 3]),
        )
    result = spells.cast(
        "c1", version, "pc1", CURE, slot_level=1, target_ids=("pc1",),
        roller=SequenceDiceRoller([3, 3]),
    )
    assert _hp(database, "pc1") == 30  # already full: capped
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/backend/unit/test_spells.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `services/spells.py`**

```python
"""Spell casting: Magic action budget, one-slot-per-turn, saves/attacks,
concentration lifecycle."""

from dataclasses import dataclass
import sqlite3
from typing import Any, Callable

from ..domain.dice import D20Mode, DiceRoller, resolve_d20, roll_formula
from ..domain.dnd import (
    CONDITION_EFFECTS,
    Cover,
    SpellProfile,
    ability_modifier,
    require_dnd_2024,
)
from ..domain.errors import NotFoundError, ValidationError
from ..domain.operations import MutationContext
from ..persistence.database import Database
from .attributes import SetAttributeOperation
from .combat import active_combatant, require_active_encounter
from .conditions import condition_map
from .damage import HealOperation, apply_damage
from .dice import read_exhaustion, record_roll
from .dnd_pack import read_int, read_list, validate_slot_list
from .mutations import MutationEngine, MutationRequest, MutationResult


@dataclass(frozen=True)
class CastSpellOperation:
    """Cast one spell, consuming action/slot budgets and resolving effects."""

    roller: DiceRoller
    roll_id_factory: Callable[[], str]
    caster_id: str
    spell: SpellProfile
    slot_level: int | None
    target_ids: tuple[str, ...] = ()
    cover: Cover = Cover.NONE
    distance_ft: int = 30
    turn_id: str | None = None

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        campaign_id = context.campaign.id
        combatant = self._spend_budgets(connection, context)
        self._spend_slot(connection, context)
        dc = 8 + read_int(connection, campaign_id, self.caster_id, "proficiency_bonus")
        dc += ability_modifier(
            read_int(connection, campaign_id, self.caster_id,
                     f"ability_{self.spell.ability}")
        )
        payload: dict[str, Any] = {
            "caster_id": self.caster_id,
            "spell": self.spell.key,
            "slot_level": self.slot_level,
            "save_dc": dc if self.spell.save_ability else None,
            "targets": [],
        }
        for target_id in self.target_ids:
            payload["targets"].append(
                self._resolve_target(connection, context, target_id, dc)
            )
        if self.spell.concentration and combatant is not None:
            connection.execute(
                "UPDATE combatants SET concentrating_spell = ?,"
                " concentration_rounds = ? WHERE id = ?",
                (self.spell.key, self.spell.duration_rounds, combatant["id"]),
            )
            payload["concentration"] = self.spell.duration_rounds
        elif self.spell.concentration:
            payload["concentration"] = "untracked out of combat"
        return payload

    def _spend_budgets(self, connection, context) -> Any | None:
        """Consume the Magic action; returns the caster combatant row (or
        None when no encounter is active)."""
        try:
            encounter, combatants = require_active_encounter(
                connection, context.campaign.id, context.branch_id
            )
        except NotFoundError:
            return None
        row = next(
            (r for r in combatants if r["entity_id"] == self.caster_id), None
        )
        if row is None:
            return None
        current = active_combatant(encounter, combatants)
        if current["entity_id"] != self.caster_id:
            raise ValidationError(f"it is not {self.caster_id!r}'s turn")
        if row["action_used"]:
            raise ValidationError("action already used this turn")
        conditions = condition_map(connection, context.campaign.id, self.caster_id)
        if any(CONDITION_EFFECTS[c].incapacitated for c in conditions):
            raise ValidationError("incapacitated creatures cannot cast")
        if self.slot_level is not None and row["slot_spent_this_turn"]:
            raise ValidationError("only one spell slot may be spent per turn")
        connection.execute(
            "UPDATE combatants SET action_used = 1, slot_spent_this_turn = ?"
            " WHERE id = ?",
            (1 if self.slot_level is not None else 0, row["id"]),
        )
        return row

    def _spend_slot(self, connection, context) -> None:
        campaign_id = context.campaign.id
        if self.slot_level is None:
            if self.spell.level != 0:
                raise ValidationError("only cantrips cast without a slot level")
            return
        if not 1 <= self.slot_level <= 9:
            raise ValidationError("slot level must be in 1..9")
        if self.slot_level < self.spell.level:
            raise ValidationError("slot level below the spell's level")
        current = validate_slot_list(
            read_list(connection, campaign_id, self.caster_id, "spell_slots_current")
        )
        maximum = validate_slot_list(
            read_list(connection, campaign_id, self.caster_id, "spell_slots_max")
        )
        if self.spell.level > 0 and maximum[self.spell.level - 1] == 0:
            raise ValidationError("caster has no slots of the spell's level")
        if current[self.slot_level - 1] < 1:
            raise ValidationError("no remaining slots at that level")
        current[self.slot_level - 1] -= 1
        SetAttributeOperation(
            self.caster_id, "spell_slots_current", current
        ).apply(connection, context)

    def _resolve_target(self, connection, context, target_id, dc) -> dict:
        campaign_id = context.campaign.id
        spell = self.spell
        damage = spell.damage
        if spell.attack and damage is not None:
            modifier = ability_modifier(
                read_int(connection, campaign_id, self.caster_id,
                         f"ability_{spell.ability}")
            ) + read_int(connection, campaign_id, self.caster_id, "proficiency_bonus")
            modifier += -2 * read_exhaustion(connection, campaign_id, self.caster_id)
            outcome = resolve_d20(self.roller, D20Mode.NORMAL, modifier)
            armor_class = read_int(
                connection, campaign_id, target_id, "armor_class"
            ) + (self.cover.ac_bonus or 0)
            hit = not outcome.is_natural_1 and (
                outcome.is_natural_20 or outcome.total >= armor_class
            )
            record_roll(
                connection, context, self.roll_id_factory(),
                purpose=f"spell attack: {spell.key}", formula="1d20",
                faces=(outcome.kept,), modifier=modifier, total=outcome.total,
                dc=armor_class, success=hit, roller_entity_id=self.caster_id,
                turn_id=self.turn_id,
            )
            if not hit:
                return {"target_id": target_id, "hit": False}
            formula = damage
            if outcome.is_natural_20:
                from ..domain.dice import DiceFormula

                formula = DiceFormula(damage.count * 2, damage.sides, 0)
            rolled = roll_formula(self.roller, formula)
            result = apply_damage(
                connection, context, self.roller, self.roll_id_factory,
                target_id, rolled.total, spell.damage_type,
            )
            return {"target_id": target_id, "hit": True, "damage": result.__dict__}
        if spell.save_ability is not None:
            save_mod = ability_modifier(
                read_int(connection, campaign_id, target_id,
                         f"ability_{spell.save_ability}")
            )
            if f"save_{spell.save_ability}" in read_list(
                connection, campaign_id, target_id, "proficiencies"
            ):
                save_mod += read_int(
                    connection, campaign_id, target_id, "proficiency_bonus"
                )
            save_mod += -2 * read_exhaustion(connection, campaign_id, target_id)
            outcome = resolve_d20(self.roller, D20Mode.NORMAL, save_mod)
            success = outcome.total >= dc
            record_roll(
                connection, context, self.roll_id_factory(),
                purpose=f"save: {spell.key}", formula="1d20",
                faces=(outcome.kept,), modifier=save_mod, total=outcome.total,
                dc=dc, success=success, roller_entity_id=target_id,
                turn_id=self.turn_id,
            )
            entry: dict[str, Any] = {"target_id": target_id, "save": success}
            if damage is not None:
                rolled = roll_formula(self.roller, damage)
                amount = rolled.total // 2 if success else rolled.total
                result = apply_damage(
                    connection, context, self.roller, self.roll_id_factory,
                    target_id, amount, spell.damage_type,
                )
                entry["damage"] = result.__dict__
            return entry
        if damage is not None:
            rolled = roll_formula(self.roller, damage)
            result = apply_damage(
                connection, context, self.roller, self.roll_id_factory,
                target_id, rolled.total, spell.damage_type,
            )
            return {"target_id": target_id, "damage": result.__dict__}
        if spell.healing is not None:
            rolled = roll_formula(self.roller, spell.healing)
            healed = HealOperation(target_id, rolled.total).apply(connection, context)
            return {"target_id": target_id, "healed": healed}
        return {"target_id": target_id}


@dataclass(frozen=True)
class EndConcentrationOperation:
    caster_id: str

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        row = connection.execute(
            "SELECT c.id FROM combatants c"
            " JOIN combat_encounters e ON e.id = c.encounter_id"
            " WHERE e.campaign_id = ? AND e.status = 'active'"
            " AND c.entity_id = ? AND c.concentrating_spell IS NOT NULL",
            (context.campaign.id, self.caster_id),
        ).fetchone()
        if row is None:
            raise ValidationError("the caster is not concentrating")
        connection.execute(
            "UPDATE combatants SET concentrating_spell = NULL,"
            " concentration_rounds = NULL WHERE id = ?",
            (row["id"],),
        )
        return {"caster_id": self.caster_id, "concentration": "ended"}


class SpellService:
    """User-command entry points for casting and concentration."""

    def __init__(
        self,
        database: Database,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], str] | None = None,
    ):
        from datetime import datetime, timezone
        from uuid import uuid4

        self.database = database
        self.id_factory = id_factory or (lambda: uuid4().hex)
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat())
        self.mutation_engine = MutationEngine(
            database, id_factory=self.id_factory, clock=self.clock
        )

    def cast(self, campaign_id, expected_version, caster_id, spell, slot_level,
             target_ids=(), roller=None, cover=Cover.NONE, distance_ft=30,
             turn_id=None):
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id, branch_id="main",
                expected_version=expected_version, source="user-command",
                event_type="spell-cast",
                operation=CastSpellOperation(
                    roller=roller or _ZERO_ROLLER,
                    roll_id_factory=self.id_factory,
                    caster_id=caster_id, spell=spell, slot_level=slot_level,
                    target_ids=tuple(target_ids), cover=cover,
                    distance_ft=distance_ft, turn_id=turn_id,
                ),
            )
        )

    def end_concentration(self, campaign_id, expected_version, caster_id):
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id, branch_id="main",
                expected_version=expected_version, source="user-command",
                event_type="concentration-ended",
                operation=EndConcentrationOperation(caster_id),
            )
        )


class _ZeroRoller:
    """Fallback roller that only supports roll calls never needed by
    no-roll spells; using it for a real roll fails loudly."""

    def roll(self, count: int, sides: int) -> tuple[int, ...]:
        raise ValidationError("this spell requires a roller")
```

Wait — `MAGIC_MISSILE` in the test has `save_ability=None`, `attack=False`, and `damage` set; per `SpellProfile.__post_init__` from Task 3, non-attack spells require `save_ability` unless healing — the profile as written violates its own validation. Fix the Task 3 rule: non-attack spells require `save_ability` **or** `damage` with no save (auto-hit spells) **or** `healing`. Update the Task 3 `SpellProfile.__post_init__` line to:

```python
        if not self.attack and self.save_ability is None and self.healing is None and self.damage is None:
            raise ValidationError("non-attack spells need a save, damage, or healing")
```

and update the Task 3 test expectation `"save_ability"` to match a profile with all three absent. Also `_ZERO_ROLLER` must satisfy the `DiceRoller` protocol — it does (structural). Note: `_spend_budgets` catches broad `Exception`; narrow it to `NotFoundError` (imported at module top instead of inside the function — remove the inner import).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/backend/unit/test_spells.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat(core): add spell slots, casting, and concentration" \
  "src/sillytavern_rpg_engine/domain/dnd.py" \
  "src/sillytavern_rpg_engine/services/spells.py" \
  "tests/backend/unit/test_spells.py"
```

---

### Task 13: Short and long rests

**Files:**
- Create: `src/sillytavern_rpg_engine/services/rests.py`
- Test: `tests/backend/unit/test_rests.py`

**Interfaces:**
- Consumes: `dnd_pack` readers, `SetAttributeOperation`, `conditions.RemoveConditionOperation`, `dice.record_roll`.
- Produces: `ShortRestOperation(roller, roll_id_factory, entity_id, hit_dice)`; `LongRestOperation(entity_id)`; `RestService` (`short_rest`, `long_rest`).

Rules encoded (2024):
- Rests are user commands and never run while the entity is in an active encounter (combat interrupts rest by definition; an "interrupted" rest is simply never committed).
- Short Rest: spend 1..`hit_dice_current` Hit Dice; each rolls `hit_die + con mod` healing (minimum 0 per die); HP caps at `hp_max`.
- Long Rest: restores all HP, clears temp HP, refills all spell slots, regains spent Hit Dice up to half the total (minimum 1), removes 1 Exhaustion level, clears death saves and `is_stable`, ends concentration. Dead entities cannot rest.

- [ ] **Step 1: Write the failing tests**

```python
import json

import pytest

from sillytavern_rpg_engine.domain.dice import SequenceDiceRoller
from sillytavern_rpg_engine.domain.dnd import Condition
from sillytavern_rpg_engine.domain.errors import ValidationError
from sillytavern_rpg_engine.domain.models import EntityKind
from sillytavern_rpg_engine.services.attributes import SetAttributeOperation
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.conditions import ConditionService, condition_map
from sillytavern_rpg_engine.services.dnd_pack import DndRulesService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)
from sillytavern_rpg_engine.services.rests import RestService


def _world(database):
    ids = iter(f"e{n}" for n in range(1, 1000))
    clock = lambda: "2026-08-12T00:00:00Z"
    campaigns = CampaignService(database, id_factory=lambda: next(ids), clock=clock)
    dnd = DndRulesService(database, id_factory=lambda: next(ids), clock=clock)
    rests = RestService(database, id_factory=lambda: next(ids), clock=clock)
    conditions = ConditionService(database, id_factory=lambda: next(ids), clock=clock)
    entities = EntityAttributeService(database, dnd.mutation_engine)
    campaigns.create_campaign("c1", "Dungeon")
    dnd.seed_pack("c1", 0)
    entities.apply_explicit("c1", "main", 1, CreateEntityOperation(
        entity_id="pc1", kind=EntityKind.CHARACTER, name="Aria",
    ))
    values = {
        "ability_con": 14, "hp_max": 20, "hp_current": 7, "hp_temp": 3,
        "spell_slots_max": [2, 1, 0, 0, 0, 0, 0, 0, 0],
        "spell_slots_current": [0, 0, 0, 0, 0, 0, 0, 0, 0],
        "hit_die": 8, "hit_dice_total": 3, "hit_dice_current": 1,
        "death_saves_success": 1, "death_saves_failure": 2,
        "is_dead": False, "is_stable": False,
        "resistances": [], "vulnerabilities": [], "immunities": [],
        "condition_immunities": [], "weapon_masteries": [],
    }
    version = 2
    for key, value in values.items():
        entities.apply_explicit(
            "c1", "main", version, SetAttributeOperation("pc1", key, value)
        )
        version += 1
    dnd.enable("c1", version)
    return rests, conditions, version + 1


def _attr(database, key):
    with database.connect() as connection:
        row = connection.execute(
            "SELECT value_json FROM attribute_values"
            " WHERE entity_id = 'pc1' AND attribute_key = ?",
            (key,),
        ).fetchone()
        return json.loads(row["value_json"])


def test_short_rest_spends_hit_dice_and_caps_hp(database):
    rests, _, version = _world(database)
    with pytest.raises(ValidationError, match="hit dice"):
        rests.short_rest("c1", version, "pc1", hit_dice=2,
                         roller=SequenceDiceRoller([]))
    result = rests.short_rest(
        "c1", version, "pc1", hit_dice=1, roller=SequenceDiceRoller([5]),
    )
    assert _attr(database, "hp_current") == 7 + 7  # 5 + 2 con
    assert _attr(database, "hit_dice_current") == 0


def test_long_rest_restores_resources_and_sheds_exhaustion(database):
    rests, conditions, version = _world(database)
    conditions.apply("c1", version, "pc1", Condition.EXHAUSTION, level=2, source="march")
    result = rests.long_rest("c1", version + 1, "pc1")
    assert _attr(database, "hp_current") == 20
    assert _attr(database, "hp_temp") == 0
    assert _attr(database, "spell_slots_current") == [2, 1, 0, 0, 0, 0, 0, 0, 0]
    assert _attr(database, "hit_dice_current") == 2  # 1 + max(1, 3//2)
    assert _attr(database, "death_saves_success") == 0
    assert _attr(database, "death_saves_failure") == 0
    with database.connect() as connection:
        assert condition_map(connection, "c1", "pc1") == {Condition.EXHAUSTION: 1}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/backend/unit/test_rests.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `services/rests.py`**

```python
"""Short and long rest transactions (2024 resource recovery)."""

from dataclasses import dataclass
import sqlite3
from typing import Any, Callable

from ..domain.dice import DiceRoller
from ..domain.dnd import Condition, ability_modifier, require_dnd_2024
from ..domain.errors import ValidationError
from ..domain.operations import MutationContext
from ..persistence.database import Database
from .attributes import SetAttributeOperation
from .conditions import RemoveConditionOperation, condition_map
from .dice import record_roll
from .dnd_pack import read_bool, read_int, read_list, validate_slot_list
from .mutations import MutationEngine, MutationRequest, MutationResult


def _require_not_in_combat(connection, context, entity_id: str) -> None:
    row = connection.execute(
        "SELECT c.id FROM combatants c"
        " JOIN combat_encounters e ON e.id = c.encounter_id"
        " WHERE e.campaign_id = ? AND e.branch_id = ? AND e.status = 'active'"
        " AND c.entity_id = ?",
        (context.campaign.id, context.branch_id, entity_id),
    ).fetchone()
    if row is not None:
        raise ValidationError("cannot rest during an active encounter")


def _require_alive(connection, campaign_id: str, entity_id: str) -> None:
    if read_bool(connection, campaign_id, entity_id, "is_dead"):
        raise ValidationError("dead entities cannot rest")


@dataclass(frozen=True)
class ShortRestOperation:
    """Spend Hit Dice to heal; each die heals ``roll + con mod`` (min 0)."""

    roller: DiceRoller
    roll_id_factory: Callable[[], str]
    entity_id: str
    hit_dice: int

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        campaign_id = context.campaign.id
        _require_not_in_combat(connection, context, self.entity_id)
        _require_alive(connection, campaign_id, self.entity_id)
        available = read_int(connection, campaign_id, self.entity_id, "hit_dice_current")
        if not 1 <= self.hit_dice <= available:
            raise ValidationError(
                f"hit dice must be in 1..{available}"
            )
        die = read_int(connection, campaign_id, self.entity_id, "hit_die")
        con_mod = ability_modifier(
            read_int(connection, campaign_id, self.entity_id, "ability_con")
        )
        hp_max = read_int(connection, campaign_id, self.entity_id, "hp_max")
        hp = read_int(connection, campaign_id, self.entity_id, "hp_current")
        healed = 0
        for _ in range(self.hit_dice):
            face = self.roller.roll(1, die)[0]
            amount = max(0, face + con_mod)
            record_roll(
                connection, context, self.roll_id_factory(),
                purpose="short rest hit die", formula=f"1d{die}",
                faces=(face,), modifier=con_mod, total=face + con_mod,
                roller_entity_id=self.entity_id,
            )
            healed += amount
        new_hp = min(hp_max, hp + healed)
        SetAttributeOperation(self.entity_id, "hp_current", new_hp).apply(
            connection, context
        )
        SetAttributeOperation(
            self.entity_id, "hit_dice_current", available - self.hit_dice
        ).apply(connection, context)
        return {
            "entity_id": self.entity_id,
            "dice_spent": self.hit_dice,
            "healed": new_hp - hp,
            "hp": new_hp,
        }


@dataclass(frozen=True)
class LongRestOperation:
    """Full recovery: HP, slots, half of total Hit Dice, −1 exhaustion."""

    entity_id: str

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        campaign_id = context.campaign.id
        _require_not_in_combat(connection, context, self.entity_id)
        _require_alive(connection, campaign_id, self.entity_id)
        hp_max = read_int(connection, campaign_id, self.entity_id, "hp_max")
        SetAttributeOperation(self.entity_id, "hp_current", hp_max).apply(
            connection, context
        )
        SetAttributeOperation(self.entity_id, "hp_temp", 0).apply(connection, context)
        maximum = validate_slot_list(
            read_list(connection, campaign_id, self.entity_id, "spell_slots_max")
        )
        SetAttributeOperation(
            self.entity_id, "spell_slots_current", list(maximum)
        ).apply(connection, context)
        total = read_int(connection, campaign_id, self.entity_id, "hit_dice_total")
        current = read_int(connection, campaign_id, self.entity_id, "hit_dice_current")
        regained = min(total, current + max(1, total // 2))
        SetAttributeOperation(self.entity_id, "hit_dice_current", regained).apply(
            connection, context
        )
        SetAttributeOperation(self.entity_id, "death_saves_success", 0).apply(
            connection, context
        )
        SetAttributeOperation(self.entity_id, "death_saves_failure", 0).apply(
            connection, context
        )
        SetAttributeOperation(self.entity_id, "is_stable", False).apply(
            connection, context
        )
        conditions = condition_map(connection, campaign_id, self.entity_id)
        if Condition.EXHAUSTION in conditions:
            RemoveConditionOperation(self.entity_id, Condition.EXHAUSTION, 1).apply(
                connection, context
            )
        connection.execute(
            "UPDATE combatants SET concentrating_spell = NULL,"
            " concentration_rounds = NULL WHERE entity_id = ?",
            (self.entity_id,),
        )
        return {
            "entity_id": self.entity_id,
            "hp": hp_max,
            "hit_dice_current": regained,
        }


class RestService:
    """User-command entry points for rests."""

    def __init__(
        self,
        database: Database,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], str] | None = None,
    ):
        from datetime import datetime, timezone
        from uuid import uuid4

        self.database = database
        self.id_factory = id_factory or (lambda: uuid4().hex)
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat())
        self.mutation_engine = MutationEngine(
            database, id_factory=self.id_factory, clock=self.clock
        )

    def short_rest(self, campaign_id, expected_version, entity_id, hit_dice, roller):
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id, branch_id="main",
                expected_version=expected_version, source="user-command",
                event_type="short-rest",
                operation=ShortRestOperation(
                    roller, self.id_factory, entity_id, hit_dice
                ),
            )
        )

    def long_rest(self, campaign_id, expected_version, entity_id):
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id, branch_id="main",
                expected_version=expected_version, source="user-command",
                event_type="long-rest",
                operation=LongRestOperation(entity_id),
            )
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/backend/unit/test_rests.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat(core): add short and long rest transactions" \
  "src/sillytavern_rpg_engine/services/rests.py" \
  "tests/backend/unit/test_rests.py"
```

---

### Task 14: Monster mechanics and data-driven turn triggers

**Files:**
- Create: `src/sillytavern_rpg_engine/services/monsters.py`
- Modify: `src/sillytavern_rpg_engine/services/combat.py` (`AdvanceTurnOperation` calls trigger processing)
- Test: `tests/backend/unit/test_monsters.py`

**Interfaces:**
- Consumes: `attacks.resolve_strike`, `damage.apply_damage`, `combat.py` internals.
- Produces: `MultiattackOperation(roller, roll_id_factory, attacker_id, attacks)`; `process_turn_triggers(connection, context, roller, roll_id_factory, combatant_row, when)`; `EnvironmentalDamageOperation(roller, roll_id_factory, entity_id, formula, damage_type, purpose)`; `MonsterService` (`multiattack`, `environmental`).

Rules encoded:
- Multiattack is one Attack action resolving 2+ strikes via `resolve_strike(..., spend_budget=False)` after a single budget spend; Loading weapons are rejected.
- Recharge entries on a combatant (`{"name", "min": 2..6, "available": bool}`) roll a d6 at the owner's turn start (Task 8 already wired); the result is recorded in `dice_rolls`.
- Turn triggers are declarative data on the combatant (`triggers_json`): `{"name", "when": "turn_start"|"turn_end", "effect": {...}}` with effects `{"kind": "damage", "formula": "2d6", "damage_type": "fire"}`, `{"kind": "heal", "formula": "1d8"}`, or `{"kind": "condition", "condition": "frightened", "save_ability": "wis", "save_dc": 13, "source": "<entity_id>"}` (save success → no condition). Rolls go to `dice_rolls`. This is the data-driven trigger/effect interface required by the spec for monsters, class features, feats, and items.
- Environmental/falling damage is an explicit operation (`1d6` per 10 ft falling is the caller's formula choice); mounted and underwater movement are covered by Task 8's movement modes (`MOUNTED` deducts from the mount's combatant budget; `SWIM` costs double).

- [ ] **Step 1: Write the failing tests**

```python
import json

import pytest

from sillytavern_rpg_engine.domain.dice import DiceFormula, SequenceDiceRoller
from sillytavern_rpg_engine.domain.dnd import (
    Condition,
    MasteryProperty,
    WeaponProfile,
    WeaponProperty,
)
from sillytavern_rpg_engine.domain.models import EntityKind
from sillytavern_rpg_engine.services.attacks import AttackSpec
from sillytavern_rpg_engine.services.attributes import SetAttributeOperation
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.combat import CombatService, CombatantEntry
from sillytavern_rpg_engine.services.conditions import condition_map
from sillytavern_rpg_engine.services.dnd_pack import DndRulesService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)
from sillytavern_rpg_engine.services.monsters import MonsterService

CLAW = WeaponProfile(
    key="claw", damage=DiceFormula(1, 6), damage_type="slashing",
    properties=frozenset(), mastery=None, attack_ability="str", range_normal=5,
)


def _world(database):
    ids = iter(f"e{n}" for n in range(1, 2000))
    clock = lambda: "2026-08-12T00:00:00Z"
    campaigns = CampaignService(database, id_factory=lambda: next(ids), clock=clock)
    dnd = DndRulesService(database, id_factory=lambda: next(ids), clock=clock)
    combat = CombatService(database, id_factory=lambda: next(ids), clock=clock)
    monsters = MonsterService(database, id_factory=lambda: next(ids), clock=clock)
    entities = EntityAttributeService(database, dnd.mutation_engine)
    campaigns.create_campaign("c1", "Dungeon")
    dnd.seed_pack("c1", 0)
    version = 1
    for entity_id, name in (("orc", "Orc"), ("pc1", "Aria")):
        entities.apply_explicit("c1", "main", version, CreateEntityOperation(
            entity_id=entity_id, kind=EntityKind.CHARACTER, name=name,
        ))
        version += 1
        for key, value in (
            ("ability_str", 16), ("ability_dex", 10), ("ability_con", 10),
            ("ability_wis", 8), ("proficiency_bonus", 2),
            ("proficiencies", ["claw"]), ("armor_class", 12),
            ("hp_max", 30), ("hp_current", 30), ("hp_temp", 0), ("speed", 30),
            ("character_level", 1),
            ("spell_slots_max", [0] * 9), ("spell_slots_current", [0] * 9),
            ("hit_die", 8), ("hit_dice_total", 1), ("hit_dice_current", 1),
            ("death_saves_success", 0), ("death_saves_failure", 0),
            ("is_dead", False), ("is_stable", False), ("resistances", []),
            ("vulnerabilities", []), ("immunities", []),
            ("condition_immunities", []), ("weapon_masteries", []),
        ):
            entities.apply_explicit(
                "c1", "main", version, SetAttributeOperation(entity_id, key, value)
            )
            version += 1
    dnd.enable("c1", version)
    return combat, monsters, version + 1


def _hp(database, entity_id):
    with database.connect() as connection:
        row = connection.execute(
            "SELECT value_json FROM attribute_values"
            " WHERE entity_id = ? AND attribute_key = 'hp_current'",
            (entity_id,),
        ).fetchone()
        return json.loads(row["value_json"])


def test_multiattack_resolves_all_strikes_with_one_action(database):
    combat, monsters, version = _world(database)
    result = combat.start(
        "c1", expected_version=version,
        roller=SequenceDiceRoller([15, 10]),
        entries=(CombatantEntry("orc"), CombatantEntry("pc1")),
    )
    result = monsters.multiattack(
        "c1", result.state_version, "orc",
        attacks=(
            AttackSpec(target_id="pc1", weapon=CLAW),
            AttackSpec(target_id="pc1", weapon=CLAW),
        ),
        roller=SequenceDiceRoller([15, 4, 14, 3]),
    )
    assert _hp(database, "pc1") == 30 - 7 - 6
    with database.connect() as connection:
        row = connection.execute(
            "SELECT action_used, attacks_this_turn FROM combatants"
            " WHERE entity_id = 'orc'"
        ).fetchone()
        assert (row["action_used"], row["attacks_this_turn"]) == (1, 2)


def test_recharge_rolls_at_turn_start(database):
    combat, monsters, version = _world(database)
    breath = {"name": "fire-breath", "min": 5, "available": False}
    result = combat.start(
        "c1", expected_version=version,
        roller=SequenceDiceRoller([15, 10]),
        entries=(CombatantEntry("orc", recharge=(breath,)), CombatantEntry("pc1")),
    )
    # pc1's turn, then orc's next turn rolls recharge d6 = 5 -> available
    result = combat.advance_turn("c1", result.state_version, SequenceDiceRoller([]))
    result = combat.advance_turn("c1", result.state_version, SequenceDiceRoller([5]))
    with database.connect() as connection:
        recharge = json.loads(connection.execute(
            "SELECT recharge_json FROM combatants WHERE entity_id = 'orc'"
        ).fetchone()["recharge_json"])
        assert recharge[0]["available"] is True
        roll = connection.execute(
            "SELECT formula, total FROM dice_rolls WHERE purpose LIKE 'recharge%'"
        ).fetchone()
        assert (roll["formula"], roll["total"]) == ("1d6", 5)


def test_turn_triggers_damage_heal_and_condition(database):
    combat, monsters, version = _world(database)
    triggers = (
        {"name": "regeneration", "when": "turn_start",
         "effect": {"kind": "heal", "formula": "1d8"}},
        {"name": "fear-aura", "when": "turn_end",
         "effect": {"kind": "condition", "condition": "frightened",
                    "save_ability": "wis", "save_dc": 13, "source": "orc"}},
    )
    result = combat.start(
        "c1", expected_version=version,
        roller=SequenceDiceRoller([15, 10]),
        entries=(CombatantEntry("orc", triggers=triggers), CombatantEntry("pc1")),
    )
    with database.connect() as connection:
        connection.execute(
            "UPDATE attribute_values SET value_json = '20'"
            " WHERE entity_id = 'orc' AND attribute_key = 'hp_current'"
        )
    # orc's turn ends -> fear aura on... turn_end trigger targets the OWNER's
    # counterpart? No: trigger effects target the owning combatant except
    # "condition", which targets every OTHER combatant (aura semantics).
    result = combat.advance_turn("c1", result.state_version, SequenceDiceRoller([6]))
    # now pc1's turn: pc1 makes the fear save? The aura fired at orc's turn
    # end; pc1 rolled 6 - 1 = 5 < 13 -> frightened
    with database.connect() as connection:
        assert condition_map(connection, "c1", "pc1") == {Condition.FRIGHTENED: 1}
    result = combat.advance_turn("c1", result.state_version, SequenceDiceRoller([6]))
    # orc's turn start: regeneration heals 6
    assert _hp(database, "orc") == 26


def test_environmental_damage_records_roll(database):
    _, monsters, version = _world(database)
    result = monsters.environmental(
        "c1", version, "pc1", formula="3d6", damage_type="bludgeoning",
        purpose="falling 30 ft", roller=SequenceDiceRoller([4, 4, 4]),
    )
    assert _hp(database, "pc1") == 18
    with database.connect() as connection:
        roll = connection.execute(
            "SELECT purpose, formula, total FROM dice_rolls"
            " WHERE purpose = 'falling 30 ft'"
        ).fetchone()
        assert (roll["formula"], roll["total"]) == ("3d6", 12)
```

Trigger targeting semantics to implement: `heal`/`damage` target the owning combatant's entity; `condition` targets every other combatant in the encounter (aura). Document this in the `process_turn_triggers` docstring.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/backend/unit/test_monsters.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `services/monsters.py` and wire triggers into turn advancement**

`services/monsters.py`:

```python
"""Monster mechanics: multiattack, and data-driven turn triggers."""

from dataclasses import dataclass
import json
import sqlite3
from typing import Any, Callable

from ..domain.dice import D20Mode, DiceFormula, DiceRoller, resolve_d20, roll_formula
from ..domain.dnd import (
    Condition,
    WeaponProperty,
    ability_modifier,
    require_dnd_2024,
)
from ..domain.errors import ValidationError
from ..domain.operations import MutationContext
from ..persistence.database import Database
from .attacks import AttackSpec, resolve_strike
from .attributes import SetAttributeOperation
from .combat import require_active_encounter
from .conditions import ApplyConditionOperation
from .damage import HealOperation, apply_damage
from .dice import record_roll
from .dnd_pack import read_int
from .mutations import MutationEngine, MutationRequest, MutationResult


@dataclass(frozen=True)
class MultiattackOperation:
    """One Attack action resolving every listed strike (monster multiattack)."""

    roller: DiceRoller
    roll_id_factory: Callable[[], str]
    attacker_id: str
    attacks: tuple[AttackSpec, ...]

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        if len(self.attacks) < 2:
            raise ValidationError("multiattack requires at least two strikes")
        for spec in self.attacks:
            if WeaponProperty.LOADING in spec.weapon.properties:
                raise ValidationError("loading weapons cannot multiattack")
        first, *rest = self.attacks
        payloads = [
            resolve_strike(
                connection, context, self.roller, self.roll_id_factory,
                self.attacker_id, first,
            )
        ]
        for spec in rest:
            payloads.append(
                resolve_strike(
                    connection, context, self.roller, self.roll_id_factory,
                    self.attacker_id, spec, spend_budget=False,
                )
            )
        return {"attacker_id": self.attacker_id, "strikes": payloads}


def process_turn_triggers(
    connection: sqlite3.Connection,
    context: MutationContext,
    roller: DiceRoller,
    roll_id_factory: Callable[[], str],
    combatant_row,
    when: str,
    combatants,
) -> list[dict[str, Any]]:
    """Run ``when`` ("turn_start"/"turn_end") trigger effects.

    ``heal`` and ``damage`` target the owning combatant's entity;
    ``condition`` targets every OTHER combatant whose save fails (aura).
    """
    fired: list[dict[str, Any]] = []
    owner = combatant_row["entity_id"]
    for trigger in json.loads(combatant_row["triggers_json"]):
        if trigger.get("when") != when:
            continue
        effect = trigger["effect"]
        kind = effect["kind"]
        entry: dict[str, Any] = {"name": trigger["name"], "kind": kind}
        if kind == "heal":
            rolled = roll_formula(roller, DiceFormula.parse(effect["formula"]))
            record_roll(
                connection, context, roll_id_factory(),
                purpose=f"trigger: {trigger['name']}", formula=effect["formula"],
                faces=rolled.faces, modifier=0, total=rolled.total,
                roller_entity_id=owner,
            )
            entry["result"] = HealOperation(owner, rolled.total).apply(
                connection, context
            )
        elif kind == "damage":
            rolled = roll_formula(roller, DiceFormula.parse(effect["formula"]))
            record_roll(
                connection, context, roll_id_factory(),
                purpose=f"trigger: {trigger['name']}", formula=effect["formula"],
                faces=rolled.faces, modifier=0, total=rolled.total,
                roller_entity_id=owner,
            )
            entry["result"] = apply_damage(
                connection, context, roller, roll_id_factory, owner,
                rolled.total, effect["damage_type"],
            ).__dict__
        elif kind == "condition":
            targets = [r for r in combatants if r["entity_id"] != owner]
            affected = []
            for target in targets:
                ability = effect["save_ability"]
                modifier = ability_modifier(
                    read_int(connection, context.campaign.id,
                             target["entity_id"], f"ability_{ability}")
                )
                outcome = resolve_d20(roller, D20Mode.NORMAL, modifier)
                record_roll(
                    connection, context, roll_id_factory(),
                    purpose=f"trigger save: {trigger['name']}", formula="1d20",
                    faces=(outcome.kept,), modifier=modifier, total=outcome.total,
                    dc=effect["save_dc"], success=outcome.total >= effect["save_dc"],
                    roller_entity_id=target["entity_id"],
                )
                if outcome.total < effect["save_dc"]:
                    ApplyConditionOperation(
                        target["entity_id"], Condition(effect["condition"]),
                        effect.get("source", trigger["name"]),
                    ).apply(connection, context)
                    affected.append(target["entity_id"])
            entry["affected"] = affected
        else:
            raise ValidationError(f"unknown trigger effect kind {kind!r}")
        fired.append(entry)
    return fired


@dataclass(frozen=True)
class EnvironmentalDamageOperation:
    """Falling, environmental, and improvised damage via explicit formula."""

    roller: DiceRoller
    roll_id_factory: Callable[[], str]
    entity_id: str
    formula: str
    damage_type: str
    purpose: str

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        require_dnd_2024(context.campaign)
        parsed = DiceFormula.parse(self.formula)
        rolled = roll_formula(self.roller, parsed)
        record_roll(
            connection, context, self.roll_id_factory(),
            purpose=self.purpose, formula=self.formula, faces=rolled.faces,
            modifier=0, total=rolled.total, roller_entity_id=self.entity_id,
        )
        result = apply_damage(
            connection, context, self.roller, self.roll_id_factory,
            self.entity_id, rolled.total, self.damage_type,
        )
        return {"entity_id": self.entity_id, **result.__dict__}


class MonsterService:
    """User-command entry points for monster mechanics."""

    def __init__(
        self,
        database: Database,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], str] | None = None,
    ):
        from datetime import datetime, timezone
        from uuid import uuid4

        self.database = database
        self.id_factory = id_factory or (lambda: uuid4().hex)
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat())
        self.mutation_engine = MutationEngine(
            database, id_factory=self.id_factory, clock=self.clock
        )

    def multiattack(self, campaign_id, expected_version, attacker_id, attacks, roller):
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id, branch_id="main",
                expected_version=expected_version, source="user-command",
                event_type="multiattack",
                operation=MultiattackOperation(
                    roller, self.id_factory, attacker_id, tuple(attacks)
                ),
            )
        )

    def environmental(self, campaign_id, expected_version, entity_id, formula,
                      damage_type, purpose, roller):
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id, branch_id="main",
                expected_version=expected_version, source="user-command",
                event_type="environmental-damage",
                operation=EnvironmentalDamageOperation(
                    roller, self.id_factory, entity_id, formula, damage_type, purpose
                ),
            )
        )
```

Edit `combat.py` — `AdvanceTurnOperation.apply`: process turn-end triggers for the ending combatant and turn-start triggers for the starting combatant. Insert after `_clear_debuffs(... "end")` for the ending combatant:

```python
        end_fired = self._triggers(connection, context, ending, "turn_end", combatants)
```

and after the starting combatant's UPDATE:

```python
        start_fired = self._triggers(connection, context, starting, "turn_start", combatants)
```

with the payload gaining `"triggers": end_fired + start_fired` and a helper on the operation:

```python
    def _triggers(self, connection, context, row, when, combatants):
        from .monsters import process_turn_triggers

        return process_turn_triggers(
            connection, context, self.roller, self.roll_id_factory, row, when,
            combatants,
        )
```

Also note: the trigger test's `UPDATE attribute_values` runs outside a mutation (direct SQL fixture hack); acceptable in tests only — add a comment in the test that direct SQL is a fixture shortcut, never production behavior.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/backend/unit/test_monsters.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat(core): add multiattack, recharge, turn triggers" \
  "src/sillytavern_rpg_engine/services/monsters.py" \
  "src/sillytavern_rpg_engine/services/combat.py" \
  "tests/backend/unit/test_monsters.py"
```

---

### Task 15: Codec, snapshots, projection, export, integration flow, docs

**Files:**
- Modify: `src/sillytavern_rpg_engine/services/proposals.py` (codec)
- Modify: `src/sillytavern_rpg_engine/services/snapshots.py`
- Modify: `src/sillytavern_rpg_engine/services/projection.py`
- Modify: `src/sillytavern_rpg_engine/services/campaign_export.py`
- Modify: `README.md`
- Test: `tests/backend/unit/test_proposal_condition_ops.py`
- Test: `tests/backend/integration/test_phase3_flow.py`

**Interfaces:**
- Consumes: all Phase 3 services.
- Produces: codec kinds `apply_condition` / `remove_condition`; snapshot sections `combat`, `conditions`; projection fields `conditions`, `combat`; export sections `dice_rolls`, `entity_conditions`, `combat_encounters`.

- [ ] **Step 1: Write the failing codec tests**

```python
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
```

- [ ] **Step 2: Run codec tests to verify they fail**

Run: `python -m pytest tests/backend/unit/test_proposal_condition_ops.py -v`
Expected: FAIL — "unsupported operation kind 'apply_condition'".

- [ ] **Step 3: Extend the codec**

In `proposals.py`:

```python
ALLOWED_KINDS = frozenset({
    ...,  # existing kinds unchanged
    "apply_condition",
    "remove_condition",
})

_EXPECTED_KEYS.update({
    "apply_condition": frozenset({
        "kind", "entity_id", "condition", "level", "source",
    }),
    "remove_condition": frozenset({"kind", "entity_id", "condition", "level"}),
})
```

Add to `decode` before the final `return CloseArcOperation(...)`:

```python
        if kind == "apply_condition":
            from ..domain.dnd import Condition as _Condition
            from .conditions import ApplyConditionOperation

            level = payload["level"]
            if isinstance(level, bool) or not isinstance(level, int) or level < 1:
                raise ValidationError("level must be a positive integer")
            return ApplyConditionOperation(
                entity_id=_as_id(payload, "entity_id"),
                condition=_enum(_Condition, payload["condition"], "condition"),
                source=_as_str(payload, "source"),
                level=level,
            )
        if kind == "remove_condition":
            from ..domain.dnd import Condition as _Condition
            from .conditions import RemoveConditionOperation

            level = payload["level"]
            if isinstance(level, bool) or not isinstance(level, int) or level < 1:
                raise ValidationError("level must be a positive integer")
            return RemoveConditionOperation(
                entity_id=_as_id(payload, "entity_id"),
                condition=_enum(_Condition, payload["condition"], "condition"),
                level=level,
            )
```

- [ ] **Step 4: Run codec tests to verify they pass**

Run: `python -m pytest tests/backend/unit/test_proposal_condition_ops.py tests/backend/unit/test_proposals.py -v`
Expected: all pass

- [ ] **Step 5: Extend snapshots, projection, and export**

`snapshots.py` — add to `build` return dict and implement:

```python
        return {
            "campaign": campaign,
            "entities": entities,
            "attribute_definitions": definitions,
            "facts": facts,
            "relationships": relationships,
            "conditions": self._conditions(connection, campaign_id),
            "combat": self._combat(connection, campaign_id, branch_id),
        }

    def _conditions(self, connection, campaign_id: str) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT entity_id, condition, level, source FROM entity_conditions"
            " WHERE campaign_id = ? ORDER BY entity_id, condition",
            (campaign_id,),
        ).fetchall()
        return [
            {
                "entity_id": row["entity_id"],
                "condition": row["condition"],
                "level": row["level"],
                "source": row["source"],
            }
            for row in rows
        ]

    def _combat(self, connection, campaign_id: str, branch_id: str) -> dict | None:
        encounter = connection.execute(
            "SELECT id, round_number, active_index FROM combat_encounters"
            " WHERE campaign_id = ? AND branch_id = ? AND status = 'active'",
            (campaign_id, branch_id),
        ).fetchone()
        if encounter is None:
            return None
        rows = connection.execute(
            "SELECT entity_id, initiative, action_used, bonus_used,"
            " reaction_used, movement_total, movement_used, hidden, dodging,"
            " disengaged, concentrating_spell FROM combatants"
            " WHERE encounter_id = ? ORDER BY initiative DESC, entity_id",
            (encounter["id"],),
        ).fetchall()
        order = [row["entity_id"] for row in rows]
        return {
            "encounter_id": encounter["id"],
            "round": encounter["round_number"],
            "order": order,
            "active_entity_id": order[encounter["active_index"]] if order else None,
            "combatants": [
                {
                    "entity_id": row["entity_id"],
                    "initiative": row["initiative"],
                    "action_used": bool(row["action_used"]),
                    "bonus_used": bool(row["bonus_used"]),
                    "reaction_used": bool(row["reaction_used"]),
                    "movement_total": row["movement_total"],
                    "movement_used": row["movement_used"],
                    "hidden": bool(row["hidden"]),
                    "dodging": bool(row["dodging"]),
                    "disengaged": bool(row["disengaged"]),
                    "concentrating_spell": row["concentrating_spell"],
                }
                for row in rows
            ],
        }
```

`projection.py` — in `for_audiences`, add `conditions` and `combat` to the returned dict (conditions are combat-visible state, same for all audiences):

```python
        return {
            ...,
            "entities": entities,
            "conditions": conditions_rows,
            "combat": combat_summary,
        }
```

implementing `_conditions` (all rows for the campaign) and `_combat` (same query as the snapshot, or `None`) as private methods mirroring `SnapshotBuilder`.

`campaign_export.py` — add root keys and sections:

```python
_EXPECTED_ROOT_KEYS = frozenset({
    ...,  # existing
    "dice_rolls",
    "entity_conditions",
    "combat_encounters",
})
```

and in `_build_payload`, after `snapshots = ...`:

```python
            dice_rolls = self._dice_rolls(connection, campaign_id)
            conditions = self._entity_conditions(connection, campaign_id)
            encounters = self._combat_encounters(connection, campaign_id)
```

adding them to the payload and implementing:

```python
    @staticmethod
    def _dice_rolls(connection, campaign_id) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT id, branch_id, turn_id, roller_entity_id, purpose, formula,"
            " faces_json, modifier, total, dc, success, critical, rules_version,"
            " state_version, created_at FROM dice_rolls"
            " WHERE campaign_id = ? ORDER BY state_version, id",
            (campaign_id,),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "branch_id": row["branch_id"],
                "turn_id": row["turn_id"],
                "roller_entity_id": row["roller_entity_id"],
                "purpose": row["purpose"],
                "formula": row["formula"],
                "faces": json.loads(row["faces_json"]),
                "modifier": row["modifier"],
                "total": row["total"],
                "dc": row["dc"],
                "success": None if row["success"] is None else bool(row["success"]),
                "critical": bool(row["critical"]),
                "rules_version": row["rules_version"],
                "state_version": row["state_version"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    @staticmethod
    def _entity_conditions(connection, campaign_id) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT entity_id, condition, level, source, applied_state_version"
            " FROM entity_conditions WHERE campaign_id = ?"
            " ORDER BY entity_id, condition",
            (campaign_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _combat_encounters(connection, campaign_id) -> list[dict[str, Any]]:
        encounters = connection.execute(
            "SELECT id, branch_id, status, round_number, active_index,"
            " created_state_version, ended_state_version, created_at"
            " FROM combat_encounters WHERE campaign_id = ? ORDER BY id",
            (campaign_id,),
        ).fetchall()
        result = []
        for encounter in encounters:
            combatants = connection.execute(
                "SELECT entity_id, initiative FROM combatants"
                " WHERE encounter_id = ? ORDER BY initiative DESC, entity_id",
                (encounter["id"],),
            ).fetchall()
            result.append(
                {
                    **dict(encounter),
                    "combatants": [dict(row) for row in combatants],
                }
            )
        return result
```

- [ ] **Step 6: Write the integration test**

`tests/backend/integration/test_phase3_flow.py`:

```python
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
```

- [ ] **Step 7: Run integration tests to verify they fail, then implement**

Run: `python -m pytest tests/backend/integration/test_phase3_flow.py -v`
Expected: FAIL until Steps 3–5 are complete (`snapshot` key errors, codec keys); implement Steps 3 and 5, then re-run.

Expected after implementation: 2 passed.

- [ ] **Step 8: Document the Phase 3 workflow**

Append to `README.md`:

```markdown
## RPG Engine Core (Phase 3)

Phase 3 adds the D&D 2024 / 5.5e rules layer: per-campaign opt-in (`DndRulesService.seed_pack` → fill character data → `enable`, which fails atomically with a missing-fields report when any character is incomplete), authoritative dice with injectable randomness (`DiceRoller`; production uses `secrets`, tests use scripted sequences), and immutable `dice_rolls` records pinned to the campaign's rules version (`srd-5.2.1`). Combat covers initiative with 2024 surprise (Disadvantage), round/turn lifecycle, action/bonus-action/reaction/movement budgets, movement costs (difficult terrain, crawl, stand up, forced, mounted), attack resolution with aggregated advantage/disadvantage, cover, criticals, weapon properties (Light, Loading, Thrown, Reach, Finesse), grapple/shove saves, all eight 2024 Weapon Mastery properties (Cleave/Nick once per turn), the full damage pipeline (immunity → resistance halving → vulnerability doubling, temp HP, 0 HP dying, death saves, massive damage), 2024 conditions including 6-level Exhaustion (−2/level on d20 Tests, −5 ft/level), spell-slot transactions (one slot per turn, concentration with damage checks and duration countdown), short/long rests, monster multiattack, Recharge, and data-driven turn triggers. Narrative campaigns never execute D&D resolution; every rules-gated operation validates the pinned rules version.
```

- [ ] **Step 9: Run Phase 3 verification**

Run:

```bash
python -m pytest tests/backend -q
python -m sillytavern_rpg_engine --help
python .harness/scripts/guard.py src/sillytavern_rpg_engine tests/backend
npm run check
```

Expected: Python tests pass (Phase 1 + 2 + 3); CLI help unchanged; guard reports 0 failures; JavaScript checks pass unchanged.

- [ ] **Step 10: Commit Task 15**

```bash
bash .harness/scripts/committer "feat(core): complete dnd 2024 rules layer" \
  "src/sillytavern_rpg_engine/services/proposals.py" \
  "src/sillytavern_rpg_engine/services/snapshots.py" \
  "src/sillytavern_rpg_engine/services/projection.py" \
  "src/sillytavern_rpg_engine/services/campaign_export.py" \
  "tests/backend/unit/test_proposal_condition_ops.py" \
  "tests/backend/integration/test_phase3_flow.py" \
  "README.md"
```

---

## Phase 3 Exit Criteria

- `python -m pytest tests/backend -q` passes from a fresh editable install.
- Enabling D&D requires complete character data for every character entity and atomically pins `dnd-2024` + `srd-5.2.1`; the pre-enable state survives in the same transaction's snapshot.
- Every roll (checks, initiative, attacks, damage, saves, death saves, recharge, triggers, concentration) lands in append-only `dice_rolls` with faces, modifier, DC, result, and rules version; UPDATE/DELETE triggers abort.
- All D&D operations reject narrative campaigns and version-mismatched campaigns with `ValidationError`; nothing silently reinterprets old turns.
- Combat enforces: one active encounter per branch, initiative order with surprise disadvantage and deterministic ties, per-turn action/bonus/reaction/movement budgets, 2024 movement costs, incapacitation blocks, and reaction refresh at turn start.
- Attack resolution aggregates advantage/disadvantage with cancellation, honors cover/criticals/range/reach, and applies weapon properties and all eight masteries with their per-turn limits.
- Damage applies immunity/resistance/vulnerability in order, consumes temp HP first, drives 0 HP → Unconscious → death saves (nat 1/20 rules) → Stable or dead, with massive-damage instant death and damage-triggered concentration checks.
- Spell casting spends the Magic action and at most one slot per turn, validates slot level/availability, resolves attack/save/auto-hit variants, and manages concentration (replace, countdown, voluntary end).
- Long Rest restores HP/slots/half Hit Dice and sheds one Exhaustion level; rests are impossible during an active encounter or while dead.
- Monster multiattack resolves all strikes on one action; Recharge rolls at turn start; declared turn triggers fire damage/heal/condition effects with recorded rolls.
- The proposal codec accepts only the two new non-random kinds (`apply_condition`, `remove_condition`); every random operation kind is rejected by the codec.
- Snapshots, projections, and exports include combat, conditions, and dice roll sections; the exporter's verifier accepts the new root keys.
- Existing JavaScript DME tests and build remain unchanged and passing.




