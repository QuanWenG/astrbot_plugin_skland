from copy import deepcopy
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from skland.api import SklandAPI, SklandLoginAPI
from skland.exception import RequestException
from skland.models import Character
from skland.schemas.endfield.gacha.base import (
    EfCharGachaResponse, EfWeaponGachaResponse, EndfieldPoolType,
)
from skland.service import SklandService
from skland.store import SklandStore
from test_service_port import endfield_account


def draw(weapon, seq="960"):
    row = dict(kind="draw", poolId="weapon_test" if weapon else "special_test",
               poolName="测试卡池", rarity=6, isNew=True, gachaTs="1720000000000", seqId=seq)
    row.update(weaponId="wpn_test", weaponName="测试武器") if weapon else row.update(
        charId="chr_test", charName="测试角色", isFree=False)
    return row


def extra(weapon, seq="959"):
    return dict(kind="gift_weapon_box" if weapon else "information", nameText="奖励",
                gachaTs="1710000000000", seqId=seq)


@pytest.mark.parametrize("kind", ["gift_weapon_box", "giftweaponbox"])
def test_reported_gift_box_entry_without_weapon_fields(kind):
    # Match the reported kind and seqId; receipt alone says nothing about opening a box.
    payload = {"list": [{"kind": kind, "poolId": "weapon_test", "poolName": "测试卡池",
                         "gachaTs": "1720000147772", "seqId": "461"}], "hasMore": True}
    page = EfWeaponGachaResponse.model_validate(payload)
    assert page.gacha_list == []
    assert (page.next_seq, page.next_ts) == ("461", "1720000147772")


@pytest.mark.parametrize("weapon", [False, True])
def test_filtered_entries_preserve_raw_page_cursor_and_input(weapon):
    model = EfWeaponGachaResponse if weapon else EfCharGachaResponse
    payload = {"list": [extra(weapon, "961"), draw(weapon), extra(weapon)], "hasMore": True}
    original = deepcopy(payload)
    page = model.model_validate(payload)
    assert len(page.gacha_list) == 1
    assert page.gacha_list[0].seqId == "960"
    assert (page.next_seq, page.next_ts) == ("959", "1710000000000")
    assert payload == original
    assert "raw_next_seq" not in page.model_dump()


@pytest.mark.parametrize("weapon", [False, True])
def test_legacy_draw_without_kind_still_validates_required_fields(weapon):
    model = EfWeaponGachaResponse if weapon else EfCharGachaResponse
    row = draw(weapon)
    row.pop("kind")
    page = model.model_validate({"list": [row], "hasMore": False})
    assert len(page.gacha_list) == 1
    assert page.next_seq == "960"
    row.pop("rarity")
    with pytest.raises(ValidationError, match="rarity"):
        model.model_validate({"list": [row], "hasMore": False})


def test_weapon_unknown_entry_is_not_silently_discarded():
    with pytest.raises(ValidationError, match="weaponId"):
        EfWeaponGachaResponse.model_validate({"list": [{"kind": "unknown"}], "hasMore": False})


@pytest.mark.asyncio
@pytest.mark.parametrize("gift_kind", ["gift_weapon_box", "giftweaponbox"])
async def test_weapon_gift_only_and_mixed_pages_sync_through_real_api_parser(tmp_path, monkeypatch, gift_kind):
    store = SklandStore(tmp_path / "db")
    await store.initialize()
    _, role = await endfield_account(store)
    service = SklandService(store)
    monkeypatch.setattr(SklandLoginAPI, "get_grant_code", AsyncMock(return_value="grant"))
    monkeypatch.setattr(SklandLoginAPI, "get_role_token_by_uid", AsyncMock(return_value="token"))
    service.enrich_endfield_pools = AsyncMock()
    service.card = AsyncMock(side_effect=RequestException("no avatar"))
    cursors = []
    pages = {
        None: {"list": [dict(extra(True, "961"), kind=gift_kind)], "hasMore": True},
        "961": {"list": [draw(True), dict(extra(True), kind=gift_kind)], "hasMore": True},
        "959": {"list": [draw(True, "958")], "hasMore": False},
    }
    def handler(request):
        if request.url.path.endswith("/weapon"):
            cursor = request.url.params.get("seq_id")
            cursors.append(cursor)
            page = pages[cursor]
        else:
            page = {"list": [], "hasMore": False}
        return httpx.Response(200, json={"code": 0, "data": page})
    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client(**kw, transport=httpx.MockTransport(handler)))
    view = await service.endfield_history_view("bot:u")
    assert cursors == [None, "961", "959"]
    assert not view.is_cached and view.new_count == 2
    assert view.record.total_pulls == view.record.weapon_total_pulls == 2
    records = await store.get_gacha_records("bot:u", role.uid, "endfield", character_id=role.id)
    assert {r.pos for r in records} == {960, 958}
    assert all(r.item_type == "weapon" and r.char_id == "wpn_test" for r in records)


@pytest.mark.asyncio
@pytest.mark.parametrize("weapon", [False, True])
async def test_all_filtered_pages_continue_until_last_page(monkeypatch, weapon):
    model = EfWeaponGachaResponse if weapon else EfCharGachaResponse
    api = AsyncMock(side_effect=[
        model.model_validate({"list": [extra(weapon, "10")], "hasMore": True}),
        model.model_validate({"list": [extra(weapon, "9")], "hasMore": False}),
    ])
    monkeypatch.setattr(SklandAPI, "get_ef_gacha_history", api)
    service = SklandService(None)
    role = Character("bot:u", "binding", "role", "endfield", "1", "管理员")
    pool = EndfieldPoolType.WEAPON if weapon else EndfieldPoolType.SPECIAL
    assert await service._all_ef_records(pool, role, "token") == []
    assert api.await_count == 2
    assert api.await_args.kwargs == {"seq_id": "10"}


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [[], [{"kind": "gift_weapon_box"}], [extra(True, "960")]])
async def test_invalid_or_repeated_cursor_fails_instead_of_returning_partial_history(monkeypatch, rows):
    first = EfWeaponGachaResponse.model_validate({"list": [draw(True)], "hasMore": True})
    next_page = EfWeaponGachaResponse.model_validate({"list": rows, "hasMore": True})
    api = AsyncMock(side_effect=[first, next_page])
    monkeypatch.setattr(SklandAPI, "get_ef_gacha_history", api)
    service = SklandService(None)
    role = Character("bot:u", "binding", "role", "endfield", "1", "管理员")
    with pytest.raises(RequestException, match="翻页游标"):
        await service._all_ef_records(EndfieldPoolType.WEAPON, role, "token")
    assert api.await_count == 2
