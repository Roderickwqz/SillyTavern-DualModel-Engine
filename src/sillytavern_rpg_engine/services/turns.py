"""Turn records: one row per committed game turn (bookkeeping, not state)."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Callable
from uuid import uuid4

from ..domain.errors import NotFoundError, ValidationError
from ..persistence.database import Database


@dataclass(frozen=True)
class Turn:
    id: str
    campaign_id: str
    branch_id: str
    parent_turn_id: str | None
    intent: str
    player_text: str
    response_text: str
    history_hash: str
    removed_instructions: tuple[str, ...]
    state_before_version: int
    state_after_version: int
    created_at: str
    lineage_hash_before: str | None = None
    lineage_hash_after: str | None = None
    response_hash: str | None = None
    status: str = "active"


def _to_turn(row) -> Turn:
    return Turn(
        id=row["id"],
        campaign_id=row["campaign_id"],
        branch_id=row["branch_id"],
        parent_turn_id=row["parent_turn_id"],
        intent=row["intent"],
        player_text=row["player_text"],
        response_text=row["response_text"],
        history_hash=row["history_hash"],
        removed_instructions=tuple(json.loads(row["removed_instructions_json"])),
        state_before_version=row["state_before_version"],
        state_after_version=row["state_after_version"],
        created_at=row["created_at"],
        lineage_hash_before=row["lineage_hash_before"],
        lineage_hash_after=row["lineage_hash_after"],
        response_hash=row["response_hash"],
        status=row["status"],
    )


class TurnService:
    """Append-only turn log; parentage follows the branch's latest turn."""

    def __init__(
        self,
        database: Database,
        id_factory: Callable[[], str] = lambda: uuid4().hex,
        clock: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(),
    ):
        self.database = database
        self.id_factory = id_factory
        self.clock = clock

    def record(
        self,
        campaign_id: str,
        branch_id: str,
        *,
        intent: str,
        player_text: str,
        response_text: str,
        history_hash: str,
        state_before_version: int,
        state_after_version: int,
        turn_id: str | None = None,
        removed_instructions: tuple[str, ...] = (),
        lineage_hash_before: str | None = None,
        lineage_hash_after: str | None = None,
        response_hash: str | None = None,
        parent_turn_id: str | None = None,
    ) -> Turn:
        """Append one turn row; when parent_turn_id is given it is stored
        verbatim (lineage-resolver fork/restore paths), otherwise the
        branch's newest active turn is linked as parent."""
        if not player_text.strip():
            raise ValidationError("player_text must not be empty")
        if state_after_version < state_before_version:
            raise ValidationError(
                "state_after_version must be >= state_before_version"
            )
        turn_id = turn_id or self.id_factory()
        now = self.clock()
        with self.database.transaction() as connection:
            if parent_turn_id is None:
                parent = connection.execute(
                    "SELECT id FROM turns WHERE campaign_id = ? AND branch_id = ?"
                    " AND status = 'active'"
                    " ORDER BY rowid DESC LIMIT 1",
                    (campaign_id, branch_id),
                ).fetchone()
                stored_parent = parent["id"] if parent else None
            else:
                stored_parent = parent_turn_id
            connection.execute(
                "INSERT INTO turns(id, campaign_id, branch_id, parent_turn_id,"
                " intent, player_text, response_text, history_hash,"
                " removed_instructions_json,"
                " state_before_version, state_after_version, created_at,"
                " lineage_hash_before, lineage_hash_after, response_hash, status)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')",
                (
                    turn_id, campaign_id, branch_id,
                    stored_parent,
                    intent, player_text, response_text, history_hash,
                    json.dumps(list(removed_instructions), ensure_ascii=False),
                    state_before_version, state_after_version, now,
                    lineage_hash_before, lineage_hash_after, response_hash,
                ),
            )
        return Turn(
            id=turn_id, campaign_id=campaign_id, branch_id=branch_id,
            parent_turn_id=stored_parent,
            intent=intent, player_text=player_text, response_text=response_text,
            history_hash=history_hash,
            removed_instructions=tuple(removed_instructions),
            state_before_version=state_before_version,
            state_after_version=state_after_version, created_at=now,
            lineage_hash_before=lineage_hash_before,
            lineage_hash_after=lineage_hash_after,
            response_hash=response_hash,
            status="active",
        )

    def latest(self, campaign_id: str, branch_id: str) -> Turn | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM turns WHERE campaign_id = ? AND branch_id = ?"
                " ORDER BY rowid DESC LIMIT 1",
                (campaign_id, branch_id),
            ).fetchone()
        return _to_turn(row) if row else None

    def get(self, turn_id: str) -> Turn:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM turns WHERE id = ?", (turn_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"turn {turn_id} not found")
        return _to_turn(row)

    def find_by_lineage_after(
        self, campaign_id: str, lineage_hash_after: str
    ) -> list[Turn]:
        """Return active turns on a campaign whose lineage_hash_after equals
        the given hash, newest first; [] when none."""
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM turns WHERE campaign_id = ? AND lineage_hash_after = ?"
                " AND status = 'active' ORDER BY rowid DESC",
                (campaign_id, lineage_hash_after),
            ).fetchall()
        return [_to_turn(row) for row in rows]

    def find_by_lineage_before(
        self, campaign_id: str, lineage_hash_before: str
    ) -> list[Turn]:
        """Return active turns on a campaign whose lineage_hash_before equals
        the given hash, newest first; [] when none."""
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM turns WHERE campaign_id = ? AND lineage_hash_before = ?"
                " AND status = 'active' ORDER BY rowid DESC",
                (campaign_id, lineage_hash_before),
            ).fetchall()
        return [_to_turn(row) for row in rows]

    def detach_after(
        self, campaign_id: str, branch_id: str, turn_id: str
    ) -> int:
        """Mark active turns recorded on the branch after the given turn as
        detached; return the number of turns updated. Later turns are the
        ones that sort after the turn in the branch's canonical order
        (rowid, i.e. append order — created_at can step backward on NTP
        correction, rowid cannot). Turns are never physically deleted."""
        with self.database.connect() as connection:
            cursor = connection.execute(
                "UPDATE turns SET status = 'detached' WHERE campaign_id = ?"
                " AND branch_id = ? AND status = 'active'"
                " AND rowid >"
                " (SELECT rowid FROM turns WHERE id = ?"
                " AND campaign_id = ? AND branch_id = ?)",
                (campaign_id, branch_id, turn_id, campaign_id, branch_id),
            )
        return cursor.rowcount
