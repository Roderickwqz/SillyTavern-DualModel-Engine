# LangGraph RPG Core Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the long-memory and personality layer to the Phase 1 core: temporally valid facts, permanent memory events with FTS5 retrieval, directed relationships, capped trait-change events, provenance-linked development arcs and summaries, and a tiered audience-filtered retrieval service.

**Architecture:** Extend the existing Python package without changing Phase 1 behavior. One new schema migration adds memory/personality tables and a trigram FTS5 index; new operation dataclasses plug into the existing `MutationEngine` so every memory write keeps the same atomic version+audit+outbox+snapshot guarantees. Trait current values live in `attribute_values`; `trait_events` is the immutable change log with Python-enforced caps and inertia. Retrieval is read-only SQL with audience filters applied in the query, never after assembly. No LLM dependency: inferred changes enter only as persistent proposals decoded by the extended strict codec.

**Tech Stack:** Python 3.11+, standard-library `sqlite3` (≥3.34 for FTS5 trigram; dev env has 3.51), dataclasses, enums, JSON, pytest 8.x.

## Global Constraints

- The authoritative store is SQLite with WAL and foreign keys enabled; all Phase 1 guarantees (CAS version bump, audit event, JSONL outbox row, snapshot in one transaction) apply unchanged to every new operation.
- Memory events and trait events are append-only: no UPDATE or DELETE of history. Facts are superseded via `valid_until`/`superseded_by`, never deleted ("forgetting" lowers retrieval priority only).
- Importance is an integer in 1..5. Trait delta caps: `normal` ≤ 3, `important` ≤ 8, `major` ≤ 20; inertia: cumulative `|delta|` over the last 5 trait events of one dimension plus the new delta ≤ 25. These are engine constants (`TRAIT_TIER_CAPS`, `TRAIT_INERTIA_WINDOW`, `TRAIT_INERTIA_CAP`), not per-campaign config.
- Pinned memory event types (`identity`, `commitment`, `rule_consequence`, `personality_shift`) always rank before unpinned events in retrieval regardless of age.
- Audience filtering happens inside SQL reads (`json_each` on `audiences_json`); no unrestricted result set is assembled and filtered afterward.
- Attributes in category `adult_intimacy` require `age_status = adult`; this now also covers trait events on those attributes.
- Trait events are allowed only on numeric attributes (`number`/`integer`) whose category is `trait` or `adult_intimacy`.
- FTS5 uses `tokenize='trigram'` for CJK support; search text shorter than 3 characters is rejected with `ValidationError`.
- LLMs never receive SQL or storage access; this phase contains no LLM dependency. Inferred memory/personality changes persist as Pending proposals and apply only through approval.
- Branch scope: events, facts, arcs, and summaries record `branch_id` for filtering, but authoritative entity/attribute state stays campaign-global until the Phase 6 branching plan. Do not build per-branch state divergence here.
- Do not modify, delete, or migrate existing JavaScript DME state in this phase.
- Do not store API keys, secrets, machine-specific absolute paths, or credentials in campaign data or exports.
- Use `.harness/scripts/committer` for every commit and stage only files listed by the task.

## Multi-Plan Roadmap

The approved specification (`docs/superpowers/specs/2026-08-10-langgraph-rpg-companion-design.md`) is split into independent plans:

1. Phase 1 (done): authoritative Python core and persistence.
2. **This plan:** memory and personality — fact validity, trait events, development arcs, FTS5 retrieval, summaries, relationships.
3. D&D 2024 / 5.5e: campaign readiness, dice, action economy, combat, conditions, Weapon Mastery, spell/resource transactions.
4. FastAPI and LangGraph: OpenAI-compatible endpoint, request normalization, model adapters, narrative/critic graph.
5. RPG Companion compatibility fork: dynamic `attributes`, rules metadata, read-only mode, narrowed JSON cleaning.
6. Branching and release: SillyTavern history hashes, swipe/edit/delete recovery, backups, long-run simulation, E2E.

## File Structure

```text
src/sillytavern_rpg_engine/
├── domain/
│   ├── memory.py               # NEW: memory/personality enums, dataclasses, cap constants
│   └── operations.py           # MODIFY: MutationContext gains `now`
├── persistence/
│   └── schema/
│       └── 0003_memory_personality.sql  # NEW: facts/events/fts/relationships/traits/arcs/summaries
├── services/
│   ├── mutations.py            # MODIFY: compute now/event_id before operation.apply
│   ├── facts.py                # NEW: AssertFactOperation, FactService
│   ├── memory_events.py        # NEW: RecordMemoryEventOperation, MemoryEventService, MemoryIndexService
│   ├── relationships.py        # NEW: SetRelationshipOperation, RelationshipService
│   ├── traits.py               # NEW: RecordTraitEventOperation, TraitService
│   ├── arcs.py                 # NEW: OpenArcOperation, CloseArcOperation, ArcService
│   ├── summaries.py            # NEW: UpsertSummaryOperation, SummaryService
│   ├── retrieval.py            # NEW: RetrievalQuery, RetrievalService
│   ├── snapshots.py            # MODIFY: + current facts, relationships sections
│   ├── projection.py           # MODIFY: + visible facts, relationships per entity
│   └── proposals.py            # MODIFY: codec accepts 7 new operation kinds
├── cli.py                      # MODIFY: rebuild-memory-index command
tests/backend/
├── unit/
│   ├── test_memory_domain.py
│   ├── test_memory_schema.py
│   ├── test_facts.py
│   ├── test_memory_events.py
│   ├── test_relationships.py
│   ├── test_trait_events.py
│   ├── test_arcs.py
│   ├── test_summaries.py
│   ├── test_retrieval.py
│   └── test_proposal_memory_ops.py
└── integration/
    └── test_phase2_flow.py
```

---

### Task 1: Memory domain contracts and MutationContext timestamp

**Files:**
- Create: `src/sillytavern_rpg_engine/domain/memory.py`
- Modify: `src/sillytavern_rpg_engine/domain/operations.py`
- Modify: `src/sillytavern_rpg_engine/services/mutations.py:60-117`
- Test: `tests/backend/unit/test_memory_domain.py`

**Interfaces:**
- Consumes: existing `domain/models.py` (`Audience`), `domain/errors.py`.
- Produces: `FactType`, `MemoryEventType`, `PINNED_EVENT_TYPES`, `TraitTier`, `SummaryScope`, `Fact`, `MemoryEvent`, `TraitEvent`, `DevelopmentArc`, `MemorySummary`, `Relationship`, `validate_importance()`, `validate_audiences()`, constants `MIN_IMPORTANCE`, `MAX_IMPORTANCE`, `TRAIT_TIER_CAPS`, `TRAIT_INERTIA_WINDOW`, `TRAIT_INERTIA_CAP`; `MutationContext.now: str`.

- [ ] **Step 1: Write failing domain tests**

```python
import sqlite3
from dataclasses import dataclass

import pytest

from sillytavern_rpg_engine.domain.errors import ValidationError
from sillytavern_rpg_engine.domain.memory import (
    FactType,
    MemoryEventType,
    PINNED_EVENT_TYPES,
    SummaryScope,
    TRAIT_TIER_CAPS,
    TraitTier,
    validate_audiences,
    validate_importance,
)
from sillytavern_rpg_engine.domain.models import Audience
from sillytavern_rpg_engine.domain.operations import MutationContext
from sillytavern_rpg_engine.services.mutations import MutationEngine, MutationRequest


def test_importance_bounds_and_bool_rejection():
    assert validate_importance(1) == 1
    assert validate_importance(5) == 5
    with pytest.raises(ValidationError, match="integer"):
        validate_importance(True)
    with pytest.raises(ValidationError, match="outside range"):
        validate_importance(0)
    with pytest.raises(ValidationError, match="outside range"):
        validate_importance(6)


def test_pinned_types_are_known_event_types():
    assert PINNED_EVENT_TYPES <= set(MemoryEventType)
    assert MemoryEventType.GENERAL not in PINNED_EVENT_TYPES


def test_tier_caps_cover_every_tier():
    assert set(TRAIT_TIER_CAPS) == {tier.value for tier in TraitTier}
    assert TRAIT_TIER_CAPS["normal"] < TRAIT_TIER_CAPS["important"]
    assert TRAIT_TIER_CAPS["important"] < TRAIT_TIER_CAPS["major"]


def test_audiences_must_not_be_empty():
    assert validate_audiences(frozenset({Audience.ENGINE}))
    with pytest.raises(ValidationError, match="audiences"):
        validate_audiences(frozenset())


@dataclass(frozen=True)
class CaptureNow:
    seen: list

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        self.seen.append(context.now)
        return {"now": context.now}


def test_mutation_context_carries_injected_clock(database):
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO campaigns(id, name, created_at, updated_at)"
            " VALUES ('c1', 'Campaign', '2026-08-10T00:00:00Z', '2026-08-10T00:00:00Z')"
        )
        connection.execute(
            "INSERT INTO branches(id, campaign_id, status, created_at)"
            " VALUES ('main', 'c1', 'active', '2026-08-10T00:00:00Z')"
        )
    seen = []
    engine = MutationEngine(
        database,
        id_factory=lambda: "event-now",
        clock=lambda: "2026-08-12T09:30:00Z",
    )
    engine.apply(MutationRequest("c1", "main", 0, "test", "tick", CaptureNow(seen)))
    assert seen == ["2026-08-12T09:30:00Z"]
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest tests/backend/unit/test_memory_domain.py -q`

Expected: import fails with `ModuleNotFoundError: No module named 'sillytavern_rpg_engine.domain.memory'`.

- [ ] **Step 3: Implement `domain/memory.py`**

```python
"""Memory and personality contracts: facts, events, traits, arcs, summaries."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .errors import ValidationError
from .models import Audience

MIN_IMPORTANCE = 1
MAX_IMPORTANCE = 5

TRAIT_TIER_CAPS: dict[str, float] = {
    "normal": 3.0,
    "important": 8.0,
    "major": 20.0,
}
TRAIT_INERTIA_WINDOW = 5
TRAIT_INERTIA_CAP = 25.0


class FactType(StrEnum):
    IDENTITY = "identity"
    COMMITMENT = "commitment"
    QUEST = "quest"
    CONFLICT = "conflict"
    RULE_CONSEQUENCE = "rule_consequence"
    GENERAL = "general"


class MemoryEventType(StrEnum):
    GENERAL = "general"
    SCENE = "scene"
    IDENTITY = "identity"
    COMMITMENT = "commitment"
    CONFLICT = "conflict"
    QUEST = "quest"
    RULE_CONSEQUENCE = "rule_consequence"
    PERSONALITY_SHIFT = "personality_shift"


PINNED_EVENT_TYPES = frozenset({
    MemoryEventType.IDENTITY,
    MemoryEventType.COMMITMENT,
    MemoryEventType.RULE_CONSEQUENCE,
    MemoryEventType.PERSONALITY_SHIFT,
})


class TraitTier(StrEnum):
    NORMAL = "normal"
    IMPORTANT = "important"
    MAJOR = "major"


class SummaryScope(StrEnum):
    CHARACTER = "character"
    RELATIONSHIP = "relationship"
    QUEST = "quest"
    PLOTLINE = "plotline"


@dataclass(frozen=True)
class Fact:
    """One keyed fact about an entity; ``valid_until`` NULL means current."""

    id: str
    campaign_id: str
    branch_id: str
    entity_id: str
    fact_type: FactType
    fact_key: str
    content: str
    importance: int
    audiences: frozenset[Audience]
    valid_from: int
    valid_until: int | None
    superseded_by: str | None
    turn_id: str | None
    source: str
    created_at: str


@dataclass(frozen=True)
class MemoryEvent:
    """One permanent raw memory event; participants load separately."""

    id: str
    campaign_id: str
    branch_id: str
    event_type: MemoryEventType
    content: str
    importance: int
    audiences: frozenset[Audience]
    participants: tuple[str, ...]
    location_entity_id: str | None
    turn_id: str | None
    source: str
    state_version: int
    created_at: str


@dataclass(frozen=True)
class TraitEvent:
    """One validated personality change with before/delta/after evidence."""

    id: str
    campaign_id: str
    branch_id: str
    entity_id: str
    trait_key: str
    tier: TraitTier
    before: float
    delta: float
    after: float
    cause: str
    turn_id: str | None
    source: str
    state_version: int
    created_at: str


@dataclass(frozen=True)
class DevelopmentArc:
    """A curated development phase with provenance to trait events."""

    id: str
    campaign_id: str
    branch_id: str
    entity_id: str
    dimension: str
    label: str
    summary: str
    source_event_ids: tuple[str, ...]
    start_turn_id: str | None
    end_turn_id: str | None
    opened_state_version: int
    closed_state_version: int | None
    created_at: str


@dataclass(frozen=True)
class MemorySummary:
    """A rebuildable summary that must cite its source memory events."""

    campaign_id: str
    branch_id: str
    scope: SummaryScope
    scope_key: str
    content: str
    audiences: frozenset[Audience]
    source_event_ids: tuple[str, ...]
    state_version: int
    created_at: str


@dataclass(frozen=True)
class Relationship:
    """One directed relationship dimension between two entities."""

    campaign_id: str
    from_entity_id: str
    to_entity_id: str
    dimension: str
    value: Any
    audiences: frozenset[Audience]
    state_version: int
    updated_turn_id: str | None


def validate_importance(value: int) -> int:
    """Require an integer importance within MIN_IMPORTANCE..MAX_IMPORTANCE."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError("importance must be an integer")
    if not MIN_IMPORTANCE <= value <= MAX_IMPORTANCE:
        raise ValidationError(
            f"importance {value} outside range {MIN_IMPORTANCE}..{MAX_IMPORTANCE}"
        )
    return value


def validate_audiences(audiences: frozenset[Audience]) -> frozenset[Audience]:
    """Require a non-empty Audience set."""
    if not audiences:
        raise ValidationError("audiences must not be empty")
    return audiences
```

- [ ] **Step 4: Add `now` to `MutationContext` and the engine**

In `src/sillytavern_rpg_engine/domain/operations.py`:

```python
@dataclass(frozen=True)
class MutationContext:
    campaign: Campaign
    branch_id: str
    next_state_version: int
    now: str
```

In `src/sillytavern_rpg_engine/services/mutations.py` `apply()`, compute the clock and id before applying the operation, and pass `now` into the context:

```python
next_version = campaign.state_version + 1
now = self.clock()
event_id = self.id_factory()
context = MutationContext(campaign, request.branch_id, next_version, now)
payload = request.operation.apply(connection, context)
```

Delete the later `now = self.clock()` / `event_id = self.id_factory()` lines; the rest of the method is unchanged.

- [ ] **Step 5: Run domain tests and the full suite**

Run: `python -m pytest tests/backend/unit/test_memory_domain.py -q && python -m pytest tests/backend -q`

Expected: new tests pass; all 46 pre-existing tests still pass (no test constructs `MutationContext` directly).

- [ ] **Step 6: Commit Task 1**

```bash
bash .harness/scripts/committer "feat(core): add memory domain contracts" \
  "src/sillytavern_rpg_engine/domain/memory.py" \
  "src/sillytavern_rpg_engine/domain/operations.py" \
  "src/sillytavern_rpg_engine/services/mutations.py" \
  "tests/backend/unit/test_memory_domain.py"
```

