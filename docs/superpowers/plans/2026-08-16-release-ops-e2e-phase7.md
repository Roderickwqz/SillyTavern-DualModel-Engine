# LangGraph RPG Core — Phase 7 (Export, Backup, Migration Diagnostics & SillyTavern E2E) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship production operations for the authoritative backend: complete credential-free campaign export/import with round-trip verification, rotated SQLite backups, JSONL audit flush CLI, migration status diagnostics and degraded-mode recovery, expanded `/health` and `/admin` surfaces, and Playwright E2E coverage (automated API smoke + optional real SillyTavern compat checklist) so spec §17.2 export/restart/migration acceptance and §17.4 release criteria are testable.

**Architecture:** Phase 4–6 already provide `CampaignExporter` (partial payload), `JsonlAuditExporter`, `verify`/`export` CLI, degraded `serve`, and branch diagnostics. Phase 7 bumps `export_schema_version` to **2** with every table required to reconstruct authoritative state (including turns, lineage, memory, definitions, branch heads). `CampaignImporter` is the inverse: one transaction per campaign import, always emitting a new `recovery_import` audit event (spec §16 — recovery never mutates old audit rows). `DatabaseBackupService` checkpoints WAL then copies the SQLite file with timestamped rotation. `MigrationRunner.status()` powers CLI and `/health` pending-migration reporting. Playwright runs an automated loop against a `webServer` that starts the real FastAPI app backed by `ScriptedLLMClient` via a test-only factory hook; optional `SILLYTAVERN_URL` specs document the manual host checklist for RPG Companion Compat.

**Tech Stack:** Python 3.11+, FastAPI, SQLite WAL backup (`sqlite3.Connection.backup` + `PRAGMA wal_checkpoint(TRUNCATE)`), pytest 8.x, Playwright 1.62.x, Vitest (unchanged), `ScriptedLLMClient` test double.

## Global Constraints

- Exports and backups must never contain API keys, tokens, or env secrets (spec §13, §15); reuse `_FORBIDDEN_KEYS` scanning.
- Recovery/import always appends a new audit event; never UPDATE/DELETE historical `audit_events` or JSONL already flushed (spec §16).
- Schema migration failure → degraded read-only mode remains: chat 503, `/health` and export/backup/import diagnostics still work (spec §16, §15).
- SQLite uses WAL + foreign keys + startup integrity check (already in `Database.connect`); backups must checkpoint WAL first so copies are restorable (spec §13).
- Admin routes live under `/admin/...`, never disguised as chat completions (spec §15).
- `GET /health` reports database integrity, applied schema version, pending migrations, and model probe status (spec §15).
- Import targets a **new** campaign id when the id already exists unless `--replace` explicitly passed (safety default).
- Playwright automated specs must not require a live LLM API key; use `ScriptedLLMClient` via `create_app` test factory or `RPG_ENGINE_TEST_MODE=scripted`.
- Real SillyTavern E2E remains optional (`SILLYTAVERN_URL`); document manual steps in `tests/e2e/rpg-compat-manual-checklist.md`.
- Conda env `py313`; `python -m pip install -e ".[dev]"`; `python -m pytest tests/backend -q`; `npm run test:e2e` for Playwright; guard: `python .harness/scripts/guard.py src/sillytavern_rpg_engine tests/backend`; commit via `bash .harness/scripts/committer "<msg>" <files...>`.

## Multi-Plan Roadmap

1. Phases 1–6 (done): core through branching/long-run.
2. **This plan (final):** export/backup/migration diagnostics + E2E/release acceptance.

## File Structure

```text
src/sillytavern_rpg_engine/
├── persistence/
│   └── migrations.py                   # MODIFY: + status()
├── services/
│   ├── campaign_export.py              # MODIFY: export schema v2 sections
│   ├── campaign_import.py              # NEW: CampaignImporter
│   ├── database_backup.py              # NEW: DatabaseBackupService
│   └── diagnostics.py                  # NEW: build_diagnostics()
├── server/
│   ├── app.py                          # MODIFY: /health, /admin/* routes, test factory
│   └── test_factory.py                 # NEW: create_test_app(scripted queue)
├── cli.py                              # MODIFY: backup, import, migrate-status, flush-audit
tests/backend/unit/
├── test_migration_status.py
├── test_campaign_import.py
├── test_database_backup.py
├── test_diagnostics.py
└── test_export_schema_v2.py
tests/backend/integration/
├── test_phase7_export_import_roundtrip.py
└── test_phase7_release_acceptance.py
tests/e2e/
├── backend-api.spec.js                 # NEW: automated Playwright against webServer
├── rpg-compat-smoke.spec.js            # NEW: optional SILLYTAVERN_URL smoke
└── rpg-compat-manual-checklist.md      # NEW: replaces outdated DME checklist
tests/fixtures/
└── mock_llm_server.py                  # NEW: only if webServer cannot use test_factory
playwright.config.js                    # MODIFY: webServer for backend-api spec
README.md                               # MODIFY: operations + release section
```

