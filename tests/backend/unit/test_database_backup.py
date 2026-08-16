import sqlite3

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
    with sqlite3.connect(str(first)) as backup:
        integrity = backup.execute("PRAGMA integrity_check").fetchall()
        assert integrity == [("ok",)]
        tables = {
            row[0]
            for row in backup.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "campaigns" in tables
    service._clock = lambda: "2026-08-16T13:00:00Z"
    second = service.backup(dest, keep=2)
    assert second != first
    assert len(list(dest.glob("*.db"))) <= 2


def test_backup_rotates_beyond_keep(tmp_path):
    db_path = tmp_path / "game.db"
    database = Database(db_path)
    MigrationRunner(database).apply()
    dest = tmp_path / "backups"
    service = DatabaseBackupService(database)
    clocks = ["2026-08-16T10:00:00Z", "2026-08-16T11:00:00Z",
              "2026-08-16T12:00:00Z"]
    targets = []
    for stamp in clocks:
        service._clock = lambda stamp=stamp: stamp
        targets.append(service.backup(dest, keep=2))
    remaining = sorted(dest.glob("*.db"))
    assert len(remaining) == 2
    assert targets[0] not in remaining
    assert targets[1:] == remaining