---

### Task 2: Migration 0003 — memory and personality schema

**Files:**
- Create: `src/sillytavern_rpg_engine/persistence/schema/0003_memory_personality.sql`
- Test: `tests/backend/unit/test_memory_schema.py`

**Interfaces:**
- Consumes: existing `MigrationRunner` (sorted packaged scripts, statement splitter handles single quotes).
- Produces: tables `facts`, `memory_events`, `memory_event_participants`, `relationships`, `trait_events`, `development_arcs`, `memory_summaries`; virtual table `memory_events_fts`; indexes `facts_current_key` (partial unique), `facts_campaign_current` (partial), `memory_events_branch`, `trait_events_turn` (partial unique), `trait_events_entity`, `development_arcs_open` (partial unique).

- [ ] **Step 1: Write failing schema tests**

```python
import sqlite3

import pytest

from sillytavern_rpg_engine.persistence.database import Database
from sillytavern_rpg_engine.persistence.migrations import MigrationRunner


def seed_campaign_entity(connection):
    connection.execute(
        "INSERT INTO campaigns(id, name, created_at, updated_at)"
        " VALUES ('c1', 'Campaign', '2026-08-10T00:00:00Z', '2026-08-10T00:00:00Z')"
    )
    connection.execute(
        "INSERT INTO branches(id, campaign_id, status, created_at)"
        " VALUES ('main', 'c1', 'active', '2026-08-10T00:00:00Z')"
    )
    connection.execute(
        "INSERT INTO entities(id, campaign_id, kind, name, normalized_name,"
        " age_status, created_state_version)"
        " VALUES ('erin', 'c1', 'character', '艾琳', '艾琳', 'adult', 0)"
    )


def test_memory_tables_and_fts_exist(database):
    with database.connect() as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }
        assert {
            "facts",
            "memory_events",
            "memory_event_participants",
            "memory_events_fts",
            "relationships",
            "trait_events",
            "development_arcs",
            "memory_summaries",
        } <= names


def test_trigram_fts_matches_chinese_substring(database):
    with database.transaction() as connection:
        seed_campaign_entity(connection)
        connection.execute(
            "INSERT INTO memory_events(id, campaign_id, branch_id, event_type,"
            " content, importance, audiences_json, source, state_version,"
            " created_at) VALUES ('e1', 'c1', 'main', 'scene',"
            " '艾琳在银月城学习炼金术', 3, '[\"engine\"]', 'test', 1,"
            " '2026-08-10T00:00:00Z')"
        )
        connection.execute(
            "INSERT INTO memory_events_fts(rowid, content)"
            " SELECT rowid, content FROM memory_events WHERE id = 'e1'"
        )
        hits = connection.execute(
            "SELECT m.id FROM memory_events m WHERE m.rowid IN ("
            " SELECT rowid FROM memory_events_fts"
            " WHERE memory_events_fts MATCH ?)",
            ('"银月城"',),
        ).fetchall()
        assert [row[0] for row in hits] == ["e1"]


def test_current_fact_partial_unique_index(database):
    with database.transaction() as connection:
        seed_campaign_entity(connection)
        connection.execute(
            "INSERT INTO facts(id, campaign_id, branch_id, entity_id, fact_type,"
            " fact_key, content, importance, audiences_json, valid_from, source,"
            " created_at) VALUES ('f1', 'c1', 'main', 'erin', 'identity',"
            " 'home', '银月城', 3, '[\"engine\"]', 1, 'test',"
            " '2026-08-10T00:00:00Z')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO facts(id, campaign_id, branch_id, entity_id,"
                " fact_type, fact_key, content, importance, audiences_json,"
                " valid_from, source, created_at) VALUES ('f2', 'c1', 'main',"
                " 'erin', 'identity', 'home', '凌云窟', 3, '[\"engine\"]', 2,"
                " 'test', '2026-08-10T00:00:00Z')"
            )
```

- [ ] **Step 2: Run schema tests and verify failure**

Run: `python -m pytest tests/backend/unit/test_memory_schema.py -q`

Expected: failures — `no such table: memory_events` / `no such table: facts`.

- [ ] **Step 3: Write `0003_memory_personality.sql`**

```sql
CREATE TABLE facts (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL,
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    fact_type TEXT NOT NULL
        CHECK (fact_type IN ('identity', 'commitment', 'quest', 'conflict',
                             'rule_consequence', 'general')),
    fact_key TEXT NOT NULL,
    content TEXT NOT NULL,
    importance INTEGER NOT NULL CHECK (importance BETWEEN 1 AND 5),
    audiences_json TEXT NOT NULL,
    valid_from INTEGER NOT NULL,
    valid_until INTEGER,
    superseded_by TEXT REFERENCES facts(id),
    turn_id TEXT,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (campaign_id, branch_id)
        REFERENCES branches(campaign_id, id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX facts_current_key
    ON facts(entity_id, fact_key) WHERE valid_until IS NULL;

CREATE INDEX facts_campaign_current
    ON facts(campaign_id, entity_id) WHERE valid_until IS NULL;

CREATE TABLE memory_events (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL,
    turn_id TEXT,
    event_type TEXT NOT NULL
        CHECK (event_type IN ('general', 'scene', 'identity', 'commitment',
                              'conflict', 'quest', 'rule_consequence',
                              'personality_shift')),
    content TEXT NOT NULL,
    importance INTEGER NOT NULL CHECK (importance BETWEEN 1 AND 5),
    audiences_json TEXT NOT NULL,
    location_entity_id TEXT REFERENCES entities(id),
    source TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (campaign_id, branch_id)
        REFERENCES branches(campaign_id, id) ON DELETE CASCADE
);

CREATE INDEX memory_events_branch
    ON memory_events(campaign_id, branch_id);

CREATE TABLE memory_event_participants (
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    event_id TEXT NOT NULL REFERENCES memory_events(id) ON DELETE CASCADE,
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    PRIMARY KEY (event_id, entity_id)
);

-- Plain FTS5 table (no external content): FTS5 validates content_rowid
-- against PRAGMA table_info of the content table, and memory_events.id is a
-- TEXT primary key, so its implicit rowid is not visible there. The index is
-- maintained manually in the same transaction as each event insert instead.
CREATE VIRTUAL TABLE memory_events_fts USING fts5(
    content,
    tokenize='trigram'
);

CREATE TABLE relationships (
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    from_entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    to_entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    dimension TEXT NOT NULL,
    value_json TEXT NOT NULL,
    audiences_json TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    updated_turn_id TEXT,
    PRIMARY KEY (from_entity_id, to_entity_id, dimension)
);

CREATE TABLE trait_events (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL,
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    trait_key TEXT NOT NULL,
    tier TEXT NOT NULL CHECK (tier IN ('normal', 'important', 'major')),
    before_value REAL NOT NULL,
    delta REAL NOT NULL,
    after_value REAL NOT NULL,
    cause TEXT NOT NULL,
    turn_id TEXT,
    source TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (campaign_id, branch_id)
        REFERENCES branches(campaign_id, id) ON DELETE CASCADE,
    FOREIGN KEY (campaign_id, trait_key)
        REFERENCES attribute_definitions(campaign_id, key) ON DELETE CASCADE
);

CREATE UNIQUE INDEX trait_events_turn
    ON trait_events(entity_id, trait_key, turn_id) WHERE turn_id IS NOT NULL;

CREATE INDEX trait_events_entity
    ON trait_events(campaign_id, entity_id, trait_key);

CREATE TABLE development_arcs (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL,
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    dimension TEXT NOT NULL,
    label TEXT NOT NULL,
    summary TEXT NOT NULL,
    source_event_ids_json TEXT NOT NULL,
    start_turn_id TEXT,
    end_turn_id TEXT,
    opened_state_version INTEGER NOT NULL,
    closed_state_version INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY (campaign_id, branch_id)
        REFERENCES branches(campaign_id, id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX development_arcs_open
    ON development_arcs(entity_id, dimension) WHERE closed_state_version IS NULL;

CREATE TABLE memory_summaries (
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL,
    scope TEXT NOT NULL
        CHECK (scope IN ('character', 'relationship', 'quest', 'plotline')),
    scope_key TEXT NOT NULL,
    content TEXT NOT NULL,
    audiences_json TEXT NOT NULL,
    source_event_ids_json TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (campaign_id, branch_id, scope, scope_key),
    FOREIGN KEY (campaign_id, branch_id)
        REFERENCES branches(campaign_id, id) ON DELETE CASCADE
);
```

- [ ] **Step 4: Run schema tests and migration regression**

Run: `python -m pytest tests/backend/unit/test_memory_schema.py tests/backend/unit/test_migrations.py -q`

Expected: all pass; `test_migrations.py` table-set assertion still passes (it uses `<=`).

- [ ] **Step 5: Commit Task 2**

```bash
bash .harness/scripts/committer "feat(storage): add memory and personality schema" \
  "src/sillytavern_rpg_engine/persistence/schema/0003_memory_personality.sql" \
  "tests/backend/unit/test_memory_schema.py"
```

---

### Task 3: Temporal facts

**Files:**
- Create: `src/sillytavern_rpg_engine/services/facts.py`
- Modify: `src/sillytavern_rpg_engine/services/snapshots.py`
- Modify: `src/sillytavern_rpg_engine/services/projection.py`
- Test: `tests/backend/unit/test_facts.py`

**Interfaces:**
- Consumes: `MutationContext.now`, `facts` table, `normalize_key()`, `dump_json()`.
- Produces: `AssertFactOperation`, `FactService.current(campaign_id, entity_id=None)`, `FactService.history(entity_id, fact_key)`; snapshot gains top-level `"facts"` list; projection entity dicts gain `"facts"` list.

- [ ] **Step 1: Write failing fact tests**

```python
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
```

- [ ] **Step 2: Run fact tests and verify failure**

Run: `python -m pytest tests/backend/unit/test_facts.py -q`

Expected: import fails because `services.facts` does not exist.

- [ ] **Step 3: Implement `services/facts.py`**

```python
"""Temporal facts: assert new values, supersede prior ones, never delete."""

from dataclasses import dataclass
import json
import sqlite3
from typing import Any

from ..domain.errors import NotFoundError, ValidationError
from ..domain.memory import Fact, FactType, validate_audiences, validate_importance
from ..domain.models import Audience
from ..domain.operations import MutationContext
from ..persistence.database import Database
from ..persistence.repositories import dump_json
from .entities import normalize_key


def _to_fact(row) -> Fact:
    """Map one facts row to the domain contract."""
    return Fact(
        id=row["id"],
        campaign_id=row["campaign_id"],
        branch_id=row["branch_id"],
        entity_id=row["entity_id"],
        fact_type=FactType(row["fact_type"]),
        fact_key=row["fact_key"],
        content=row["content"],
        importance=row["importance"],
        audiences=frozenset(Audience(a) for a in json.loads(row["audiences_json"])),
        valid_from=row["valid_from"],
        valid_until=row["valid_until"],
        superseded_by=row["superseded_by"],
        turn_id=row["turn_id"],
        source=row["source"],
        created_at=row["created_at"],
    )


_FACT_COLUMNS = (
    "id, campaign_id, branch_id, entity_id, fact_type, fact_key, content,"
    " importance, audiences_json, valid_from, valid_until, superseded_by,"
    " turn_id, source, created_at"
)


@dataclass(frozen=True)
class AssertFactOperation:
    """Assert one fact; the current fact with the same (entity, key) is
    superseded in the same transaction instead of overwritten or deleted."""

    fact_id: str
    entity_id: str
    fact_type: FactType
    fact_key: str
    content: str
    importance: int
    audiences: frozenset[Audience]
    turn_id: str | None = None

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict[str, Any]:
        if not self.content.strip():
            raise ValidationError("fact content must not be empty")
        validate_importance(self.importance)
        validate_audiences(self.audiences)
        if connection.execute(
            "SELECT id FROM entities WHERE id = ? AND campaign_id = ?",
            (self.entity_id, context.campaign.id),
        ).fetchone() is None:
            raise NotFoundError(
                f"entity {self.entity_id!r} not found in campaign {context.campaign.id}"
            )
        key = normalize_key(self.fact_key)
        current = connection.execute(
            "SELECT id, valid_from FROM facts"
            " WHERE entity_id = ? AND fact_key = ? AND valid_until IS NULL",
            (self.entity_id, key),
        ).fetchone()
        if current is not None:
            connection.execute(
                "UPDATE facts SET valid_until = ?, superseded_by = ? WHERE id = ?",
                (context.next_state_version, self.fact_id, current["id"]),
            )
        connection.execute(
            "INSERT INTO facts(id, campaign_id, branch_id, entity_id, fact_type,"
            " fact_key, content, importance, audiences_json, valid_from, turn_id,"
            " source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self.fact_id,
                context.campaign.id,
                context.branch_id,
                self.entity_id,
                self.fact_type.value,
                key,
                self.content,
                self.importance,
                dump_json(sorted(a.value for a in self.audiences)),
                context.next_state_version,
                self.turn_id,
                "user-command",
                context.now,
            ),
        )
        return {
            "fact_id": self.fact_id,
            "entity_id": self.entity_id,
            "fact_type": self.fact_type.value,
            "fact_key": key,
            "content": self.content,
            "importance": self.importance,
            "superseded_fact_id": current["id"] if current is not None else None,
        }


class FactService:
    """Reads current and historical facts; writes go through operations."""

    def __init__(self, database: Database):
        self.database = database

    def current(self, campaign_id: str, entity_id: str | None = None) -> list[Fact]:
        """Return current facts ordered by entity and key."""
        sql = f"SELECT {_FACT_COLUMNS} FROM facts WHERE campaign_id = ? AND valid_until IS NULL"
        params: list[Any] = [campaign_id]
        if entity_id is not None:
            sql += " AND entity_id = ?"
            params.append(entity_id)
        sql += " ORDER BY entity_id, fact_key"
        with self.database.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [_to_fact(row) for row in rows]

    def history(self, entity_id: str, fact_key: str) -> list[Fact]:
        """Return every version of one fact key, oldest first."""
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_FACT_COLUMNS} FROM facts"
                " WHERE entity_id = ? AND fact_key = ? ORDER BY valid_from",
                (entity_id, normalize_key(fact_key)),
            ).fetchall()
        return [_to_fact(row) for row in rows]
```

- [ ] **Step 4: Add facts to snapshot and projection**

In `snapshots.py` `build()`, add `"facts": facts` to the returned dict and implement:

```python
def _facts(self, connection, campaign_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT entity_id, fact_type, fact_key, content, importance,"
        " audiences_json, valid_from, turn_id, source FROM facts"
        " WHERE campaign_id = ? AND valid_until IS NULL"
        " ORDER BY entity_id, fact_key",
        (campaign_id,),
    ).fetchall()
    return [
        {
            "entity_id": row["entity_id"],
            "fact_type": row["fact_type"],
            "fact_key": row["fact_key"],
            "content": row["content"],
            "importance": row["importance"],
            "audiences": json.loads(row["audiences_json"]),
            "valid_from": row["valid_from"],
            "turn_id": row["turn_id"],
            "source": row["source"],
        }
        for row in rows
    ]
```

In `projection.py`, add a `_facts` read with the audience check in SQL and attach results to each entity dict:

```python
def _facts(
    self, connection, campaign_id: str, requested: frozenset[Audience]
) -> dict[str, list[dict[str, Any]]]:
    """Read current facts visible to the requested audiences, by entity."""
    audience_values = sorted(audience.value for audience in requested)
    placeholders = ", ".join("?" for _ in audience_values)
    rows = connection.execute(
        "SELECT entity_id, fact_type, fact_key, content, importance"
        " FROM facts f WHERE campaign_id = ? AND valid_until IS NULL"
        " AND EXISTS (SELECT 1 FROM json_each(f.audiences_json)"
        f" WHERE value IN ({placeholders}))"
        " ORDER BY entity_id, fact_key",
        (campaign_id, *audience_values),
    ).fetchall()
    facts: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        facts.setdefault(row["entity_id"], []).append(
            {
                "fact_type": row["fact_type"],
                "fact_key": row["fact_key"],
                "content": row["content"],
                "importance": row["importance"],
            }
        )
    return facts
```

`_entities` gains a `facts` parameter and each entity dict becomes `{"id", "kind", "name", "attributes", "facts": facts.get(entity_id, [])}`.

- [ ] **Step 5: Run fact tests and full suite**

Run: `python -m pytest tests/backend/unit/test_facts.py -q && python -m pytest tests/backend -q`

Expected: all pass, including Phase 1 tests (snapshot gains one key; projections gain one per-entity key — no existing assertion breaks).

- [ ] **Step 6: Commit Task 3**

```bash
bash .harness/scripts/committer "feat(core): add temporal facts" \
  "src/sillytavern_rpg_engine/services/facts.py" \
  "src/sillytavern_rpg_engine/services/snapshots.py" \
  "src/sillytavern_rpg_engine/services/projection.py" \
  "tests/backend/unit/test_facts.py"
```

---

### Task 4: Memory events and FTS5 index

**Files:**
- Create: `src/sillytavern_rpg_engine/services/memory_events.py`
- Test: `tests/backend/unit/test_memory_events.py`

**Interfaces:**
- Consumes: `memory_events`, `memory_event_participants`, `memory_events_fts` tables; `MutationEngine`.
- Produces: `RecordMemoryEventOperation`, `MemoryEventService.record()/.search()/.recent()/.for_entities()`, `MemoryIndexService.rebuild() -> int`.

- [ ] **Step 1: Write failing memory-event tests**

```python
import pytest

from sillytavern_rpg_engine.domain.errors import NotFoundError, ValidationError
from sillytavern_rpg_engine.domain.memory import MemoryEventType
from sillytavern_rpg_engine.domain.models import AgeStatus, Audience, EntityKind
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)
from sillytavern_rpg_engine.services.memory_events import (
    MemoryEventService,
    MemoryIndexService,
)

ALL = frozenset(Audience)


@pytest.fixture
def seeded(database):
    ids = iter(f"mem-{i}" for i in range(30))
    campaigns = CampaignService(
        database, id_factory=ids.__next__, clock=lambda: "2026-08-10T00:00:00Z"
    )
    campaigns.create_campaign("c1", "Campaign")
    state = EntityAttributeService(database, campaigns.mutation_engine)
    state.apply_explicit("c1", "main", 0, CreateEntityOperation(
        "erin", EntityKind.CHARACTER, "艾琳", AgeStatus.ADULT, ()))
    state.apply_explicit("c1", "main", 1, CreateEntityOperation(
        "silvermoon", EntityKind.LOCATION, "银月城", AgeStatus.UNKNOWN, ()))
    service = MemoryEventService(
        database, campaigns.mutation_engine,
        id_factory=ids.__next__, clock=lambda: "2026-08-10T00:00:00Z",
    )
    return service, state


def test_record_search_and_audience_filter(database, seeded):
    service, _ = seeded
    service.record("c1", "main", 2, type=MemoryEventType.SCENE,
                   content="艾琳在银月城的集市购买稀有药材", importance=3,
                   audiences=ALL, participants=("erin",),
                   location_entity_id="silvermoon", turn_id="turn-1")
    service.record("c1", "main", 3, type=MemoryEventType.IDENTITY,
                   content="艾琳其实是王国密探", importance=5,
                   audiences=frozenset({Audience.ENGINE}),
                   participants=("erin",), turn_id="turn-2")
    hits = service.search("c1", "main", ALL, "银月城")
    assert [event.content for event in hits] == ["艾琳在银月城的集市购买稀有药材"]
    player_hits = service.search("c1", "main", {Audience.PLAYER_UI}, "王国密探")
    assert player_hits == []
    engine_hits = service.search("c1", "main", {Audience.ENGINE}, "王国密探")
    assert engine_hits[0].participants == ("erin",)
    with pytest.raises(ValidationError, match="3 characters"):
        service.search("c1", "main", ALL, "银月")


def test_unknown_participant_and_index_rebuild(database, seeded):
    service, _ = seeded
    with pytest.raises(NotFoundError, match="ghost"):
        service.record("c1", "main", 2, type=MemoryEventType.SCENE,
                       content="不存在的人路过", importance=1, audiences=ALL,
                       participants=("ghost",))
    service.record("c1", "main", 2, type=MemoryEventType.SCENE,
                   content="艾琳在银月城张贴寻人告示", importance=2,
                   audiences=ALL, participants=("erin",))
    with database.transaction() as connection:
        connection.execute("DELETE FROM memory_events_fts")
    assert service.search("c1", "main", ALL, "寻人告示") == []
    assert MemoryIndexService(database).rebuild() == 1
    assert len(service.search("c1", "main", ALL, "寻人告示")) == 1


def test_recent_orders_pinned_before_newer_unpinned(database, seeded):
    service, _ = seeded
    service.record("c1", "main", 2, type=MemoryEventType.COMMITMENT,
                   content="艾琳承诺保护银月城的村民", importance=4,
                   audiences=ALL, participants=("erin",), turn_id="turn-1")
    version = 3
    for index in range(6):
        service.record("c1", "main", version, type=MemoryEventType.SCENE,
                       content=f"日常琐事 {index}", importance=2, audiences=ALL,
                       turn_id=f"turn-{version}")
        version += 1
    recent = service.recent("c1", "main", ALL, limit=3)
    assert recent[0].event_type is MemoryEventType.COMMITMENT
```

- [ ] **Step 2: Run memory-event tests and verify failure**

Run: `python -m pytest tests/backend/unit/test_memory_events.py -q`

Expected: import fails because `services.memory_events` does not exist.

- [ ] **Step 3: Implement `services/memory_events.py`**

```python
"""Permanent memory events, participant links, and the FTS5 search index."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Callable, Iterable
from uuid import uuid4

from ..domain.errors import NotFoundError, ValidationError
from ..domain.memory import (
    MemoryEvent,
    MemoryEventType,
    PINNED_EVENT_TYPES,
    validate_audiences,
    validate_importance,
)
from ..domain.models import Audience
from ..domain.operations import MutationContext
from ..persistence.database import Database
from ..persistence.repositories import dump_json
from .mutations import MutationEngine, MutationRequest, MutationResult

_EVENT_COLUMN_NAMES = (
    "id", "campaign_id", "branch_id", "turn_id", "event_type", "content",
    "importance", "audiences_json", "location_entity_id", "source",
    "state_version", "created_at",
)


def _event_columns(alias: str | None = None) -> str:
    """Comma-joined event columns, optionally table-qualified for joins."""
    if alias is None:
        return ", ".join(_EVENT_COLUMN_NAMES)
    return ", ".join(f"{alias}.{column}" for column in _EVENT_COLUMN_NAMES)


_PINNED_SQL_VALUES = tuple(sorted(event.value for event in PINNED_EVENT_TYPES))


def _audience_filter(column: str, audiences: Iterable[Audience]) -> tuple[str, list[str]]:
    """SQL EXISTS clause matching rows whose audience set intersects the
    requested one; filtering stays in the query, never in Python afterward."""
    values = sorted(audience.value for audience in audiences)
    placeholders = ", ".join("?" for _ in values)
    return (
        f" AND EXISTS (SELECT 1 FROM json_each({column})"
        f" WHERE value IN ({placeholders}))",
        values,
    )


@dataclass(frozen=True)
class RecordMemoryEventOperation:
    """Append one memory event, its participants, and its FTS row."""

    event_id: str
    event_type: MemoryEventType
    content: str
    importance: int
    audiences: frozenset[Audience]
    participants: tuple[str, ...] = ()
    location_entity_id: str | None = None
    turn_id: str | None = None
    source: str = "narrative_development"

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict[str, Any]:
        if not self.content.strip():
            raise ValidationError("memory event content must not be empty")
        validate_importance(self.importance)
        validate_audiences(self.audiences)
        for entity_id in (*self.participants, self.location_entity_id):
            if entity_id is None:
                continue
            if connection.execute(
                "SELECT id FROM entities WHERE id = ? AND campaign_id = ?",
                (entity_id, context.campaign.id),
            ).fetchone() is None:
                raise NotFoundError(
                    f"entity {entity_id!r} not found in campaign"
                    f" {context.campaign.id}"
                )
        connection.execute(
            "INSERT INTO memory_events(id, campaign_id, branch_id, turn_id,"
            " event_type, content, importance, audiences_json,"
            " location_entity_id, source, state_version, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self.event_id,
                context.campaign.id,
                context.branch_id,
                self.turn_id,
                self.event_type.value,
                self.content,
                self.importance,
                dump_json(sorted(a.value for a in self.audiences)),
                self.location_entity_id,
                self.source,
                context.next_state_version,
                context.now,
            ),
        )
        connection.execute(
            "INSERT INTO memory_events_fts(rowid, content)"
            " SELECT rowid, content FROM memory_events WHERE id = ?",
            (self.event_id,),
        )
        for entity_id in self.participants:
            connection.execute(
                "INSERT INTO memory_event_participants(campaign_id, event_id,"
                " entity_id) VALUES (?, ?, ?)",
                (context.campaign.id, self.event_id, entity_id),
            )
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "importance": self.importance,
            "participants": list(self.participants),
            "location_entity_id": self.location_entity_id,
            "turn_id": self.turn_id,
        }


class MemoryEventService:
    """Records events through the mutation engine and reads them back."""

    def __init__(
        self,
        database: Database,
        mutation_engine: MutationEngine | None = None,
        id_factory: Callable[[], str] = lambda: uuid4().hex,
        clock: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(),
    ):
        self.database = database
        self.mutation_engine = mutation_engine or MutationEngine(
            database, id_factory=id_factory, clock=clock
        )
        self.id_factory = id_factory

    def record(
        self,
        campaign_id: str,
        branch_id: str,
        expected_version: int,
        *,
        type: MemoryEventType,
        content: str,
        importance: int,
        audiences: frozenset[Audience],
        participants: tuple[str, ...] = (),
        location_entity_id: str | None = None,
        turn_id: str | None = None,
        source: str = "user-command",
    ) -> MutationResult:
        """Record one event as an atomic versioned mutation."""
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id,
                branch_id=branch_id,
                expected_version=expected_version,
                source=source,
                event_type="memory-event-recorded",
                operation=RecordMemoryEventOperation(
                    event_id=self.id_factory(),
                    event_type=type,
                    content=content,
                    importance=importance,
                    audiences=audiences,
                    participants=participants,
                    location_entity_id=location_entity_id,
                    turn_id=turn_id,
                    source=source,
                ),
            )
        )

    def search(
        self,
        campaign_id: str,
        branch_id: str,
        audiences: Iterable[Audience],
        text: str,
        limit: int = 20,
    ) -> list[MemoryEvent]:
        """Full-text search, audience-filtered, importance/recency ordered."""
        terms = text.strip()
        if len(terms) < 3:
            raise ValidationError(
                "FTS5 trigram search needs at least 3 characters"
            )
        phrase = '"' + terms.replace('"', '""') + '"'
        audience_sql, audience_values = _audience_filter(
            "m.audiences_json", audiences
        )
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_event_columns('m')} FROM memory_events m"
                " WHERE m.rowid IN (SELECT rowid FROM memory_events_fts"
                " WHERE memory_events_fts MATCH ?)"
                " AND m.campaign_id = ? AND m.branch_id = ?"
                f"{audience_sql}"
                " ORDER BY m.importance DESC, m.rowid DESC LIMIT ?",
                (phrase, campaign_id, branch_id, *audience_values, limit),
            ).fetchall()
            return [self._to_event(connection, row) for row in rows]

    def recent(
        self,
        campaign_id: str,
        branch_id: str,
        audiences: Iterable[Audience],
        limit: int = 20,
    ) -> list[MemoryEvent]:
        """Latest events, pinned types first, then importance, then recency."""
        audience_sql, audience_values = _audience_filter(
            "m.audiences_json", audiences
        )
        pinned = ", ".join(f"'{value}'" for value in _PINNED_SQL_VALUES)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_event_columns('m')} FROM memory_events m"
                " WHERE m.campaign_id = ? AND m.branch_id = ?"
                f"{audience_sql}"
                f" ORDER BY CASE WHEN m.event_type IN ({pinned}) THEN 1 ELSE 0"
                " END DESC, m.importance DESC, m.rowid DESC LIMIT ?",
                (campaign_id, branch_id, *audience_values, limit),
            ).fetchall()
            return [self._to_event(connection, row) for row in rows]

    def for_entities(
        self,
        campaign_id: str,
        branch_id: str,
        entity_ids: Iterable[str],
        audiences: Iterable[Audience],
        limit: int = 20,
    ) -> list[MemoryEvent]:
        """Latest events involving any of the given entities or locations."""
        ids = tuple(dict.fromkeys(entity_ids))
        if not ids:
            return []
        audience_sql, audience_values = _audience_filter(
            "m.audiences_json", audiences
        )
        placeholders = ", ".join("?" for _ in ids)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_event_columns('m')} FROM memory_events m"
                " LEFT JOIN memory_event_participants p"
                " ON p.event_id = m.id"
                " WHERE m.campaign_id = ? AND m.branch_id = ?"
                f" AND (p.entity_id IN ({placeholders})"
                f" OR m.location_entity_id IN ({placeholders}))"
                f"{audience_sql}"
                " GROUP BY m.id"
                " ORDER BY m.rowid DESC LIMIT ?",
                (campaign_id, branch_id, *ids, *ids, *audience_values, limit),
            ).fetchall()
            return [self._to_event(connection, row) for row in rows]

    def _to_event(self, connection, row) -> MemoryEvent:
        participants = tuple(
            item[0]
            for item in connection.execute(
                "SELECT entity_id FROM memory_event_participants"
                " WHERE event_id = ? ORDER BY entity_id",
                (row["id"],),
            ).fetchall()
        )
        return MemoryEvent(
            id=row["id"],
            campaign_id=row["campaign_id"],
            branch_id=row["branch_id"],
            event_type=MemoryEventType(row["event_type"]),
            content=row["content"],
            importance=row["importance"],
            audiences=frozenset(
                Audience(a) for a in json.loads(row["audiences_json"])
            ),
            participants=participants,
            location_entity_id=row["location_entity_id"],
            turn_id=row["turn_id"],
            source=row["source"],
            state_version=row["state_version"],
            created_at=row["created_at"],
        )


class MemoryIndexService:
    """Maintains the FTS index; rebuild derives it from the event table."""

    def __init__(self, database: Database):
        self.database = database

    def rebuild(self) -> int:
        """Drop and repopulate the FTS index; returns indexed event count."""
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM memory_events_fts")
            connection.execute(
                "INSERT INTO memory_events_fts(rowid, content)"
                " SELECT rowid, content FROM memory_events"
            )
            return connection.execute(
                "SELECT COUNT(*) FROM memory_events_fts"
            ).fetchone()[0]
```