---

### Task 1: Migration status and diagnostics core

**Files:**
- Modify: `src/sillytavern_rpg_engine/persistence/migrations.py`
- Create: `src/sillytavern_rpg_engine/services/diagnostics.py`
- Test: `tests/backend/unit/test_migration_status.py`, `tests/backend/unit/test_diagnostics.py`

**Interfaces:**
- Consumes: packaged SQL in `persistence/schema/`.
- Produces: `MigrationRunner.status() -> MigrationStatus(applied: list[int], pending: list[int], latest: int)`; `build_diagnostics(database, *, degraded: bool, settings) -> dict`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/backend/unit/test_migration_status.py
from sillytavern_rpg_engine.persistence.database import Database
from sillytavern_rpg_engine.persistence.migrations import MigrationRunner


def test_status_lists_applied_and_pending(database):
    runner = MigrationRunner(database)
    runner.apply()
    status = runner.status()
    assert status.latest >= 8
    assert status.pending == []
    assert status.latest in status.applied
```

```python
# tests/backend/unit/test_diagnostics.py
from sillytavern_rpg_engine.config import load_settings
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.diagnostics import build_diagnostics


def test_build_diagnostics_reports_schema_and_campaign_count(database):
    CampaignService(database, id_factory=iter("e").__next__,
                    clock=lambda: "2026-08-16T00:00:00Z").create_campaign("c1", "T")
    diag = build_diagnostics(database, degraded=False, settings=load_settings({}))
    assert diag["schema"]["pending_migrations"] == []
    assert diag["campaigns"]["count"] == 1
    assert diag["database"]["integrity"] == "ok"
```

- [ ] **Step 2: Run tests — FAIL**

Run: `python -m pytest tests/backend/unit/test_migration_status.py tests/backend/unit/test_diagnostics.py -v`

- [ ] **Step 3: Implement**

Add to `migrations.py`:

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class MigrationStatus:
    applied: list[int]
    pending: list[int]
    latest: int

# On MigrationRunner:
def status(self) -> MigrationStatus:
    scripts = self._load_scripts()
    latest = max(scripts) if scripts else 0
    with self.database.connect() as connection:
        rows = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
    applied = [row[0] for row in rows]
    pending = sorted(set(scripts) - set(applied))
    return MigrationStatus(applied=applied, pending=pending, latest=latest)
```

`diagnostics.py`:

```python
def build_diagnostics(database, *, degraded: bool, settings) -> dict[str, Any]:
    migration = MigrationRunner(database).status()
    with database.connect() as connection:
        integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
        campaigns = connection.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0]
        branches = connection.execute("SELECT COUNT(*) FROM branches").fetchone()[0]
        turns = connection.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
    return {
        "status": "degraded" if degraded or integrity != "ok" else "ok",
        "database": {"integrity": integrity, "path": str(database.path)},
        "schema": {
            "applied": migration.applied,
            "pending_migrations": migration.pending,
            "latest": migration.latest,
        },
        "campaigns": {"count": campaigns},
        "branches": {"count": branches},
        "turns": {"count": turns},
        "models": {
            "narrator": settings.narrator.model,
            "critic": settings.critic.model if settings.critic else None,
        },
    }
```

- [ ] **Step 4: Run tests — PASS**

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: add migration status and diagnostics core" \
  src/sillytavern_rpg_engine/persistence/migrations.py \
  src/sillytavern_rpg_engine/services/diagnostics.py \
  tests/backend/unit/test_migration_status.py \
  tests/backend/unit/test_diagnostics.py
