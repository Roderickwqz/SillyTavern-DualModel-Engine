import sqlite3

import pytest

from sillytavern_rpg_engine.persistence.database import Database
from sillytavern_rpg_engine.persistence.migrations import MigrationRunner


def seed_campaign_entity(connection):
    connection.execute(
        "INSERT INTO campaigns(id, name, created_at, updated_at)"
        " VALUES ('c1', 'Campaign', '2026-08-10T00:00:00Z', '2026-08-10T00:00:00Z')"
    )
    connection.execute(
        "INSERT INTO branches(id, campaign_id, status, created_at)"
        " VALUES ('main', 'c1', 'active', '2026-08-10T00:00:00Z')"
    )
    connection.execute(
        "INSERT INTO entities(id, campaign_id, kind, name, normalized_name,"
        " age_status, created_state_version)"
        " VALUES ('erin', 'c1', 'character', '艾琳', '艾琳', 'adult', 0)"
    )


def test_memory_tables_and_fts_exist(database):
    with database.connect() as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }
        assert {
            "facts",
            "memory_events",
            "memory_event_participants",
            "memory_events_fts",
            "relationships",
            "trait_events",
            "development_arcs",
            "memory_summaries",
        } <= names


def test_trigram_fts_matches_chinese_substring(database):
    with database.transaction() as connection:
        seed_campaign_entity(connection)
        connection.execute(
            "INSERT INTO memory_events(id, campaign_id, branch_id, event_type,"
            " content, importance, audiences_json, source, state_version,"
            " created_at) VALUES ('e1', 'c1', 'main', 'scene',"
            " '艾琳在银月城学习炼金术', 3, '[\"engine\"]', 'test', 1,"
            " '2026-08-10T00:00:00Z')"
        )
        connection.execute(
            "INSERT INTO memory_events_fts(rowid, content)"
            " SELECT rowid, content FROM memory_events WHERE id = 'e1'"
        )
        hits = connection.execute(
            "SELECT m.id FROM memory_events m WHERE m.rowid IN ("
            " SELECT rowid FROM memory_events_fts"
            " WHERE memory_events_fts MATCH ?)",
            ('"银月城"',),
        ).fetchall()
        assert [row[0] for row in hits] == ["e1"]


def test_current_fact_partial_unique_index(database):
    with database.transaction() as connection:
        seed_campaign_entity(connection)
        connection.execute(
            "INSERT INTO facts(id, campaign_id, branch_id, entity_id, fact_type,"
            " fact_key, content, importance, audiences_json, valid_from, source,"
            " created_at) VALUES ('f1', 'c1', 'main', 'erin', 'identity',"
            " 'home', '银月城', 3, '[\"engine\"]', 1, 'test',"
            " '2026-08-10T00:00:00Z')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO facts(id, campaign_id, branch_id, entity_id,"
                " fact_type, fact_key, content, importance, audiences_json,"
                " valid_from, source, created_at) VALUES ('f2', 'c1', 'main',"
                " 'erin', 'identity', 'home', '凌云窟', 3, '[\"engine\"]', 2,"
                " 'test', '2026-08-10T00:00:00Z')"
            )