`for_entities` uses `GROUP BY m.id` (not `SELECT DISTINCT`) so SQLite accepts `ORDER BY m.rowid`, which is not in the select list.

- [ ] **Step 4: Run memory-event tests**

Run: `python -m pytest tests/backend/unit/test_memory_events.py -q`

Expected: all pass.

- [ ] **Step 5: Commit Task 4**

```bash
bash .harness/scripts/committer "feat(core): add memory events with FTS5 search" \
  "src/sillytavern_rpg_engine/services/memory_events.py" \
  "tests/backend/unit/test_memory_events.py"
```

---

### Task 5: Directed relationships

**Files:**
- Create: `src/sillytavern_rpg_engine/services/relationships.py`
- Modify: `src/sillytavern_rpg_engine/services/snapshots.py`
- Modify: `src/sillytavern_rpg_engine/services/projection.py`
- Test: `tests/backend/unit/test_relationships.py`

**Interfaces:**
- Consumes: `relationships` table, entity existence, `dump_json()`.
- Produces: `DEFAULT_RELATIONSHIP_AUDIENCES`, `SetRelationshipOperation`, `RelationshipService.between(from_entity_id, to_entity_id)` and `.list_current(campaign_id)`; snapshot gains top-level `"relationships"`; projection entity dicts gain `"relationships"`.

- [ ] **Step 1: Write failing relationship tests**

```python
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
```

- [ ] **Step 2: Run relationship tests and verify failure**

Run: `python -m pytest tests/backend/unit/test_relationships.py -q`

Expected: import fails because `services.relationships` does not exist.

- [ ] **Step 3: Implement `services/relationships.py`**

```python
"""Directed, dimensioned relationship values between entities."""

from dataclasses import dataclass
import json
import sqlite3
from typing import Any

from ..domain.errors import NotFoundError, ValidationError
from ..domain.memory import Relationship, validate_audiences
from ..domain.models import Audience
from ..domain.operations import MutationContext
from ..persistence.database import Database
from ..persistence.repositories import dump_json
from .entities import normalize_key

DEFAULT_RELATIONSHIP_AUDIENCES = frozenset({Audience.ENGINE, Audience.NPC_AGENT})


def _to_relationship(row) -> Relationship:
    """Map one relationships row to the domain contract."""
    return Relationship(
        campaign_id=row["campaign_id"],
        from_entity_id=row["from_entity_id"],
        to_entity_id=row["to_entity_id"],
        dimension=row["dimension"],
        value=json.loads(row["value_json"]),
        audiences=frozenset(Audience(a) for a in json.loads(row["audiences_json"])),
        state_version=row["state_version"],
        updated_turn_id=row["updated_turn_id"],
    )


@dataclass(frozen=True)
class SetRelationshipOperation:
    """Upsert one directed relationship dimension; defaults to hidden
    audiences because precise relationship values are engine-internal."""

    from_entity_id: str
    to_entity_id: str
    dimension: str
    value: Any
    audiences: frozenset[Audience] | None = None
    turn_id: str | None = None

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict[str, Any]:
        if self.from_entity_id == self.to_entity_id:
            raise ValidationError("self-relationship is not allowed")
        for entity_id in (self.from_entity_id, self.to_entity_id):
            if connection.execute(
                "SELECT id FROM entities WHERE id = ? AND campaign_id = ?",
                (entity_id, context.campaign.id),
            ).fetchone() is None:
                raise NotFoundError(
                    f"entity {entity_id!r} not found in campaign"
                    f" {context.campaign.id}"
                )
        dimension = normalize_key(self.dimension)
        audiences = (
            self.audiences
            if self.audiences is not None
            else DEFAULT_RELATIONSHIP_AUDIENCES
        )
        validate_audiences(audiences)
        try:
            value_json = dump_json(self.value)
        except TypeError as exc:
            raise ValidationError(
                "relationship value must be JSON-serializable"
            ) from exc
        connection.execute(
            "INSERT INTO relationships(campaign_id, from_entity_id, to_entity_id,"
            " dimension, value_json, audiences_json, state_version,"
            " updated_turn_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(from_entity_id, to_entity_id, dimension) DO UPDATE SET"
            " value_json = excluded.value_json,"
            " audiences_json = excluded.audiences_json,"
            " state_version = excluded.state_version,"
            " updated_turn_id = excluded.updated_turn_id",
            (
                context.campaign.id,
                self.from_entity_id,
                self.to_entity_id,
                dimension,
                value_json,
                dump_json(sorted(a.value for a in audiences)),
                context.next_state_version,
                self.turn_id,
            ),
        )
        return {
            "from_entity_id": self.from_entity_id,
            "to_entity_id": self.to_entity_id,
            "dimension": dimension,
            "value": self.value,
            "audiences": sorted(a.value for a in audiences),
            "updated_turn_id": self.turn_id,
        }


class RelationshipService:
    """Reads current relationships; writes go through operations."""

    def __init__(self, database: Database):
        self.database = database

    def between(
        self, from_entity_id: str, to_entity_id: str
    ) -> list[Relationship]:
        """Return every dimension from one entity to another, by dimension."""
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT campaign_id, from_entity_id, to_entity_id, dimension,"
                " value_json, audiences_json, state_version, updated_turn_id"
                " FROM relationships WHERE from_entity_id = ? AND to_entity_id = ?"
                " ORDER BY dimension",
                (from_entity_id, to_entity_id),
            ).fetchall()
        return [_to_relationship(row) for row in rows]

    def list_current(self, campaign_id: str) -> list[Relationship]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT campaign_id, from_entity_id, to_entity_id, dimension,"
                " value_json, audiences_json, state_version, updated_turn_id"
                " FROM relationships WHERE campaign_id = ?"
                " ORDER BY from_entity_id, to_entity_id, dimension",
                (campaign_id,),
            ).fetchall()
        return [_to_relationship(row) for row in rows]
```

- [ ] **Step 4: Add relationships to snapshot and projection**

In `snapshots.py` `build()`, add `"relationships": relationships` and implement:

```python
def _relationships(self, connection, campaign_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT from_entity_id, to_entity_id, dimension, value_json,"
        " audiences_json, updated_turn_id FROM relationships"
        " WHERE campaign_id = ?"
        " ORDER BY from_entity_id, to_entity_id, dimension",
        (campaign_id,),
    ).fetchall()
    return [
        {
            "from_entity_id": row["from_entity_id"],
            "to_entity_id": row["to_entity_id"],
            "dimension": row["dimension"],
            "value": json.loads(row["value_json"]),
            "audiences": json.loads(row["audiences_json"]),
            "updated_turn_id": row["updated_turn_id"],
        }
        for row in rows
    ]
```

In `projection.py`, add a `_relationships` read shaped per entity (audience check in SQL, mirroring `_facts` from Task 3):

```python
def _relationships(
    self, connection, campaign_id: str, requested: frozenset[Audience]
) -> dict[str, list[dict[str, Any]]]:
    """Read outgoing relationships visible to the audiences, by source."""
    audience_values = sorted(audience.value for audience in requested)
    placeholders = ", ".join("?" for _ in audience_values)
    rows = connection.execute(
        "SELECT from_entity_id, to_entity_id, dimension, value_json"
        " FROM relationships r WHERE campaign_id = ?"
        " AND EXISTS (SELECT 1 FROM json_each(r.audiences_json)"
        f" WHERE value IN ({placeholders}))"
        " ORDER BY from_entity_id, to_entity_id, dimension",
        (campaign_id, *audience_values),
    ).fetchall()
    relationships: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        relationships.setdefault(row["from_entity_id"], []).append(
            {
                "to": row["to_entity_id"],
                "dimension": row["dimension"],
                "value": json.loads(row["value_json"]),
            }
        )
    return relationships
```

`_entities` also receives this mapping; entity dicts become `{"id", "kind", "name", "attributes", "facts", "relationships"}`.

- [ ] **Step 5: Run relationship tests and full suite**

Run: `python -m pytest tests/backend/unit/test_relationships.py -q && python -m pytest tests/backend -q`

Expected: all pass.

- [ ] **Step 6: Commit Task 5**

```bash
bash .harness/scripts/committer "feat(core): add directed relationships" \
  "src/sillytavern_rpg_engine/services/relationships.py" \
  "src/sillytavern_rpg_engine/services/snapshots.py" \
  "src/sillytavern_rpg_engine/services/projection.py" \
  "tests/backend/unit/test_relationships.py"
```

---

### Task 6: Trait change events with caps and inertia

**Files:**
- Create: `src/sillytavern_rpg_engine/services/traits.py`
- Test: `tests/backend/unit/test_trait_events.py`

**Interfaces:**
- Consumes: `attribute_definitions`/`attribute_values`, `trait_events`, `TRAIT_TIER_CAPS`, `TRAIT_INERTIA_WINDOW`, `TRAIT_INERTIA_CAP`, `validate_attribute_value()`.
- Produces: `RecordTraitEventOperation`, `TraitService.record_change()/.history()/.baseline()`.

- [ ] **Step 1: Write failing trait-event tests**

```python
import pytest

from sillytavern_rpg_engine.domain.errors import NotFoundError, ValidationError
from sillytavern_rpg_engine.domain.memory import TraitTier
from sillytavern_rpg_engine.domain.models import (
    AgeStatus,
    AttributeDefinition,
    AttributeType,
    Audience,
    DisplayType,
    EntityKind,
)
from sillytavern_rpg_engine.services.attributes import (
    DefineAttributeOperation,
    SetAttributeOperation,
)
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)
from sillytavern_rpg_engine.services.traits import TraitService

ALL = frozenset(Audience)


@pytest.fixture
def seeded(database):
    ids = iter(f"trait-{i}" for i in range(40))
    campaigns = CampaignService(
        database, id_factory=ids.__next__, clock=lambda: "2026-08-10T00:00:00Z"
    )
    campaigns.create_campaign("c1", "Campaign")
    state = EntityAttributeService(database, campaigns.mutation_engine)
    state.apply_explicit("c1", "main", 0, CreateEntityOperation(
        "erin", EntityKind.CHARACTER, "艾琳", AgeStatus.ADULT, ()))
    state.apply_explicit("c1", "main", 1, DefineAttributeOperation(
        AttributeDefinition("c1", "openness", "开放度", "trait",
                            AttributeType.NUMBER, DisplayType.BAR, ALL, 0, 100),
        (),
    ))
    state.apply_explicit("c1", "main", 2,
                         SetAttributeOperation("erin", "openness", 35, "turn-0"))
    service = TraitService(
        database, campaigns.mutation_engine,
        id_factory=ids.__next__, clock=lambda: "2026-08-10T00:00:00Z",
    )
    return service, state


def test_trait_event_updates_value_and_baseline(database, seeded):
    service, _ = seeded
    result = service.record_change(
        "c1", "main", 3, entity_id="erin", trait_key="openness",
        tier=TraitTier.IMPORTANT, delta=8, cause="连续多次主动探索新的关系观念",
        turn_id="turn-182",
    )
    assert result.snapshot["entities"][0]["attributes"][0]["value"] == 43
    history = service.history("erin", "openness")
    assert (history[0].before, history[0].delta, history[0].after) == (35, 8, 43)
    assert service.baseline("erin", "openness") == 35


def test_caps_inertia_duplicates_and_bounds(database, seeded):
    service, state = seeded
    with pytest.raises(ValidationError, match="cap"):
        service.record_change("c1", "main", 3, entity_id="erin",
                              trait_key="openness", tier=TraitTier.NORMAL,
                              delta=4, cause="小幅好奇", turn_id="t-1")
    service.record_change("c1", "main", 3, entity_id="erin", trait_key="openness",
                          tier=TraitTier.MAJOR, delta=20, cause="重大转折",
                          turn_id="t-2")
    with pytest.raises(ValidationError, match="inertia"):
        service.record_change("c1", "main", 4, entity_id="erin",
                              trait_key="openness", tier=TraitTier.IMPORTANT,
                              delta=8, cause="再次波动", turn_id="t-3")
    with pytest.raises(ValidationError, match="duplicate"):
        service.record_change("c1", "main", 4, entity_id="erin",
                              trait_key="openness", tier=TraitTier.MAJOR,
                              delta=20, cause="重大转折", turn_id="t-4")
    with pytest.raises(ValidationError, match="turn"):
        service.record_change("c1", "main", 4, entity_id="erin",
                              trait_key="openness", tier=TraitTier.NORMAL,
                              delta=1, cause="新证据", turn_id="t-2")
    with pytest.raises(NotFoundError):
        service.record_change("c1", "main", 4, entity_id="erin",
                              trait_key="curiosity", tier=TraitTier.NORMAL,
                              delta=1, cause="未初始化的维度", turn_id="t-6")
    state.apply_explicit("c1", "main", 4, DefineAttributeOperation(
        AttributeDefinition("c1", "risk", "风险容忍", "trait",
                            AttributeType.NUMBER, DisplayType.BAR, ALL, 0, 40),
        (),
    ))
    state.apply_explicit("c1", "main", 5,
                         SetAttributeOperation("erin", "risk", 35, "t-7"))
    with pytest.raises(ValidationError, match="range"):
        service.record_change("c1", "main", 6, entity_id="erin",
                              trait_key="risk", tier=TraitTier.MAJOR,
                              delta=20, cause="过度冒险", turn_id="t-8")
```

- [ ] **Step 2: Run trait tests and verify failure**

Run: `python -m pytest tests/backend/unit/test_trait_events.py -q`

Expected: import fails because `services.traits` does not exist.

- [ ] **Step 3: Implement `services/traits.py`**