```

---

### Task 2: Export schema v2 (complete campaign payload)

**Files:**
- Modify: `src/sillytavern_rpg_engine/services/campaign_export.py`
- Test: `tests/backend/unit/test_export_schema_v2.py`

**Interfaces:**
- Consumes: all campaign-scoped tables through Phase 6.
- Produces: `export_schema_version: 2` payload with sections:
  `attribute_definitions`, `attribute_values`, `entity_aliases`, `facts`, `relationships`,
  `memory_events`, `memory_summaries`, `trait_events`, `turns`, `branch_heads`,
  `pending_proposals`, `audit_events`, `dice_rolls`, `entity_conditions`,
  `combat_encounters`, `latest_snapshots`, `branches`, `campaign`.

- [ ] **Step 1: Write the failing test**

```python
# tests/backend/unit/test_export_schema_v2.py
import json
from sillytavern_rpg_engine.services.campaign_export import CampaignExporter
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import CreateEntityOperation
from sillytavern_rpg_engine.domain.models import EntityKind


def test_export_v2_includes_definitions_turns_and_memory(database, tmp_path):
    ids = iter(f"id-{i}" for i in range(20))
    campaigns = CampaignService(database, id_factory=ids.__next__,
                                clock=lambda: "2026-08-16T00:00:00Z")
    campaigns.create_campaign("c1", "Export")
    from sillytavern_rpg_engine.services.entities import EntityAttributeService
    attrs = EntityAttributeService(database, campaigns.mutation_engine)
    attrs.apply_explicit("c1", "main", 0, CreateEntityOperation(
        "erin", EntityKind.CHARACTER, "艾琳",
    ))
    output = tmp_path / "c1.json"
    CampaignExporter(database).export("c1", output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["export_schema_version"] == 2
    for key in (
        "attribute_definitions", "attribute_values", "turns",
        "branch_heads", "memory_events",
    ):
        assert key in payload, f"missing {key}"
    assert payload["campaign"]["id"] == "c1"
```

- [ ] **Step 2: Run test — FAIL**

- [ ] **Step 3: Extend CampaignExporter**

Bump `_EXPECTED_ROOT_KEYS` and `export_schema_version` to `2`. Add private `_turns`, `_branch_heads`, `_attribute_definitions`, `_attribute_values`, `_facts`, `_relationships`, `_memory_events`, `_memory_summaries`, `_trait_events` query helpers mirroring existing `_audit_events` style (deterministic `ORDER BY`). Keep v1 verify accepting only if we add `verify_export` version check — v2 required for new exports.

Update `tests/backend/unit/test_audit_export.py` assertion `export_schema_version == 1` → `2`.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/backend/unit/test_export_schema_v2.py tests/backend/unit/test_audit_export.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: expand campaign export to schema v2" \
  src/sillytavern_rpg_engine/services/campaign_export.py \
  tests/backend/unit/test_export_schema_v2.py \
  tests/backend/unit/test_audit_export.py
```

---

### Task 3: CampaignImporter with recovery audit event

**Files:**
- Create: `src/sillytavern_rpg_engine/services/campaign_import.py`
- Test: `tests/backend/unit/test_campaign_import.py`

**Interfaces:**
- Consumes: v2 export payload; `CampaignExporter.verify_export`.
- Produces: `CampaignImporter.import_payload(path, *, new_campaign_id: str | None = None, replace: bool = False) -> str`; writes `recovery_import` audit event with `source="import"` and export metadata in payload.

- [ ] **Step 1: Write the failing test**

```python
# tests/backend/unit/test_campaign_import.py
import json
import pytest
from sillytavern_rpg_engine.domain.errors import ValidationError
from sillytavern_rpg_engine.services.campaign_export import CampaignExporter
from sillytavern_rpg_engine.services.campaign_import import CampaignImporter
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import CreateEntityOperation, EntityAttributeService
from sillytavern_rpg_engine.domain.models import EntityKind
from sillytavern_rpg_engine.services.projection import ProjectionService
from sillytavern_rpg_engine.domain.models import Audience


def _seed(database):
    ids = iter(f"id-{i}" for i in range(50))
    campaigns = CampaignService(database, id_factory=ids.__next__,
                                clock=lambda: "2026-08-16T00:00:00Z")
    campaigns.create_campaign("c1", "Roundtrip")
    attrs = EntityAttributeService(database, campaigns.mutation_engine)
    attrs.apply_explicit("c1", "main", 0, CreateEntityOperation(
        "erin", EntityKind.CHARACTER, "艾琳",
    ))
    return campaigns


def test_import_restores_entities_on_empty_database(tmp_path, database):
    _seed(database)
    export_path = tmp_path / "c1.json"
    CampaignExporter(database).export("c1", export_path)
    # Simulate fresh DB by deleting campaign rows in place is unsafe; use second database file:
    from sillytavern_rpg_engine.persistence.database import Database
    fresh = Database(tmp_path / "fresh.db")
    from sillytavern_rpg_engine.persistence.migrations import MigrationRunner
    MigrationRunner(fresh).apply()
    campaign_id = CampaignImporter(fresh).import_file(export_path)
    assert campaign_id == "c1"
    projection = ProjectionService(fresh).for_audience(campaign_id, "main", Audience.PLAYER_UI)
    assert any(e["name"] == "艾琳" for e in projection["entities"])


def test_import_refuses_existing_id_without_replace(tmp_path, database):
    _seed(database)
    export_path = tmp_path / "c1.json"
    CampaignExporter(database).export("c1", export_path)
    with pytest.raises(ValidationError, match="already exists"):
        CampaignImporter(database).import_file(export_path)
```

- [ ] **Step 2: Run test — FAIL**

- [ ] **Step 3: Implement CampaignImporter**

```python
# src/sillytavern_rpg_engine/services/campaign_import.py
class CampaignImporter:
    def import_file(self, path: Path, *, new_campaign_id: str | None = None,
                    replace: bool = False) -> str:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        verify = CampaignExporter(self.database).verify_export(path)
        if not verify["ok"]:
            raise ValidationError("; ".join(verify["errors"]))
        if payload.get("export_schema_version") != 2:
            raise ValidationError("unsupported export_schema_version")
        campaign_id = new_campaign_id or payload["campaign"]["id"]
        with self.database.transaction() as connection:
            exists = connection.execute(
                "SELECT 1 FROM campaigns WHERE id = ?", (campaign_id,),
            ).fetchone()
            if exists and not replace:
                raise ValidationError(f"campaign {campaign_id!r} already exists")
            if exists and replace:
                self._delete_campaign(connection, campaign_id)
            self._insert_campaign_tree(connection, campaign_id, payload)
            self._insert_recovery_audit(connection, campaign_id, payload)
        return campaign_id
```

Implement `_delete_campaign` as `DELETE FROM campaigns WHERE id = ?` relying on `ON DELETE CASCADE`. Implement `_insert_campaign_tree` inserting sections in FK order (definitions → entities → aliases → values → facts → relationships → branches → branch_heads → turns → memory → proposals → combat → snapshots). Mirror export column names exactly.

- [ ] **Step 4: Run tests — PASS**

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: import campaigns from export schema v2" \
  src/sillytavern_rpg_engine/services/campaign_import.py \
  tests/backend/unit/test_campaign_import.py
```

---

### Task 4: SQLite backup with rotation

**Files:**
- Create: `src/sillytavern_rpg_engine/services/database_backup.py`
- Modify: `src/sillytavern_rpg_engine/cli.py`
- Test: `tests/backend/unit/test_database_backup.py`

**Interfaces:**
- Produces: `DatabaseBackupService.backup(dest_dir: Path, *, keep: int = 7) -> Path`; CLI `backup --database DB --dest-dir DIR [--keep N]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/backend/unit/test_database_backup.py
from pathlib import Path
from sillytavern_rpg_engine.persistence.database import Database
from sillytavern_rpg_engine.persistence.migrations import MigrationRunner
from sillytavern_rpg_engine.services.database_backup import DatabaseBackupService


def test_backup_creates_timestamped_copy_and_rotates(tmp_path):
    db_path = tmp_path / "game.db"
    database = Database(db_path)
    MigrationRunner(database).apply()
    dest = tmp_path / "backups"
    service = DatabaseBackupService(database, clock=lambda: "2026-08-16T12:00:00Z")
    first = service.backup(dest, keep=2)
    assert first.is_file()
    service._clock = lambda: "2026-08-16T13:00:00Z"  # or inject distinct clock
    second = service.backup(dest, keep=2)
    assert second != first
    assert len(list(dest.glob("*.db"))) <= 2
```

- [ ] **Step 2: Run test — FAIL**

- [ ] **Step 3: Implement**

```python
class DatabaseBackupService:
    def backup(self, dest_dir: Path, *, keep: int = 7) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        stamp = self.clock().replace(":", "").replace("-", "")[:15]
        target = dest_dir / f"{self.database.path.stem}-{stamp}.db"
        with self.database.connect() as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            backup = sqlite3.connect(target)
            try:
                connection.backup(backup)
            finally:
                backup.close()
        self._rotate(dest_dir, keep)
        return target
```

Wire CLI `backup` subcommand.

- [ ] **Step 4: Run tests — PASS**

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: add rotated SQLite backup command" \
  src/sillytavern_rpg_engine/services/database_backup.py \
  src/sillytavern_rpg_engine/cli.py \
  tests/backend/unit/test_database_backup.py
```

---

### Task 5: CLI operations — migrate-status, flush-audit, import, verify-export

**Files:**
- Modify: `src/sillytavern_rpg_engine/cli.py`
- Test: extend `tests/backend/unit/test_audit_export.py`

**Interfaces:**
- Produces CLI subcommands: `migrate-status`, `flush-audit --output PATH`, `import --input PATH [--campaign ID] [--replace]`, `verify-export --input PATH`.

- [ ] **Step 1: Write failing CLI tests** using `cli.main([...])` exit codes.

```python
def test_cli_migrate_status_reports_ok(database, tmp_path, capsys):
    database = Database(tmp_path / "t.db")
    MigrationRunner(database).apply()
    assert cli.main(["migrate-status", "--database", str(tmp_path / "t.db")]) == 0
    assert "pending" in capsys.readouterr().out
```

- [ ] **Step 2–4: Implement handlers printing JSON or human text; verify PASS**

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: add migrate-status import and audit flush CLI" \
  src/sillytavern_rpg_engine/cli.py \
  tests/backend/unit/test_audit_export.py
```

---

### Task 6: Expanded `/health` and `/admin/diagnostics`

**Files:**
- Modify: `src/sillytavern_rpg_engine/server/app.py`
- Test: extend `tests/backend/unit/test_api.py`

**Interfaces:**
- Produces: `GET /health` includes `pending_migrations`, `degraded` flag; `GET /admin/diagnostics` returns `build_diagnostics()`; `GET /admin/campaigns` lists `{id, name, state_version, last_active_branch_id}`.

- [ ] **Step 1: Write failing API tests**

```python
def test_health_reports_pending_migrations_none(database):
    client = _client(database)
    body = client.get("/health").json()
    assert body["schema_version"] >= 8
    assert body.get("pending_migrations") == []


def test_admin_diagnostics_lists_campaigns(database):
    from fastapi.testclient import TestClient
    from sillytavern_rpg_engine.config import ModelConfig, Settings
    from sillytavern_rpg_engine.llm.scripted import ScriptedLLMClient
    from sillytavern_rpg_engine.server.app import create_app
    client = TestClient(create_app(
        Settings(database_path=database.path, host="127.0.0.1", port=8000,
                 max_history_messages=40,
                 narrator=ModelConfig("http://x", "", "n", 0.8, 30, 256)),
        database, ScriptedLLMClient(),
    ))
    response = client.get("/admin/diagnostics")
    assert response.status_code == 200
    assert response.json()["campaigns"]["count"] >= 1
```

- [ ] **Step 2–4: Implement routes; degraded chat still 503; diagnostics always 200 unless DB unreadable.**

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: expand health and admin diagnostics endpoints" \
  src/sillytavern_rpg_engine/server/app.py \
  tests/backend/unit/test_api.py
```

---

### Task 7: Degraded-mode raw database backup endpoint

**Files:**
- Modify: `src/sillytavern_rpg_engine/server/app.py`
- Modify: `src/sillytavern_rpg_engine/cli.py` (`serve` message)
- Test: `tests/backend/unit/test_api.py`

**Interfaces:**
- Produces: `GET /admin/database/backup` returns `FileResponse` of checkpointed SQLite copy (temp file or streaming) even when `degraded=True`; chat remains 503.

- [ ] **Step 1: Write failing test** using `create_app(..., degraded=True)` and asserting backup endpoint returns `application/octet-stream` with SQLite magic header `b"SQLite format 3\\000"`.

- [ ] **Step 2–4: Implement with `DatabaseBackupService` to temp dir; document in README that this is the migration-failure escape hatch (spec §16).

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "feat: allow database backup download in degraded mode" \
  src/sillytavern_rpg_engine/server/app.py \
  tests/backend/unit/test_api.py
```

---

### Task 8: Export → restart → import roundtrip integration test

**Files:**
- Create: `tests/backend/integration/test_phase7_export_import_roundtrip.py`

**Interfaces:**
- Proves spec §17.2: "导出、重启、迁移和备份恢复保持相同权威状态".

- [ ] **Step 1: Write failing integration test**

```python
def test_export_backup_import_roundtrip_preserves_state(tmp_path):
    source_db = tmp_path / "source.db"
    target_db = tmp_path / "target.db"
    # 1) Play scripted turns on source (attribute change + memory event)
    # 2) export JSON + backup SQLite
    # 3) init fresh target DB, import JSON
    # 4) assert projection equal for PLAYER_UI fields
    # 5) verify recovery_import audit event exists on target
```

- [ ] **Step 2–4: Implement until PASS**

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "test: add export import roundtrip integration" \
  tests/backend/integration/test_phase7_export_import_roundtrip.py
```

---

### Task 9: Playwright test factory and backend API E2E

**Files:**
- Create: `src/sillytavern_rpg_engine/server/test_factory.py`
- Create: `tests/e2e/backend-api.spec.js`
- Modify: `playwright.config.js`
- Modify: `pyproject.toml` or `src/sillytavern_rpg_engine/__main__.py` to honor `RPG_ENGINE_TEST_MODE=scripted`

**Interfaces:**
- Produces: `create_scripted_app(database_path, responses: list[str])` used when env `RPG_ENGINE_TEST_MODE=scripted`; Playwright `webServer` runs engine against temp DB.

- [ ] **Step 1: Write Playwright spec**

```javascript
// tests/e2e/backend-api.spec.js
import { expect, test } from '@playwright/test';

test('health and scripted chat return tracker json', async ({ request }) => {
    const health = await request.get('/health');
    expect(health.ok()).toBeTruthy();
    const body = await health.json();
    expect(body.status).toBe('ok');

    const chat = await request.post('/v1/chat/completions', {
        data: {
            campaign_id: 'playwright-c1',
            messages: [{ role: 'user', content: '你好' }],
        },
    });
    expect(chat.ok()).toBeTruthy();
    const payload = await chat.json();
    expect(payload.choices[0].message.content).toContain('```json');
});
```

- [ ] **Step 2: Configure playwright webServer**

```javascript
webServer: {
    command: 'RPG_ENGINE_TEST_MODE=scripted RPG_TEST_DATABASE=:memory: python -m sillytavern_rpg_engine serve --database /tmp/rpg-e2e.db --port 8765',
    url: 'http://127.0.0.1:8765/health',
    reuseExistingServer: !process.env.CI,
},
use: { baseURL: 'http://127.0.0.1:8765' },
```

Use a temp DB path under `os.tmpdir()`; test factory pre-creates campaign `playwright-c1` and queues one narrator response containing Tracker JSON on startup.

- [ ] **Step 3–4: Implement test factory hook in `serve` path; run `npm run test:e2e -- tests/e2e/backend-api.spec.js` — PASS**

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "test: add Playwright backend API e2e" \
  src/sillytavern_rpg_engine/server/test_factory.py \
  src/sillytavern_rpg_engine/cli.py \
  tests/e2e/backend-api.spec.js \
  playwright.config.js
```

