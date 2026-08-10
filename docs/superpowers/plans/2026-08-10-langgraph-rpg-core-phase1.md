# LangGraph RPG Core Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the local authoritative state core for long-running RPG campaigns: SQLite persistence, campaign-scoped optional rules, versioned transactions, dynamic entities and attributes, Audience filtering, persistent proposals, snapshots, and JSONL audit export.

**Architecture:** Add a new Python package beside the existing JavaScript extension without changing the current DME runtime. Pure domain contracts describe campaigns and dynamic attributes; SQLite repositories own storage; one mutation engine performs compare-and-swap, audit, outbox, and snapshot writes in a single transaction. This phase exposes a Python service/CLI boundary only; FastAPI, LangGraph model calls, D&D calculations, long-memory retrieval, and the RPG Companion fork are separate follow-up plans.

**Tech Stack:** Python 3.11+, standard-library `sqlite3`, dataclasses, enums, JSON, pytest 8.x.

## Global Constraints

- The authoritative store is SQLite with WAL and foreign keys enabled.
- New campaigns default to `rules.mode = narrative` and `rules.enabled = false`.
- Supported rule identifiers in this phase are `narrative`, `dnd-2024`, and `custom:<preset_id>`; this phase persists selection but does not calculate D&D outcomes.
- Disabling D&D preserves all entity, attribute, snapshot, and audit data.
- LLMs never receive SQL or storage access; this phase contains no LLM dependency.
- Every authoritative mutation requires an expected state version and commits mutation, version, audit event, JSONL outbox row, and snapshot atomically.
- Hidden attributes are filtered by exact Audience membership; projections never load unrestricted state and filter afterward at the presentation boundary.
- Attributes in category `adult_intimacy` require `age_status = adult`; unknown and minor entities are rejected.
- Do not modify, delete, or migrate existing JavaScript DME state in this phase.
- Do not store API keys, secrets, machine-specific absolute paths, or credentials in campaign data or exports.
- Use `.harness/scripts/committer` for every commit and stage only files listed by the task.

## Multi-Plan Roadmap

The approved specification contains independent subsystems and is intentionally split:

1. **This plan:** authoritative Python core and persistence.
2. Memory and personality: fact validity, trait events, development arcs, FTS5 retrieval, summaries.
3. D&D 2024 / 5.5e: campaign readiness, dice, action economy, combat, conditions, Weapon Mastery, spell/resource transactions.
4. FastAPI and LangGraph: OpenAI-compatible endpoint, request normalization, model adapters, narrative/critic graph.
5. RPG Companion compatibility fork: dynamic `attributes`, rules metadata, read-only mode, narrowed JSON cleaning.
6. Branching and release: SillyTavern history hashes, swipe/edit/delete recovery, backups, long-run simulation, E2E.

## File Structure

```text
src/sillytavern_rpg_engine/
├── __init__.py                 # public package version and exports
├── __main__.py                 # local maintenance CLI entry
├── cli.py                      # init-db, verify, and export commands
├── domain/
│   ├── __init__.py
│   ├── errors.py               # stable domain exception types
│   ├── models.py               # enums and immutable value contracts
│   ├── operations.py           # mutation operation protocol and composite operation
│   └── validation.py           # dynamic value and rule-mode validation
├── persistence/
│   ├── __init__.py
│   ├── database.py             # connection setup and transaction context
│   ├── migrations.py           # ordered embedded migration runner
│   ├── repositories.py         # transaction-local SQL repositories
│   └── schema/
│       └── 0001_initial.sql    # Phase 1 schema
├── services/
│   ├── __init__.py
│   ├── campaigns.py            # campaign bootstrap and rules operations
│   ├── mutations.py            # CAS mutation coordinator
│   ├── entities.py             # entity and alias operations
│   ├── attributes.py           # definition/value operations
│   ├── proposals.py            # inferred proposal persistence and approval
│   ├── projection.py           # Audience-scoped read models
│   ├── snapshots.py            # canonical state snapshot builder
│   ├── audit_export.py         # idempotent SQLite-outbox to JSONL flush
│   └── campaign_export.py      # credential-free JSON export
tests/backend/
├── conftest.py
├── unit/
│   ├── test_domain_models.py
│   ├── test_migrations.py
│   ├── test_mutations.py
│   ├── test_campaign_rules.py
│   ├── test_dynamic_attributes.py
│   ├── test_projection.py
│   ├── test_proposals.py
│   └── test_audit_export.py
└── integration/
    └── test_phase1_flow.py
```

---

### Task 1: Python package and domain contracts

**Files:**
- Modify: `pyproject.toml`
- Create: `src/sillytavern_rpg_engine/__init__.py`
- Create: `src/sillytavern_rpg_engine/domain/__init__.py`
- Create: `src/sillytavern_rpg_engine/domain/errors.py`
- Create: `src/sillytavern_rpg_engine/domain/models.py`
- Create: `src/sillytavern_rpg_engine/domain/validation.py`
- Create: `tests/backend/unit/test_domain_models.py`

**Interfaces:**
- Consumes: no application code.
- Produces: `RulesMode`, `Audience`, `AgeStatus`, `EntityKind`, `AttributeType`, `DisplayType`, `ProposalStatus`, `CampaignRules`, `Campaign`, `Entity`, `AttributeDefinition`, `AttributeValue`, `DomainError`, `ValidationError`, `StaleStateError`, `NotFoundError`, `validate_attribute_value()`, and `validate_rules()`.

- [ ] **Step 1: Write failing domain tests**

```python
from dataclasses import FrozenInstanceError

import pytest

from sillytavern_rpg_engine.domain.errors import ValidationError
from sillytavern_rpg_engine.domain.models import (
    AgeStatus,
    AttributeDefinition,
    AttributeType,
    Audience,
    CampaignRules,
    DisplayType,
    RulesMode,
)
from sillytavern_rpg_engine.domain.validation import (
    validate_attribute_value,
    validate_rules,
)


def test_campaign_rules_default_to_narrative_disabled():
    rules = CampaignRules()
    assert rules.mode is RulesMode.NARRATIVE
    assert rules.enabled is False
    assert rules.version is None
    with pytest.raises(FrozenInstanceError):
        rules.enabled = True


def test_number_attribute_enforces_range_and_rejects_boolean():
    definition = AttributeDefinition(
        campaign_id="c1",
        key="alchemy",
        label="炼金术",
        category="skill",
        value_type=AttributeType.NUMBER,
        display=DisplayType.BAR,
        audiences=frozenset({Audience.ENGINE, Audience.PLAYER_UI}),
        minimum=0,
        maximum=100,
    )
    assert validate_attribute_value(definition, 35) == 35
    with pytest.raises(ValidationError, match="boolean is not a number"):
        validate_attribute_value(definition, True)
    with pytest.raises(ValidationError, match="outside range"):
        validate_attribute_value(definition, 101)


def test_dnd_rules_require_version_only_when_enabled():
    validate_rules(CampaignRules())
    validate_rules(CampaignRules(mode=RulesMode.DND_2024, enabled=False))
    with pytest.raises(ValidationError, match="version"):
        validate_rules(CampaignRules(mode=RulesMode.DND_2024, enabled=True))
```