```python
"""Personality trait change events: capped, inertia-checked, append-only."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Callable
from uuid import uuid4

from ..domain.errors import NotFoundError, ValidationError
from ..domain.memory import (
    TRAIT_INERTIA_CAP,
    TRAIT_INERTIA_WINDOW,
    TRAIT_TIER_CAPS,
    TraitEvent,
    TraitTier,
)
from ..domain.models import (
    AgeStatus,
    AttributeDefinition,
    AttributeType,
    Audience,
    DisplayType,
)
from ..domain.operations import MutationContext
from ..domain.validation import validate_attribute_value
from ..persistence.database import Database
from ..persistence.repositories import dump_json
from .attributes import ADULT_INTIMACY_CATEGORY
from .entities import normalize_key
from .mutations import MutationEngine, MutationRequest, MutationResult

TRAIT_CATEGORY = "trait"
_TRAIT_CATEGORIES = (TRAIT_CATEGORY, ADULT_INTIMACY_CATEGORY)


@dataclass(frozen=True)
class RecordTraitEventOperation:
    """Record one personality change and update the current trait value in
    the same transaction; the event log is the evidence, the value is state."""

    event_id: str
    entity_id: str
    trait_key: str
    tier: TraitTier
    delta: float
    cause: str
    turn_id: str | None = None
    source: str = "narrative_development"

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict[str, Any]:
        if not self.cause.strip():
            raise ValidationError("trait event cause must not be empty")
        if isinstance(self.delta, bool) or not isinstance(self.delta, (int, float)):
            raise ValidationError("delta must be a number")
        if self.delta == 0:
            raise ValidationError("delta must not be zero")
        entity = connection.execute(
            "SELECT id, age_status FROM entities WHERE id = ? AND campaign_id = ?",
            (self.entity_id, context.campaign.id),
        ).fetchone()
        if entity is None:
            raise NotFoundError(
                f"entity {self.entity_id!r} not found in campaign"
                f" {context.campaign.id}"
            )
        row = connection.execute(
            "SELECT key, label, category, value_type, display, audiences_json,"
            " minimum, maximum, enum_values_json, unit FROM attribute_definitions"
            " WHERE campaign_id = ? AND key = ?",
            (context.campaign.id, normalize_key(self.trait_key)),
        ).fetchone()
        if row is None or row["value_type"] not in (
            AttributeType.NUMBER.value,
            AttributeType.INTEGER.value,
        ) or row["category"] not in _TRAIT_CATEGORIES:
            raise NotFoundError(
                f"numeric trait {self.trait_key!r} not defined in campaign"
                f" {context.campaign.id}"
            )
        if (
            row["category"] == ADULT_INTIMACY_CATEGORY
            and entity["age_status"] != AgeStatus.ADULT.value
        ):
            raise ValidationError(
                "adult attribute requires a confirmed adult entity"
            )
        definition = AttributeDefinition(
            campaign_id=context.campaign.id,
            key=row["key"],
            label=row["label"],
            category=row["category"],
            value_type=AttributeType(row["value_type"]),
            display=DisplayType(row["display"]),
            audiences=frozenset(
                Audience(a) for a in json.loads(row["audiences_json"])
            ),
            minimum=row["minimum"],
            maximum=row["maximum"],
            enum_values=tuple(json.loads(row["enum_values_json"])),
            unit=row["unit"],
        )
        cap = TRAIT_TIER_CAPS[self.tier.value]
        if abs(self.delta) > cap:
            raise ValidationError(
                f"delta {self.delta} exceeds {self.tier.value} cap {cap}"
            )
        current = connection.execute(
            "SELECT value_json FROM attribute_values"
            " WHERE entity_id = ? AND attribute_key = ?",
            (self.entity_id, definition.key),
        ).fetchone()
        if current is None:
            raise NotFoundError(
                f"trait {definition.key!r} must be initialized with"
                " set_attribute before recording change events"
            )
        if self.turn_id is not None and connection.execute(
            "SELECT 1 FROM trait_events"
            " WHERE entity_id = ? AND trait_key = ? AND turn_id = ?",
            (self.entity_id, definition.key, self.turn_id),
        ).fetchone() is not None:
            raise ValidationError(
                "duplicate trait event for turn"
            )
        latest = connection.execute(
            "SELECT delta, cause FROM trait_events"
            " WHERE entity_id = ? AND trait_key = ? ORDER BY rowid DESC LIMIT 1",
            (self.entity_id, definition.key),
        ).fetchone()
        if (
            latest is not None
            and latest["cause"] == self.cause
            and latest["delta"] == self.delta
        ):
            raise ValidationError("duplicate trait event evidence")
        recent = connection.execute(
            "SELECT delta FROM trait_events"
            " WHERE entity_id = ? AND trait_key = ?"
            " ORDER BY rowid DESC LIMIT ?",
            (self.entity_id, definition.key, TRAIT_INERTIA_WINDOW),
        ).fetchall()
        inertia = sum(abs(item["delta"]) for item in recent) + abs(self.delta)
        if inertia > TRAIT_INERTIA_CAP:
            raise ValidationError(
                f"trait change exceeds inertia cap {TRAIT_INERTIA_CAP}"
                f" over last {TRAIT_INERTIA_WINDOW} events"
            )
        before = json.loads(current["value_json"])
        after = validate_attribute_value(definition, before + self.delta)
        connection.execute(
            "INSERT INTO trait_events(id, campaign_id, branch_id, entity_id,"
            " trait_key, tier, before_value, delta, after_value, cause, turn_id,"
            " source, state_version, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self.event_id,
                context.campaign.id,
                context.branch_id,
                self.entity_id,
                definition.key,
                self.tier.value,
                before,
                self.delta,
                after,
                self.cause,
                self.turn_id,
                self.source,
                context.next_state_version,
                context.now,
            ),
        )
        connection.execute(
            "INSERT INTO attribute_values(campaign_id, entity_id, attribute_key,"
            " value_json, state_version, updated_turn_id) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(entity_id, attribute_key) DO UPDATE SET"
            " value_json = excluded.value_json,"
            " state_version = excluded.state_version,"
            " updated_turn_id = excluded.updated_turn_id",
            (
                context.campaign.id,
                self.entity_id,
                definition.key,
                dump_json(after),
                context.next_state_version,
                self.turn_id,
            ),
        )
        return {
            "event_id": self.event_id,
            "entity_id": self.entity_id,
            "trait_key": definition.key,
            "tier": self.tier.value,
            "before": before,
            "delta": self.delta,
            "after": after,
            "cause": self.cause,
            "turn_id": self.turn_id,
        }


class TraitService:
    """Records trait changes and reads trait history and baselines."""

    def __init__(
        self,
        database: Database,
        mutation_engine: MutationEngine | None = None,
        id_factory: Callable[[], str] = lambda: uuid4().hex,
        clock: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(),
    ):
        self.database = database
        self.mutation_engine = mutation_engine or MutationEngine(
            database, id_factory=id_factory, clock=clock
        )
        self.id_factory = id_factory

    def record_change(
        self,
        campaign_id: str,
        branch_id: str,
        expected_version: int,
        *,
        entity_id: str,
        trait_key: str,
        tier: TraitTier,
        delta: float,
        cause: str,
        turn_id: str | None = None,
        source: str = "user-command",
    ) -> MutationResult:
        """Record one trait change as an atomic versioned mutation."""
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id,
                branch_id=branch_id,
                expected_version=expected_version,
                source=source,
                event_type="trait-change-recorded",
                operation=RecordTraitEventOperation(
                    event_id=self.id_factory(),
                    entity_id=entity_id,
                    trait_key=trait_key,
                    tier=tier,
                    delta=delta,
                    cause=cause,
                    turn_id=turn_id,
                    source=source,
                ),
            )
        )

    def history(self, entity_id: str, trait_key: str) -> list[TraitEvent]:
        """Return every change event of one dimension, oldest first."""
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT id, campaign_id, branch_id, entity_id, trait_key, tier,"
                " before_value, delta, after_value, cause, turn_id, source,"
                " state_version, created_at FROM trait_events"
                " WHERE entity_id = ? AND trait_key = ? ORDER BY rowid",
                (entity_id, normalize_key(trait_key)),
            ).fetchall()
        return [
            TraitEvent(
                id=row["id"],
                campaign_id=row["campaign_id"],
                branch_id=row["branch_id"],
                entity_id=row["entity_id"],
                trait_key=row["trait_key"],
                tier=TraitTier(row["tier"]),
                before=row["before_value"],
                delta=row["delta"],
                after=row["after_value"],
                cause=row["cause"],
                turn_id=row["turn_id"],
                source=row["source"],
                state_version=row["state_version"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def baseline(self, entity_id: str, trait_key: str) -> float | None:
        """Baseline = the before-value of the first recorded event, else the
        current attribute value, else None when the trait is unset."""
        key = normalize_key(trait_key)
        with self.database.connect() as connection:
            first = connection.execute(
                "SELECT before_value FROM trait_events"
                " WHERE entity_id = ? AND trait_key = ? ORDER BY rowid LIMIT 1",
                (entity_id, key),
            ).fetchone()
            if first is not None:
                return first["before_value"]
            current = connection.execute(
                "SELECT value_json FROM attribute_values"
                " WHERE entity_id = ? AND attribute_key = ?",
                (entity_id, key),
            ).fetchone()
        return json.loads(current["value_json"]) if current is not None else None
```

- [ ] **Step 4: Run trait tests and full suite**

Run: `python -m pytest tests/backend/unit/test_trait_events.py -q && python -m pytest tests/backend -q`

Expected: all pass.

- [ ] **Step 5: Commit Task 6**

```bash
bash .harness/scripts/committer "feat(core): add capped trait change events" \
  "src/sillytavern_rpg_engine/services/traits.py" \
  "tests/backend/unit/test_trait_events.py"
```

---

### Task 7: Development arcs

**Files:**
- Create: `src/sillytavern_rpg_engine/services/arcs.py`
- Test: `tests/backend/unit/test_arcs.py`

**Interfaces:**
- Consumes: `development_arcs`, `trait_events` (provenance), entity existence.
- Produces: `OpenArcOperation`, `CloseArcOperation`, `ArcService.list(entity_id, dimension=None)` and `.current(entity_id, dimension)`.

- [ ] **Step 1: Write failing arc tests**

```python
import pytest

from sillytavern_rpg_engine.domain.errors import ValidationError
from sillytavern_rpg_engine.domain.memory import TraitTier
from sillytavern_rpg_engine.domain.models import (
    AgeStatus,
    AttributeDefinition,
    AttributeType,
    Audience,
    DisplayType,
    EntityKind,
)
from sillytavern_rpg_engine.services.arcs import ArcService
from sillytavern_rpg_engine.services.attributes import (
    DefineAttributeOperation,
    SetAttributeOperation,
)
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)
from sillytavern_rpg_engine.services.traits import TraitService

ALL = frozenset(Audience)


@pytest.fixture
def seeded(database):
    ids = iter(f"arc-{i}" for i in range(40))
    campaigns = CampaignService(
        database, id_factory=ids.__next__, clock=lambda: "2026-08-10T00:00:00Z"
    )
    campaigns.create_campaign("c1", "Campaign")
    state = EntityAttributeService(database, campaigns.mutation_engine)
    state.apply_explicit("c1", "main", 0, CreateEntityOperation(
        "erin", EntityKind.CHARACTER, "艾琳", AgeStatus.ADULT, ()))
    state.apply_explicit("c1", "main", 1, DefineAttributeOperation(
        AttributeDefinition("c1", "openness", "开放度", "trait",
                            AttributeType.NUMBER, DisplayType.BAR, ALL, 0, 100),
        (),
    ))
    state.apply_explicit("c1", "main", 2,
                         SetAttributeOperation("erin", "openness", 35, "turn-0"))
    traits = TraitService(
        database, campaigns.mutation_engine,
        id_factory=ids.__next__, clock=lambda: "2026-08-10T00:00:00Z",
    )
    traits.record_change("c1", "main", 3, entity_id="erin", trait_key="openness",
                         tier=TraitTier.MAJOR, delta=8, cause="首次主动袒露过去",
                         turn_id="turn-1")
    return state, traits, ids


def test_open_arc_closes_previous_and_validates_provenance(database, seeded):
    state, traits, ids = seeded
    event_id = traits.history("erin", "openness")[0].id
    arcs = ArcService(database, state.mutation_engine, id_factory=ids.__next__,
                      clock=lambda: "2026-08-10T00:00:00Z")
    with pytest.raises(ValidationError, match="unknown trait events"):
        arcs.open("c1", "main", 4, entity_id="erin", dimension="openness",
                  label="好奇与矛盾", summary="开始动摇",
                  source_event_ids=("missing-event",), start_turn_id="turn-1")
    arcs.open("c1", "main", 4, entity_id="erin", dimension="openness",
              label="好奇与矛盾", summary="开始动摇",
              source_event_ids=(event_id,), start_turn_id="turn-1")
    first = arcs.current("erin", "openness")
    assert first.label == "好奇与矛盾"
    arcs.open("c1", "main", 5, entity_id="erin", dimension="openness",
              label="主动探索", summary="愿意尝试新关系",
              source_event_ids=(event_id,), start_turn_id="turn-9")
    arcs_list = arcs.list("erin", "openness")
    assert [arc.label for arc in arcs_list] == ["好奇与矛盾", "主动探索"]
    assert arcs_list[0].closed_state_version == 6
    assert arcs_list[0].end_turn_id == "turn-9"
    assert arcs.current("erin", "openness").label == "主动探索"


def test_close_arc(database, seeded):
    state, traits, ids = seeded
    event_id = traits.history("erin", "openness")[0].id
    arcs = ArcService(database, state.mutation_engine, id_factory=ids.__next__,
                      clock=lambda: "2026-08-10T00:00:00Z")
    arcs.open("c1", "main", 4, entity_id="erin", dimension="openness",
              label="好奇与矛盾", summary="开始动摇",
              source_event_ids=(event_id,), start_turn_id="turn-1")
    arc_id = arcs.current("erin", "openness").id
    arcs.close("c1", "main", 5, arc_id=arc_id, end_turn_id="turn-12")
    assert arcs.current("erin", "openness") is None
    with pytest.raises(ValidationError, match="not open"):
        arcs.close("c1", "main", 6, arc_id=arc_id)
```

- [ ] **Step 2: Run arc tests and verify failure**

Run: `python -m pytest tests/backend/unit/test_arcs.py -q`

Expected: import fails because `services.arcs` does not exist.

- [ ] **Step 3: Implement `services/arcs.py`**

