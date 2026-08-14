"""Branch forks and per-branch head pointers."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

from ..domain.errors import NotFoundError, ValidationError
from ..persistence.database import Database
from ..persistence.repositories import BranchRepository


@dataclass(frozen=True)
class BranchHead:
    campaign_id: str
    branch_id: str
    state_version: int
    latest_turn_id: str | None
    updated_at: str


class BranchService:
    """Creates child branches and tracks each branch's head pointer."""

    def __init__(
        self,
        database: Database,
        id_factory: Callable[[], str] = lambda: uuid4().hex,
        clock: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(),
    ):
        self.database = database
        self.branch_repository = BranchRepository()
        self._id_factory = id_factory
        self._clock = clock

    def get_head(self, campaign_id: str, branch_id: str) -> BranchHead:
        """Return the head pointer for a branch, or raise NotFoundError."""
        with self.database.connect() as connection:
            self.branch_repository.require(connection, campaign_id, branch_id)
            row = connection.execute(
                "SELECT campaign_id, branch_id, state_version, latest_turn_id,"
                " updated_at FROM branch_heads WHERE campaign_id = ? AND branch_id = ?",
                (campaign_id, branch_id),
            ).fetchone()
            if row is None:
                raise NotFoundError(f"branch head {branch_id!r} missing")
            return BranchHead(**dict(row))

    def update_head(
        self, campaign_id: str, branch_id: str,
        state_version: int, turn_id: str | None,
    ) -> None:
        """Upsert the branch's head pointer to the given turn and version;
        a committed turn also marks the branch as the live owner."""
        now = self._clock()
        with self.database.connect() as connection:
            self.branch_repository.require(connection, campaign_id, branch_id)
            connection.execute(
                "INSERT INTO branch_heads(campaign_id, branch_id, state_version,"
                " latest_turn_id, updated_at) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(campaign_id, branch_id) DO UPDATE SET"
                " state_version = excluded.state_version,"
                " latest_turn_id = excluded.latest_turn_id,"
                " updated_at = excluded.updated_at",
                (campaign_id, branch_id, state_version, turn_id, now),
            )
            connection.execute(
                "UPDATE campaigns SET last_active_branch_id = ?"
                " WHERE id = ?",
                (branch_id, campaign_id),
            )

    def fork(
        self, campaign_id: str, parent_branch_id: str,
        parent_turn_id: str, *, new_branch_id: str | None = None,
    ) -> str:
        """Create a child branch at a parent turn and seed its head; return
        the new branch id."""
        branch_id = new_branch_id or f"branch-{self._id_factory()}"
        now = self._clock()
        with self.database.transaction() as connection:
            self.branch_repository.require(connection, campaign_id, parent_branch_id)
            parent = connection.execute(
                "SELECT id, state_after_version FROM turns WHERE id = ? AND campaign_id = ?",
                (parent_turn_id, campaign_id),
            ).fetchone()
            if parent is None:
                raise NotFoundError(f"parent turn {parent_turn_id!r} not found")
            exists = connection.execute(
                "SELECT 1 FROM branches WHERE campaign_id = ? AND id = ?",
                (campaign_id, branch_id),
            ).fetchone()
            if exists:
                raise ValidationError(f"branch {branch_id!r} already exists")
            connection.execute(
                "INSERT INTO branches(id, campaign_id, parent_branch_id, status, created_at)"
                " VALUES (?, ?, ?, 'active', ?)",
                (branch_id, campaign_id, parent_branch_id, now),
            )
            connection.execute(
                "INSERT INTO branch_heads(campaign_id, branch_id, state_version,"
                " latest_turn_id, updated_at) VALUES (?, ?, ?, ?, ?)",
                (campaign_id, branch_id, parent["state_after_version"],
                 parent_turn_id, now),
            )
        return branch_id