- [ ] **Step 2: Run tests and verify import failure**

Run: `python -m pytest tests/backend/unit/test_domain_models.py -q`

Expected: collection fails with `ModuleNotFoundError: No module named 'sillytavern_rpg_engine'`.

- [ ] **Step 3: Configure the package**

Change `pyproject.toml` to use the new package while leaving JavaScript build configuration untouched:

```toml
[build-system]
requires = ["setuptools>=64.0"]
build-backend = "setuptools.build_meta"

[project]
name = "sillytavern-rpg-engine"
version = "0.1.0"
description = "Local authoritative RPG state engine for SillyTavern"
readme = "README.md"
requires-python = ">=3.11"
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=8.3,<9"]

[tool.setuptools]
package-dir = {"" = "src"}

[tool.setuptools.packages.find]
where = ["src"]
include = ["sillytavern_rpg_engine*"]

[tool.setuptools.package-data]
sillytavern_rpg_engine = ["persistence/schema/*.sql"]
```

- [ ] **Step 4: Implement immutable domain models and validation**

Use string enums and frozen dataclasses. Exact public shapes:

```python
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class RulesMode(StrEnum):
    NARRATIVE = "narrative"
    DND_2024 = "dnd-2024"
    CUSTOM = "custom"


class Audience(StrEnum):
    ENGINE = "engine"
    NPC_AGENT = "npc_agent"
    NARRATOR = "narrator"
    PLAYER_UI = "player_ui"


class AgeStatus(StrEnum):
    ADULT = "adult"
    MINOR = "minor"
    UNKNOWN = "unknown"


class EntityKind(StrEnum):
    CHARACTER = "character"
    LOCATION = "location"
    ORGANIZATION = "organization"
    ITEM = "item"


class AttributeType(StrEnum):
    NUMBER = "number"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    TEXT = "text"
    ENUM = "enum"
    LIST = "list"


class DisplayType(StrEnum):
    BAR = "bar"
    NUMBER = "number"
    BADGE = "badge"
    TEXT = "text"
    LIST = "list"
    PROGRESS = "progress"


class ProposalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    STALE = "stale"


@dataclass(frozen=True)
class CampaignRules:
    mode: RulesMode = RulesMode.NARRATIVE
    enabled: bool = False
    version: str | None = None
    custom_preset_id: str | None = None


@dataclass(frozen=True)
class Campaign:
    id: str
    name: str
    state_version: int
    rules: CampaignRules = field(default_factory=CampaignRules)


@dataclass(frozen=True)
class Entity:
    id: str
    campaign_id: str
    kind: EntityKind
    name: str
    age_status: AgeStatus = AgeStatus.UNKNOWN


@dataclass(frozen=True)
class AttributeDefinition:
    campaign_id: str
    key: str
    label: str
    category: str
    value_type: AttributeType
    display: DisplayType
    audiences: frozenset[Audience]
    minimum: float | None = None
    maximum: float | None = None
    enum_values: tuple[str, ...] = ()
    unit: str | None = None


@dataclass(frozen=True)
class AttributeValue:
    entity_id: str
    attribute_key: str
    value: Any
    state_version: int
    updated_turn_id: str | None = None
```

`validate_rules()` must reject enabled Narrative, enabled D&D without a version, Custom without `custom_preset_id`, and a non-Custom mode with `custom_preset_id`. `validate_attribute_value()` must distinguish `bool` from numeric values, enforce min/max and enum membership, and return a JSON-serializable value.

- [ ] **Step 5: Run domain tests**

Run: `python -m pytest tests/backend/unit/test_domain_models.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit Task 1**

```bash
bash .harness/scripts/committer "feat(core): add RPG domain contracts" \
  "pyproject.toml" \
  "src/sillytavern_rpg_engine/__init__.py" \
  "src/sillytavern_rpg_engine/domain/__init__.py" \
  "src/sillytavern_rpg_engine/domain/errors.py" \
  "src/sillytavern_rpg_engine/domain/models.py" \
  "src/sillytavern_rpg_engine/domain/validation.py" \
  "tests/backend/unit/test_domain_models.py"
```

---

### Task 2: SQLite connection and initial migration

**Files:**
- Create: `src/sillytavern_rpg_engine/persistence/__init__.py`
- Create: `src/sillytavern_rpg_engine/persistence/database.py`
- Create: `src/sillytavern_rpg_engine/persistence/migrations.py`
- Create: `src/sillytavern_rpg_engine/persistence/schema/0001_initial.sql`
- Create: `tests/backend/conftest.py`
- Create: `tests/backend/unit/test_migrations.py`

**Interfaces:**
- Consumes: domain enum string values.
- Produces: `Database(path: Path)`, `Database.connect()`, `Database.transaction()`, `MigrationRunner(database).apply()`, and the Phase 1 SQL schema.

- [ ] **Step 1: Write failing migration tests**

```python
import sqlite3

from sillytavern_rpg_engine.persistence.database import Database
from sillytavern_rpg_engine.persistence.migrations import MigrationRunner


def test_migration_creates_tables_and_enables_safety_pragmas(tmp_path):
    database = Database(tmp_path / "campaigns.sqlite3")
    MigrationRunner(database).apply()
    with database.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "campaigns",
            "branches",
            "entities",
            "entity_aliases",
            "attribute_definitions",
            "attribute_aliases",
            "attribute_values",
            "pending_proposals",
            "audit_events",
            "jsonl_outbox",
            "state_snapshots",
            "schema_migrations",
        } <= tables
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_transaction_rolls_back_all_writes(tmp_path):
    database = Database(tmp_path / "rollback.sqlite3")
    MigrationRunner(database).apply()
    try:
        with database.transaction() as connection:
            connection.execute(
                "INSERT INTO campaigns(id, name) VALUES (?, ?)",
                ("c1", "Campaign"),
            )
            raise RuntimeError("stop")
    except RuntimeError:
        pass
    with database.connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0]
    assert count == 0
