from types import SimpleNamespace, MethodType
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from plugin_harness import Event, make_plugin, run, texts
from skland.store import SklandStore


@pytest_asyncio.fixture
async def plugin(tmp_path):
    store = SklandStore(tmp_path / "db")
    await store.initialize()
    obj, ns = make_plugin(store)
    ns["react"] = AsyncMock()
    return obj, ns


@pytest.mark.asyncio
async def test_version_reports_loaded_code_without_account_requests(plugin):
    obj, _ = plugin
    obj.service.require_account = AsyncMock(side_effect=AssertionError("credential lookup"))
    result = texts(await run(obj, Event(group="g"), ["version"]))
    assert "运行版本：2.7.6" in result
    assert "角色接口 401 恢复：已加载" in result
    assert "凭证自动恢复链：已加载" in result
    assert "接口文件" not in result and "服务文件" not in result
    obj.service.require_account.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_sees_actual_loaded_module_paths(plugin):
    obj, _ = plugin
    result = texts(await run(obj, Event(admin=True), ["version"]))
    assert "角色接口文件：" in result and "凭证服务文件：" in result
    assert "request.py" in result and "service.py" in result


@pytest.mark.asyncio
async def test_partial_copy_with_old_api_is_detected(plugin, monkeypatch):
    obj, _ = plugin
    async def old_api(*args):
        return {}
    actual_globals = obj.service._with_refresh.__func__.__globals__
    monkeypatch.setitem(actual_globals, "SklandAPI", SimpleNamespace(ark_card_raw=old_api))
    result = texts(await run(obj, Event(group="g"), ["version"]))
    assert "角色接口 401 恢复：未加载" in result
    assert "凭证自动恢复链：已加载" in result
    assert "完整覆盖实际插件目录" in result


@pytest.mark.asyncio
async def test_partial_copy_with_old_service_is_detected(plugin):
    obj, _ = plugin
    async def old_service(self, *args):
        return None
    obj.service._with_refresh = MethodType(old_service, obj.service)
    result = texts(await run(obj, Event(group="g"), ["version"]))
    assert "凭证自动恢复链：未加载" in result
    assert "完整覆盖实际插件目录" in result


@pytest.mark.asyncio
async def test_version_uses_loaded_version_constant(plugin):
    obj, ns = plugin
    ns["PLUGIN_VERSION"] = "loaded-build"
    result = texts(await run(obj, Event(group="g"), ["version"]))
    assert "运行版本：loaded-build" in result


@pytest.mark.asyncio
async def test_version_rejects_unexpected_arguments(plugin):
    obj, _ = plugin
    assert "用法：/sk version" in texts(await run(obj, Event(), ["version", "extra"]))
