from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from skland.operator_snapshot import build_operator_snapshot
from skland.schemas.arknights.game_data import (
    OperatorCatalog,
    OperatorCatalogEntry,
    OperatorCatalogModule,
)
from skland.schemas.arknights.models.assist_chars import Equipment
from skland.schemas.arknights.models.base import Equip
from skland.schemas.arknights.models.chars import Character, Skill
from skland.schemas.arknights.operators import OperatorCard


def _role() -> SimpleNamespace:
    return SimpleNamespace(
        uid="123456",
        nickname="博士",
        channel_master_id="1",
        cred="must-not-leak",
    )


def _snapshot_card(
    *,
    rarity: int = 5,
    variant_group_id: str = "",
    modules: list[SimpleNamespace] | None = None,
    skills: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        char_id="char_1",
        variant_group_id=variant_group_id,
        name="风笛",
        rarity=rarity,
        profession="先锋",
        character=SimpleNamespace(
            evolvePhase=2,
            level=90,
            potentialRank=5,
        ),
        modules=modules or [],
        skills=skills or [],
    )


def test_builds_versioned_credential_free_snapshot() -> None:
    card = _snapshot_card(
        variant_group_id="char_1",
        modules=[
            SimpleNamespace(
                module_id="uniequip_1",
                name="破城号角",
                type_code="X",
                type_icon="uniequip_002_x",
                level=3,
                locked=False,
            ),
            SimpleNamespace(
                module_id="uniequip_2",
                name="未解锁模组",
                type_code="Y",
                type_icon="uniequip_002_y",
                level=0,
                locked=True,
            ),
        ],
        skills=[
            SimpleNamespace(id="sk_1", specializeLevel=3),
            SimpleNamespace(id="sk_2", specializeLevel=2),
            SimpleNamespace(id="sk_3", specializeLevel=0),
        ],
    )

    snapshot = build_operator_snapshot(
        _role(),
        SimpleNamespace(cards=[card]),
        snapshot_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        variant_metadata_complete=True,
    )

    assert snapshot["schema_version"] == 2
    assert snapshot["snapshot_at"] == "2026-08-10T00:00:00+00:00"
    assert snapshot["variant_metadata_complete"] is True
    assert snapshot["role"] == {
        "uid": "123456",
        "nickname": "博士",
        "server_id": "1",
        "server_name": "官服",
    }
    assert snapshot["operators"] == [
        {
            "char_id": "char_1",
            "variant_group_id": "char_1",
            "name": "风笛",
            "rarity": 6,
            "profession": "先锋",
            "evolve_phase": 2,
            "level": 90,
            "modules": [
                {
                    "module_id": "uniequip_1",
                    "module_name": "破城号角",
                    "type_code": "X",
                    "type_icon": "uniequip_002_x",
                    "level": 3,
                }
            ],
            "skills": [
                {"skill_id": "sk_1", "skill_index": 1, "mastery_level": 3},
                {"skill_id": "sk_2", "skill_index": 2, "mastery_level": 2},
                {"skill_id": "sk_3", "skill_index": 3, "mastery_level": 0},
            ],
            "potential_rank": 5,
        }
    ]
    assert "cred" not in repr(snapshot).casefold()
    assert "must-not-leak" not in repr(snapshot)


def test_snapshot_marks_fallback_variant_metadata_incomplete() -> None:
    snapshot = build_operator_snapshot(
        _role(),
        SimpleNamespace(cards=[_snapshot_card()]),
        variant_metadata_complete=True,
    )

    assert snapshot["variant_metadata_complete"] is False


@pytest.mark.parametrize("skill_count", [1, 2, 4])
def test_snapshot_preserves_actual_dynamic_skill_count(skill_count: int) -> None:
    card = _snapshot_card(
        # Deliberately use a non-six-star rarity: skill cardinality comes from
        # game data, never from a rarity-based assumption.
        rarity=4,
        skills=[
            SimpleNamespace(id=f"sk_{index}", specializeLevel=index % 4)
            for index in range(1, skill_count + 1)
        ],
    )

    operator = build_operator_snapshot(_role(), SimpleNamespace(cards=[card]))[
        "operators"
    ][0]

    assert operator["rarity"] == 5
    assert len(operator["skills"]) == skill_count
    assert [skill["skill_index"] for skill in operator["skills"]] == list(
        range(1, skill_count + 1)
    )


def test_snapshot_supports_more_than_three_modules_and_omits_locked_modules() -> None:
    modules = [
        SimpleNamespace(
            module_id=f"uniequip_{index}",
            name=f"模组 {index}",
            type_code=("X", "Y", "D", "Z", "W")[index - 1],
            type_icon=f"uniequip_type_{index}",
            level=3,
            locked=index == 5,
        )
        for index in range(1, 6)
    ]

    operator = build_operator_snapshot(
        _role(),
        SimpleNamespace(cards=[_snapshot_card(modules=modules)]),
    )["operators"][0]

    assert [module["module_id"] for module in operator["modules"]] == [
        "uniequip_1",
        "uniequip_2",
        "uniequip_3",
        "uniequip_4",
    ]


