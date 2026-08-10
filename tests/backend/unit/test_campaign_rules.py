import pytest

from sillytavern_rpg_engine.domain.errors import ConfirmationRequiredError
from sillytavern_rpg_engine.domain.models import CampaignRules, RulesMode
from sillytavern_rpg_engine.services.campaigns import CampaignService


def test_campaign_defaults_to_narrative_and_rules_are_isolated(database):
    service = CampaignService(database, id_factory=iter(["event-c1", "event-c2", "event-c3"]).__next__, clock=lambda: "2026-08-10T00:00:00Z")
    first = service.create_campaign("c1", "Story")
    second = service.create_campaign("c2", "Dungeon")
    assert first.rules == CampaignRules()
    enabled = service.set_rules(
        "c2",
        expected_version=0,
        rules=CampaignRules(RulesMode.DND_2024, True, "5.2.1"),
    )
    assert enabled.snapshot["campaign"]["rules"]["mode"] == "dnd-2024"
    assert service.get_campaign("c1").rules == CampaignRules()


def test_disable_preserves_state_and_active_combat_requires_confirmation(database):
    service = CampaignService(database, id_factory=iter(["create", "enable", "disable"]).__next__, clock=lambda: "2026-08-10T00:00:00Z")
    service.create_campaign("c1", "Dungeon")
    service.set_rules("c1", 0, CampaignRules(RulesMode.DND_2024, True, "5.2.1"))
    with pytest.raises(ConfirmationRequiredError, match="active combat"):
        service.disable_rules("c1", 1, active_combat=True, confirmed=False)
    result = service.disable_rules("c1", 1, active_combat=True, confirmed=True)
    assert result.snapshot["campaign"]["rules"] == {
        "mode": "dnd-2024",
        "enabled": False,
        "version": "5.2.1",
        "custom_preset_id": None,
    }