```

- [ ] **Step 2: Run migration tests and verify failure**

Run: `python -m pytest tests/backend/unit/test_migrations.py -q`

Expected: import fails because `persistence.database` does not exist.

- [ ] **Step 3: Implement `Database`**

```python
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import sqlite3


class Database:
    """Open configured SQLite connections and explicit transactions."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()
```

Do not create parent directories implicitly; the caller must supply an existing, explicit data directory.

Create the shared test fixture in `tests/backend/conftest.py`:

```python
from pathlib import Path

import pytest

from sillytavern_rpg_engine.persistence.database import Database
from sillytavern_rpg_engine.persistence.migrations import MigrationRunner


@pytest.fixture
def database(tmp_path: Path) -> Database:
    value = Database(tmp_path / "test.sqlite3")
    MigrationRunner(value, clock=lambda: "2026-08-10T00:00:00Z").apply()
    return value
```

- [ ] **Step 4: Write the exact initial SQL migration**

The migration must define these invariants:

```sql
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE campaigns (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    state_version INTEGER NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    rules_mode TEXT NOT NULL DEFAULT 'narrative'
        CHECK (rules_mode IN ('narrative', 'dnd-2024', 'custom')),
    rules_enabled INTEGER NOT NULL DEFAULT 0 CHECK (rules_enabled IN (0, 1)),
    rules_version TEXT,
    custom_preset_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE branches (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    parent_branch_id TEXT REFERENCES branches(id),
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    UNIQUE(campaign_id, id)
);

CREATE TABLE entities (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    age_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (age_status IN ('adult', 'minor', 'unknown')),
    created_state_version INTEGER NOT NULL,
    UNIQUE(campaign_id, normalized_name)
);

CREATE TABLE entity_aliases (
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    PRIMARY KEY(campaign_id, normalized_alias)
);

CREATE TABLE attribute_definitions (
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    label TEXT NOT NULL,
    category TEXT NOT NULL,
    value_type TEXT NOT NULL,
    display TEXT NOT NULL,
    audiences_json TEXT NOT NULL,
    minimum REAL,
    maximum REAL,
    enum_values_json TEXT NOT NULL DEFAULT '[]',
    unit TEXT,
    created_state_version INTEGER NOT NULL,
    PRIMARY KEY(campaign_id, key)
);

CREATE TABLE attribute_aliases (
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    attribute_key TEXT NOT NULL,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    PRIMARY KEY(campaign_id, normalized_alias),
    FOREIGN KEY(campaign_id, attribute_key)
        REFERENCES attribute_definitions(campaign_id, key) ON DELETE CASCADE
);

CREATE TABLE attribute_values (
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    attribute_key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    updated_turn_id TEXT,
    PRIMARY KEY(entity_id, attribute_key),
    FOREIGN KEY(campaign_id, attribute_key)
        REFERENCES attribute_definitions(campaign_id, key) ON DELETE CASCADE
);

CREATE TABLE pending_proposals (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    base_state_version INTEGER NOT NULL,
    operation_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'stale')),
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE audit_events (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    state_version INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    source TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE jsonl_outbox (
    event_id TEXT PRIMARY KEY REFERENCES audit_events(id) ON DELETE CASCADE,
    payload_json TEXT NOT NULL,
    exported_at TEXT
);

CREATE TABLE state_snapshots (
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    branch_id TEXT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    state_version INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id, branch_id, state_version)
);
```

`MigrationRunner.apply()` reads packaged `schema/0001_initial.sql`, checks `schema_migrations`, applies unapplied scripts inside a transaction, and records an injected UTC ISO timestamp.

- [ ] **Step 5: Run migration tests**

Run: `python -m pytest tests/backend/unit/test_migrations.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit Task 2**

```bash
bash .harness/scripts/committer "feat(storage): add SQLite campaign schema" \
  "src/sillytavern_rpg_engine/persistence/__init__.py" \
  "src/sillytavern_rpg_engine/persistence/database.py" \
  "src/sillytavern_rpg_engine/persistence/migrations.py" \
  "src/sillytavern_rpg_engine/persistence/schema/0001_initial.sql" \
  "tests/backend/conftest.py" \
  "tests/backend/unit/test_migrations.py"
```

---

### Task 3: Repositories, snapshots, and mutation coordinator

**Files:**
- Create: `src/sillytavern_rpg_engine/domain/operations.py`
- Create: `src/sillytavern_rpg_engine/persistence/repositories.py`
- Create: `src/sillytavern_rpg_engine/services/__init__.py`
- Create: `src/sillytavern_rpg_engine/services/snapshots.py`
- Create: `src/sillytavern_rpg_engine/services/mutations.py`
- Create: `tests/backend/unit/test_mutations.py`

**Interfaces:**
- Consumes: `Database`, Phase 1 tables, domain errors and models.
- Produces: `MutationOperation.apply(connection, context)`, `CompositeOperation`, `MutationRequest`, `MutationResult`, `MutationContext`, `MutationEngine.apply()`, `CampaignRepository`, `AuditRepository`, `SnapshotRepository`, and `SnapshotBuilder.build()`.

- [ ] **Step 1: Write failing atomicity and stale-version tests**

```python
from dataclasses import dataclass
import sqlite3

import pytest

from sillytavern_rpg_engine.domain.errors import StaleStateError
from sillytavern_rpg_engine.domain.operations import MutationContext
from sillytavern_rpg_engine.services.mutations import MutationEngine, MutationRequest


@pytest.fixture
def seeded_campaign(database):
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO campaigns(id, name, created_at, updated_at)
            VALUES ('c1', 'Campaign', '2026-08-10T00:00:00Z', '2026-08-10T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO branches(id, campaign_id, status, created_at)
            VALUES ('main', 'c1', 'active', '2026-08-10T00:00:00Z')
            """
        )
    return "c1"


@dataclass(frozen=True)
class InsertMarker:
    marker: str

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        connection.execute(
            "UPDATE campaigns SET name = ? WHERE id = ?",
            (self.marker, context.campaign.id),
        )
        return {"marker": self.marker}


@dataclass(frozen=True)
class Explode:
    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        connection.execute(
            "UPDATE campaigns SET name = 'corrupt' WHERE id = ?",
            (context.campaign.id,),
        )
        raise RuntimeError("boom")