```python
"""Development arcs: curated phases with trait-event provenance."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Callable
from uuid import uuid4

from ..domain.errors import NotFoundError, ValidationError
from ..domain.memory import DevelopmentArc
from ..domain.operations import MutationContext
from ..persistence.database import Database
from ..persistence.repositories import dump_json
from .entities import normalize_key
from .mutations import MutationEngine, MutationRequest, MutationResult

_ARC_COLUMNS = (
    "id, campaign_id, branch_id, entity_id, dimension, label, summary,"
    " source_event_ids_json, start_turn_id, end_turn_id,"
    " opened_state_version, closed_state_version, created_at"
)


def _to_arc(row) -> DevelopmentArc:
    """Map one development_arcs row to the domain contract."""
    return DevelopmentArc(
        id=row["id"],
        campaign_id=row["campaign_id"],
        branch_id=row["branch_id"],
        entity_id=row["entity_id"],
        dimension=row["dimension"],
        label=row["label"],
        summary=row["summary"],
        source_event_ids=tuple(json.loads(row["source_event_ids_json"])),
        start_turn_id=row["start_turn_id"],
        end_turn_id=row["end_turn_id"],
        opened_state_version=row["opened_state_version"],
        closed_state_version=row["closed_state_version"],
        created_at=row["created_at"],
    )


@dataclass(frozen=True)
class OpenArcOperation:
    """Open one arc; any open arc for the same (entity, dimension) closes
    atomically with the new arc's start turn."""

    arc_id: str
    entity_id: str
    dimension: str
    label: str
    summary: str
    source_event_ids: tuple[str, ...]
    start_turn_id: str | None = None

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict[str, Any]:
        if not self.label.strip() or not self.summary.strip():
            raise ValidationError("arc label and summary must not be empty")
        if not self.source_event_ids:
            raise ValidationError("arc must cite at least one source event")
        dimension = normalize_key(self.dimension)
        if connection.execute(
            "SELECT id FROM entities WHERE id = ? AND campaign_id = ?",
            (self.entity_id, context.campaign.id),
        ).fetchone() is None:
            raise NotFoundError(
                f"entity {self.entity_id!r} not found in campaign"
                f" {context.campaign.id}"
            )
        placeholders = ", ".join("?" for _ in self.source_event_ids)
        found = connection.execute(
            f"SELECT COUNT(*) FROM trait_events WHERE id IN ({placeholders})"
            " AND entity_id = ? AND trait_key = ?",
            (*self.source_event_ids, self.entity_id, dimension),
        ).fetchone()[0]
        if found != len(self.source_event_ids):
            raise ValidationError(
                "arc references unknown trait events for this entity/dimension"
            )
        closed = connection.execute(
            "UPDATE development_arcs"
            " SET closed_state_version = ?, end_turn_id = ?"
            " WHERE entity_id = ? AND dimension = ?"
            " AND closed_state_version IS NULL",
            (context.next_state_version, self.start_turn_id,
             self.entity_id, dimension),
        ).rowcount
        connection.execute(
            "INSERT INTO development_arcs(id, campaign_id, branch_id, entity_id,"
            " dimension, label, summary, source_event_ids_json, start_turn_id,"
            " opened_state_version, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self.arc_id,
                context.campaign.id,
                context.branch_id,
                self.entity_id,
                dimension,
                self.label,
                self.summary,
                dump_json(sorted(self.source_event_ids)),
                self.start_turn_id,
                context.next_state_version,
                context.now,
            ),
        )
        return {
            "arc_id": self.arc_id,
            "entity_id": self.entity_id,
            "dimension": dimension,
            "label": self.label,
            "closed_previous": closed == 1,
            "source_event_ids": sorted(self.source_event_ids),
        }


@dataclass(frozen=True)
class CloseArcOperation:
    """Close an open arc without opening a successor."""

    arc_id: str
    end_turn_id: str | None = None
    summary: str | None = None

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict[str, Any]:
        cursor = connection.execute(
            "UPDATE development_arcs"
            " SET closed_state_version = ?, end_turn_id = ?,"
            " summary = COALESCE(?, summary)"
            " WHERE id = ? AND closed_state_version IS NULL",
            (context.next_state_version, self.end_turn_id,
             self.summary, self.arc_id),
        )
        if cursor.rowcount == 0:
            raise ValidationError(f"arc {self.arc_id!r} is not open")
        return {"arc_id": self.arc_id, "closed": True}


class ArcService:
    """Opens/closes arcs through the mutation engine and reads them back."""

    def __init__(
        self,
        database: Database,
        mutation_engine: MutationEngine | None = None,
        id_factory: Callable[[], str] = lambda: uuid4().hex,
        clock: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(),
    ):
        self.database = database
        self.mutation_engine = mutation_engine or MutationEngine(
            database, id_factory=id_factory, clock=clock
        )
        self.id_factory = id_factory

    def open(
        self,
        campaign_id: str,
        branch_id: str,
        expected_version: int,
        *,
        entity_id: str,
        dimension: str,
        label: str,
        summary: str,
        source_event_ids: tuple[str, ...],
        start_turn_id: str | None = None,
        source: str = "user-command",
    ) -> MutationResult:
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id,
                branch_id=branch_id,
                expected_version=expected_version,
                source=source,
                event_type="development-arc-opened",
                operation=OpenArcOperation(
                    arc_id=self.id_factory(),
                    entity_id=entity_id,
                    dimension=dimension,
                    label=label,
                    summary=summary,
                    source_event_ids=source_event_ids,
                    start_turn_id=start_turn_id,
                ),
            )
        )

    def close(
        self,
        campaign_id: str,
        branch_id: str,
        expected_version: int,
        *,
        arc_id: str,
        end_turn_id: str | None = None,
        summary: str | None = None,
    ) -> MutationResult:
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id,
                branch_id=branch_id,
                expected_version=expected_version,
                source="user-command",
                event_type="development-arc-closed",
                operation=CloseArcOperation(arc_id, end_turn_id, summary),
            )
        )

    def list(
        self, entity_id: str, dimension: str | None = None
    ) -> list[DevelopmentArc]:
        """Return arcs of one entity, oldest first, optionally one dimension."""
        sql = f"SELECT {_ARC_COLUMNS} FROM development_arcs WHERE entity_id = ?"
        params: list[Any] = [entity_id]
        if dimension is not None:
            sql += " AND dimension = ?"
            params.append(normalize_key(dimension))
        sql += " ORDER BY opened_state_version"
        with self.database.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [_to_arc(row) for row in rows]

    def current(self, entity_id: str, dimension: str) -> DevelopmentArc | None:
        """Return the open arc for one dimension, or None."""
        with self.database.connect() as connection:
            row = connection.execute(
                f"SELECT {_ARC_COLUMNS} FROM development_arcs"
                " WHERE entity_id = ? AND dimension = ?"
                " AND closed_state_version IS NULL",
                (entity_id, normalize_key(dimension)),
            ).fetchone()
        return _to_arc(row) if row is not None else None
```

- [ ] **Step 4: Run arc tests**

Run: `python -m pytest tests/backend/unit/test_arcs.py -q`

Expected: all pass.

- [ ] **Step 5: Commit Task 7**

```bash
bash .harness/scripts/committer "feat(core): add development arcs" \
  "src/sillytavern_rpg_engine/services/arcs.py" \
  "tests/backend/unit/test_arcs.py"
```

---

### Task 8: Memory summaries with provenance

**Files:**
- Create: `src/sillytavern_rpg_engine/services/summaries.py`
- Test: `tests/backend/unit/test_summaries.py`

**Interfaces:**
- Consumes: `memory_summaries`, `memory_events` (provenance), `SummaryScope`.
- Produces: `UpsertSummaryOperation`, `SummaryService.get(scope, scope_key, campaign_id, branch_id)` and `.list(campaign_id, branch_id, scope=None)`.

- [ ] **Step 1: Write failing summary tests**

```python
import pytest

from sillytavern_rpg_engine.domain.errors import ValidationError
from sillytavern_rpg_engine.domain.memory import MemoryEventType, SummaryScope
from sillytavern_rpg_engine.domain.models import AgeStatus, Audience, EntityKind
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)
from sillytavern_rpg_engine.services.memory_events import MemoryEventService
from sillytavern_rpg_engine.services.summaries import SummaryService

ALL = frozenset(Audience)


@pytest.fixture
def seeded(database):
    ids = iter(f"sum-{i}" for i in range(30))
    campaigns = CampaignService(
        database, id_factory=ids.__next__, clock=lambda: "2026-08-10T00:00:00Z"
    )
    campaigns.create_campaign("c1", "Campaign")
    state = EntityAttributeService(database, campaigns.mutation_engine)
    state.apply_explicit("c1", "main", 0, CreateEntityOperation(
        "erin", EntityKind.CHARACTER, "艾琳", AgeStatus.ADULT, ()))
    events = MemoryEventService(
        database, campaigns.mutation_engine,
        id_factory=ids.__next__, clock=lambda: "2026-08-10T00:00:00Z",
    )
    events.record("c1", "main", 1, type=MemoryEventType.SCENE,
                  content="艾琳在银月城的集市购买稀有药材", importance=3,
                  audiences=ALL, participants=("erin",), turn_id="turn-1")
    summaries = SummaryService(
        database, campaigns.mutation_engine,
        id_factory=ids.__next__, clock=lambda: "2026-08-10T00:00:00Z",
    )
    return summaries, events


def test_summary_upsert_and_provenance(database, seeded):
    summaries, events = seeded
    event_id = events.recent("c1", "main", ALL)[0].id
    with pytest.raises(ValidationError, match="unknown memory events"):
        summaries.upsert("c1", "main", 2, scope=SummaryScope.CHARACTER,
                         scope_key="erin", content="半精灵炼金术师",
                         audiences=ALL, source_event_ids=("missing",))
    summaries.upsert("c1", "main", 2, scope=SummaryScope.CHARACTER,
                     scope_key="erin", content="半精灵炼金术师，谨慎",
                     audiences=ALL, source_event_ids=(event_id,))
    first = summaries.get(SummaryScope.CHARACTER, "erin", "c1", "main")
    assert first.source_event_ids == (event_id,)
    assert first.state_version == 3
    summaries.upsert("c1", "main", 3, scope=SummaryScope.CHARACTER,
                     scope_key="erin", content="谨慎但开始主动探索",
                     audiences=ALL, source_event_ids=(event_id,))
    updated = summaries.get(SummaryScope.CHARACTER, "erin", "c1", "main")
    assert updated.content == "谨慎但开始主动探索"
    assert updated.state_version == 4
    assert len(summaries.list("c1", "main")) == 1
```

- [ ] **Step 2: Run summary tests and verify failure**

Run: `python -m pytest tests/backend/unit/test_summaries.py -q`

Expected: import fails because `services.summaries` does not exist.

- [ ] **Step 3: Implement `services/summaries.py`**

```python
"""Rebuildable memory summaries with mandatory event provenance."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Callable
from uuid import uuid4

from ..domain.errors import ValidationError
from ..domain.memory import MemorySummary, SummaryScope, validate_audiences
from ..domain.models import Audience
from ..domain.operations import MutationContext
from ..persistence.database import Database
from ..persistence.repositories import dump_json
from .entities import normalize_key
from .mutations import MutationEngine, MutationRequest, MutationResult

_SUMMARY_COLUMNS = (
    "campaign_id, branch_id, scope, scope_key, content, audiences_json,"
    " source_event_ids_json, state_version, created_at"
)


def _to_summary(row) -> MemorySummary:
    """Map one memory_summaries row to the domain contract."""
    return MemorySummary(
        campaign_id=row["campaign_id"],
        branch_id=row["branch_id"],
        scope=SummaryScope(row["scope"]),
        scope_key=row["scope_key"],
        content=row["content"],
        audiences=frozenset(Audience(a) for a in json.loads(row["audiences_json"])),
        source_event_ids=tuple(json.loads(row["source_event_ids_json"])),
        state_version=row["state_version"],
        created_at=row["created_at"],
    )


@dataclass(frozen=True)
class UpsertSummaryOperation:
    """Insert or replace one summary; the cited events keep it rebuildable."""

    scope: SummaryScope
    scope_key: str
    content: str
    audiences: frozenset[Audience]
    source_event_ids: tuple[str, ...]

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict[str, Any]:
        if not self.content.strip():
            raise ValidationError("summary content must not be empty")
        validate_audiences(self.audiences)
        if not self.source_event_ids:
            raise ValidationError("summary must cite at least one source event")
        scope_key = normalize_key(self.scope_key)
        placeholders = ", ".join("?" for _ in self.source_event_ids)
        found = connection.execute(
            f"SELECT COUNT(*) FROM memory_events WHERE id IN ({placeholders})"
            " AND campaign_id = ? AND branch_id = ?",
            (*self.source_event_ids, context.campaign.id, context.branch_id),
        ).fetchone()[0]
        if found != len(self.source_event_ids):
            raise ValidationError(
                "summary references unknown memory events for this branch"
            )
        connection.execute(
            "INSERT INTO memory_summaries(campaign_id, branch_id, scope,"
            " scope_key, content, audiences_json, source_event_ids_json,"
            " state_version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(campaign_id, branch_id, scope, scope_key) DO UPDATE SET"
            " content = excluded.content,"
            " audiences_json = excluded.audiences_json,"
            " source_event_ids_json = excluded.source_event_ids_json,"
            " state_version = excluded.state_version",
            (
                context.campaign.id,
                context.branch_id,
                self.scope.value,
                scope_key,
                self.content,
                dump_json(sorted(a.value for a in self.audiences)),
                dump_json(sorted(self.source_event_ids)),
                context.next_state_version,
                context.now,
            ),
        )
        return {
            "scope": self.scope.value,
            "scope_key": scope_key,
            "content": self.content,
            "source_event_ids": sorted(self.source_event_ids),
        }


class SummaryService:
    """Writes summaries through the mutation engine and reads them back."""

    def __init__(
        self,
        database: Database,
        mutation_engine: MutationEngine | None = None,
        id_factory: Callable[[], str] = lambda: uuid4().hex,
        clock: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(),
    ):
        self.database = database
        self.mutation_engine = mutation_engine or MutationEngine(
            database, id_factory=id_factory, clock=clock
        )
        self.id_factory = id_factory

    def upsert(
        self,
        campaign_id: str,
        branch_id: str,
        expected_version: int,
        *,
        scope: SummaryScope,
        scope_key: str,
        content: str,
        audiences: frozenset[Audience],
        source_event_ids: tuple[str, ...],
    ) -> MutationResult:
        return self.mutation_engine.apply(
            MutationRequest(
                campaign_id=campaign_id,
                branch_id=branch_id,
                expected_version=expected_version,
                source="user-command",
                event_type="memory-summary-updated",
                operation=UpsertSummaryOperation(
                    scope=scope,
                    scope_key=scope_key,
                    content=content,
                    audiences=audiences,
                    source_event_ids=source_event_ids,
                ),
            )
        )

    def get(
        self,
        scope: SummaryScope,
        scope_key: str,
        campaign_id: str,
        branch_id: str,
    ) -> MemorySummary | None:
        with self.database.connect() as connection:
            row = connection.execute(
                f"SELECT {_SUMMARY_COLUMNS} FROM memory_summaries"
                " WHERE campaign_id = ? AND branch_id = ?"
                " AND scope = ? AND scope_key = ?",
                (campaign_id, branch_id, scope.value, normalize_key(scope_key)),
            ).fetchone()
        return _to_summary(row) if row is not None else None

    def list(
        self,
        campaign_id: str,
        branch_id: str,
        scope: SummaryScope | None = None,
    ) -> list[MemorySummary]:
        sql = (
            f"SELECT {_SUMMARY_COLUMNS} FROM memory_summaries"
            " WHERE campaign_id = ? AND branch_id = ?"
        )
        params: list[Any] = [campaign_id, branch_id]
        if scope is not None:
            sql += " AND scope = ?"
            params.append(scope.value)
        sql += " ORDER BY scope, scope_key"
        with self.database.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [_to_summary(row) for row in rows]
```

- [ ] **Step 4: Run summary tests**

Run: `python -m pytest tests/backend/unit/test_summaries.py -q`

Expected: all pass.

- [ ] **Step 5: Commit Task 8**

```bash
bash .harness/scripts/committer "feat(core): add memory summaries" \
  "src/sillytavern_rpg_engine/services/summaries.py" \
  "tests/backend/unit/test_summaries.py"
```

---

### Task 9: Tiered retrieval service

**Files:**
- Create: `src/sillytavern_rpg_engine/services/retrieval.py`
- Test: `tests/backend/unit/test_retrieval.py`

**Interfaces:**
- Consumes: `ProjectionService`, `MemoryEventService`, facts table, `trait_events` + `attribute_definitions` (audience join), `development_arcs`, `memory_summaries`.
- Produces: `RetrievalQuery`, `RetrievalService.assemble(query) -> dict` with keys `current_state`, `recent_events`, `related_events`, `scene_events`, `open_commitments`, `personality`, `summaries`.

- [ ] **Step 1: Write failing retrieval tests**

