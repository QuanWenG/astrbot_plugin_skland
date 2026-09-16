import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from skland.access import validate_group_sid
from skland.binding import PreparedBinding
from skland.models import Account, Character
from skland.store import DDL, SklandStore
from skland.support_integration import import_support_after_login
from plugin_harness import Event, make_plugin, run, texts
from test_account_migration import add_account
from test_plugin_interactions import PNG

ENTRY = "bot:GroupMessage:A"
TARGET = "bot:GroupMessage:B"


@pytest_asyncio.fixture
async def plugin(tmp_path):
    store = SklandStore(tmp_path / "db")
    await store.initialize()
    obj, ns = make_plugin(store)
    obj.config["credential_registration_groups"] = [ENTRY]
    ns["react"] = AsyncMock()
    return obj, ns


@pytest.mark.parametrize("value", ["", "123", "bot:FriendMessage:u", "bot:GroupMessage:", ":GroupMessage:g", " bot :GroupMessage:g", "b:GroupMessage:g\nx", "b:GroupMessage:" + "x" * 512])
def test_requires_complete_group_sid(value):
    with pytest.raises(ValueError, match="SID"):
        validate_group_sid(value)


@pytest.mark.asyncio
async def test_entry_and_target_permissions_are_independent(plugin):
    obj, _ = plugin
    assert "已加入" in texts(await run(obj, Event(group="A"), ["register", TARGET]))
    assert "已在" in texts(await run(obj, Event(group="A"), ["register", TARGET]))
    assert "入口群" in texts(await run(obj, Event(group="B"), ["register", "bot:GroupMessage:C"]))
    assert "入口群" in texts(await run(obj, Event(), ["register", TARGET]))
    assert "不会默认" in texts(await run(obj, Event(group="A"), ["register"]))
    obj._confirm_binding = AsyncMock(return_value=False)
    assert "白名单" in texts(await run(obj, Event(group="A"), ["bind", "secret"]))
    assert "白名单" in texts(await run(obj, Event(platform="other", group="B"), ["bind", "secret"]))
    obj._confirm_binding.assert_not_awaited()
    obj.config["credential_registration_groups"] = []
    assert "入口群" in texts(await run(obj, Event(group="A"), ["register", "bot:GroupMessage:C"]))
    await run(obj, Event(group="B"), ["bind", "secret"])
    obj._confirm_binding.assert_awaited_once()
    row, = await obj.store.list_credential_groups()
    assert (row["target_sid"], row["entry_umo"], row["registered_by"]) == (TARGET, ENTRY, "bot:u")


@pytest.mark.asyncio
async def test_admin_revoke_is_immediate_and_survives_restart(plugin):
    obj, _ = plugin
    await run(obj, Event(group="A"), ["register", TARGET])
    for command in ("whitelist", "unregister"):
        assert "管理员" in texts(await run(obj, Event(group="A"), [command, TARGET]))
    assert TARGET in texts(await run(obj, Event(admin=True), ["whitelist"]))
    assert "已撤销" in texts(await run(obj, Event(admin=True), ["unregister", TARGET]))
    await obj.store.initialize(legacy_credential_groups=[TARGET])
    assert not await obj.store.credential_group_allowed("B", TARGET)
    for command in ("bind", "qrcode", "unbind"):
        assert "白名单" in texts(await run(obj, Event(group="B"), [command, "secret"]))
    await obj._require_credential_access(Event())