def test_mutation_commits_version_event_outbox_and_snapshot(database, seeded_campaign):
    engine = MutationEngine(database, id_factory=lambda: "event-1", clock=lambda: "2026-08-10T00:00:00Z")
    result = engine.apply(
        MutationRequest(
            campaign_id="c1",
            branch_id="main",
            expected_version=0,
            source="user-command",
            event_type="campaign-renamed",
            operation=InsertMarker("Renamed"),
        )
    )
    assert result.state_version == 1
    with database.connect() as connection:
        assert connection.execute("SELECT state_version FROM campaigns WHERE id = 'c1'").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM jsonl_outbox").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM state_snapshots WHERE state_version = 1").fetchone()[0] == 1


def test_mutation_rolls_back_and_rejects_stale_version(database, seeded_campaign):
    engine = MutationEngine(database, id_factory=lambda: "event-2", clock=lambda: "2026-08-10T00:00:00Z")
    with pytest.raises(RuntimeError, match="boom"):
        engine.apply(MutationRequest("c1", "main", 0, "test", "explode", Explode()))
    with database.connect() as connection:
        assert connection.execute("SELECT name FROM campaigns WHERE id = 'c1'").fetchone()[0] == "Campaign"
        assert connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0
    with pytest.raises(StaleStateError, match="expected 9, found 0"):
        engine.apply(MutationRequest("c1", "main", 9, "test", "stale", InsertMarker("No")))
```

- [ ] **Step 2: Run mutation tests and verify failure**

Run: `python -m pytest tests/backend/unit/test_mutations.py -q`

Expected: import fails because mutation modules do not exist.

- [ ] **Step 3: Implement operation and result contracts**

```python
from dataclasses import dataclass
from typing import Any, Protocol
import sqlite3

from .models import Campaign


@dataclass(frozen=True)
class MutationContext:
    campaign: Campaign
    branch_id: str
    next_state_version: int


class MutationOperation(Protocol):
    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict[str, Any]:
        """Apply transaction-local writes and return an audit-safe payload."""


@dataclass(frozen=True)
class CompositeOperation:
    operations: tuple[MutationOperation, ...]

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict[str, Any]:
        return {
            "operations": [operation.apply(connection, context) for operation in self.operations]
        }
```

`MutationRequest` fields are `campaign_id`, `branch_id`, `expected_version`, `source`, `event_type`, and `operation`. `MutationResult` fields are `campaign_id`, `branch_id`, `state_version`, `event_id`, and `snapshot`.

- [ ] **Step 4: Implement transaction-local repositories and snapshots**

`CampaignRepository.get()` returns a `Campaign`; `cas_bump()` performs this exact guard:

```sql
UPDATE campaigns
SET state_version = ?, updated_at = ?
WHERE id = ? AND state_version = ?
```

`SnapshotBuilder.build(connection, campaign_id, branch_id)` returns a deterministic JSON-compatible dictionary containing Campaign rules, ordered entities, ordered aliases, ordered attribute definitions, and ordered values. It must use `json.loads(value_json)` and must not include audit outbox state or credentials.

- [ ] **Step 5: Implement `MutationEngine.apply()`**

Within one `BEGIN IMMEDIATE` transaction:

```python
campaign = campaign_repository.require(connection, request.campaign_id)
if campaign.state_version != request.expected_version:
    raise StaleStateError(
        f"expected {request.expected_version}, found {campaign.state_version}"
    )
branch_repository.require(connection, request.campaign_id, request.branch_id)
next_version = campaign.state_version + 1
context = MutationContext(campaign, request.branch_id, next_version)
payload = request.operation.apply(connection, context)
campaign_repository.cas_bump(
    connection,
    campaign.id,
    campaign.state_version,
    next_version,
    now,
)
snapshot = snapshot_builder.build(connection, campaign.id, request.branch_id)
event = {
    "event_id": event_id,
    "campaign_id": campaign.id,
    "branch_id": request.branch_id,
    "state_version": next_version,
    "event_type": request.event_type,
    "source": request.source,
    "payload": payload,
    "created_at": now,
}
audit_repository.insert(connection, event)
outbox_repository.insert(connection, event_id, event)
snapshot_repository.insert(connection, campaign.id, request.branch_id, next_version, snapshot, now)
```

Return only after transaction commit. Serialize JSON with `ensure_ascii=False`, `sort_keys=True`, and compact separators for stable tests.

- [ ] **Step 6: Run mutation tests**

Run: `python -m pytest tests/backend/unit/test_mutations.py -q`

Expected: all tests pass.

- [ ] **Step 7: Commit Task 3**

```bash
bash .harness/scripts/committer "feat(core): add versioned mutation engine" \
  "src/sillytavern_rpg_engine/domain/operations.py" \
  "src/sillytavern_rpg_engine/persistence/repositories.py" \
  "src/sillytavern_rpg_engine/services/__init__.py" \
  "src/sillytavern_rpg_engine/services/snapshots.py" \
  "src/sillytavern_rpg_engine/services/mutations.py" \
  "tests/backend/unit/test_mutations.py"
```

---

### Task 4: Campaign bootstrap and optional rules lifecycle

**Files:**
- Create: `src/sillytavern_rpg_engine/services/campaigns.py`
- Create: `tests/backend/unit/test_campaign_rules.py`

**Interfaces:**
- Consumes: `Database`, `MutationEngine`, `CampaignRules`, repositories.
- Produces: `CampaignService.mutation_engine`, `CampaignService.create_campaign()`, `CampaignService.get_campaign()`, `SetRulesOperation`, `CampaignService.set_rules()`, and `CampaignService.disable_rules()`.

- [ ] **Step 1: Write failing campaign-rules tests**

```python
import pytest

from sillytavern_rpg_engine.domain.errors import ConfirmationRequiredError
from sillytavern_rpg_engine.domain.models import CampaignRules, RulesMode
from sillytavern_rpg_engine.services.campaigns import CampaignService


def test_campaign_defaults_to_narrative_and_rules_are_isolated(database):
    service = CampaignService(database, id_factory=iter(["event-c1", "event-c2", "event-c3"]).__next__, clock=lambda: "2026-08-10T00:00:00Z")
    first = service.create_campaign("c1", "Story")
    second = service.create_campaign("c2", "Dungeon")
    assert first.rules == CampaignRules()
    enabled = service.set_rules(
        "c2",
        expected_version=0,
        rules=CampaignRules(RulesMode.DND_2024, True, "5.2.1"),
    )
    assert enabled.snapshot["campaign"]["rules"]["mode"] == "dnd-2024"
    assert service.get_campaign("c1").rules == CampaignRules()


