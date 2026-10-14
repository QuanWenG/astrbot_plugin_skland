import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from skland.binding import PreparedBinding
from skland.models import Account, Character
from skland.store import DDL, SklandStore, SCHEMA_VERSION
from skland.support_integration import import_support_after_login
from plugin_harness import Event, make_plugin, run, texts
from test_account_migration import add_account
from test_plugin_interactions import PNG

TARGET = "bot:GroupMessage:B"


@pytest.mark.asyncio
@pytest.mark.parametrize("group", ["any-group", ""])
async def test_legacy_settings_do_not_restrict_binding_scan_or_unbind(plugin, group):
    obj, _ = plugin
    obj.config.update(credential_group_whitelist=[], credential_registration_groups=[])
    obj._bind_account = AsyncMock(return_value=False)
    obj._qrcode_login = AsyncMock(return_value=False)
    obj._unbind_interaction = AsyncMock()
    event = Event(group=group)
    for command in ("bind", "qrcode", "unbind"):
        assert not texts(await run(obj, event, [command, "secret"]))
    obj._bind_account.assert_awaited_once()
    obj._qrcode_login.assert_awaited_once()
    obj._unbind_interaction.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [3, 4])
async def test_upgrade_preserves_identities_and_history_with_backup(tmp_path, version):
    path = tmp_path / "db"
    with sqlite3.connect(path) as db:
        for statement in DDL:
            db.execute(statement)
        db.execute(f"PRAGMA user_version={version}")
        if version == 4:
            db.execute("CREATE TABLE credential_groups(target_sid TEXT PRIMARY KEY)")
            db.execute("INSERT INTO credential_groups VALUES ('legacy-group')")
    store = SklandStore(path)
    account, role = await add_account(store)
    await store.save_sign_result("arknights", "bot:u", "博士", "ok", "today", character_id=role.id)
    await store.initialize()
    assert (await store.get_account("bot:u")).id == account.id
    assert (await store.get_characters("bot:u"))[0].id == role.id
    assert (await store.get_sign_results("arknights", "bot:u"))[0]["result"] == "ok"
    with sqlite3.connect(store.migration_summary["backup"]) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == version
        assert backup.execute("SELECT id FROM accounts").fetchone()[0] == account.id
    with sqlite3.connect(path) as db:
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='credential_groups'").fetchall()
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    await store.subscribe_sanity("bot:u", role.id, "bot:FriendMessage:u")
    await store.initialize()
    assert len(await store.list_sanity_subscriptions()) == 1
    assert len(list(tmp_path.glob("*.bak"))) == 1


@pytest.mark.asyncio
async def test_v4_upgrade_failure_restores_registration_and_version(tmp_path, monkeypatch):
    path = tmp_path / "db"
    with sqlite3.connect(path) as db:
        for statement in DDL:
            db.execute(statement)
        db.execute("CREATE TABLE credential_groups(target_sid TEXT PRIMARY KEY)")
        db.execute("INSERT INTO credential_groups VALUES ('saved-group')")
        db.execute("PRAGMA user_version=4")
    store = SklandStore(path)
    upgrade = store._upgrade_subscriptions
    async def failed(db):
        await upgrade(db)
        raise RuntimeError("injected failure")
    monkeypatch.setattr(store, "_upgrade_subscriptions", failed)
    with pytest.raises(RuntimeError, match="injected"):
        await store.initialize()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert db.execute("SELECT target_sid FROM credential_groups").fetchone()[0] == "saved-group"
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='sanity_subscriptions'").fetchall()
    assert len(list(tmp_path.glob("*.bak"))) == 1
    await SklandStore(path).initialize()


@pytest_asyncio.fixture
async def plugin(tmp_path):
    store = SklandStore(tmp_path / "db")
    await store.initialize()
    obj, ns = make_plugin(store)
    ns["react"] = AsyncMock()
    return obj, ns



async def prepare_login(obj, ns):
    prepared = PreparedBinding(
        Account("bot:u", None, "cred", "token", "remote"),
        [Character("bot:u", "100", None, "arknights", "1", "博士")], 0, object(),
    )
    obj.service.bindings.prepare = AsyncMock(return_value=prepared)
    ns["render_bound_roles_card"] = AsyncMock(return_value=PNG)
    ns["wait_choice"] = AsyncMock(side_effect=AssertionError("unexpected confirmation"))



@pytest.mark.asyncio
@pytest.mark.parametrize("saved", [True, False])
async def test_login_hook_only_after_commit_and_outside_management_lock(plugin, saved):
    obj, ns = plugin
    await prepare_login(obj, ns)
    if not saved:
        obj.service.bindings.commit = AsyncMock(side_effect=ValueError("提交失败"))
    async def imported(context, **kwargs):
        assert await obj.store.list_accounts("bot:u")
        async with obj.service.bindings.exclusive("bot:u"):
            pass
        assert kwargs == dict(owner_id="bot:u", group_umo=TARGET, platform_id="bot", group_id="B", group_name="", member_nickname="用户")
        return "已导入 UID：100"
    ns["import_support_after_login"] = AsyncMock(side_effect=imported)
    event = Event(group="B")
    await run(obj, event, ["bind", "x" * 32])
    assert ns["import_support_after_login"].await_count == int(saved)
    assert bool(await obj.store.list_accounts("bot:u")) == saved
    ns["wait_choice"].assert_not_awaited()



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
        assert choices == ("取消",)
        if scan_result == "cancel":
            return "取消"
        await asyncio.Future()
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
async def test_skland_group_login_works_without_support_plugin(plugin):
    obj, ns = plugin
    await prepare_login(obj, ns)
    event = Event(group="B")
    await run(obj, event, ["bind", "x" * 32])
    assert await obj.store.list_accounts("bot:u")
    messages = texts([call.args[0] for call in event.send.await_args_list])
    assert "绑定成功" in messages
    assert "失败" not in messages

