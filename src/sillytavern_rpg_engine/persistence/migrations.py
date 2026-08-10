from datetime import datetime, timezone
from importlib.resources import files
import sqlite3

from .database import Database


class MigrationRunner:
    """Apply packaged SQL migration scripts in version order."""

    def __init__(self, database: Database, clock=lambda: datetime.now(timezone.utc).isoformat()):
        self.database = database
        self.clock = clock

    def apply(self) -> None:
        scripts = self._load_scripts()
        with self.database.transaction() as connection:
            has_migrations_table = connection.execute(
                "SELECT COUNT(*) FROM sqlite_master"
                " WHERE type = 'table' AND name = 'schema_migrations'"
            ).fetchone()[0]
            applied = (
                {
                    row[0]
                    for row in connection.execute(
                        "SELECT version FROM schema_migrations"
                    )
                }
                if has_migrations_table
                else set()
            )
            for version, sql in sorted(scripts.items()):
                if version in applied:
                    continue
                connection.executescript(sql)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at)"
                    " VALUES (?, ?)",
                    (version, self.clock()),
                )

    def _load_scripts(self) -> dict[int, str]:
        schema_dir = files("sillytavern_rpg_engine.persistence").joinpath("schema")
        scripts = {}
        for resource in schema_dir.iterdir():
            if not resource.name.endswith(".sql"):
                continue
            version = int(resource.name.split("_", 1)[0])
            scripts[version] = resource.read_text(encoding="utf-8")
        return scripts
