from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.resources import files
import re
import sqlite3

from .database import Database


@dataclass(frozen=True)
class MigrationStatus:
    """Snapshot of which migration versions are applied and which remain."""

    applied: list[int]
    pending: list[int]
    latest: int


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
                for statement in self._split_statements(sql):
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at)"
                    " VALUES (?, ?)",
                    (version, self.clock()),
                )

    def status(self) -> MigrationStatus:
        """Report applied and pending migration versions against the schema."""
        scripts = self._load_scripts()
        latest = max(scripts) if scripts else 0
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        applied = [row[0] for row in rows]
        pending = sorted(set(scripts) - set(applied))
        return MigrationStatus(applied=applied, pending=pending, latest=latest)

    def _load_scripts(self) -> dict[int, str]:
        schema_dir = files("sillytavern_rpg_engine.persistence").joinpath("schema")
        scripts = {}
        for resource in schema_dir.iterdir():
            if not resource.name.endswith(".sql"):
                continue
            version = int(resource.name.split("_", 1)[0])
            scripts[version] = resource.read_text(encoding="utf-8")
        return scripts

    def _split_statements(self, sql: str) -> list[str]:
        statements = []
        current = []
        in_quote = False
        begin_depth = 0
        last_block_token = ""
        index = 0
        while index < len(sql):
            char = sql[index]
            if in_quote:
                current.append(char)
                if char == "'" and index + 1 < len(sql) and sql[index + 1] == "'":
                    current.append(sql[index + 1])
                    index += 1
                elif char == "'":
                    in_quote = False
            elif char == "'":
                in_quote = True
                current.append(char)
            elif char == ";":
                if begin_depth == 0:
                    statement = "".join(current).strip()
                    if statement:
                        statements.append(statement)
                    current = []
                    last_block_token = ""
                else:
                    current.append(char)
            else:
                current.append(char)
                if not in_quote:
                    text = "".join(current).rstrip()
                    if re.search(r"(?<!\w)BEGIN$", text, re.IGNORECASE):
                        if last_block_token != "BEGIN":
                            begin_depth += 1
                            last_block_token = "BEGIN"
                    elif re.search(r"(?<!\w)END$", text, re.IGNORECASE):
                        if last_block_token != "END":
                            begin_depth = max(0, begin_depth - 1)
                            last_block_token = "END"
                    elif not (char.isalnum() or char == "_"):
                        last_block_token = ""
            index += 1
        statement = "".join(current).strip()
        if statement:
            statements.append(statement)
        return statements
