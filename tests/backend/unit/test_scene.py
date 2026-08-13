import pytest

from sillytavern_rpg_engine.domain.errors import (
    AmbiguousEntityError,
    NotFoundError,
)
from sillytavern_rpg_engine.domain.models import EntityKind
from sillytavern_rpg_engine.orchestration.scene import (
    resolve_reference,
    scan_scene,
)
from sillytavern_rpg_engine.services.campaigns import CampaignService
from sillytavern_rpg_engine.services.entities import (
    CreateEntityOperation,
    EntityAttributeService,
)


def _world(database):
    ids = iter(f"evt-{i}" for i in range(20))
    campaigns = CampaignService(database, id_factory=ids.__next__,
                                clock=lambda: "2026-08-13T00:00:00Z")
    campaigns.create_campaign("c1", "测试")
    entities = EntityAttributeService(database, campaigns.mutation_engine)
    version = 0  # create_campaign leaves state_version 0
    for entity_id, name, aliases in (
        ("erin", "艾琳", ("小艾",)),
        ("silvermoon", "银月城", ()),
        ("erin-2", "艾琳·复制体", ()),
    ):
        entities.apply_explicit("c1", "main", version, CreateEntityOperation(
            entity_id=entity_id, kind=EntityKind.CHARACTER, name=name,
            aliases=aliases,
        ))
        version += 1
    return entities


def test_scan_finds_names_and_aliases_longest_first(database):
    _world(database)
    with database.connect() as connection:
        scan = scan_scene(connection, "c1", "小艾和艾琳·复制体在银月城碰面")
    assert scan.names == {
        "erin": "艾琳",
        "erin-2": "艾琳·复制体",
        "silvermoon": "银月城",
    }


def test_scan_without_matches_is_empty(database):
    _world(database)
    with database.connect() as connection:
        assert scan_scene(connection, "c1", "荒野").entity_ids == ()


def test_resolve_reference_by_id_name_alias(database):
    _world(database)
    with database.connect() as connection:
        assert resolve_reference(connection, "c1", "erin") == "erin"
        assert resolve_reference(connection, "c1", "艾琳") == "erin"
        assert resolve_reference(connection, "c1", "小艾") == "erin"
        with pytest.raises(NotFoundError):
            resolve_reference(connection, "c1", "不存在的人")


def test_resolve_reference_ambiguous_name_and_alias(database):
    ids = iter(f"evt-{i}" for i in range(20))
    campaigns = CampaignService(database, id_factory=ids.__next__,
                                clock=lambda: "2026-08-13T00:00:00Z")
    campaigns.create_campaign("c1", "测试")
    entities = EntityAttributeService(database, campaigns.mutation_engine)
    entities.apply_explicit("c1", "main", 0, CreateEntityOperation(
        entity_id="a", kind=EntityKind.CHARACTER, name="星光",
    ))
    entities.apply_explicit("c1", "main", 1, CreateEntityOperation(
        entity_id="b", kind=EntityKind.CHARACTER, name="另一个人", aliases=("星光",),
    ))
    with database.connect() as connection:
        with pytest.raises(AmbiguousEntityError):
            resolve_reference(connection, "c1", "星光")
