from pathlib import Path

import pytest

from sillytavern_rpg_engine.persistence.database import Database
from sillytavern_rpg_engine.persistence.migrations import MigrationRunner


@pytest.fixture
def database(tmp_path: Path) -> Database:
    value = Database(tmp_path / "test.sqlite3")
    MigrationRunner(value, clock=lambda: "2026-08-10T00:00:00Z").apply()
    return value
