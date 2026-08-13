import json

from sillytavern_rpg_engine.domain.models import (
    AttributeDefinition,
    AttributeType,
    Audience,
    DisplayType,
    EntityKind,
)
from sillytavern_rpg_engine.orchestration.presenter import TrackerPresenter
from sillytavern_rpg_engine.services.attributes import (
    DefineAttributeOperation,
    SetAttributeOperation,
)
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)
from sillytavern_rpg_engine.services.proposals import ProposalService


def _world(database):
    ids = iter(f"evt-{i}" for i in range(1000))
    campaigns = CampaignService(database, id_factory=ids.__next__,
                                clock=lambda: "2026-08-13T00:00:00Z")
    campaigns.create_campaign("c1", "测试")
    attributes = EntityAttributeService(database, campaigns.mutation_engine)
    attributes.apply_explicit("c1", "main", 0, CreateEntityOperation(
        entity_id="erin", kind=EntityKind.CHARACTER, name="艾琳",
    ))
    attributes.apply_explicit("c1", "main", 1, CreateEntityOperation(
        entity_id="silvermoon", kind=EntityKind.LOCATION, name="银月城",
    ))
    return campaigns, attributes


def _define(key, audiences):
    return DefineAttributeOperation(AttributeDefinition(
        campaign_id="c1", key=key, label=f"标签{key}", category="skill",
        value_type=AttributeType.NUMBER, display=DisplayType.BAR,
        audiences=frozenset(audiences), minimum=0, maximum=100,
    ))


def test_build_contains_rules_characters_and_visible_attributes_only(database):
    campaigns, attributes = _world(database)
    attributes.apply_explicit("c1", "main", 2, _define(
        "alchemy", {Audience.ENGINE, Audience.NARRATOR, Audience.PLAYER_UI},
    ))
    attributes.apply_explicit("c1", "main", 3, _define(
        "secret", {Audience.ENGINE},
    ))
    tracker = TrackerPresenter(database).build("c1", "main")
    assert tracker["rules"] == {"mode": "narrative", "enabled": False,
                                "version": None}
    assert tracker["userStats"]["attributes"] == []
    assert [c["name"] for c in tracker["characters"]] == ["艾琳"]
    keys = [a["key"] for a in tracker["characters"][0]["attributes"]]
    assert keys == []  # 尚无值
    assert tracker["pending_proposals"] == []


def test_values_rendered_and_hidden_keys_never_leak(database):
    campaigns, attributes = _world(database)
    attributes.apply_explicit("c1", "main", 2, _define(
        "alchemy", {Audience.PLAYER_UI},
    ))
    attributes.apply_explicit("c1", "main", 3, _define(
        "secret", {Audience.ENGINE},
    ))
    attributes.apply_explicit("c1", "main", 4, SetAttributeOperation(
        "erin", "alchemy", 35, None,
    ))
    attributes.apply_explicit("c1", "main", 5, SetAttributeOperation(
        "erin", "secret", 99, None,
    ))
    tracker = TrackerPresenter(database).build("c1", "main")
    attributes_out = tracker["characters"][0]["attributes"]
    assert [a["key"] for a in attributes_out] == ["alchemy"]
    assert attributes_out[0]["value"] == 35
    assert attributes_out[0]["max"] == 100
    assert "secret" not in json.dumps(tracker, ensure_ascii=False)


def test_pending_proposals_listed_and_render_wraps_json(database):
    campaigns, attributes = _world(database)
    prop_ids = iter(["prop-1", "prop-2", "prop-3"])
    proposals = ProposalService(database, campaigns.mutation_engine,
                                id_factory=prop_ids.__next__)
    proposals.create("c1", "main", {
        "kind": "set_attribute", "entity_id": "erin", "attribute_key": "alchemy",
        "value": 40, "turn_id": None,
    }, "剧情推断")
    presenter = TrackerPresenter(database)
    tracker = presenter.build("c1", "main")
    assert [p["id"] for p in tracker["pending_proposals"]] == ["prop-1"]
    rendered = presenter.render("c1", "main")
    assert rendered.startswith("```json\n") and rendered.endswith("\n```")
    json.loads(rendered.removeprefix("```json\n").removesuffix("\n```"))
