from dataclasses import dataclass
import sqlite3

import pytest

from sillytavern_rpg_engine.domain.errors import StaleStateError
from sillytavern_rpg_engine.domain.operations import MutationContext
from sillytavern_rpg_engine.services.mutations import MutationEngine, MutationRequest


@pytest.fixture
def seeded_campaign(database):
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO campaigns(id, name, created_at, updated_at)
            VALUES ('c1', 'Campaign', '2026-08-10T00:00:00Z', '2026-08-10T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO branches(id, campaign_id, status, created_at)
            VALUES ('main', 'c1', 'active', '2026-08-10T00:00:00Z')
            """
        )
    return "c1"


@dataclass(frozen=True)
class InsertMarker:
    marker: str

    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        connection.execute(
            "UPDATE campaigns SET name = ? WHERE id = ?",
            (self.marker, context.campaign.id),
        )
        return {"marker": self.marker}


@dataclass(frozen=True)
class Explode:
    def apply(self, connection: sqlite3.Connection, context: MutationContext) -> dict:
        connection.execute(
            "UPDATE campaigns SET name = 'corrupt' WHERE id = ?",
            (context.campaign.id,),
        )
        raise RuntimeError("boom")


def test_mutation_commits_version_event_outbox_and_snapshot(database, seeded_campaign):
    engine = MutationEngine(database, id_factory=lambda: "event-1", clock=lambda: "2026-08-10T00:00:00Z")
    result = engine.apply(
        MutationRequest(
            campaign_id="c1",
            branch_id="main",
            expected_version=0,
            source="user-command",
            event_type="campaign-renamed",
            operation=InsertMarker("Renamed"),
        )
    )
    assert result.state_version == 1
    with database.connect() as connection:
        assert connection.execute("SELECT state_version FROM campaigns WHERE id = 'c1'").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM jsonl_outbox").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM state_snapshots WHERE state_version = 1").fetchone()[0] == 1


def test_mutation_rolls_back_and_rejects_stale_version(database, seeded_campaign):
    engine = MutationEngine(database, id_factory=lambda: "event-2", clock=lambda: "2026-08-10T00:00:00Z")
    with pytest.raises(RuntimeError, match="boom"):
        engine.apply(MutationRequest("c1", "main", 0, "test", "explode", Explode()))
    with database.connect() as connection:
        assert connection.execute("SELECT name FROM campaigns WHERE id = 'c1'").fetchone()[0] == "Campaign"
        assert connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0
    with pytest.raises(StaleStateError, match="expected 9, found 0"):
        engine.apply(MutationRequest("c1", "main", 9, "test", "stale", InsertMarker("No")))