```python
from sillytavern_rpg_engine.domain.memory import (
    FactType,
    MemoryEventType,
    SummaryScope,
    TraitTier,
)
from sillytavern_rpg_engine.domain.models import (
    AgeStatus,
    AttributeDefinition,
    AttributeType,
    Audience,
    DisplayType,
    EntityKind,
)
from sillytavern_rpg_engine.services.arcs import ArcService
from sillytavern_rpg_engine.services.attributes import (
    DefineAttributeOperation,
    SetAttributeOperation,
)
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)
from sillytavern_rpg_engine.services.facts import AssertFactOperation
from sillytavern_rpg_engine.services.memory_events import MemoryEventService
from sillytavern_rpg_engine.services.retrieval import RetrievalQuery, RetrievalService
from sillytavern_rpg_engine.services.summaries import SummaryService
from sillytavern_rpg_engine.services.traits import TraitService

ALL = frozenset(Audience)


def build_world(database):
    """Seed one campaign and return helpers that track the state version."""
    ids = iter(f"ret-{i}" for i in range(200))
    clock = lambda: "2026-08-10T00:00:00Z"
    campaigns = CampaignService(database, id_factory=ids.__next__, clock=clock)
    campaigns.create_campaign("c1", "Campaign")
    state = EntityAttributeService(database, campaigns.mutation_engine)
    events = MemoryEventService(database, campaigns.mutation_engine,
                                id_factory=ids.__next__, clock=clock)
    traits = TraitService(database, campaigns.mutation_engine,
                          id_factory=ids.__next__, clock=clock)
    summaries = SummaryService(database, campaigns.mutation_engine,
                               id_factory=ids.__next__, clock=clock)
    arcs = ArcService(database, campaigns.mutation_engine,
                      id_factory=ids.__next__, clock=clock)
    version = 0

    def advance(result):
        nonlocal version
        version = result.state_version
        return result

    def apply(operation):
        return advance(state.apply_explicit("c1", "main", version, operation))

    def record(**kwargs):
        return advance(events.record("c1", "main", version, **kwargs))

    def change(**kwargs):
        return advance(traits.record_change("c1", "main", version, **kwargs))

    def open_arc(**kwargs):
        return advance(arcs.open("c1", "main", version, **kwargs))

    def upsert(**kwargs):
        return advance(summaries.upsert("c1", "main", version, **kwargs))

    apply(CreateEntityOperation("erin", EntityKind.CHARACTER, "艾琳", AgeStatus.ADULT, ()))
    apply(DefineAttributeOperation(AttributeDefinition(
        "c1", "openness", "开放度", "trait", AttributeType.NUMBER,
        DisplayType.BAR, ALL, 0, 100), ()))
    apply(SetAttributeOperation("erin", "openness", 35, "turn-0"))
    helpers = {
        "apply": apply, "record": record, "change": change,
        "open_arc": open_arc, "upsert": upsert,
    }
    return events, traits, helpers


def test_assemble_filters_audience_and_pins_commitments(database):
    events, traits, helpers = build_world(database)
    helpers["record"](type=MemoryEventType.COMMITMENT,
                      content="艾琳承诺保护银月城的村民", importance=4,
                      audiences=ALL, participants=("erin",), turn_id="turn-1")
    helpers["record"](type=MemoryEventType.IDENTITY,
                      content="艾琳其实是王国密探", importance=5,
                      audiences=frozenset({Audience.ENGINE}),
                      participants=("erin",), turn_id="turn-2")
    for index in range(8):
        helpers["record"](type=MemoryEventType.SCENE,
                          content=f"集市日常琐事编号 {index}",
                          importance=1, audiences=ALL, turn_id=f"noise-{index}")
    helpers["change"](entity_id="erin", trait_key="openness",
                      tier=TraitTier.NORMAL, delta=2,
                      cause="主动询问村民近况", turn_id="turn-3")
    trait_event_id = traits.history("erin", "openness")[0].id
    helpers["open_arc"](entity_id="erin", dimension="openness",
                        label="主动探索", summary="开始关心他人",
                        source_event_ids=(trait_event_id,), start_turn_id="turn-3")
    commitment_event_id = events.recent("c1", "main", ALL)[0].id
    helpers["upsert"](scope=SummaryScope.CHARACTER, scope_key="erin",
                      content="谨慎的炼金术师，开始主动关心他人",
                      audiences=ALL, source_event_ids=(commitment_event_id,))
    helpers["apply"](AssertFactOperation(
        fact_id="f-1", entity_id="erin", fact_type=FactType.COMMITMENT,
        fact_key="protect_villagers", content="保护银月城的村民", importance=4,
        audiences=ALL, turn_id="turn-1"))

    player = RetrievalService(database).assemble(RetrievalQuery(
        campaign_id="c1", branch_id="main",
        audiences=frozenset({Audience.PLAYER_UI}),
        text="银月城", scene_entity_ids=("erin",), limit=3,
    ))
    assert player["current_state"]["entities"][0]["name"] == "艾琳"
    assert player["recent_events"][0]["content"] == "艾琳承诺保护银月城的村民"
    assert all(
        "王国密探" not in event["content"] for event in player["recent_events"]
    )
    assert player["related_events"][0]["content"] == "艾琳承诺保护银月城的村民"
    assert player["scene_events"]
    assert player["open_commitments"][0]["content"] == "保护银月城的村民"
    personality = player["personality"]["erin"]
    assert personality["traits"] == [{"key": "openness", "value": 37}]
    assert personality["recent_trait_events"][0]["cause"] == "主动询问村民近况"
    assert personality["open_arcs"][0]["label"] == "主动探索"
    assert player["summaries"][0]["source_event_ids"] == [commitment_event_id]

    engine = RetrievalService(database).assemble(RetrievalQuery(
        campaign_id="c1", branch_id="main",
        audiences=frozenset({Audience.ENGINE}), limit=5,
    ))
    assert any(
        "王国密探" in event["content"] for event in engine["recent_events"]
    )
```

- [ ] **Step 2: Run retrieval tests and verify failure**

Run: `python -m pytest tests/backend/unit/test_retrieval.py -q`

Expected: import fails because `services.retrieval` does not exist.

- [ ] **Step 3: Implement `services/retrieval.py`**

```python
"""Tiered, audience-filtered memory context assembly for generation."""

from dataclasses import dataclass
import json
from typing import Any, Iterable

from ..domain.memory import PINNED_EVENT_TYPES
from ..domain.models import Audience
from ..persistence.database import Database
from ..persistence.repositories import BranchRepository, CampaignRepository
from .memory_events import MemoryEventService
from .projection import ProjectionService

_PINNED = tuple(sorted(event.value for event in PINNED_EVENT_TYPES))


@dataclass(frozen=True)
class RetrievalQuery:
    """One bounded context request; ``limit`` caps every section."""

    campaign_id: str
    branch_id: str
    audiences: frozenset[Audience]
    text: str | None = None
    scene_entity_ids: tuple[str, ...] = ()
    limit: int = 5


class RetrievalService:
    """Assemble the prompt-facing memory sections from committed state.

    Section order and row order are deterministic: pinned event types first,
    then importance, then recency. Nothing is ever deleted; unpinned old
    events simply fall below the limit ("forgetting" is ranking only).
    """

    def __init__(self, database: Database):
        self.database = database
        self.projections = ProjectionService(database)
        self.events = MemoryEventService(database)
        self.campaign_repository = CampaignRepository()
        self.branch_repository = BranchRepository()

    def assemble(self, query: RetrievalQuery) -> dict[str, Any]:
        with self.database.connect() as connection:
            self.campaign_repository.require(connection, query.campaign_id)
            self.branch_repository.require(
                connection, query.campaign_id, query.branch_id
            )
        return {
            "current_state": self.projections.for_audiences(
                query.campaign_id, query.branch_id, query.audiences
            ),
            "recent_events": self._event_dicts(
                self.events.recent(
                    query.campaign_id, query.branch_id, query.audiences,
                    limit=query.limit,
                )
            ),
            "related_events": self._event_dicts(
                self.events.search(
                    query.campaign_id, query.branch_id, query.audiences,
                    query.text, limit=query.limit,
                )
            ) if query.text else [],
            "scene_events": self._event_dicts(
                self.events.for_entities(
                    query.campaign_id, query.branch_id,
                    query.scene_entity_ids, query.audiences, limit=query.limit,
                )
            ),
            "open_commitments": self._open_commitments(query),
            "personality": self._personality(query),
            "summaries": self._summaries(query),
        }

    @staticmethod
    def _event_dicts(events) -> list[dict[str, Any]]:
        return [
            {
                "id": event.id,
                "event_type": event.event_type.value,
                "content": event.content,
                "importance": event.importance,
                "participants": list(event.participants),
                "location_entity_id": event.location_entity_id,
                "turn_id": event.turn_id,
            }
            for event in events
        ]

    def _open_commitments(self, query: RetrievalQuery) -> list[dict[str, Any]]:
        audience_values = sorted(a.value for a in query.audiences)
        placeholders = ", ".join("?" for _ in audience_values)
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT entity_id, fact_type, fact_key, content, importance"
                " FROM facts f"
                " WHERE campaign_id = ? AND valid_until IS NULL"
                " AND fact_type IN ('commitment', 'quest', 'conflict')"
                " AND EXISTS (SELECT 1 FROM json_each(f.audiences_json)"
                f" WHERE value IN ({placeholders}))"
                " ORDER BY importance DESC, valid_from DESC LIMIT ?",
                (query.campaign_id, *audience_values, query.limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def _personality(self, query: RetrievalQuery) -> dict[str, Any]:
        """Per scene entity: visible trait values, recent change evidence,
        and open development arcs (hidden dimensions excluded by join)."""
        if not query.scene_entity_ids:
            return {}
        audience_values = sorted(a.value for a in query.audiences)
        placeholders = ", ".join("?" for _ in audience_values)
        entity_placeholders = ", ".join("?" for _ in query.scene_entity_ids)
        result: dict[str, Any] = {}
        with self.database.connect() as connection:
            trait_rows = connection.execute(
                "SELECT v.entity_id, v.attribute_key, v.value_json"
                " FROM attribute_values v"
                " JOIN attribute_definitions d"
                " ON d.campaign_id = v.campaign_id AND d.key = v.attribute_key"
                " WHERE v.campaign_id = ? AND d.category IN ('trait', 'adult_intimacy')"
                f" AND v.entity_id IN ({entity_placeholders})"
                " AND EXISTS (SELECT 1 FROM json_each(d.audiences_json)"
                f" WHERE value IN ({placeholders}))"
                " ORDER BY v.entity_id, v.attribute_key",
                (query.campaign_id, *query.scene_entity_ids, *audience_values),
            ).fetchall()
            event_rows = connection.execute(
                "SELECT t.entity_id, t.trait_key, t.before_value, t.delta,"
                " t.after_value, t.cause, t.turn_id, t.tier FROM trait_events t"
                " JOIN attribute_definitions d"
                " ON d.campaign_id = t.campaign_id AND d.key = t.trait_key"
                " WHERE t.campaign_id = ? AND t.branch_id = ?"
                f" AND t.entity_id IN ({entity_placeholders})"
                " AND EXISTS (SELECT 1 FROM json_each(d.audiences_json)"
                f" WHERE value IN ({placeholders}))"
                " ORDER BY t.entity_id, t.trait_key, t.rowid DESC",
                (query.campaign_id, query.branch_id, *query.scene_entity_ids,
                 *audience_values),
            ).fetchall()
            arc_rows = connection.execute(
                "SELECT a.entity_id, a.dimension, a.label, a.summary,"
                " a.start_turn_id FROM development_arcs a"
                " JOIN attribute_definitions d"
                " ON d.campaign_id = a.campaign_id AND d.key = a.dimension"
                " WHERE a.campaign_id = ? AND a.branch_id = ?"
                " AND a.closed_state_version IS NULL"
                f" AND a.entity_id IN ({entity_placeholders})"
                " AND EXISTS (SELECT 1 FROM json_each(d.audiences_json)"
                f" WHERE value IN ({placeholders}))"
                " ORDER BY a.entity_id, a.dimension",
                (query.campaign_id, query.branch_id, *query.scene_entity_ids,
                 *audience_values),
            ).fetchall()
        for entity_id in query.scene_entity_ids:
            traits = [
                {"key": row["attribute_key"], "value": json.loads(row["value_json"])}
                for row in trait_rows if row["entity_id"] == entity_id
            ]
            seen: set[str] = set()
            evidence = []
            for row in event_rows:
                if row["entity_id"] != entity_id or row["trait_key"] in seen:
                    continue
                seen.add(row["trait_key"])
                evidence.append({
                    "trait_key": row["trait_key"],
                    "before": row["before_value"],
                    "delta": row["delta"],
                    "after": row["after_value"],
                    "cause": row["cause"],
                    "turn_id": row["turn_id"],
                    "tier": row["tier"],
                })
            arcs = [
                {"dimension": row["dimension"], "label": row["label"],
                 "summary": row["summary"], "start_turn_id": row["start_turn_id"]}
                for row in arc_rows if row["entity_id"] == entity_id
            ]
            if traits or evidence or arcs:
                result[entity_id] = {
                    "traits": traits,
                    "recent_trait_events": evidence,
                    "open_arcs": arcs,
                }
        return result

    def _summaries(self, query: RetrievalQuery) -> list[dict[str, Any]]:
        audience_values = sorted(a.value for a in query.audiences)
        placeholders = ", ".join("?" for _ in audience_values)
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT scope, scope_key, content, source_event_ids_json"
                " FROM memory_summaries s"
                " WHERE campaign_id = ? AND branch_id = ?"
                " AND EXISTS (SELECT 1 FROM json_each(s.audiences_json)"
                f" WHERE value IN ({placeholders}))"
                " ORDER BY scope, scope_key LIMIT ?",
                (query.campaign_id, query.branch_id, *audience_values, query.limit),
            ).fetchall()
        return [
            {
                "scope": row["scope"],
                "scope_key": row["scope_key"],
                "content": row["content"],
                "source_event_ids": json.loads(row["source_event_ids_json"]),
            }
            for row in rows
        ]
```

- [ ] **Step 4: Run retrieval tests and full suite**

Run: `python -m pytest tests/backend/unit/test_retrieval.py -q && python -m pytest tests/backend -q`

Expected: all pass.

- [ ] **Step 5: Commit Task 9**

```bash
bash .harness/scripts/committer "feat(core): add tiered memory retrieval" \
  "src/sillytavern_rpg_engine/services/retrieval.py" \
  "tests/backend/unit/test_retrieval.py"
```

---

### Task 10: Proposal codec, CLI rebuild, integration flow, docs