def test_disable_preserves_state_and_active_combat_requires_confirmation(database):
    service = CampaignService(database, id_factory=iter(["create", "enable", "disable"]).__next__, clock=lambda: "2026-08-10T00:00:00Z")
    service.create_campaign("c1", "Dungeon")
    service.set_rules("c1", 0, CampaignRules(RulesMode.DND_2024, True, "5.2.1"))
    with pytest.raises(ConfirmationRequiredError, match="active combat"):
        service.disable_rules("c1", 1, active_combat=True, confirmed=False)
    result = service.disable_rules("c1", 1, active_combat=True, confirmed=True)
    assert result.snapshot["campaign"]["rules"] == {
        "mode": "dnd-2024",
        "enabled": False,
        "version": "5.2.1",
        "custom_preset_id": None,
    }
```

- [ ] **Step 2: Run campaign tests and verify failure**

Run: `python -m pytest tests/backend/unit/test_campaign_rules.py -q`

Expected: import fails because `services.campaigns` does not exist.

- [ ] **Step 3: Implement campaign bootstrap**

`create_campaign()` must use one transaction to insert Campaign version 0, branch `main`, a `campaign-created` audit event/outbox row, and snapshot version 0. Duplicate Campaign IDs raise `ValidationError("campaign already exists")`.

- [ ] **Step 4: Implement rule lifecycle**

```python
@dataclass(frozen=True)
class SetRulesOperation:
    rules: CampaignRules

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        validate_rules(self.rules)
        connection.execute(
            """
            UPDATE campaigns
            SET rules_mode = ?, rules_enabled = ?, rules_version = ?, custom_preset_id = ?
            WHERE id = ?
            """,
            (
                self.rules.mode.value,
                int(self.rules.enabled),
                self.rules.version,
                self.rules.custom_preset_id,
                context.campaign.id,
            ),
        )
        return {"rules": asdict(self.rules)}
```

`disable_rules()` retains mode, version, and custom preset, changing only `enabled=False`. If `active_combat and not confirmed`, raise `ConfirmationRequiredError` before entering a write transaction.

- [ ] **Step 5: Run campaign tests**

Run: `python -m pytest tests/backend/unit/test_campaign_rules.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit Task 4**

```bash
bash .harness/scripts/committer "feat(core): add campaign rule modes" \
  "src/sillytavern_rpg_engine/services/campaigns.py" \
  "tests/backend/unit/test_campaign_rules.py"
```

---

### Task 5: Dynamic entities, aliases, definitions, and values

**Files:**
- Create: `src/sillytavern_rpg_engine/services/entities.py`
- Create: `src/sillytavern_rpg_engine/services/attributes.py`
- Create: `tests/backend/unit/test_dynamic_attributes.py`

**Interfaces:**
- Consumes: `MutationEngine`, operation protocol, domain validation, entity/attribute SQL tables.
- Produces: `normalize_key()`, `CreateEntityOperation`, `DefineAttributeOperation`, `SetAttributeOperation`, `EntityService.resolve()`, and `EntityAttributeService.apply_explicit()`.

- [ ] **Step 1: Write failing dynamic-state tests**

```python
import pytest

from sillytavern_rpg_engine.domain.errors import ValidationError
from sillytavern_rpg_engine.domain.models import (
    AgeStatus,
    AttributeDefinition,
    AttributeType,
    Audience,
    DisplayType,
    EntityKind,
)
from sillytavern_rpg_engine.domain.operations import CompositeOperation
from sillytavern_rpg_engine.services.attributes import (
    DefineAttributeOperation,
    SetAttributeOperation,
)
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import CreateEntityOperation, EntityAttributeService


@pytest.fixture
def campaign_service(database):
    service = CampaignService(
        database,
        id_factory=iter(f"event-{index}" for index in range(20)).__next__,
        clock=lambda: "2026-08-10T00:00:00Z",
    )
    service.create_campaign("c1", "Campaign")
    return service


def test_one_explicit_command_creates_person_skill_and_value(database, campaign_service):
    service = EntityAttributeService(database, campaign_service.mutation_engine)
    result = service.apply_explicit(
        campaign_id="c1",
        branch_id="main",
        expected_version=0,
        operation=CompositeOperation((
            CreateEntityOperation("erin", EntityKind.CHARACTER, "艾琳", AgeStatus.ADULT, ("Erin",)),
            DefineAttributeOperation(AttributeDefinition(
                "c1", "alchemy", "炼金术", "skill", AttributeType.NUMBER,
                DisplayType.BAR, frozenset({Audience.ENGINE, Audience.PLAYER_UI}), 0, 100,
            ), ("炼金", "Alchemy")),
            SetAttributeOperation("erin", "alchemy", 35, None),
        )),
    )
    assert result.state_version == 1
    assert result.snapshot["entities"][0]["attributes"][0]["value"] == 35


def test_aliases_are_unique_and_adult_attributes_require_adult_entity(database, campaign_service):
    service = EntityAttributeService(database, campaign_service.mutation_engine)
    service.apply_explicit("c1", "main", 0, CreateEntityOperation(
        "erin", EntityKind.CHARACTER, "艾琳", AgeStatus.UNKNOWN, ("Erin",)
    ))
    with pytest.raises(ValidationError, match="confirmed adult"):
        service.apply_explicit("c1", "main", 1, CompositeOperation((
            DefineAttributeOperation(AttributeDefinition(
                "c1", "intimacy_openness", "亲密开放度", "adult_intimacy",
                AttributeType.NUMBER, DisplayType.BAR,
                frozenset({Audience.ENGINE}), 0, 100,
            ), ()),
            SetAttributeOperation("erin", "intimacy_openness", 40, None),
        )))
```

- [ ] **Step 2: Run dynamic-state tests and verify failure**

Run: `python -m pytest tests/backend/unit/test_dynamic_attributes.py -q`

Expected: imports fail because entity and attribute services do not exist.

- [ ] **Step 3: Implement canonical normalization and entity operations**

`normalize_key()` performs Unicode NFKC, trims, casefolds, collapses whitespace, and converts non-alphanumeric runs to `_`. Reject an empty result. `CreateEntityOperation` inserts the entity and all aliases at `context.next_state_version`; translate unique-name and unique-alias `sqlite3.IntegrityError` into stable `ValidationError` messages.

`EntityService.resolve(campaign_id, name_or_alias)` queries normalized primary names and aliases. Return one `Entity`, raise `NotFoundError` for zero, and raise `AmbiguousEntityError` if storage corruption or future fuzzy candidates yield more than one.

- [ ] **Step 4: Implement definition and value operations**

`DefineAttributeOperation` validates that the definition Campaign matches the mutation Campaign, serializes Audience and enum values as sorted JSON arrays, and writes aliases. `SetAttributeOperation` loads entity and definition within the same transaction, applies adult-category gating, calls `validate_attribute_value()`, and upserts `attribute_values` with `context.next_state_version`.

