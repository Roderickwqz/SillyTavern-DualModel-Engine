"""Health and schema diagnostics for the engine's SQLite database."""

from typing import Any

from ..persistence.database import Database
from ..persistence.migrations import MigrationRunner


def build_diagnostics(database: Database, *, degraded: bool, settings) -> dict[str, Any]:
    """Report database integrity, schema migration state, content counts,
    and configured models; ``degraded`` forces an overall degraded status."""
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
