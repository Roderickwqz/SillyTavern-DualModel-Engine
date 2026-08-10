import sqlite3

import pytest

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
                "INSERT INTO campaigns(id, name, created_at, updated_at)"
                " VALUES (?, ?, ?, ?)",
                ("c1", "Campaign", "2026-08-10T00:00:00Z", "2026-08-10T00:00:00Z"),
            )
            raise RuntimeError("stop")
    except RuntimeError:
        pass
    with database.connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0]
    assert count == 0


def test_failing_migration_leaves_no_tables(tmp_path):
    database = Database(tmp_path / "atomic.sqlite3")
    runner = MigrationRunner(database)
    runner._load_scripts = lambda: {
        1: (
            "CREATE TABLE partial_one (id INTEGER);"
            "CREATE TABLE partial_two (id INTEGER);"
            "INSERT INTO does_not_exist (id) VALUES (1);"
        )
    }
    with pytest.raises(sqlite3.OperationalError):
        runner.apply()
    with database.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert tables == set()
