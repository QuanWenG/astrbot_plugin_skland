"""Exercise the public facade without importing the AstrBot host runtime."""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from skland.service import SklandService


def facade():
    tree = ast.parse((Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8"))
    method = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "export_arknights_operator_snapshots")
    namespace = {"game_data": SimpleNamespace(variant_groups_loaded=True), "build_operator_snapshot": lambda role, roster, **kwargs: {"role": role, "roster": roster}}
    exec(compile(ast.Module(body=[method], type_ignores=[]), "main.py", "exec"), namespace)
    return namespace[method.name]


@pytest.mark.asyncio
async def test_rosters_empty_is_explicitly_opt_in():
    store = SimpleNamespace(get_characters=AsyncMock(return_value=[]))
    service = SklandService(store)
    assert await service.operator_rosters("bot:user", allow_empty=True) == []
    with pytest.raises(ValueError, match="角色"):
        await service.operator_rosters("bot:user")


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [[], [("one", "box1"), ("two", "box2")]])
async def test_export_checks_binding_version_even_for_empty(rows):
    service = SimpleNamespace(store=SimpleNamespace(owner_version=AsyncMock(side_effect=[1, 2])), operator_rosters=AsyncMock(return_value=rows))
    with pytest.raises(ValueError, match="导出期间"):
        await facade()(SimpleNamespace(service=service), "bot:user", allow_empty=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("allow_empty", [False, True])
async def test_export_preserves_default_and_forwards_empty_opt_in(allow_empty):
    service = SimpleNamespace(store=SimpleNamespace(owner_version=AsyncMock(return_value=3)), operator_rosters=AsyncMock(return_value=[]))
    assert await facade()(SimpleNamespace(service=service), "bot:user", allow_empty=allow_empty) == []
    service.operator_rosters.assert_awaited_once_with("bot:user", allow_empty=allow_empty)
    assert service.store.owner_version.await_count == 2


@pytest.mark.asyncio
async def test_export_failure_is_not_replaced_with_an_empty_set():
    service = SimpleNamespace(store=SimpleNamespace(owner_version=AsyncMock(return_value=1)), operator_rosters=AsyncMock(side_effect=RuntimeError("credential failed")))
    with pytest.raises(RuntimeError, match="credential failed"):
        await facade()(SimpleNamespace(service=service), "bot:user", allow_empty=True)