---

### Task 10: Optional real SillyTavern compat smoke + manual checklist

**Files:**
- Create: `tests/e2e/rpg-compat-smoke.spec.js`
- Create: `tests/e2e/rpg-compat-manual-checklist.md`
- Deprecate: `tests/e2e/extension-smoke.spec.js` (skip with pointer) or replace

**Interfaces:**
- Automated spec skipped unless `SILLYTAVERN_URL` set; checks RPG Companion Compat panel mounts, no DualModel Engine errors, backend health reachable at `RPG_BACKEND_URL` (default `http://127.0.0.1:8000`).

- [ ] **Step 1: Write manual checklist** covering spec §17.4 items 1–3, 6, 9, 11–15:

```markdown
# RPG Companion Compat + LangGraph Backend — Manual Release Checklist

- [ ] SillyTavern ≥ 1.18.0; DualModel Engine **disabled**; RPG Companion Compat enabled
- [ ] Chat Completion → `http://127.0.0.1:8000/v1`, Custom Body `{"campaign_id":"..."}`
- [ ] `GET /health` → status ok; `deep=1` narrator up
- [ ] New character in chat appears in Compat panel next turn (§17.4 #1)
- [ ] New skill renders dynamic attribute control (§17.4 #2)
- [ ] Restart backend + SillyTavern → state matches (§17.4 #3)
- [ ] Swipe branch A/B do not cross-pollute (§17.4 #6)
- [ ] Tracker parse failure shows stale banner; backend unchanged (§17.4 #9)
- [ ] D&D campaign shows combat panel; narrative campaign does not (§17.4 #13–14)
```

- [ ] **Step 2: Write optional smoke spec**

```javascript
test.skip(!process.env.SILLYTAVERN_URL, 'Set SILLYTAVERN_URL');
test('compat extension loads without DualModel errors', async ({ page }) => { ... });
test('backend health reachable', async ({ request }) => {
    const base = process.env.RPG_BACKEND_URL ?? 'http://127.0.0.1:8000';
    expect((await request.get(`${base}/health`)).ok()).toBeTruthy();
});
```

- [ ] **Step 3–4: Run optional spec locally with ST — document in README**

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "docs: add RPG compat manual checklist and optional e2e" \
  tests/e2e/rpg-compat-smoke.spec.js \
  tests/e2e/rpg-compat-manual-checklist.md \
  tests/e2e/extension-smoke.spec.js
```

---

### Task 11: Release acceptance integration test (§17.4)

**Files:**
- Create: `tests/backend/integration/test_phase7_release_acceptance.py`

**Interfaces:**
- Automated subset of §17.4 not requiring manual ST.

- [ ] **Step 1: Write test module** with named tests:

```python
def test_release_hidden_attributes_never_in_tracker(database): ...  # #4
def test_release_tracker_parse_failure_does_not_mutate_db(database): ...  # #9
def test_release_narrative_campaign_has_no_dice_rolls(database): ...  # #13
def test_release_export_restart_import_state_match(database): ...  # #3 via import
def test_release_audit_append_only_on_import(database): ...  # #11 partial
```

- [ ] **Step 2–4: Implement — PASS**

- [ ] **Step 5: Commit**

```bash
bash .harness/scripts/committer "test: add phase 7 release acceptance coverage" \
  tests/backend/integration/test_phase7_release_acceptance.py
```

---

### Task 12: README operations guide and final verification gate

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add "Operations (Phase 7)" section**

Document:

```markdown
## Operations

### Daily
- `python -m sillytavern_rpg_engine serve --database ./campaign.db`
- `python -m sillytavern_rpg_engine flush-audit --database ./campaign.db --output ./audit.jsonl`

### Backup
- `python -m sillytavern_rpg_engine backup --database ./campaign.db --dest-dir ./backups --keep 7`

### Export / Import
- `python -m sillytavern_rpg_engine export --database ./campaign.db --campaign ID --output ./ID.json`
- `python -m sillytavern_rpg_engine import --database ./campaign.db --input ./ID.json`
- `python -m sillytavern_rpg_engine verify-export --input ./ID.json`

### Migration failure
Server starts degraded; use `GET /admin/database/backup` or CLI `backup` before fixing schema.

### E2E
- Automated: `npm run test:e2e -- tests/e2e/backend-api.spec.js`
- Manual: `tests/e2e/rpg-compat-manual-checklist.md`
```

- [ ] **Step 2: Run full verification**

```bash
python -m pytest tests/backend -q
npm run test:run
npm run test:e2e -- tests/e2e/backend-api.spec.js
python .harness/scripts/guard.py src/sillytavern_rpg_engine tests/backend
```

Expected: all PASS

- [ ] **Step 3: Commit**

```bash
bash .harness/scripts/committer "docs: add phase 7 operations and release guide" README.md
```

---

## Self-Review Checklist

| Spec requirement | Task |
|---|---|
| §13 WAL + backup with explicit target and rotation | 4, 7 |
| §13 export includes schema version, campaign data, events, snapshots; no API keys | 2, 3, 8 |
| §15 `/health` DB + migration + model diagnostics | 1, 6 |
| §15 admin routes separate from chat | 6, 7 |
| §16 migration failure → degraded + export raw data | 6, 7 |
| §16 recovery generates new audit events | 3, 8 |
| §17.2 export/restart/import state match | 8, 11 |
| §17.4 release acceptance (automated subset) | 11 |
| Real SillyTavern E2E | 10 |

**Type consistency:** `export_schema_version` 2 in exporter, importer, and tests. `MigrationStatus.pending` matches `/health.pending_migrations`.

**This is the final roadmap phase.** After Phase 7, the spec §18 implementation order is complete; remaining work is ongoing maintenance, not a Phase 8.
