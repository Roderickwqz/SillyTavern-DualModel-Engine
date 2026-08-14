"""Resolve visible chat history to exactly one parent turn and branch."""

from dataclasses import dataclass
from uuid import uuid4

from ..domain.errors import BranchResolutionError
from ..llm.client import ChatMessage
from ..orchestration.normalize import (
    _history_hash,
    lineage_hash_before,
    response_hash,
)
from ..persistence.database import Database
from .branches import BranchService
from .snapshot_restore import SnapshotRestoreService
from .turns import TurnService


@dataclass(frozen=True)
class BranchContext:
    """Resolution result: which branch and parent turn to continue from.

    branch_id: branch the next turn should be recorded on.
    parent_turn_id: stored turn to link the next turn to; None only for
        fresh-start and swipe-fork contexts.
    restored: whether live state was rolled back to a stored snapshot.
    """

    branch_id: str
    parent_turn_id: str | None
    restored: bool


class LineageResolver:
    """Maps a tracker-free visible history to exactly one parent turn and
    branch, detaching or forking around edits and swipes.

    Raises BranchResolutionError when the history is ambiguous (multiple
    candidate turns) or unmappable (no known prefix matches); no state is
    written in those cases.
    """

    def __init__(self, database: Database):
        self.database = database
        self.turns = TurnService(database)
        self.branches = BranchService(database)
        self.snapshots = SnapshotRestoreService(database)

    def resolve(
        self, campaign_id: str, messages: tuple[ChatMessage, ...]
    ) -> BranchContext:
        """Return the branch context for the given visible history.

        Exact match first: if exactly one active turn's
        lineage_hash_after equals lineage_hash_before(messages), continue
        on that turn's branch without any restore. Otherwise fall back to
        the longest known prefix of the visible history (edit/delete/
        swipe). A first-ever message on a fresh campaign (no recorded
        history) resolves to branch "main" with no parent. Raises
        BranchResolutionError on ambiguity or when no prefix matches any
        active turn.
        """
        if len(messages) == 1:
            if self.turns.find_by_lineage_before(
                campaign_id, _history_hash(())
            ):
                raise BranchResolutionError(
                    f"no visible history to resolve a parent turn for campaign"
                    f" {campaign_id}; echo the chat's message array, or start a"
                    f" new campaign"
                )
            return BranchContext("main", None, restored=False)
        prefix_hash = lineage_hash_before(messages)
        rows = self.turns.find_by_lineage_after(campaign_id, prefix_hash)
        if len(rows) > 1:
            raise BranchResolutionError(
                f"ambiguous parent for lineage {prefix_hash}: {len(rows)} candidates"
            )
        if len(rows) == 1:
            parent = rows[0]
            return BranchContext(parent.branch_id, parent.id, restored=False)
        return self._resolve_by_prefix(campaign_id, messages)

    def _resolve_by_prefix(
        self, campaign_id: str, messages: tuple[ChatMessage, ...]
    ) -> BranchContext:
        """Match the deepest known prefix of the visible history.

        Walk k from len(messages) - 1 down to 0, comparing the hash of
        messages[:k] against stored lineage_hash_before of active turns.
        The deepest (largest k) match is the parent. If the trailing
        assistant message is a swipe (a stored response_hash exists and
        differs from the visible reply), fork a child branch and restore
        the parent's before-state; otherwise restore the matched head
        first (so a failed restore leaves branch state consistent), then
        detach later turns on the matched branch. Raises
        BranchResolutionError when no prefix matches any active turn.
        """
        for k in range(len(messages) - 1, -1, -1):
            h = _history_hash(messages[:k])
            candidates = self.turns.find_by_lineage_before(campaign_id, h)
            if not candidates:
                continue
            if len(candidates) > 1:
                raise BranchResolutionError(
                    f"ambiguous parent for lineage {h}: {len(candidates)} candidates"
                )
            parent = candidates[0]
            swiped = (
                parent.response_hash is not None
                and len(messages) >= 2
                and messages[-2].role == "assistant"
                and response_hash(messages[-2].content) != parent.response_hash
            )
            if swiped:
                child = self.branches.fork(
                    campaign_id, parent.branch_id, parent.id,
                    new_branch_id=f"branch-swipe-{uuid4().hex}",
                )
                self.snapshots.restore(
                    campaign_id, child, parent.state_before_version
                )
                return BranchContext(child, None, restored=True)
            self.snapshots.restore(
                campaign_id, parent.branch_id, parent.state_after_version
            )
            self.turns.detach_after(campaign_id, parent.branch_id, parent.id)
            return BranchContext(parent.branch_id, parent.id, restored=True)
        raise BranchResolutionError(
            f"cannot map visible history to any known parent for campaign"
            f" {campaign_id}"
        )
