import sqlite3
from dataclasses import asdict
from unittest.mock import AsyncMock

import pytest

from skland.store import SklandStore
from plugin_harness import Event, make_plugin, run, texts
from test_account_migration import legacy_database
from test_plugin_interactions import PNG


@pytest.mark.asyncio
@pytest.mark.parametrize("app_code", ["exa", "future-game"])
@pytest.mark.parametrize("available", [True, False])
async def test_char_and_confirmation_cards_ignore_unsupported_legacy_games(tmp_path, app_code, available):
    path = tmp_path / "legacy.sqlite3"
    legacy_database(path)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO characters VALUES ('bot:u','extra','',?,'1','旧游戏角色',1)", (app_code,))
        db.execute("INSERT INTO sign_results VALUES (?,'bot:u','旧游戏角色','保留历史','today')", (app_code,))
    store = SklandStore(path)
    await store.initialize()
    async with store.connection() as db:
        await db.execute("UPDATE characters SET is_available=? WHERE app_code=?", (available, app_code))
    roles_before = [asdict(r) for r in await store.get_characters("bot:u", include_unavailable=True)]
    history_before = await store.get_sign_results(app_code, "bot:u")
    plugin, ns = make_plugin(store)
    ns["react"] = AsyncMock()
    renderer = ns["render_bound_roles_card"] = AsyncMock(return_value=PNG)

    results = await run(plugin, Event(), ["char"])
    assert "切换默认角色" in texts(results)
    renderer.assert_awaited_once()
    overview = renderer.await_args.args[0]
    shown = overview.accounts[0].roles
    assert [r.index for r in shown] == [1, 2, 3]
    assert all(r.app_code == "arknights" for r in shown)
    for item in shown:
        selected = await plugin.service.require_character("bot:u", "arknights", item.index)
        assert (selected.uid, selected.channel_master_id) == (item.binding_uid, item.server_id)

    for mode in ("bind_confirmation", "unbind_selection", "unbind_confirmation"):
        card = await plugin.service.bindings.overview("bot:u", mode=mode)
        assert card.accounts[0].roles == shown
    assert [asdict(r) for r in await store.get_characters("bot:u", include_unavailable=True)] == roles_before
    assert await store.get_sign_results(app_code, "bot:u") == history_before
    assert len(history_before) == 1