`EntityAttributeService.apply_explicit()` submits the operation to `MutationEngine` with `source="user-command"` and `event_type="explicit-state-change"`.

- [ ] **Step 5: Run dynamic-state tests**

Run: `python -m pytest tests/backend/unit/test_dynamic_attributes.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit Task 5**

```bash
bash .harness/scripts/committer "feat(core): add dynamic entity attributes" \
  "src/sillytavern_rpg_engine/services/entities.py" \
  "src/sillytavern_rpg_engine/services/attributes.py" \
  "tests/backend/unit/test_dynamic_attributes.py"
```

---

### Task 6: Audience-scoped projections

**Files:**
- Create: `src/sillytavern_rpg_engine/services/projection.py`
- Create: `tests/backend/unit/test_projection.py`

**Interfaces:**
- Consumes: Campaign, entity, definition, and value tables.
- Produces: `ProjectionService.for_audience(campaign_id, branch_id, audience) -> dict` and `ProjectionService.for_audiences(campaign_id, branch_id, audiences) -> dict`.

- [ ] **Step 1: Write a failing no-leak test**

```python
import pytest

from sillytavern_rpg_engine.domain.models import (
    AgeStatus,
    AttributeDefinition,
    AttributeType,
    Audience,
    DisplayType,
    EntityKind,
)
from sillytavern_rpg_engine.domain.operations import CompositeOperation
from sillytavern_rpg_engine.services.attributes import DefineAttributeOperation, SetAttributeOperation
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import CreateEntityOperation, EntityAttributeService
from sillytavern_rpg_engine.services.projection import ProjectionService


@pytest.fixture
def seeded_attributes(database):
    campaigns = CampaignService(
        database,
        id_factory=iter(f"projection-event-{index}" for index in range(10)).__next__,
        clock=lambda: "2026-08-10T00:00:00Z",
    )
    campaigns.create_campaign("c1", "Campaign")
    state = EntityAttributeService(database, campaigns.mutation_engine)
    state.apply_explicit("c1", "main", 0, CompositeOperation((
        CreateEntityOperation("erin", EntityKind.CHARACTER, "艾琳", AgeStatus.ADULT, ()),
        DefineAttributeOperation(AttributeDefinition(
            campaign_id="c1", key="alchemy", label="炼金术", category="skill",
            value_type=AttributeType.NUMBER, display=DisplayType.BAR,
            audiences=frozenset({Audience.ENGINE, Audience.PLAYER_UI}),
            minimum=0, maximum=100,
        ), ()),
        SetAttributeOperation("erin", "alchemy", 35, None),
        DefineAttributeOperation(AttributeDefinition(
            campaign_id="c1", key="secret_identity", label="真实身份", category="secret",
            value_type=AttributeType.TEXT, display=DisplayType.TEXT,
            audiences=frozenset({Audience.ENGINE}),
        ), ()),
        SetAttributeOperation("erin", "secret_identity", "王国密探", None),
    )))
    return "c1"


def test_player_projection_never_contains_engine_only_value(seeded_attributes, database):
    service = ProjectionService(database)
    player = service.for_audience("c1", "main", Audience.PLAYER_UI)
    engine = service.for_audience("c1", "main", Audience.ENGINE)
    player_text = str(player)
    assert "alchemy" in player_text
    assert "secret_identity" not in player_text
    assert "王国密探" not in player_text
    assert "secret_identity" in str(engine)
    assert player["rules"] == {"mode": "narrative", "enabled": False, "version": None, "custom_preset_id": None}
```

- [ ] **Step 2: Run projection tests and verify failure**

Run: `python -m pytest tests/backend/unit/test_projection.py -q`

Expected: import fails because `services.projection` does not exist.

- [ ] **Step 3: Implement Audience filtering in SQL-backed reads**

Load definitions, parse `audiences_json`, and include a definition/value only when its set intersects the requested Audience set. Do not build a complete unrestricted snapshot and remove keys afterward. Return this stable shape:

```python
{
    "campaign_id": campaign.id,
    "branch_id": branch_id,
    "state_version": campaign.state_version,
    "rules": {
        "mode": campaign.rules.mode.value,
        "enabled": campaign.rules.enabled,
        "version": campaign.rules.version,
        "custom_preset_id": campaign.rules.custom_preset_id,
    },
    "entities": [
        {
            "id": entity.id,
            "kind": entity.kind.value,
            "name": entity.name,
            "attributes": visible_attributes,
        }
    ],
}
```

Order entities by normalized name and attributes by category/key for deterministic rendering and hashing.

- [ ] **Step 4: Run projection tests**

Run: `python -m pytest tests/backend/unit/test_projection.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit Task 6**

```bash
bash .harness/scripts/committer "feat(core): add Audience projections" \
  "src/sillytavern_rpg_engine/services/projection.py" \
  "tests/backend/unit/test_projection.py"
```

---

### Task 7: Persistent inferred proposals and approval

**Files:**
- Create: `src/sillytavern_rpg_engine/services/proposals.py`
- Create: `tests/backend/unit/test_proposals.py`

**Interfaces:**
- Consumes: `MutationEngine`, allowed entity/attribute operations, proposal table and status enum.
- Produces: `ProposalService.create()`, `ProposalService.approve()`, `ProposalService.reject()`, `ApproveProposalOperation`, and strict `OperationCodec`.

- [ ] **Step 1: Write failing proposal lifecycle tests**