**Files:**
- Modify: `src/sillytavern_rpg_engine/services/proposals.py:31-53` (ALLOWED_KINDS, _EXPECTED_KEYS) and `OperationCodec.decode`
- Modify: `src/sillytavern_rpg_engine/cli.py` (new `rebuild-memory-index` subcommand)
- Create: `tests/backend/unit/test_proposal_memory_ops.py`
- Create: `tests/backend/integration/test_phase2_flow.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: every Phase 2 operation class.
- Produces: codec kinds `assert_fact`, `record_memory_event`, `record_trait_event`, `set_relationship`, `upsert_summary`, `open_arc`, `close_arc`; CLI `python -m sillytavern_rpg_engine rebuild-memory-index --database PATH`.

- [ ] **Step 1: Write failing codec tests**

```python
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
```

- [ ] **Step 2: Run codec tests and verify failure**

Run: `python -m pytest tests/backend/unit/test_proposal_memory_ops.py -q`

Expected: failures — `unsupported operation kind 'assert_fact'`.

- [ ] **Step 3: Extend `OperationCodec`**

In `proposals.py`, extend `ALLOWED_KINDS` and `_EXPECTED_KEYS`:

```python
ALLOWED_KINDS = frozenset({
    "create_entity",
    "define_attribute",
    "set_attribute",
    "set_rules",
    "assert_fact",
    "record_memory_event",
    "record_trait_event",
    "set_relationship",
    "upsert_summary",
    "open_arc",
    "close_arc",
})
```

```python
_EXPECTED_KEYS: dict[str, frozenset[str]] = {
    # ... existing Phase 1 entries (create_entity, define_attribute,
    # set_attribute, set_rules) stay unchanged ...
    "assert_fact": frozenset({
        "kind", "fact_id", "entity_id", "fact_type", "fact_key", "content",
        "importance", "audiences", "turn_id",
    }),
    "record_memory_event": frozenset({
        "kind", "event_id", "event_type", "content", "importance", "audiences",
        "participant_entity_ids", "location_entity_id", "turn_id", "source",
    }),
    "record_trait_event": frozenset({
        "kind", "event_id", "entity_id", "trait_key", "tier", "delta", "cause",
        "turn_id", "source",
    }),
    "set_relationship": frozenset({
        "kind", "from_entity_id", "to_entity_id", "dimension", "value",
        "audiences", "turn_id",
    }),
    "upsert_summary": frozenset({
        "kind", "scope", "scope_key", "content", "audiences",
        "source_event_ids",
    }),
    "open_arc": frozenset({
        "kind", "arc_id", "entity_id", "dimension", "label", "summary",
        "source_event_ids", "start_turn_id",
    }),
    "close_arc": frozenset({"kind", "arc_id", "end_turn_id", "summary"}),
}
```

Decode branches appended at the end of `OperationCodec.decode()` (all ID fields must be non-empty strings; reuse `_enum`, `_as_tuple`; `importance` goes through `validate_importance`; the `delta` numeric check lives in the operation):

```python
class OperationCodec:
    # ... existing docstring, ALLOWED_KINDS check, and the Phase 1
    # create_entity / define_attribute / set_attribute / set_rules branches
    # stay unchanged; the Phase 2 branches below append after them ...

    @staticmethod
    def decode(payload: dict[str, Any]) -> MutationOperation:
        kind = payload.get("kind")
        if kind == "assert_fact":
            return AssertFactOperation(
                fact_id=_as_id(payload, "fact_id"),
                entity_id=_as_id(payload, "entity_id"),
                fact_type=_enum(FactType, payload["fact_type"], "fact_type"),
                fact_key=payload["fact_key"],
                content=payload["content"],
                importance=validate_importance(payload["importance"]),
                audiences=frozenset(
                    _enum(Audience, a, "audience")
                    for a in _as_tuple(payload, "audiences")
                ),
                turn_id=payload["turn_id"],
            )
        if kind == "record_memory_event":
            return RecordMemoryEventOperation(
                event_id=_as_id(payload, "event_id"),
                event_type=_enum(
                    MemoryEventType, payload["event_type"], "event_type"
                ),
                content=payload["content"],
                importance=validate_importance(payload["importance"]),
                audiences=frozenset(
                    _enum(Audience, a, "audience")
                    for a in _as_tuple(payload, "audiences")
                ),
                participants=_as_tuple(payload, "participant_entity_ids"),
                location_entity_id=payload["location_entity_id"],
                turn_id=payload["turn_id"],
                source=payload["source"],
            )
        if kind == "record_trait_event":
            return RecordTraitEventOperation(
                event_id=_as_id(payload, "event_id"),
                entity_id=_as_id(payload, "entity_id"),
                trait_key=payload["trait_key"],
                tier=_enum(TraitTier, payload["tier"], "tier"),
                delta=payload["delta"],
                cause=payload["cause"],
                turn_id=payload["turn_id"],
                source=payload["source"],
            )
        if kind == "set_relationship":
            return SetRelationshipOperation(
                from_entity_id=payload["from_entity_id"],
                to_entity_id=payload["to_entity_id"],
                dimension=payload["dimension"],
                value=payload["value"],
                audiences=(
                    None
                    if payload["audiences"] is None
                    else frozenset(
                        _enum(Audience, a, "audience")
                        for a in _as_tuple(payload, "audiences")
                    )
                ),
                turn_id=payload["turn_id"],
            )
        if kind == "upsert_summary":
            return UpsertSummaryOperation(
                scope=_enum(SummaryScope, payload["scope"], "scope"),
                scope_key=payload["scope_key"],
                content=payload["content"],
                audiences=frozenset(
                    _enum(Audience, a, "audience")
                    for a in _as_tuple(payload, "audiences")
                ),
                source_event_ids=_as_tuple(payload, "source_event_ids"),
            )
        if kind == "open_arc":
            return OpenArcOperation(
                arc_id=_as_id(payload, "arc_id"),
                entity_id=_as_id(payload, "entity_id"),
                dimension=payload["dimension"],
                label=payload["label"],
                summary=payload["summary"],
                source_event_ids=_as_tuple(payload, "source_event_ids"),
                start_turn_id=payload["start_turn_id"],
            )
        return CloseArcOperation(
            arc_id=_as_id(payload, "arc_id"),
            end_turn_id=payload["end_turn_id"],
            summary=payload["summary"],
        )
```

Add the `_as_id` helper:

```python
def _as_id(payload: dict[str, Any], key: str) -> str:
    """Require a non-empty string identifier."""
    value = payload[key]
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{key} must be a non-empty string")
    return value
```

- [ ] **Step 4: Add the CLI command**

In `cli.py`, add the handler beside the other `_run_*` functions:

```python
def _run_rebuild_memory_index(args: argparse.Namespace) -> int:
    database = Database(args.database)
    try:
        count = MemoryIndexService(database).rebuild()
    except sqlite3.Error as exc:
        print(f"rebuild-memory-index failed: {exc}", file=sys.stderr)
        return 1
    print(f"Indexed {count} memory events")
    return 0
```

Import `MemoryIndexService` from `.services.memory_events`. Register the subcommand in `_build_parser()`:

```python
rebuild = subparsers.add_parser(
    "rebuild-memory-index",
    help="rebuild the FTS5 memory event index from the event table",
)
_add_database_argument(rebuild)
rebuild.set_defaults(handler=_run_rebuild_memory_index)
```

- [ ] **Step 5: Write the integration test**

`tests/backend/integration/test_phase2_flow.py` — one flow covering the whole phase across a restart:

```python
from pathlib import Path

from sillytavern_rpg_engine.cli import main
from sillytavern_rpg_engine.domain.memory import (
    FactType,
    MemoryEventType,
    SummaryScope,
    TraitTier,
)
from sillytavern_rpg_engine.domain.models import (
    AgeStatus,
    AttributeDefinition,
    AttributeType,
    Audience,
    DisplayType,
    EntityKind,
)
from sillytavern_rpg_engine.persistence.database import Database
from sillytavern_rpg_engine.persistence.migrations import MigrationRunner
from sillytavern_rpg_engine.services.arcs import ArcService
from sillytavern_rpg_engine.services.attributes import (
    DefineAttributeOperation,
    SetAttributeOperation,
)
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)
from sillytavern_rpg_engine.services.facts import AssertFactOperation, FactService
from sillytavern_rpg_engine.services.memory_events import MemoryEventService
from sillytavern_rpg_engine.services.proposals import ProposalService
from sillytavern_rpg_engine.services.retrieval import RetrievalQuery, RetrievalService
from sillytavern_rpg_engine.services.summaries import SummaryService
from sillytavern_rpg_engine.services.traits import TraitService

ALL = frozenset(Audience)
CLOCK = lambda: "2026-08-10T00:00:00Z"


def test_phase2_memory_and_personality_survive_restart(tmp_path: Path, capsys):
    path = tmp_path / "world.sqlite3"
    database = Database(path)
    MigrationRunner(database).apply()
    ids = iter(f"flow-{i}" for i in range(60))
    campaigns = CampaignService(database, id_factory=ids.__next__, clock=CLOCK)
    campaigns.create_campaign("story", "长篇战役")
    state = EntityAttributeService(database, campaigns.mutation_engine)
    state.apply_explicit("story", "main", 0, CreateEntityOperation(
        "erin", EntityKind.CHARACTER, "艾琳", AgeStatus.ADULT, ("Erin",)))
    state.apply_explicit("story", "main", 1, DefineAttributeOperation(
        AttributeDefinition("story", "openness", "开放度", "trait",
                            AttributeType.NUMBER, DisplayType.BAR, ALL, 0, 100),
        ("开放性",)))
    state.apply_explicit("story", "main", 2,
                         SetAttributeOperation("erin", "openness", 35, "turn-0"))

    events = MemoryEventService(database, campaigns.mutation_engine,
                                id_factory=ids.__next__, clock=CLOCK)
    traits = TraitService(database, campaigns.mutation_engine,
                          id_factory=ids.__next__, clock=CLOCK)
    summaries = SummaryService(database, campaigns.mutation_engine,
                               id_factory=ids.__next__, clock=CLOCK)
    arcs = ArcService(database, campaigns.mutation_engine,
                      id_factory=ids.__next__, clock=CLOCK)
    events.record("story", "main", 3, type=MemoryEventType.COMMITMENT,
                  content="艾琳承诺保护银月城的村民", importance=4,
                  audiences=ALL, participants=("erin",), turn_id="turn-1")
    traits.record_change("story", "main", 4, entity_id="erin",
                         trait_key="openness", tier=TraitTier.IMPORTANT,
                         delta=8, cause="连续多次主动探索新的关系观念",
                         turn_id="turn-2")
    trait_event_id = traits.history("erin", "openness")[0].id
    arcs.open("story", "main", 5, entity_id="erin", dimension="openness",
              label="主动探索", summary="从拘谨转向主动",
              source_event_ids=(trait_event_id,), start_turn_id="turn-2")
    memory_event_id = events.recent("story", "main", ALL)[0].id
    summaries.upsert("story", "main", 6, scope=SummaryScope.CHARACTER,
                     scope_key="erin", content="谨慎的炼金术师，开始主动探索",
                     audiences=ALL, source_event_ids=(memory_event_id,))
    state.apply_explicit("story", "main", 7, AssertFactOperation(
        fact_id="f-1", entity_id="erin", fact_type=FactType.COMMITMENT,
        fact_key="protect_villagers", content="保护银月城的村民", importance=4,
        audiences=ALL, turn_id="turn-1"))

    proposals = ProposalService(database, campaigns.mutation_engine,
                                id_factory=lambda: "p-flow-1", clock=CLOCK)
    proposals.create("story", "main", {
        "kind": "record_trait_event", "event_id": "t-proposed",
        "entity_id": "erin", "trait_key": "openness", "tier": "normal",
        "delta": 2, "cause": "主动安慰受惊的村民", "turn_id": "turn-3",
        "source": "narrative_development",
    }, reason="剧情表现出进一步开放")
    approved = proposals.approve("p-flow-1", expected_version=8)
    assert approved.snapshot["entities"][0]["attributes"][0]["value"] == 45

    reopened = Database(path)
    assert TraitService(reopened).baseline("erin", "openness") == 35
    assert [f.content for f in FactService(reopened).current("story", "erin")] == [
        "保护银月城的村民"
    ]
    assert ArcService(reopened).current("erin", "openness").label == "主动探索"
    context = RetrievalService(reopened).assemble(RetrievalQuery(
        campaign_id="story", branch_id="main", audiences=ALL,
        text="银月城", scene_entity_ids=("erin",), limit=5,
    ))
    assert context["recent_events"][0]["event_type"] == "commitment"
    assert context["related_events"][0]["content"] == "艾琳承诺保护银月城的村民"
    assert context["personality"]["erin"]["traits"] == [
        {"key": "openness", "value": 45}
    ]
    assert context["summaries"][0]["source_event_ids"] == [memory_event_id]

    assert main(["rebuild-memory-index", "--database", str(path)]) == 0
    assert "Indexed 1 memory events" in capsys.readouterr().out
```

- [ ] **Step 6: Run integration and codec tests**

Run: `python -m pytest tests/backend/unit/test_proposal_memory_ops.py tests/backend/integration -q`

Expected: all pass.

- [ ] **Step 7: Document the Phase 2 workflow**

Append to `README.md` a section:

```markdown
## RPG Engine Core (Phase 2)

Phase 2 adds long memory and personality on top of the Phase 1 core: temporally valid facts (superseded, never deleted), permanent audience-filtered memory events with FTS5 trigram search, directed relationships, capped and inertia-checked trait change events, provenance-linked development arcs and summaries, and a tiered retrieval service for future prompt assembly. Trait deltas are capped per tier (normal ≤ 3, important ≤ 8, major ≤ 20) and by a 25-point inertia window over the last 5 events. The FTS index is derivable and can be rebuilt at any time:

```bash
python -m sillytavern_rpg_engine rebuild-memory-index --database ./data/campaigns.sqlite3
```

Authoritative entity/attribute state remains campaign-global; branch-scoped divergence arrives with the Phase 6 branching plan.
```

- [ ] **Step 8: Run Phase 2 verification**

Run:

```bash
python -m pytest tests/backend -q
python -m sillytavern_rpg_engine --help
python .harness/scripts/guard.py src/sillytavern_rpg_engine tests/backend
npm run check
```

Expected: Python tests pass (46 Phase 1 + new Phase 2); CLI help lists `rebuild-memory-index`; guard reports 0 failures; JavaScript checks pass unchanged.

- [ ] **Step 9: Commit Task 10**

```bash
bash .harness/scripts/committer "feat(core): complete memory and personality layer" \
  "src/sillytavern_rpg_engine/services/proposals.py" \
  "src/sillytavern_rpg_engine/cli.py" \
  "tests/backend/unit/test_proposal_memory_ops.py" \
  "tests/backend/integration/test_phase2_flow.py" \
  "README.md"
```

---

## Phase 2 Exit Criteria

- `python -m pytest tests/backend -q` passes from a fresh editable install.
- A fact update closes the old version (`valid_until` + `superseded_by`) and keeps full history; one current fact per (entity, key) is DB-enforced.
- Memory events are append-only, carry participants/location/importance/audience, and are searchable in Chinese via trigram FTS5; the index rebuilds from the event table.
- Pinned event types (identity, commitment, rule consequence, personality shift) outrank newer unpinned events in retrieval.
- Trait changes enforce tier caps, the inertia window, duplicate-turn and duplicate-evidence guards, attribute min/max, and adult gating; baseline is derivable from history.
- Development arcs validate trait-event provenance; one open arc per (entity, dimension) is DB-enforced; opening closes the predecessor atomically.
- Summaries require existing source event IDs and stay upsertable per (branch, scope, key).
- Retrieval assembles all sections with audience filtering in SQL; player-audience assembly never contains engine-only events, facts, relationships, traits, arcs, or summaries.
- All seven new operation kinds decode through the strict proposal codec and apply atomically on approval with the same stale-version protection as Phase 1.
- Snapshots and projections include facts and relationships; every new write still produces exactly one version bump, audit event, outbox row, and snapshot.
- Restarting with a new `Database` instance restores identical memory and personality state.
- `rebuild-memory-index` CLI restores a wiped FTS index.
- Existing JavaScript DME tests and build remain unchanged and passing.
