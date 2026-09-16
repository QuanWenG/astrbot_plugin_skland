from types import SimpleNamespace
from unittest.mock import AsyncMock
from dataclasses import replace

import httpx
import pytest

import skland.service as module
from skland.models import Character, Account
from skland.schemas import CRED, BindingApp
from skland.schemas.endfield.gacha.base import EfCharGachaInfo, EfWeaponGachaInfo, EndfieldPoolType
from skland.service import SklandService
from skland.store import SklandStore
from skland.exception import RequestException, LoginException
from skland.card_cache import ArkCardCache
from test_account_migration import add_account
from test_war_echoes import _war_echoes_data


async def endfield_account(store):
    account = Account("bot:u", "access", "cred", "token", "remote")
    char = Character("bot:u", "binding", "role", "endfield", "1", "管理员", is_skland_default=True, server_name="China")
    await store.commit_binding(account, [char], await store.owner_version("bot:u"))
    return account, char


@pytest.mark.asyncio
async def test_endfield_auto_sync_weapon_free_cache_fallback_and_negative_slice(tmp_path, monkeypatch):
    store = SklandStore(tmp_path / "db")
    await store.initialize()
    account, role = await endfield_account(store)
    service = SklandService(store)
    monkeypatch.setattr(module.SklandLoginAPI, "get_grant_code", AsyncMock(return_value="grant"))
    monkeypatch.setattr(module.SklandLoginAPI, "get_role_token_by_uid", AsyncMock(return_value="role-token"))
    char = EfCharGachaInfo(poolId="special_new", poolName="寻访", charId="char", charName="角色", rarity=6,
        isNew=True, isFree=True, gachaTs="100000", seqId="1")
    # Optional API annotations are intentionally absent.
    weapon = EfWeaponGachaInfo(poolId="weapon_new", poolName="武器", weaponId="weapon", weaponName="武器", rarity=6,
        isNew=True, gachaTs="100000", seqId="1")
    async def records(pool, char, token):
        assert char.id == role.id and token == "role-token"
        return [weapon] if pool == EndfieldPoolType.WEAPON else [globals_char] if pool == EndfieldPoolType.SPECIAL else []
    globals_char = char
    service._all_ef_records = AsyncMock(side_effect=records)
    service.enrich_endfield_pools = AsyncMock()
    service.card = AsyncMock(side_effect=RequestException("avatar unavailable"))
    view = await service.endfield_history_view("bot:u", begin=-1)
    assert view.new_count == 2 and not view.is_cached
    assert view.record.total_pulls == 2 and view.record.weapon_total_pulls == 1
    assert view.record.char_total_pulls == 1 and not view.avatar_url
    assert view.pools[0].events[0].is_free
    service._all_ef_records = AsyncMock(side_effect=RequestException("offline"))
    cached = await service.endfield_history_view("bot:u")
    assert cached.is_cached and "同步失败" in cached.notice
    assert cached.record.total_pulls == 2
    assert len(await store.get_gacha_records("bot:u", "binding", "endfield", character_id=role.id)) == 2


@pytest.mark.asyncio
async def test_endfield_partial_category_fetch_never_writes(tmp_path, monkeypatch):
    store = SklandStore(tmp_path / "db")
    await store.initialize()
    _, role = await endfield_account(store)
    service = SklandService(store)
    monkeypatch.setattr(module.SklandLoginAPI, "get_grant_code", AsyncMock(return_value="grant"))
    monkeypatch.setattr(module.SklandLoginAPI, "get_role_token_by_uid", AsyncMock(return_value="token"))
    service._all_ef_records = AsyncMock(side_effect=RequestException("category failed"))
    with pytest.raises(RequestException, match="暂无缓存"):
        await service.endfield_history_view("bot:u")
    assert await store.get_gacha_records("bot:u", "binding", "endfield", character_id=role.id) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("season,requests", [(None, [None]), (3, [3]), (-1, [None, "2"])])
