from types import SimpleNamespace

import pytest

from skland.schemas import OperatorCatalog, OperatorCatalogEntry, OperatorRosterQuery


def _catalog() -> OperatorCatalog:
    return OperatorCatalog(
        entries=(
            OperatorCatalogEntry(
                char_id="char_1",
                name="测试近卫",
                appellation="Tester",
                profession="近卫",
                rarity=5,
                sort_id=2,
                position="近战位",
                gender="女",
                races=frozenset({"萨卡兹"}),
            ),
        )
    )


def test_operator_query_supports_natural_filters():
    query = OperatorRosterQuery.from_input(
        _catalog(), filters=("未拥有", "6星", "近卫", "女", "萨卡兹")
    )
    entry = _catalog().entries[0]
    assert query.matches(entry, None)


def test_unowned_and_potential_is_rejected():
    with pytest.raises(ValueError, match="未拥有干员没有潜能"):
        OperatorRosterQuery.from_input(_catalog(), filters=("未拥有", "满潜"))


def test_multiple_natural_filters_are_combined_with_and_semantics():
    catalog = OperatorCatalog(
        entries=(
            OperatorCatalogEntry(
                char_id="match",
                name="匹配干员",
                appellation="Match",
                profession="先锋",
                rarity=5,
                sort_id=4,
                position="近战位",
            ),
            OperatorCatalogEntry(
                char_id="wrong_profession",
                name="错误职业",
                appellation="WrongProfession",
                profession="近卫",
                rarity=5,
                sort_id=3,
                position="近战位",
            ),
            OperatorCatalogEntry(
                char_id="wrong_position",
                name="错误部署位",
                appellation="WrongPosition",
                profession="先锋",
                rarity=5,
                sort_id=2,
                position="远程位",
            ),
            OperatorCatalogEntry(
                char_id="wrong_rarity",
                name="错误星级",
                appellation="WrongRarity",
                profession="先锋",
                rarity=4,
                sort_id=1,
                position="近战位",
            ),
        )
    )
    query = OperatorRosterQuery.from_input(
        catalog,
        filters=("6星", "先锋", "近战", "练度排序"),
    )
    owned = SimpleNamespace()

    assert query.sort.value == "training"
    assert [
        entry.char_id for entry in catalog.entries if query.matches(entry, owned)
    ] == ["match"]
