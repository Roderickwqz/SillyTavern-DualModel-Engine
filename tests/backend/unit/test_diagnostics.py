from sillytavern_rpg_engine.config import load_settings
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.diagnostics import build_diagnostics


def test_build_diagnostics_reports_schema_and_campaign_count(database):
    CampaignService(database, id_factory=iter("e").__next__,
                    clock=lambda: "2026-08-16T00:00:00Z").create_campaign("c1", "T")
    diag = build_diagnostics(database, degraded=False,
                             settings=load_settings({"RPG_NARRATOR_MODEL": "n", "RPG_CRITIC_MODEL": ""}))
    assert diag["schema"]["pending_migrations"] == []
    assert diag["campaigns"]["count"] == 1
    assert diag["database"]["integrity"] == "ok"
