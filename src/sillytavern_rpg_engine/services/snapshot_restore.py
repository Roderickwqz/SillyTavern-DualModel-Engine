"""Restore live campaign state from a stored branch snapshot."""

from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Callable
from uuid import uuid4

from ..domain.errors import NotFoundError
from ..persistence.database import Database
from ..persistence.repositories import (
    BranchRepository,
    CampaignRepository,
    dump_json,
)
from .combat_serialization import COMBATANT_INSERT_SQL, combatant_insert_values
from .entities import normalize_key

# Memory events are deleted first: surviving events reference restored
# entities via location_entity_id with NO ACTION, so the rows newer than
# the snapshot must be gone before any entity is deleted.
_DELETE_CAMPAIGN_STATE_SQL = [
    (
        # Stale auto-stored snapshots would collide with the snapshot the
        # next mutation stores at target + 1 (PK campaign_id, branch_id,
        # state_version); prune everything newer than the restored version.
        "DELETE FROM state_snapshots WHERE campaign_id = ? AND branch_id = ?"
        " AND state_version > ?",
        lambda campaign_id, branch_id, state_version: (
            campaign_id, branch_id, state_version,
        ),
    ),
    (
        "DELETE FROM memory_events_fts WHERE rowid IN (SELECT rowid FROM"
        " memory_events WHERE campaign_id = ? AND branch_id = ?"
        " AND state_version > ?)",
        lambda campaign_id, branch_id, state_version: (
            campaign_id, branch_id, state_version,
        ),
    ),
    (
        "DELETE FROM memory_event_participants WHERE event_id IN (SELECT id FROM"
        " memory_events WHERE campaign_id = ? AND branch_id = ?"
        " AND state_version > ?)",
        lambda campaign_id, branch_id, state_version: (
            campaign_id, branch_id, state_version,
        ),
    ),
    (
        "DELETE FROM memory_events WHERE campaign_id = ? AND branch_id = ?"
        " AND state_version > ?",
        lambda campaign_id, branch_id, state_version: (
            campaign_id, branch_id, state_version,
        ),
    ),
    (
        "DELETE FROM attribute_values WHERE campaign_id = ?",
        lambda campaign_id, branch_id, state_version: (campaign_id,),
    ),
    (
        "DELETE FROM entity_aliases WHERE campaign_id = ?",
        lambda campaign_id, branch_id, state_version: (campaign_id,),
    ),
    (
        "DELETE FROM entities WHERE campaign_id = ?",
        lambda campaign_id, branch_id, state_version: (campaign_id,),
    ),
    (
        "DELETE FROM attribute_definitions WHERE campaign_id = ?",
        lambda campaign_id, branch_id, state_version: (campaign_id,),
    ),
    (
        "DELETE FROM facts WHERE campaign_id = ?",
        lambda campaign_id, branch_id, state_version: (campaign_id,),
    ),
    (
        "DELETE FROM relationships WHERE campaign_id = ?",
        lambda campaign_id, branch_id, state_version: (campaign_id,),
    ),
    (
        "DELETE FROM entity_conditions WHERE campaign_id = ?",
        lambda campaign_id, branch_id, state_version: (campaign_id,),
    ),
    (
        "DELETE FROM combatants WHERE encounter_id IN (SELECT id FROM"
        " combat_encounters WHERE campaign_id = ? AND branch_id = ?)",
        lambda campaign_id, branch_id, state_version: (campaign_id, branch_id),
    ),
    (
        "DELETE FROM combat_encounters WHERE campaign_id = ? AND branch_id = ?",
        lambda campaign_id, branch_id, state_version: (campaign_id, branch_id),
    ),
]