```python
import pytest

from sillytavern_rpg_engine.domain.errors import StaleStateError
from sillytavern_rpg_engine.domain.models import (
    AgeStatus,
    AttributeDefinition,
    AttributeType,
    Audience,
    DisplayType,
    EntityKind,
    ProposalStatus,
)
from sillytavern_rpg_engine.domain.operations import CompositeOperation
from sillytavern_rpg_engine.services.attributes import DefineAttributeOperation, SetAttributeOperation
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import CreateEntityOperation, EntityAttributeService
from sillytavern_rpg_engine.services.proposals import ProposalService


@pytest.fixture
def prepared_alchemy(database):
    campaigns = CampaignService(
        database,
        id_factory=iter(f"proposal-event-{index}" for index in range(20)).__next__,
        clock=lambda: "2026-08-10T00:00:00Z",
    )
    campaigns.create_campaign("c1", "Campaign")
    state = EntityAttributeService(database, campaigns.mutation_engine)
    state.apply_explicit("c1", "main", 0, CompositeOperation((
        CreateEntityOperation("erin", EntityKind.CHARACTER, "艾琳", AgeStatus.ADULT, ()),
        DefineAttributeOperation(AttributeDefinition(
            campaign_id="c1", key="alchemy", label="炼金术", category="skill",
            value_type=AttributeType.NUMBER, display=DisplayType.BAR,
            audiences=frozenset({Audience.ENGINE, Audience.PLAYER_UI}),
            minimum=0, maximum=100,
        ), ()),
        SetAttributeOperation("erin", "alchemy", 35, "turn-1"),
    )))
    return state, campaigns.mutation_engine


def test_inferred_change_stays_pending_until_approved(database, prepared_alchemy):
    _, mutation_engine = prepared_alchemy
    service = ProposalService(database, mutation_engine, id_factory=lambda: "p-1", clock=lambda: "2026-08-10T00:00:00Z")
    proposal = service.create(
        campaign_id="c1",
        branch_id="main",
        operation={"kind": "set_attribute", "entity_id": "erin", "attribute_key": "alchemy", "value": 40, "turn_id": "turn-2"},
        reason="艾琳表现出专业药剂分析能力",
    )
    assert proposal.status is ProposalStatus.PENDING
    result = service.approve("p-1", expected_version=1)
    assert result.snapshot["entities"][0]["attributes"][0]["value"] == 40


def test_proposal_is_marked_stale_when_campaign_advanced(database, prepared_alchemy):
    state, mutation_engine = prepared_alchemy
    service = ProposalService(database, mutation_engine, id_factory=lambda: "p-2", clock=lambda: "2026-08-10T00:00:00Z")
    service.create("c1", "main", {"kind": "set_attribute", "entity_id": "erin", "attribute_key": "alchemy", "value": 50, "turn_id": None}, "candidate")
    state.apply_explicit("c1", "main", 1, SetAttributeOperation("erin", "alchemy", 36, "turn-2"))
    with pytest.raises(StaleStateError):
        service.approve("p-2", expected_version=1)
    assert service.get("p-2").status is ProposalStatus.STALE
```

- [ ] **Step 2: Run proposal tests and verify failure**

Run: `python -m pytest tests/backend/unit/test_proposals.py -q`

Expected: import fails because `services.proposals` does not exist.

- [ ] **Step 3: Implement strict operation codec**

Allow only these Phase 1 payload kinds:

```python
ALLOWED_KINDS = frozenset({
    "create_entity",
    "define_attribute",
    "set_attribute",
    "set_rules",
})
```

`OperationCodec.decode()` requires exact key sets per kind, reconstructs domain enums/dataclasses, and rejects unknown keys or kinds with `ValidationError`. Never deserialize class names, import paths, callables, SQL, or pickle data.

- [ ] **Step 4: Implement proposal persistence and resolution**

`create()` reads current state version and inserts a Pending row in its own short SQLite transaction; it does not increment authoritative state because no game fact changed. `approve()` checks the caller's expected version and the proposal's base version against current Campaign state. On mismatch, mark it Stale in a short transaction, commit that status, and only then raise `StaleStateError` outside the transaction so the status update is not rolled back.

For valid approval, `ApproveProposalOperation.apply()` decodes and applies the inner operation, then updates the proposal to Approved within the same authoritative mutation transaction. `reject()` changes only proposal status and resolution time; it cannot mutate Campaign state.

- [ ] **Step 5: Run proposal tests**

Run: `python -m pytest tests/backend/unit/test_proposals.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit Task 7**

```bash
bash .harness/scripts/committer "feat(core): add persistent change proposals" \
  "src/sillytavern_rpg_engine/services/proposals.py" \
  "tests/backend/unit/test_proposals.py"
```

---

### Task 8: Idempotent JSONL audit and Campaign export

**Files:**
- Create: `src/sillytavern_rpg_engine/services/audit_export.py`
- Create: `src/sillytavern_rpg_engine/services/campaign_export.py`
- Create: `tests/backend/unit/test_audit_export.py`

**Interfaces:**
- Consumes: `jsonl_outbox`, `audit_events`, `SnapshotBuilder`, explicit output paths.
- Produces: `JsonlAuditExporter.flush(path) -> int`, `CampaignExporter.export(campaign_id, path) -> Path`, and `CampaignExporter.verify_export(path) -> dict`.

- [ ] **Step 1: Write failing retry and secret-exclusion tests**

```python
import json

from sillytavern_rpg_engine.services.audit_export import JsonlAuditExporter
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.campaign_export import CampaignExporter


def create_campaign(database, event_id):
    service = CampaignService(
        database,
        id_factory=lambda: event_id,
        clock=lambda: "2026-08-10T00:00:00Z",
    )
    service.create_campaign("c1", "Campaign")


def test_jsonl_retry_does_not_duplicate_event(database, tmp_path):
    create_campaign(database, "event-1")
    output = tmp_path / "audit.jsonl"
    exporter = JsonlAuditExporter(database)
    assert exporter.flush(output) == 1
    with database.transaction() as connection:
        connection.execute("UPDATE jsonl_outbox SET exported_at = NULL")
    assert exporter.flush(output) == 0
    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert [record["event_id"] for record in records] == ["event-1"]


