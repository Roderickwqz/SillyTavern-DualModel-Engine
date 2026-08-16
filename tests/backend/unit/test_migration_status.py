from sillytavern_rpg_engine.persistence.database import Database
from sillytavern_rpg_engine.persistence.migrations import MigrationRunner


def test_status_lists_applied_and_pending(database):
    runner = MigrationRunner(database)
    runner.apply()
    status = runner.status()
    assert status.latest >= 8
    assert status.pending == []
    assert status.latest in status.applied


def test_status_on_fresh_unmigrated_database(tmp_path):
    database = Database(tmp_path / "fresh.db")
    status = MigrationRunner(database).status()
    assert status.applied == []
    assert status.latest >= 8
    assert set(status.pending) == set(range(1, status.latest + 1))