def _insert_from_snapshot(
    connection: sqlite3.Connection,
    snapshot: dict[str, Any],
    campaign_id: str,
    branch_id: str,
    state_version: int,
    id_factory: Callable[[], str],
    now: str,
) -> None:
    """Re-insert campaign state from the snapshot in FK-safe order."""
    for definition in snapshot["attribute_definitions"]:
        connection.execute(
            "INSERT INTO attribute_definitions(campaign_id, key, label, category,"
            " value_type, display, audiences_json, minimum, maximum,"
            " enum_values_json, unit, created_state_version)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                campaign_id, definition["key"], definition["label"],
                definition["category"], definition["value_type"],
                definition["display"], dump_json(definition["audiences"]),
                definition["minimum"], definition["maximum"],
                dump_json(definition["enum_values"]), definition["unit"],
                state_version,
            ),
        )
    for entity in snapshot["entities"]:
        connection.execute(
            "INSERT INTO entities(id, campaign_id, kind, name, normalized_name,"
            " age_status, created_state_version) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                entity["id"], campaign_id, entity["kind"], entity["name"],
                normalize_key(entity["name"]), entity["age_status"], state_version,
            ),
        )
        for alias in entity["aliases"]:
            connection.execute(
                "INSERT INTO entity_aliases(campaign_id, entity_id, alias,"
                " normalized_alias) VALUES (?, ?, ?, ?)",
                (campaign_id, entity["id"], alias, normalize_key(alias)),
            )
        for attribute in entity["attributes"]:
            connection.execute(
                "INSERT INTO attribute_values(campaign_id, entity_id,"
                " attribute_key, value_json, state_version, updated_turn_id)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    campaign_id, entity["id"], attribute["key"],
                    dump_json(attribute["value"]), attribute["state_version"],
                    attribute["updated_turn_id"],
                ),
            )
    for fact in snapshot["facts"]:
        connection.execute(
            "INSERT INTO facts(id, campaign_id, branch_id, entity_id, fact_type,"
            " fact_key, content, importance, audiences_json, valid_from,"
            " turn_id, source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?,"
            " ?, ?, ?, ?, ?)",
            (
                id_factory(), campaign_id, branch_id, fact["entity_id"],
                fact["fact_type"], fact["fact_key"], fact["content"],
                fact["importance"], dump_json(fact["audiences"]),
                fact["valid_from"], fact["turn_id"], fact["source"], now,
            ),
        )
    for relationship in snapshot["relationships"]:
        connection.execute(
            "INSERT INTO relationships(campaign_id, from_entity_id, to_entity_id,"
            " dimension, value_json, audiences_json, state_version,"
            " updated_turn_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                campaign_id, relationship["from_entity_id"],
                relationship["to_entity_id"], relationship["dimension"],
                dump_json(relationship["value"]),
                dump_json(relationship["audiences"]), state_version,
                relationship["updated_turn_id"],
            ),
        )
    for condition in snapshot["conditions"]:
        connection.execute(
            "INSERT INTO entity_conditions(campaign_id, entity_id, condition,"
            " level, source, applied_state_version) VALUES (?, ?, ?, ?, ?, ?)",
            (
                campaign_id, condition["entity_id"], condition["condition"],
                condition["level"], condition["source"], state_version,
            ),
        )
    combat = snapshot["combat"]
    if combat is not None:
        order = combat["order"]
        active_index = (
            order.index(combat["active_entity_id"])
            if combat["active_entity_id"] is not None
            else 0
        )
        connection.execute(
            "INSERT INTO combat_encounters(id, campaign_id, branch_id,"
            " round_number, active_index, created_state_version, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                combat["encounter_id"], campaign_id, branch_id, combat["round"],
                active_index, state_version, now,
            ),
        )
        for combatant in combat["combatants"]:
            connection.execute(
                COMBATANT_INSERT_SQL,
                combatant_insert_values(
                    f"{combat['encounter_id']}:{combatant['entity_id']}",
                    combat["encounter_id"],
                    combatant,
                ),
            )


def _participant_rows(
    connection: sqlite3.Connection,
    campaign_id: str,
    branch_id: str,
    state_version: int,
) -> list[sqlite3.Row]:
    """Participant links of a branch's events at or below state_version.

    Captured before the delete pass: entity rows are deleted and
    re-inserted during restore, and memory_event_participants cascades on
    entity_id, so the links would otherwise be lost.
    """
    return connection.execute(
        "SELECT event_id, entity_id FROM memory_event_participants"
        " JOIN memory_events"
        " ON memory_events.id = memory_event_participants.event_id"
        " WHERE memory_events.campaign_id = ? AND memory_events.branch_id = ?"
        " AND memory_events.state_version <= ?",
        (campaign_id, branch_id, state_version),
    ).fetchall()