@pytest.mark.asyncio
async def test_v3_upgrade_preserves_accounts_and_migrates_legacy_once(tmp_path):
    path = tmp_path / "db"
    with sqlite3.connect(path) as db:
        for statement in DDL:
            db.execute(statement)
        db.execute("PRAGMA user_version=3")
    store = SklandStore(path)
    account, role = await add_account(store)
    await store.initialize(legacy_credential_groups=["B", " B ", "other:GroupMessage:C"])
    assert (await store.get_account("bot:u")).id == account.id
    assert (await store.get_characters("bot:u"))[0].id == role.id
    assert await store.credential_group_allowed("B", "different:GroupMessage:B")
    assert await store.credential_group_allowed("C", "other:GroupMessage:C")
    assert not await store.credential_group_allowed("C", "bot:GroupMessage:C")
    assert not await store.register_credential_group(TARGET, entry_umo=ENTRY, registered_by="bot:u")
    assert len(await store.list_credential_groups()) == 2
    with sqlite3.connect(store.migration_summary["backup"]) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert db.execute("SELECT id FROM accounts").fetchone()[0] == account.id
    await store.revoke_credential_group("B")
    await store.initialize(legacy_credential_groups=["B", "new"])
    assert not await store.credential_group_allowed("B", TARGET)
    assert not await store.credential_group_allowed("new", "bot:GroupMessage:new")
    assert len(list(tmp_path.glob("*.bak"))) == 1


@pytest.mark.asyncio
async def test_v3_registration_migration_failure_rolls_back(tmp_path):
    path = tmp_path / "db"
    with sqlite3.connect(path) as db:
        for statement in DDL:
            db.execute(statement)
        db.execute("PRAGMA user_version=3")
    class InvalidValue:
        def __str__(self):
            raise RuntimeError("migration failed")
    with pytest.raises(RuntimeError):
        await SklandStore(path).initialize(legacy_credential_groups=[InvalidValue()])
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='credential_groups'").fetchall()
    await SklandStore(path).initialize(legacy_credential_groups=[TARGET])


async def prepare_login(obj, ns):
    await obj.store.register_credential_group(TARGET, entry_umo=ENTRY, registered_by="bot:u")
    prepared = PreparedBinding(
        Account("bot:u", None, "cred", "token", "remote"),
        [Character("bot:u", "100", None, "arknights", "1", "博士")], 0, object(),
    )
    obj.service.bindings.prepare = AsyncMock(return_value=prepared)
    ns["render_bound_roles_card"] = AsyncMock(return_value=PNG)
    ns["wait_choice"] = AsyncMock(return_value="确认")


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", ["确认", "取消", None])
async def test_login_hook_only_after_commit_and_outside_management_lock(plugin, choice):
    obj, ns = plugin
    await prepare_login(obj, ns)
    ns["wait_choice"].return_value = choice
    async def imported(context, **kwargs):
        assert await obj.store.list_accounts("bot:u")
        async with obj.service.bindings.exclusive("bot:u"):
            pass
        assert kwargs == dict(owner_id="bot:u", group_umo=TARGET, platform_id="bot", group_id="B", group_name="", member_nickname="用户")
        return "已导入 UID：100"
    ns["import_support_after_login"] = AsyncMock(side_effect=imported)
    event = Event(group="B")
    await run(obj, event, ["bind", "x" * 32])
    assert ns["import_support_after_login"].await_count == int(choice == "确认")
    assert bool(await obj.store.list_accounts("bot:u")) == (choice == "确认")


@pytest.mark.asyncio
async def test_revocation_during_confirmation_prevents_commit_and_import(plugin):
    obj, ns = plugin
    await prepare_login(obj, ns)
    async def revoked(*args, **kwargs):
        await obj.store.revoke_credential_group(TARGET)
        return "确认"
    ns["wait_choice"] = revoked
    ns["import_support_after_login"] = AsyncMock()
    assert "白名单" in texts(await run(obj, Event(group="B"), ["bind", "x" * 32]))
    assert not await obj.store.list_accounts("bot:u")
    ns["import_support_after_login"].assert_not_awaited()


@pytest.mark.asyncio
async def test_import_failure_does_not_undo_login_or_expose_exception(plugin):
    obj, ns = plugin
    await prepare_login(obj, ns)
    ns["import_support_after_login"] = AsyncMock(side_effect=RuntimeError("sensitive-token"))
    event = Event(group="B")
    assert not texts(await run(obj, event, ["bind", "x" * 32]))
    assert await obj.store.list_accounts("bot:u")
    messages = texts([call.args[0] for call in event.send.await_args_list])
    assert "登录已成功" in messages and "自动导入失败" in messages
    assert "sensitive-token" not in messages