def test_snapshot_exports_official_variant_group() -> None:
    operator = build_operator_snapshot(
        _role(),
        SimpleNamespace(
            cards=[_snapshot_card(variant_group_id="char_003_kalts")]
        ),
    )["operators"][0]

    assert operator["variant_group_id"] == "char_003_kalts"


def test_game_catalog_and_live_card_propagate_module_identity() -> None:
    catalog = OperatorCatalog.from_game_tables(
        character_table={
            "char_1": {
                "name": "测试干员",
                "appellation": "Tester",
                "profession": "PIONEER",
                "rarity": 4,
                "skills": [{"skillId": "sk_1"}, {"skillId": "sk_2"}],
            }
        },
        char_patch_table={},
        uniequip_table={
            "charEquip": {"char_1": ["uniequip_x"]},
            "equipDict": {
                "uniequip_x": {
                    "typeIcon": "uniequip_002_x",
                    "uniEquipName": "目录中的 X 模组",
                    "typeName2": "X",
                }
            },
        },
        handbook_info_table={"handbookDict": {}},
        handbook_team_table={},
        char_meta_table={"spCharGroups": {"char_base": ["char_1", "char_2"]}},
    )
    entry = catalog.entries[0]
    assert entry.variant_group_id == "char_base"
    assert entry.modules == (
        OperatorCatalogModule(
            id="uniequip_x",
            type_icon="uniequip_002_x",
            module_name="目录中的 X 模组",
            type_code="X",
        ),
    )

    character = Character(
        charId="char_1",
        skinId="char_1#1",
        level=70,
        evolvePhase=2,
        potentialRank=2,
        mainSkillLvl=7,
        skills=[Skill(id="sk_1", specializeLevel=3)],
        equip=[
            Equip(id="uniequip_x", level=3, locked=False),
            Equip(id="uniequip_future", level=2, locked=False),
        ],
        favorPercent=200,
        defaultSkillId="sk_1",
        gainTime=1,
        defaultEquipId="uniequip_future",
    )
    equipment_map = {
        "uniequip_x": Equipment(
            id="uniequip_x",
            name="账号返回的 X 模组",
            typeIcon="uniequip_002_x",
        ),
        "uniequip_future": Equipment(
            id="uniequip_future",
            name="目录尚未收录的 Y 模组",
            typeIcon="uniequip_003_y",
        ),
    }

    card = OperatorCard.from_entry(entry, character, equipment_map)

    assert card.variant_group_id == "char_base"
    assert [(module.module_id, module.name, module.type_code) for module in card.modules] == [
        ("uniequip_x", "账号返回的 X 模组", "X"),
        ("uniequip_future", "目录尚未收录的 Y 模组", "Y"),
    ]
    # The catalog defines two real skills.  A missing mastery row is represented
    # as M0 without padding the operator to a fixed three-skill shape.
    assert [(skill.id, skill.specializeLevel) for skill in card.skills] == [
        ("sk_1", 3),
        ("sk_2", 0),
    ]


def test_patch_form_inherits_base_variant_group() -> None:
    base_data = {
        "name": "阿米娅",
        "appellation": "Amiya",
        "profession": "CASTER",
        "rarity": 4,
        "skills": [],
    }
    patch_data = {
        **base_data,
        "name": "阿米娅",
        "profession": "WARRIOR",
    }
    catalog = OperatorCatalog.from_game_tables(
        character_table={"char_002_amiya": base_data},
        char_patch_table={
            "patchChars": {"char_1001_amiya2": patch_data},
            "infos": {
                "char_002_amiya": {"tmplIds": ["char_1001_amiya2"]}
            },
        },
        uniequip_table={"charEquip": {}, "equipDict": {}},
        handbook_info_table={"handbookDict": {}},
        handbook_team_table={},
        char_meta_table={
            "spCharGroups": {"char_002_amiya": ["char_002_amiya"]}
        },
    )

    assert catalog.by_id["char_002_amiya"].variant_group_id == "char_002_amiya"
    assert catalog.by_id["char_1001_amiya2"].variant_group_id == "char_002_amiya"


def test_operator_card_falls_back_to_live_equipment_when_catalog_is_missing() -> None:
    entry = OperatorCatalogEntry.fallback("char_future")
    character = Character(
        charId="char_future",
        skinId="char_future#1",
        level=50,
        evolvePhase=1,
        potentialRank=0,
        mainSkillLvl=7,
        skills=[Skill(id="sk_future", specializeLevel=1)],
        equip=[Equip(id="uniequip_delta", level=1, locked=False)],
        favorPercent=100,
        defaultSkillId="sk_future",
        gainTime=1,
        defaultEquipId="uniequip_delta",
    )

    card = OperatorCard.from_entry(
        entry,
        character,
        {
            "uniequip_delta": Equipment(
                id="uniequip_delta",
                name="未来 Δ 模组",
                typeIcon="uniequip_004_delta",
            )
        },
    )

    assert [(module.module_id, module.type_code, module.level) for module in card.modules] == [
        ("uniequip_delta", "D", 1)
    ]
    assert [(skill.id, skill.specializeLevel) for skill in card.skills] == [
        ("sk_future", 1)
    ]
