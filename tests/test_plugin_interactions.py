from dataclasses import replace
from io import BytesIO
from PIL import Image as PillowImage

_buffer = BytesIO()
PillowImage.new("RGB", (8, 8), "white").save(_buffer, format="PNG")
PNG = _buffer.getvalue()
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from skland.binding import PreparedBinding
from skland.models import Account, Character
from skland.store import SklandStore
from skland.renderer import RenderError
from test_account_migration import add_account
from plugin_harness import Event, At, make_plugin, run, texts


@pytest_asyncio.fixture
async def plugin(tmp_path):
    store = SklandStore(tmp_path / "db")
    await store.initialize()
    obj, ns = make_plugin(store)
    ns["react"] = AsyncMock()
    return obj, ns


@pytest.mark.asyncio
@pytest.mark.parametrize("choice,saved", [("确认", True), ("取消", False), (None, False)])
async def test_preview_confirmation_cancel_timeout(plugin, choice, saved):
    obj, ns = plugin
    account = Account("bot:u", None, "cred", "token", "remote")
    role = Character("bot:u", "100", None, "arknights", "1", "博士")
    prepared = PreparedBinding(account, [role], 0, object())
    obj.service.bindings.prepare = AsyncMock(return_value=prepared)
    ns["render_bound_roles_card"] = AsyncMock(return_value=PNG)
    async def confirm(*args, **kwargs):
        # The preview cannot open a transaction or save credentials before user confirmation.
        assert await obj.store.list_accounts("bot:u") == []
        return choice
    ns["wait_choice"] = confirm
    results = await run(obj, Event(), ["bind", "x" * 32])
    assert not texts(results)
    assert bool(await obj.store.list_accounts("bot:u")) is saved


@pytest.mark.asyncio
async def test_render_failure_and_changed_state_never_commit(plugin):
    obj, ns = plugin
    account = Account("bot:u", None, "cred", "token", "remote")
    prepared = PreparedBinding(account, [], 0, object())
    obj.service.bindings.prepare = AsyncMock(return_value=prepared)
    ns["render_bound_roles_card"] = AsyncMock(side_effect=RenderError("failed"))
    assert "failed" in texts(await run(obj, Event(), ["bind", "x" * 32]))
    assert await obj.store.list_accounts("bot:u") == []
    ns["render_bound_roles_card"] = AsyncMock(return_value=PNG)
    async def changed(*args, **kwargs):
        await add_account(obj.store)
        return "确认"
    ns["wait_choice"] = changed
    assert "已变化" in texts(await run(obj, Event(), ["bind", "x" * 32]))
    assert len(await obj.store.list_accounts("bot:u")) == 1


@pytest.mark.asyncio
async def test_owner_management_mutex_and_group_permission(plugin):
    obj, ns = plugin
    obj._confirm_binding = AsyncMock()
    async with obj.service.bindings.exclusive("bot:u"):
        assert "进行中" in texts(await run(obj, Event(), ["bind", "secret"]))
    assert "白名单" in texts(await run(obj, Event(group="123"), ["bind", "secret"]))
    obj._confirm_binding.assert_not_awaited()
    await obj.store.register_credential_group("bot:GroupMessage:123", entry_umo="bot:GroupMessage:entry", registered_by="bot:u")
    await run(obj, Event(group="123"), ["bind", "secret"])
    obj._confirm_binding.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", ["1", "全部", "取消", None])
async def test_unbind_selection_and_confirmation(plugin, selection):
    obj, ns = plugin
    a, _ = await add_account(obj.store)
    b, _ = await add_account(obj.store, "remote-2")
    ns["render_bound_roles_card"] = AsyncMock(return_value=PNG)
    ns["wait_choice"] = AsyncMock(side_effect=[selection, "确认"])
    await run(obj, Event(), ["unbind"])
    remaining = await obj.store.list_accounts("bot:u")
    assert [a.id for a in remaining] == ({"1": [b.id], "全部": []}.get(selection, [a.id, b.id]))


@pytest.mark.asyncio
async def test_shortcut_persistence_exact_match_tail_and_permissions(plugin):
    obj, ns = plugin
    await run(obj, Event(admin=True), ["shortcut", "add", "我的六星", "box", "--rarity", "6"])
    restored, _ = make_plugin(obj.store)
    restored._shortcuts = await obj.store.shortcuts()
    assert restored._shortcuts == {"我的六星": ["box", "--rarity", "6"]}
    received = []
    async def capture(event, argv):
        received.append(argv)
        if False: yield
    restored._run = capture
    for text in ("我的六星 2 -r 3", "我的六星更多 2"):
        _ = [r async for r in restored.dynamic_shortcut(Event(message=text))]
    assert received == [["box", "--rarity", "6", "2", "-r", "3"]]
    for argv in (["add", "sk", "card"], ["add", "循环", "我的六星"], ["add", "递归", "shortcut", "list"]):
        assert "操作失败" in texts(await run(obj, Event(admin=True), ["shortcut", *argv]))
    assert "管理员" in texts(await run(obj, Event(), ["shortcut", "remove", "我的六星"]))
    await run(obj, Event(admin=True), ["shortcut", "add", "更新一下", "sync"])
    assert "管理员" in texts([r async for r in obj.dynamic_shortcut(Event(message="更新一下"))])


@pytest.mark.asyncio
async def test_role_target_and_box_numeric_arguments(plugin):
    obj, ns = plugin
    roster = SimpleNamespace(cards=[])
    obj.service.operator_roster = AsyncMock(return_value=SimpleNamespace(cards=[], summary="test"))
    await run(obj, Event(), ["box", "--rarity", "6", "-r", "2", "3"])
    obj.service.operator_roster.assert_awaited_once_with("bot:u", identity=2, filters=(), options={"rarities": "6"})
    event = Event()
    event.segments = [At("other")]
    for command in ("card", "efcard", "box", "rogue", "gacha", "efgacha", "efwar"):
        assert "不能指定角色" in texts(await run(obj, event, [command, "-r", "1"]))
    assert obj._page_ranges(-2, None, 6, 2) == [(4, 6)]
    assert obj._page_ranges(1, -1, 6, 2) == [(1, 3), (3, 5)]


@pytest.mark.asyncio
async def test_context_receipt_isolated_and_no_receipt_recent_fallback(plugin):
    obj, ns = plugin
    ns["send_with_receipt"] = AsyncMock(return_value=SimpleNamespace(message_id="sent"))
    event = Event(group="g")
    await obj._send_context_image(event, PNG, "card", {"background": {"uri": "file:///image.png"}})
    assert await obj._query_context(Event(group="g", reply="sent"), "background")
    assert await obj._query_context(Event(group="g"), "background")
    assert not await obj._query_context(Event(group="other", reply="sent"), "background")
    assert not await obj._query_context(Event(group="g", owner="other", reply="sent"), "background")
    assert not await obj._query_context(Event(group="g", platform="other", reply="sent"), "background")
    assert not await obj._query_context(Event(group="g", reply="unknown"), "background")
    ns["send_with_receipt"] = AsyncMock(return_value=None)
    await obj._send_context_image(event, PNG, "card", {"background": {"uri": "latest"}})
    assert (await obj._query_context(event, "background"))["uri"] == "latest"
    assert (await obj._query_context(Event(group="g", reply="unavailable-receipt"), "background"))["uri"] == "latest"
    await obj.store.save_context("bot:u", event.unified_msg_origin, "background", {}, ttl=-1)
    assert not await obj._query_context(event, "background")
