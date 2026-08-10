"""Idempotent JSONL export of audit events staged in the outbox."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..persistence.database import Database


class JsonlAuditExporter:
    """Append unexported audit events to a JSONL file, never twice.

    Crash recovery: if the process dies after appending but before marking
    rows exported, the next flush finds those event IDs already in the file
    and skips them instead of writing duplicates.
    """

    def __init__(
        self,
        database: Database,
        clock: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(),
    ):
        self.database = database
        self.clock = clock

    def flush(self, path: Path) -> int:
        """Append unexported events to ``path`` and return how many were
        written. The parent directory must already exist. Marks confirmed
        rows exported in one transaction."""
        output = Path(path)
        parent = output.parent
        if not parent.is_dir():
            raise FileNotFoundError(
                f"output directory does not exist: {parent}"
            )
        existing = self._existing_event_ids(output)
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT o.event_id, o.payload_json, a.created_at"
                " FROM jsonl_outbox o"
                " JOIN audit_events a ON a.id = o.event_id"
                " WHERE o.exported_at IS NULL"
                " ORDER BY a.created_at, o.event_id"
            ).fetchall()
        pending = [row for row in rows if row["event_id"] not in existing]
        if not pending:
            return 0
        with open(output, "a", encoding="utf-8") as handle:
            for row in pending:
                handle.write(row["payload_json"] + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        written_ids = [row["event_id"] for row in pending]
        now = self.clock()
        with self.database.transaction() as connection:
            placeholders = ",".join("?" for _ in written_ids)
            connection.execute(
                f"UPDATE jsonl_outbox SET exported_at = ?"
                f" WHERE exported_at IS NULL AND event_id IN ({placeholders})",
                [now, *written_ids],
            )
        return len(written_ids)

    @staticmethod
    def _existing_event_ids(output: Path) -> set[str]:
        """Event IDs already present in the file; torn trailing lines from a
        crash are skipped so their events get appended whole on retry."""
        if not output.is_file():
            return set()
        existing: set[str] = set()
        with open(output, encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                existing.add(record["event_id"])
        return existing