def _copy_ancestor_memory_events(
    connection: sqlite3.Connection,
    campaign_id: str,
    branch_id: str,
    ancestor_branch_id: str,
    state_version: int,
    ancestor_participants: dict[str, list[str]],
    id_factory: Callable[[], str],
) -> None:
    """Copy the ancestor's memory_events at or below state_version into a
    fresh child branch.

    Events are strictly branch-scoped, so without this the child's
    recent/search/scene retrieval would stay permanently empty.
    memory_events.id is the global primary key, so the copies get fresh
    ids (events reference turns, never the other way round); FTS rows and
    participant links are written per copy.
    """
    source_events = connection.execute(
        "SELECT id, turn_id, event_type, content, importance,"
        " audiences_json, location_entity_id, source,"
        " state_version, created_at FROM memory_events"
        " WHERE campaign_id = ? AND branch_id = ?"
        " AND state_version <= ?",
        (campaign_id, ancestor_branch_id, state_version),
    ).fetchall()
    for event in source_events:
        event_id = id_factory()
        connection.execute(
            "INSERT INTO memory_events(id, campaign_id, branch_id,"
            " turn_id, event_type, content, importance, audiences_json,"
            " location_entity_id, source, state_version, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, campaign_id, branch_id, event["turn_id"],
             event["event_type"], event["content"], event["importance"],
             event["audiences_json"], event["location_entity_id"],
             event["source"], event["state_version"], event["created_at"]),
        )
        connection.execute(
            "INSERT INTO memory_events_fts(rowid, content)"
            " SELECT rowid, content FROM memory_events WHERE id = ?",
            (event_id,),
        )
        for entity_id in ancestor_participants.get(event["id"], ()):
            connection.execute(
                "INSERT INTO memory_event_participants(campaign_id, event_id,"
                " entity_id) VALUES (?, ?, ?)",
                (campaign_id, event_id, entity_id),
            )


