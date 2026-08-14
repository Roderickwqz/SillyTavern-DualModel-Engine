"""Restore live campaign state from a stored branch snapshot."""

from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Callable
from uuid import uuid4

from ..domain.errors import NotFoundError
from ..persistence.database import Database
from ..persistence.repositories import dump_json
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
                "INSERT INTO combatants(id, encounter_id, entity_id, initiative,"
                " action_used, bonus_used, reaction_used, movement_total,"
                " movement_used, hidden, dodging, disengaged,"
                " concentrating_spell) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
                " ?, ?, ?)",
                (
                    f"{combat['encounter_id']}:{combatant['entity_id']}",
                    combat["encounter_id"], combatant["entity_id"],
                    combatant["initiative"], int(combatant["action_used"]),
                    int(combatant["bonus_used"]), int(combatant["reaction_used"]),
                    combatant["movement_total"], combatant["movement_used"],
                    int(combatant["hidden"]), int(combatant["dodging"]),
                    int(combatant["disengaged"]), combatant["concentrating_spell"],
                ),
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
        Also prunes stale auto-stored snapshots newer than state_version and
        resets the branch head pointer to the restored version, so the
        branch can keep applying mutations. Returns the restored
        state_version."""
        with self.database.transaction() as connection:
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
                snapshot_json, _ancestor = ancestor
                connection.execute(
                    "INSERT INTO state_snapshots(campaign_id, branch_id,"
                    " state_version, snapshot_json, created_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (campaign_id, branch_id, state_version, snapshot_json,
                     self.clock()),
                )
                snapshot = json.loads(snapshot_json)
            else:
                snapshot = json.loads(row["snapshot_json"])
            # Entity deletes cascade participant links and would violate the
            # NO ACTION location_entity_id reference of surviving events;
            # entities are re-inserted with identical ids, so defer FK
            # checks until commit.
            connection.execute("PRAGMA defer_foreign_keys = ON")
            surviving_participants = connection.execute(
                "SELECT event_id, entity_id FROM memory_event_participants"
                " JOIN memory_events"
                " ON memory_events.id = memory_event_participants.event_id"
                " WHERE memory_events.campaign_id = ? AND memory_events.branch_id = ?"
                " AND memory_events.state_version <= ?",
                (campaign_id, branch_id, state_version),
            ).fetchall()
            for sql, params in _DELETE_CAMPAIGN_STATE_SQL:
                connection.execute(
                    sql, params(campaign_id, branch_id, state_version)
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
            connection.executemany(
                "INSERT INTO memory_event_participants(campaign_id, event_id,"
                " entity_id) VALUES (?, ?, ?)",
                (
                    (campaign_id, row["event_id"], row["entity_id"])
                    for row in surviving_participants
                ),
            )
            connection.execute(
                "UPDATE campaigns SET state_version = ? WHERE id = ?",
                (state_version, campaign_id),
            )
            connection.execute(
                # The head must reflect the restored version, or later
                # mutations and head tracking (forks, edits) lie about
                # where the branch is. Upsert: fresh campaigns have no
                # branch_heads row (migration 0006 only backfills rows that
                # existed at migration time); mirrors BranchService.update_head.
                "INSERT INTO branch_heads(campaign_id, branch_id, state_version,"
                " latest_turn_id, updated_at) VALUES (?, ?, ?, NULL, ?)"
                " ON CONFLICT(campaign_id, branch_id) DO UPDATE SET"
                " state_version = excluded.state_version,"
                " updated_at = excluded.updated_at",
                (campaign_id, branch_id, state_version, self.clock()),
            )
        return state_version
