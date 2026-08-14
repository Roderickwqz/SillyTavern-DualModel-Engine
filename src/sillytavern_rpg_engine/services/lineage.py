"""Resolve visible chat history to exactly one parent turn and branch."""

from dataclasses import dataclass
from uuid import uuid4

from ..domain.errors import BranchResolutionError, NotFoundError
from ..llm.client import ChatMessage
from ..orchestration.normalize import (
    _history_hash,
    lineage_hash_before,
    response_hash,
)
from ..persistence.database import Database
from ..persistence.repositories import CampaignRepository
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
        on that turn's branch; the branch's stored head is restored first
        when it diverged from the live global state (another branch owns
        the live tables since this one was last active, tracked by
        campaigns.last_active_branch_id, or the head version no longer
        matches the live version). Otherwise fall back to the longest
        known prefix of the visible history (edit/delete/swipe). A
        first-ever message on a fresh campaign (no recorded history)
        resolves to branch "main" with no parent. Raises
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
            head_version, live_version, last_active_branch_id = (
                self._branch_head_and_live(campaign_id, parent.branch_id)
            )
            if self._needs_restore(
                parent.branch_id, head_version, live_version,
                last_active_branch_id,
            ):
                # The live shared tables belong to a different branch since
                # this branch was last active (swipe forks roll the global
                # version back to their fork point). Restore this branch's
                # stored head so the next mutation lands on this branch's
                # snapshot chain again instead of colliding with a snapshot
                # version the other branch already occupies.
                self.snapshots.restore(
                    campaign_id, parent.branch_id, head_version
                )
                return BranchContext(parent.branch_id, parent.id, restored=True)
            return BranchContext(parent.branch_id, parent.id, restored=False)
        return self._resolve_by_prefix(campaign_id, messages)

    def _branch_head_and_live(
        self, campaign_id: str, branch_id: str,
    ) -> tuple[int | None, int, str | None]:
        """Return (branch head version, live campaign version,
        last_active_branch_id) in one read.

        The head version is None when the branch has no head row yet
        (fresh campaigns and legacy data predating branch_heads); callers
        treat that as "no divergence". Raises NotFoundError when the
        campaign is unknown.
        """
        with self.database.connect() as connection:
            head = connection.execute(
                "SELECT state_version FROM branch_heads"
                " WHERE campaign_id = ? AND branch_id = ?",
                (campaign_id, branch_id),
            ).fetchone()
            live = connection.execute(
                "SELECT state_version, last_active_branch_id"
                " FROM campaigns WHERE id = ?",
                (campaign_id,),
            ).fetchone()
        if live is None:
            raise NotFoundError(f"campaign {campaign_id} not found")
        return (
            head["state_version"] if head is not None else None,
            live["state_version"],
            live["last_active_branch_id"],
        )

    @staticmethod
    def _needs_restore(
        branch_id: str,
        head_version: int | None,
        live_version: int,
        last_active_branch_id: str | None,
    ) -> bool:
        """True when the branch's state must be restored before continuing.

        Divergence means either another branch owns the live tables
        (last_active_branch_id differs; version equality is not an
        ownership test, so a child that re-rolled to the same version
        would otherwise silently pollute the parent's chain) or the
        branch's head version no longer matches the live version. A NULL
        last_active_branch_id is legacy data: fall back to the version
        check only. A missing head (no head row yet) carries no
        divergence signal; there is nothing to restore to.
        """
        if head_version is None:
            return False
        if last_active_branch_id is not None and last_active_branch_id != branch_id:
            return True
        return head_version != live_version

    def _resolve_by_prefix(
        self, campaign_id: str, messages: tuple[ChatMessage, ...]
    ) -> BranchContext:
        """Match the deepest known prefix of the visible history.

        Walk k from len(messages) - 1 down to 0, comparing the hash of
        messages[:k] against stored lineage_hash_before of active turns.
        The deepest (largest k) match is the parent. If the trailing
        assistant message is a swipe (a stored response_hash exists and
        differs from the visible reply), fork a child branch and restore
        the parent's before-state; otherwise detach later turns on the
        matched branch first, then restore the matched head (restore
        recomputes the head's latest_turn_id from the post-detach active
        set, and a failed restore leaves the branch with a correct head
        and detached tail for the resolver's retry). Before forking,
        trailing pairs that are unrecorded query exchanges (QUERY turns
        record no turn row) continue on the matched turn instead of
        forking, so a mid-chat question does not spawn a spurious branch.
        Raises BranchResolutionError when no prefix matches any active
        turn.
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
                continuation = self._unrecorded_tail_continuation(
                    campaign_id, messages
                )
                if continuation is not None:
                    return continuation
                child = self.branches.fork(
                    campaign_id, parent.branch_id, parent.id,
                    new_branch_id=f"branch-swipe-{uuid4().hex}",
                )
                self.snapshots.restore(
                    campaign_id, child, parent.state_before_version
                )
                return BranchContext(child, None, restored=True)
            # Detach before restore: detach_after is an idempotent plain
            # UPDATE that cannot fail meaningfully, so restoring afterwards
            # (1) recomputes latest_turn_id from the post-detach active set
            # and points the head at the matched parent instead of a
            # now-detached turn, and (2) on a failed restore leaves the
            # branch with a correct head and detached tail; the resolver's
            # retry then re-attempts the restore, which is consistent.
            self.turns.detach_after(campaign_id, parent.branch_id, parent.id)
            self.snapshots.restore(
                campaign_id, parent.branch_id, parent.state_after_version
            )
            return BranchContext(parent.branch_id, parent.id, restored=True)
        raise BranchResolutionError(
            f"cannot map visible history to any known parent for campaign"
            f" {campaign_id}"
        )

    def _unrecorded_tail_continuation(
        self, campaign_id: str, messages: tuple[ChatMessage, ...]
    ) -> BranchContext | None:
        """Return the continue-context when the trailing completed pairs
        are unrecorded query exchanges; None when they include a replaced
        reply (a genuine swipe that must fork).

        QUERY turns record no turn row, so a request echoing a query
        exchange looks like a swipe of the last recorded reply: the
        deepest prefix lands on an older turn and the trailing assistant
        reply differs from its stored response. Walk k from
        len(messages) - 3 down by 2, dropping one completed pair per
        step: when the visible prefix at k matches some active turn's
        lineage_hash_after and none of the dropped pairs starts a
        recorded turn, the trailing pairs are unrecorded exchanges and
        the next turn continues on that turn's branch with no fork (the
        new turn's lineage hashes include the query pairs, so the chain
        self-heals). A switch-back hidden behind the query pair still
        restores the branch's stored head when the branch diverged from
        the live state.
        """
        for k in range(len(messages) - 3, -1, -2):
            prefix_hash = _history_hash(messages[:k])
            rows = self.turns.find_by_lineage_after(campaign_id, prefix_hash)
            if len(rows) > 1:
                raise BranchResolutionError(
                    f"ambiguous query-continuation parent for lineage"
                    f" {prefix_hash}: {len(rows)} candidates"
                )
            if not rows:
                continue
            if self._dropped_pairs_recorded(campaign_id, messages, k):
                return None
            turn = rows[0]
            head_version, live_version, last_active_branch_id = (
                self._branch_head_and_live(campaign_id, turn.branch_id)
            )
            if self._needs_restore(
                turn.branch_id, head_version, live_version,
                last_active_branch_id,
            ):
                # The trailing query exchange still counts as a switch
                # back: when another branch owns the live tables (or the
                # version diverged), the continued turn must land on this
                # branch's restored state, or it narrates against the
                # other branch's state and the next mutation collides
                # with this branch's own snapshot.
                self.snapshots.restore(
                    campaign_id, turn.branch_id, head_version
                )
                return BranchContext(turn.branch_id, turn.id, restored=True)
            return BranchContext(turn.branch_id, turn.id, restored=False)
        return None

    def _dropped_pairs_recorded(
        self, campaign_id: str, messages: tuple[ChatMessage, ...], k: int
    ) -> bool:
        """True when any completed pair dropped below position k starts a
        recorded active turn.

        A dropped pair's user message sits at position p, so the pair is
        recorded iff some active turn's lineage_hash_before equals
        hash(messages[:p]). Such a pair is a replaced reply (its turn row
        exists but the visible reply differs, or the walk would have
        matched that turn at a deeper k first), so the trailing exchanges
        are not all unrecorded queries and the swipe must fork.
        """
        for p in range(k, len(messages) - 2, 2):
            if self.turns.find_by_lineage_before(
                campaign_id, _history_hash(messages[:p])
            ):
                return True
        return False