def test_campaign_export_contains_schema_and_no_credentials(database, tmp_path):
    create_campaign(database, "event-2")
    output = tmp_path / "campaign.json"
    CampaignExporter(database).export("c1", output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["export_schema_version"] == 1
    assert payload["campaign"]["id"] == "c1"
    serialized = json.dumps(payload).casefold()
    assert "api_key" not in serialized
    assert "authorization" not in serialized
```

- [ ] **Step 2: Run export tests and verify failure**

Run: `python -m pytest tests/backend/unit/test_audit_export.py -q`

Expected: imports fail because export services do not exist.

- [ ] **Step 3: Implement idempotent JSONL flush**

Require an explicit file path whose parent already exists. Read existing JSONL event IDs into a set, select unexported outbox rows ordered by audit creation time/event ID, append only absent IDs, flush and `os.fsync()`, then mark all confirmed IDs exported in one SQLite transaction. A crash after append but before marking is repaired by the existing-ID scan on retry.

- [ ] **Step 4: Implement deterministic Campaign export**

Write a temporary sibling file with suffix `.tmp`, flush and fsync, then replace the explicit destination with `Path.replace()`. Export this root shape:

```python
{
    "export_schema_version": 1,
    "exported_at": now,
    "campaign": campaign_snapshot["campaign"],
    "branches": branches,
    "entities": campaign_snapshot["entities"],
    "pending_proposals": proposals,
    "audit_events": audit_events,
    "latest_snapshots": latest_snapshots,
}
```

`verify_export()` parses JSON, checks root keys, Campaign ID, monotonically increasing state versions, and that no case-folded key equals `api_key`, `authorization`, `token`, or `secret`.

- [ ] **Step 5: Run export tests**

Run: `python -m pytest tests/backend/unit/test_audit_export.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit Task 8**

```bash
bash .harness/scripts/committer "feat(storage): add audit and campaign export" \
  "src/sillytavern_rpg_engine/services/audit_export.py" \
  "src/sillytavern_rpg_engine/services/campaign_export.py" \
  "tests/backend/unit/test_audit_export.py"
```

---

### Task 9: Maintenance CLI and Phase 1 integration flow

**Files:**
- Create: `src/sillytavern_rpg_engine/cli.py`
- Create: `src/sillytavern_rpg_engine/__main__.py`
- Create: `tests/backend/integration/test_phase1_flow.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: migration, Campaign service, dynamic attributes, projections, proposals, audit/export.
- Produces: `python -m sillytavern_rpg_engine init-db --database PATH`, `verify --database PATH`, and `export --database PATH --campaign ID --output PATH`.

- [ ] **Step 1: Write the failing end-to-end core test**

```python
from pathlib import Path

import pytest

from sillytavern_rpg_engine.domain.models import (
    AgeStatus,
    AttributeDefinition,
    AttributeType,
    Audience,
    CampaignRules,
    DisplayType,
    EntityKind,
    RulesMode,
)
from sillytavern_rpg_engine.persistence.database import Database
from sillytavern_rpg_engine.persistence.migrations import MigrationRunner
from sillytavern_rpg_engine.cli import main
from sillytavern_rpg_engine.services.attributes import DefineAttributeOperation, SetAttributeOperation
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import CreateEntityOperation, EntityAttributeService
from sillytavern_rpg_engine.services.projection import ProjectionService


def test_cli_help_exits_zero():
    with pytest.raises(SystemExit) as raised:
        main(["--help"])
    assert raised.value.code == 0


def test_phase1_state_survives_restart_and_campaign_rules_are_optional(tmp_path: Path):
    path = tmp_path / "world.sqlite3"
    database = Database(path)
    MigrationRunner(database).apply()
    ids = iter(f"event-{index}" for index in range(20))
    campaigns = CampaignService(database, id_factory=ids.__next__, clock=lambda: "2026-08-10T00:00:00Z")
    campaigns.create_campaign("story", "自由剧情")
    campaigns.create_campaign("dungeon", "地下城")
    campaigns.set_rules("dungeon", 0, CampaignRules(RulesMode.DND_2024, True, "5.2.1"))

    entity_state = EntityAttributeService(database, campaigns.mutation_engine)
    entity_state.apply_explicit("story", "main", 0, CreateEntityOperation(
        "erin", EntityKind.CHARACTER, "艾琳", AgeStatus.ADULT, ("Erin",)
    ))
    definition = AttributeDefinition(
        "story", "alchemy", "炼金术", "skill", AttributeType.NUMBER,
        DisplayType.BAR, frozenset({Audience.ENGINE, Audience.PLAYER_UI}), 0, 100,
    )
    entity_state.apply_explicit("story", "main", 1, DefineAttributeOperation(definition, ("炼金",)))
    entity_state.apply_explicit("story", "main", 2, SetAttributeOperation("erin", "alchemy", 35, "turn-1"))

    reopened = Database(path)
    story = ProjectionService(reopened).for_audience("story", "main", Audience.PLAYER_UI)
    dungeon = ProjectionService(reopened).for_audience("dungeon", "main", Audience.PLAYER_UI)
    assert story["rules"]["mode"] == "narrative"
    assert story["entities"][0]["attributes"][0]["value"] == 35
    assert dungeon["rules"] == {
        "mode": "dnd-2024",
        "enabled": True,
        "version": "5.2.1",
        "custom_preset_id": None,
    }
```

- [ ] **Step 2: Run integration test and verify CLI is absent**

Run: `python -m pytest tests/backend/integration/test_phase1_flow.py -q`

Expected: test collection fails with `ModuleNotFoundError: No module named 'sillytavern_rpg_engine.cli'`.

- [ ] **Step 3: Implement maintenance CLI**

Use `argparse`; require explicit `--database` paths. `init-db` applies migrations and prints the Schema version. `verify` runs `PRAGMA integrity_check`, verifies foreign keys, and returns nonzero on any error. `export` delegates to `CampaignExporter`. Do not add start-server or model options in this phase.

- [ ] **Step 4: Document Phase 1 developer workflow**

Add a README section with exact commands:

```bash
python -m pip install -e '.[dev]'
python -m sillytavern_rpg_engine init-db --database ./data/campaigns.sqlite3
python -m sillytavern_rpg_engine verify --database ./data/campaigns.sqlite3
python -m pytest tests/backend -q
```

State that Phase 1 is a local state library/maintenance CLI, not yet a SillyTavern API. State that existing JavaScript DME remains untouched but must not be used as a second authority once the later LangGraph API is enabled.

- [ ] **Step 5: Run Phase 1 verification**

Run:

```bash
python -m pytest tests/backend -q
python -m sillytavern_rpg_engine --help
python .harness/scripts/guard.py src/sillytavern_rpg_engine tests/backend
npm run check
```

Expected: Python tests pass; CLI help exits 0; guard reports 0 failures; existing JavaScript lint/tests/build/dist check pass unchanged.

- [ ] **Step 6: Commit Task 9**

```bash
bash .harness/scripts/committer "feat(core): complete persistent RPG state foundation" \
  "src/sillytavern_rpg_engine/cli.py" \
  "src/sillytavern_rpg_engine/__main__.py" \
  "tests/backend/integration/test_phase1_flow.py" \
  "README.md"
```

---

## Phase 1 Exit Criteria

- `python -m pytest tests/backend -q` passes from a fresh editable install.
- Two Campaigns can use Narrative and D&D-selected modes independently in one database.
- Explicit commands can atomically create a person, define a dynamic skill, and set its value.
- Inferred changes persist as Pending and cannot mutate state before approval.
- Every authoritative mutation produces exactly one version bump, audit event, outbox row, and snapshot.
- Rollback and stale-version tests prove no partial or overwritten state.
- Player projections cannot contain engine-only attributes.
- Adult-intimacy attributes reject unknown/minor entities.
- Restarting with a new `Database` instance restores identical Campaign state.
- JSONL retry is idempotent and Campaign export excludes credential-like keys.
- Existing JavaScript DME tests and build remain unchanged and passing.
