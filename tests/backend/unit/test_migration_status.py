from sillytavern_rpg_engine.persistence.database import Database
from sillytavern_rpg_engine.persistence.migrations import MigrationRunner


def test_status_lists_applied_and_pending(database):
    runner = MigrationRunner(database)
    runner.apply()
    status = runner.status()
    assert status.latest >= 8
    assert status.pending == []
    assert status.latest in status.applied
