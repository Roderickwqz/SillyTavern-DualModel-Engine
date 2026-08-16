"""Timestamped SQLite backups with rotation, checkpointed for restorability."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..persistence.database import Database


class DatabaseBackupService:
    """Copy a database to a timestamped file, checkpointing WAL first so the
    copy is restorable, then delete the oldest copies beyond ``keep``."""

    def __init__(
        self,
        database: Database,
        clock: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(),
    ):
        self.database = database
        self._clock = clock

    def backup(self, dest_dir: Path, *, keep: int = 7) -> Path:
        """Write a WAL-checkpointed copy of the database into ``dest_dir``,
        rotate to the newest ``keep`` copies, and return the new copy path."""
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        stamp = self._clock().replace(":", "").replace("-", "")[:15]
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

    def _rotate(self, dest_dir: Path, keep: int) -> None:
        """Delete the oldest backups of this database beyond the newest
        ``keep``; only files matching this database's stem are touched."""
        copies = sorted(dest_dir.glob(f"{self.database.path.stem}-*.db"))
        for stale in copies[:-keep] if keep > 0 else copies:
            stale.unlink()