class SnapshotRestoreService:
    """Replaces live campaign state with the contents of a stored snapshot."""

    def __init__(
        self,
        database: Database,
        id_factory: Callable[[], str] = lambda: uuid4().hex,
        clock: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(),
    ):
        self.database = database
        self.id_factory = id_factory
        self.clock = clock

    def _find_ancestor_snapshot(
        self, connection: sqlite3.Connection,
        campaign_id: str, branch_id: str, state_version: int,
    ) -> tuple[str, str] | None:
        """Return (snapshot_json, ancestor_branch_id) of the nearest branch
        in the parent_branch_id chain that holds a snapshot at
        state_version, or None when no ancestor has one."""
        current = branch_id
        while True:
            row = connection.execute(
                "SELECT parent_branch_id FROM branches WHERE campaign_id = ? AND id = ?",
                (campaign_id, current),
            ).fetchone()
            if row is None or row["parent_branch_id"] is None:
                return None
            ancestor = row["parent_branch_id"]
            snapshot = connection.execute(
                "SELECT snapshot_json FROM state_snapshots"
                " WHERE campaign_id = ? AND branch_id = ? AND state_version = ?",
                (campaign_id, ancestor, state_version),
            ).fetchone()
            if snapshot is not None:
                return snapshot["snapshot_json"], ancestor
            current = ancestor

    def restore(self, campaign_id: str, branch_id: str, state_version: int) -> int:
        """Reset campaign and branch state to the snapshot stored at
        state_version; raise NotFoundError when no such snapshot exists.
        Also prunes stale auto-stored snapshots newer than state_version,
        marks Pending proposals based on state newer than state_version
        as Stale, and resets the branch head pointer to the restored
        version (pointing at the branch's newest active turn), so the
        branch can keep applying mutations. The restored branch becomes
        the campaign's live owner (last_active_branch_id). When the
        snapshot is inherited from an ancestor branch (a fresh child), the
        ancestor's memory_events at or below state_version are copied to
        the child with fresh ids, FTS parity, and participant links, so
        the child's retrieval is not permanently empty. Returns the
        restored state_version. Raises NotFoundError when the campaign or
        branch does not exist.
        """
        with self.database.transaction() as connection:
            CampaignRepository().require(connection, campaign_id)
            BranchRepository().require(connection, campaign_id, branch_id)
            row = connection.execute(
                "SELECT snapshot_json FROM state_snapshots"
                " WHERE campaign_id = ? AND branch_id = ? AND state_version = ?",
                (campaign_id, branch_id, state_version),
            ).fetchone()
            if row is None:
                ancestor = self._find_ancestor_snapshot(
                    connection, campaign_id, branch_id, state_version
                )
                if ancestor is None:
                    raise NotFoundError(
                        f"snapshot {state_version} not found for campaign"
                        f" {campaign_id} branch {branch_id}"
                    )
                # A fresh child branch has no snapshots of its own; inherit
                # the nearest ancestor's snapshot and copy it under the
                # child so the child owns its state going forward.
                snapshot_json = ancestor[0]
                connection.execute(
                    "INSERT INTO state_snapshots(campaign_id, branch_id,"
                    " state_version, snapshot_json, created_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (campaign_id, branch_id, state_version, snapshot_json,
                     self.clock()),
                )
                ancestor_branch_id = ancestor[1]
                snapshot = json.loads(snapshot_json)
            else:
                ancestor_branch_id = branch_id
                snapshot = json.loads(row["snapshot_json"])
            # Participant links must survive the delete pass below: entity
            # rows are deleted and re-inserted, and memory_event_participants
            # cascades on entity_id. Capture them for both the restored
            # branch and (in the fork case) the ancestor whose events the
            # child inherits.
            surviving_participants = _participant_rows(
                connection, campaign_id, branch_id, state_version
            )
            ancestor_participants: dict[str, list[str]] = {}
            if ancestor_branch_id != branch_id:
                for participant in _participant_rows(
                    connection, campaign_id, ancestor_branch_id, state_version
                ):
                    ancestor_participants.setdefault(
                        participant["event_id"], []
                    ).append(participant["entity_id"])
            # Entity deletes cascade participant links and would violate the
            # NO ACTION location_entity_id reference of surviving events;
            # entities are re-inserted with identical ids, so defer FK
            # checks until commit.
            connection.execute("PRAGMA defer_foreign_keys = ON")
            for sql, params in _DELETE_CAMPAIGN_STATE_SQL:
                connection.execute(
                    sql, params(campaign_id, branch_id, state_version)
                )
            # Proposals based on state newer than the restored version can
            # no longer be valid: their base state was just rolled back.
            # Mark them Stale (never physically delete), mirroring the
            # snapshot pruning above.
            connection.execute(
                "UPDATE pending_proposals SET status = 'stale', resolved_at = ?"
                " WHERE campaign_id = ? AND branch_id = ? AND status = 'pending'"
                " AND base_state_version > ?",
                (self.clock(), campaign_id, branch_id, state_version),
            )
            _insert_from_snapshot(
                connection,
                snapshot,
                campaign_id,
                branch_id,
                state_version,
                self.id_factory,
                self.clock(),
            )
            if ancestor_branch_id != branch_id:
                _copy_ancestor_memory_events(
                    connection,
                    campaign_id,
                    branch_id,
                    ancestor_branch_id,
                    state_version,
                    ancestor_participants,
                    self.id_factory,
                )
            connection.executemany(
                "INSERT INTO memory_event_participants(campaign_id, event_id,"
                " entity_id) VALUES (?, ?, ?)",
                (
                    (campaign_id, row["event_id"], row["entity_id"])
                    for row in surviving_participants
                ),
            )
            connection.execute(
                "UPDATE campaigns SET state_version = ?,"
                " last_active_branch_id = ? WHERE id = ?",
                (state_version, branch_id, campaign_id),
            )
            latest_turn = connection.execute(
                "SELECT id FROM turns WHERE campaign_id = ? AND branch_id = ?"
                " AND status = 'active'"
                " ORDER BY rowid DESC LIMIT 1",
                (campaign_id, branch_id),
            ).fetchone()
            latest_turn_id = latest_turn["id"] if latest_turn else None
            connection.execute(
                # The head must reflect the restored version, or later
                # mutations and head tracking (forks, edits) lie about
                # where the branch is. Upsert: fresh campaigns have no
                # branch_heads row (migration 0006 only backfills rows that
                # existed at migration time); mirrors BranchService.update_head.
                # latest_turn_id points at the branch's newest active turn so
                # diagnostics do not point at a now-detached turn; a branch
                # with no active turns yet (fresh swipe child) keeps the
                # existing pointer (the fork-point turn).
                "INSERT INTO branch_heads(campaign_id, branch_id, state_version,"
                " latest_turn_id, updated_at) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(campaign_id, branch_id) DO UPDATE SET"
                " state_version = excluded.state_version,"
                " latest_turn_id = CASE WHEN excluded.latest_turn_id IS NULL"
                " THEN branch_heads.latest_turn_id"
                " ELSE excluded.latest_turn_id END,"
                " updated_at = excluded.updated_at",
                (campaign_id, branch_id, state_version, latest_turn_id,
                 self.clock()),
            )
        return state_version
