import sqlite3

import pytest


def test_migration_0004_creates_tables(database):
    with database.connect() as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "dice_rolls",
            "combat_encounters",
            "combatants",
            "entity_conditions",
        } <= names
        version = connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0]
        assert version >= 4


def _campaign(connection):
    connection.execute(
        "INSERT INTO campaigns(id, name, created_at, updated_at)"
        " VALUES ('c1', 'Dungeon', '2026-08-12', '2026-08-12')"
    )
    connection.execute(
        "INSERT INTO branches(id, campaign_id, status, created_at)"
        " VALUES ('main', 'c1', 'active', '2026-08-12')"
    )


def test_dice_rolls_are_append_only(database):
    with database.transaction() as connection:
        _campaign(connection)
        connection.execute(
            "INSERT INTO dice_rolls(id, campaign_id, branch_id, purpose, formula,"
            " faces_json, total, rules_version, state_version, created_at)"
            " VALUES ('r1', 'c1', 'main', 'check', '1d20', '[12]', 12,"
            " 'srd-5.2.1', 1, '2026-08-12')"
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE dice_rolls SET total = 99 WHERE id = 'r1'")
    with database.transaction() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM dice_rolls WHERE id = 'r1'")


def test_one_active_encounter_per_branch(database):
    with database.transaction() as connection:
        _campaign(connection)
        connection.execute(
            "INSERT INTO combat_encounters(id, campaign_id, branch_id,"
            " created_state_version, created_at)"
            " VALUES ('e1', 'c1', 'main', 1, '2026-08-12')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO combat_encounters(id, campaign_id, branch_id,"
                " created_state_version, created_at)"
                " VALUES ('e3', 'c1', 'main', 1, '2026-08-12')"
            )
    with database.transaction() as connection:
        connection.execute(
            "UPDATE combat_encounters SET status = 'ended', ended_state_version = 2"
            " WHERE id = 'e1'"
        )
        connection.execute(
            "INSERT INTO combat_encounters(id, campaign_id, branch_id,"
            " created_state_version, created_at)"
            " VALUES ('e4', 'c1', 'main', 3, '2026-08-12')"
        )


def test_combatant_unique_per_encounter_and_conditions_pk(database):
    with database.transaction() as connection:
        _campaign(connection)
        connection.execute(
            "INSERT INTO entities(id, campaign_id, kind, name, normalized_name,"
            " created_state_version) VALUES ('pc1', 'c1', 'character', 'Aria',"
            " 'aria', 1)"
        )
        connection.execute(
            "INSERT INTO combat_encounters(id, campaign_id, branch_id,"
            " created_state_version, created_at)"
            " VALUES ('e1', 'c1', 'main', 1, '2026-08-12')"
        )
        connection.execute(
            "INSERT INTO combatants(id, encounter_id, entity_id, initiative)"
            " VALUES ('cb1', 'e1', 'pc1', 15)"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO combatants(id, encounter_id, entity_id, initiative)"
                " VALUES ('cb2', 'e1', 'pc1', 12)"
            )
        connection.execute(
            "INSERT INTO entity_conditions(campaign_id, entity_id, condition,"
            " level, source, applied_state_version)"
            " VALUES ('c1', 'pc1', 'prone', 1, 'test', 1)"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO entity_conditions(campaign_id, entity_id, condition,"
                " level, source, applied_state_version)"
                " VALUES ('c1', 'pc1', 'prone', 1, 'test', 2)"
            )