async def test_war_service_selects_remote_history_and_optional_profile(tmp_path, monkeypatch, season, requests):
    store = SklandStore(tmp_path / "db")
    await store.initialize()
    account, role = await endfield_account(store)
    service = SklandService(store)
    api = AsyncMock(return_value=_war_echoes_data())
    monkeypatch.setattr(module.SklandAPI, "endfield_war_echoes", api)
    service.card = AsyncMock(side_effect=ValueError("avatar failure"))
    view = await service.war_echoes("bot:u", season_id=season, week_id=1)
    assert view.season.id == ("2" if season == -1 else "3")
    assert [c.kwargs["season_id"] for c in api.await_args_list] == requests
    assert all(c.kwargs["role_id"] == "role" and c.kwargs["user_id"] == "remote" for c in api.await_args_list)


@pytest.mark.asyncio
async def test_account_preview_remote_identity_upsert_and_role_order(tmp_path, monkeypatch):
    store = SklandStore(tmp_path / "db")
    await store.initialize()
    service = SklandService(store)
    monkeypatch.setattr(module.SklandLoginAPI, "refresh_token", AsyncMock(return_value="token"))
    monkeypatch.setattr(module.SklandAPI, "get_user_ID", AsyncMock(return_value="remote"))
    apps = [BindingApp.model_validate({"appCode": "arknights", "appName": "明日方舟", "bindingList": [
        {"uid": "100", "nickName": "博士", "channelMasterId": "1", "channelName": "官服", "isDefault": True, "isDelete": False,
         "isOfficial": True, "gameName": "明日方舟", "gameId": "1", "roles": [], "defaultRole": None}
    ]})]
    monkeypatch.setattr(module.SklandAPI, "get_binding", AsyncMock(return_value=apps))
    first = await service.bindings.prepare("bot:u", "a" * 32)
    assert await store.list_accounts("bot:u") == []
    await service.bindings.commit(first)
    old_role = (await store.get_characters("bot:u"))[0]
    second = await service.bindings.prepare("bot:u", "b" * 32)
    assert second.account.id == first.account.id
    assert second.card.accounts[0].state == "pending_update"
    await service.bindings.commit(second)
    assert len(await store.list_accounts("bot:u")) == 1
    assert (await store.get_account("bot:u")).cred == "b" * 32
    assert (await store.get_characters("bot:u"))[0].id == old_role.id


@pytest.mark.asyncio
async def test_account_and_server_cache_keys_and_export_deduplication(tmp_path, monkeypatch):
    store = SklandStore(tmp_path / "db")
    await store.initialize()
    a, first = await add_account(store)
    b, duplicate = await add_account(store, "remote-2")
    _, other_server = await add_account(store, "remote-3", server="2")
    await store.set_default("bot:u", "arknights", duplicate.id)
    service = SklandService(store)
    monkeypatch.setattr(module, "ark_card_cache", ArkCardCache())
    api = AsyncMock(return_value=SimpleNamespace(chars=[], status="card", equipmentInfoMap={}))
    monkeypatch.setattr(module.SklandAPI, "ark_card", api)
    await service.card("bot:u", "arknights", 1)
    await service.card("bot:u", "arknights", 2)
    await service.card("bot:u", "arknights", 3)
    assert api.await_count == 3
    service._ensure_operator_catalog = AsyncMock()
    monkeypatch.setattr(module.OperatorRosterQuery, "from_input", lambda _: object())
    monkeypatch.setattr(module.OperatorRoster, "build", lambda **_: object())
    snapshots = await service.operator_rosters("bot:u")
    assert [r.id for r, _ in snapshots] == [duplicate.id, other_server.id]
    await module.ark_card_cache.invalidate_owner("bot:u")
    api.side_effect = [SimpleNamespace(chars=[], status="card", equipmentInfoMap={}), RequestException("second role failed")]
    with pytest.raises(RequestException, match="second role"):
        await service.operator_rosters("bot:u")


@pytest.mark.asyncio
async def test_refresh_rejects_different_remote_account(tmp_path, monkeypatch):
    store = SklandStore(tmp_path / "db")
    await store.initialize()
    account, _ = await add_account(store)
    service = SklandService(store)
    monkeypatch.setattr(module.SklandLoginAPI, "get_grant_code", AsyncMock(return_value="grant"))
    monkeypatch.setattr(module.SklandLoginAPI, "get_cred", AsyncMock(return_value=CRED("newcred", "newtoken", "someone-else")))
    with pytest.raises(RequestException, match="身份不一致"):
        await service._with_refresh(account, AsyncMock(side_effect=LoginException("expired")))
    assert (await store.get_account("bot:u")).cred == "cred"