@pytest.mark.asyncio
async def test_private_login_never_calls_optional_plugin(plugin):
    obj, ns = plugin
    await prepare_login(obj, ns)
    ns["import_support_after_login"] = AsyncMock()
    await run(obj, Event(), ["bind", "x" * 32])
    assert await obj.store.list_accounts("bot:u")
    ns["import_support_after_login"].assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("scan_result", ["code", None, "cancel"])
async def test_scan_cleanup_before_import_and_no_import_on_timeout_or_cancel(plugin, scan_result):
    obj, ns = plugin
    await prepare_login(obj, ns)
    ns["fetch_user_avatar"] = AsyncMock(return_value=None)
    ns["render_qrcode_card"] = lambda *args: PNG
    async def get_status(scan):
        if scan_result == "cancel":
            await asyncio.Future()
        return scan_result
    ns["SklandLoginAPI"] = SimpleNamespace(get_scan=AsyncMock(return_value="scan"), get_scan_status=get_status,
                                           get_token_by_scan_code=AsyncMock(return_value="x" * 24))
    receipt = SimpleNamespace(recall=AsyncMock())
    ns["send_with_receipt"] = AsyncMock(return_value=receipt)
    async def wait(event, choices, *args, **kwargs):
        if choices == ("取消",):
            if scan_result == "cancel":
                return "取消"
            await asyncio.Future()
        return "确认"
    ns["wait_choice"] = wait
    async def imported(*args, **kwargs):
        receipt.recall.assert_awaited_once()
        assert not obj._interaction_tasks
        assert await obj.store.list_accounts("bot:u")
        return "导入完成"
    ns["import_support_after_login"] = AsyncMock(side_effect=imported)
    await run(obj, Event(group="B"), ["qrcode"])
    receipt.recall.assert_awaited_once()
    assert ns["import_support_after_login"].await_count == int(scan_result == "code")
    assert bool(await obj.store.list_accounts("bot:u")) == (scan_result == "code")


@pytest.mark.asyncio
async def test_optional_plugin_capability_is_discovered_each_time():
    callback = AsyncMock(return_value="imported")
    context = SimpleNamespace(get_registered_star=lambda name: None)
    kwargs = dict(owner_id="bot:u", group_umo=TARGET, platform_id="bot", group_id="B", group_name="群B", member_nickname="用户")
    for metadata in (None, SimpleNamespace(activated=False, star_cls=object()), SimpleNamespace(activated=True, star_cls=object())):
        context.get_registered_star = lambda name: metadata
        assert await import_support_after_login(context, **kwargs) is None
    context.get_registered_star = lambda name: SimpleNamespace(activated=True, star_cls=SimpleNamespace(import_skland_after_login=callback))
    assert await import_support_after_login(context, **kwargs) == "imported"
    callback.assert_awaited_once_with(**kwargs)
    kwargs["group_id"] = ""
    assert await import_support_after_login(context, **kwargs) is None
    callback.assert_awaited_once()


@pytest.mark.asyncio
async def test_revoke_sid_also_removes_overlapping_legacy_group_rule(tmp_path):
    store = SklandStore(tmp_path / "db")
    await store.initialize(legacy_credential_groups=["B", TARGET])
    assert await store.revoke_credential_group(TARGET)
    assert not await store.credential_group_allowed("B", TARGET)
    await store.initialize(legacy_credential_groups=["B", TARGET])
    assert not await store.credential_group_allowed("B", TARGET)


@pytest.mark.asyncio
async def test_skland_group_login_works_without_support_plugin(plugin):
    obj, ns = plugin
    await prepare_login(obj, ns)
    event = Event(group="B")
    await run(obj, event, ["bind", "x" * 32])
    assert await obj.store.list_accounts("bot:u")
    messages = texts([call.args[0] for call in event.send.await_args_list])
    assert "绑定成功" in messages
    assert "失败" not in messages
