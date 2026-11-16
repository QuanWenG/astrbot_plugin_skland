import json
from base64 import b64encode
from io import BytesIO
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from PIL import Image

import skland.service as service_module
from skland.api import SklandAPI
from skland.api import server_clock
from skland.cards import render_garrison_history
from skland.exception import LoginException, RequestException, UnauthorizedException
from skland.garrison import build_garrison_overview, build_garrison_pages, relative_time
from skland.models import Account, Character
from skland.renderer import renderer
from skland.schemas import CRED, GarrisonHistory
from skland.schemas.arknights.garrison import GarrisonBond, GarrisonBoss, GarrisonEquipment
from skland.service import SklandService
from skland.store import SklandStore
from plugin_harness import Event, make_plugin, run, texts


@pytest.fixture(autouse=True)
def fresh_battle_clock(monkeypatch):
    monkeypatch.setattr(server_clock, "BATTLE_CLOCK", server_clock.ServerClock())


def history(count=1):
    return GarrisonHistory.model_validate({"records": [
        {"id": str(i), "completeState": 1 if i % 2 else 0, "passRound": 15 if i % 2 else 8,
         "gameEndTs": str(1000 + i), "modeType": "MULTI", "modeDifficulty": "ABYSS",
         "bandId": "band_ducklord", "chessSquad": [
             {"charId": "char_4054_malist", "chessInfo": {"charName": "至简", "chessLevel": (i % 6) + 1}}
         ]}
        for i in range(count)
    ]})


@pytest.mark.asyncio
@pytest.mark.parametrize("code,error", [(0, None), (10000, UnauthorizedException),
                                        (10002, LoginException), (10001, RequestException),
                                        (123, RequestException)])
async def test_garrison_api_uses_signed_season_request(monkeypatch, code, error):
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={"code": code, "message": "error", "data": {"history": {"records": []}}})
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(**kw, transport=httpx.MockTransport(handle)))
    signing = AsyncMock(return_value={"test-sign": "signed"})
    monkeypatch.setattr(SklandAPI, "get_sign_header", signing)
    if error:
        with pytest.raises(error):
            await SklandAPI.garrison_history(CRED("cred", "token"), "12345678")
    else:
        assert (await SklandAPI.garrison_history(CRED("cred", "token"), "12345678")).records == []
    assert len(seen) == 1
    assert seen[0].url.path == "/api/ms/game/arknights/garrison/info"
    assert dict(seen[0].url.params) == {"activityId": "act3autochess", "uid": "12345678"}
    assert seen[0].headers["test-sign"] == "signed"
    assert seen[0].headers["x-transformed"] == "base64"
    assert signing.await_args.kwargs == {"method": "get", "battle_profile": True}
    assert signing.await_args.args[1] == str(seen[0].url)


@pytest.mark.asyncio
@pytest.mark.parametrize("message_key", ["message", "msg"])
async def test_garrison_business_error_preserves_reason_without_credentials(monkeypatch, message_key):
    cred = CRED("private-cred-value", "private-signing-token")
    payload = {"code": 10001, message_key: f"请求参数错误\n{cred.cred} {cred.token}"}
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(**kw, transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json=payload))))
    monkeypatch.setattr(SklandAPI, "get_sign_header", AsyncMock(return_value={}))
    with pytest.raises(RequestException) as error:
        await SklandAPI.garrison_history(cred, "12345678")
    message = str(error.value)
    assert "10001" in message and "请求参数错误" in message
    assert cred.cred not in message and cred.token not in message
    assert "\n" not in message


@pytest.mark.asyncio
@pytest.mark.parametrize("status,payload,error,match", [
    (401, "Unauthorized", UnauthorizedException, "认证已失效"),
    (401, {"code": 10002}, LoginException, "登录已失效"),
    (404, {"message": "Not Found"}, RequestException, "HTTP 404"),
    (503, "Service Unavailable", RequestException, "HTTP 503"),
])
async def test_garrison_http_errors_remain_actionable(monkeypatch, status, payload, error, match):
    def handle(_):
        return (httpx.Response(status, json=payload) if isinstance(payload, dict)
                else httpx.Response(status, text=payload))
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(**kw, transport=httpx.MockTransport(handle)))
    monkeypatch.setattr(SklandAPI, "get_sign_header", AsyncMock(return_value={}))
    with pytest.raises(error, match=match):
        await SklandAPI.garrison_history(CRED("cred", "token"), "12345678")


@pytest.mark.asyncio
async def test_garrison_api_accepts_null_records_as_empty(monkeypatch):
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(**kw, transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"code": 0, "data": {"history": {"records": None}}}))))
    monkeypatch.setattr(SklandAPI, "get_sign_header", AsyncMock(return_value={}))
    assert (await SklandAPI.garrison_history(CRED("cred", "token"), "12345678")).records == []


@pytest.mark.asyncio
@pytest.mark.parametrize("encoded", [False, True])
async def test_official_battle_api_keeps_promoted_equipment_and_unique_favourites(monkeypatch, encoded):
    # Shape and decoding follow the public auto-chess page, rather than the
    # guide editor's flat {records: ...} response.
    record = {"id": "battle-1", "completeState": 1, "passRound": 15, "gameEndTs": 1200,
              "chessSquad": [{"charId": "char_472_pasngr", "chessId": "chess_char_4_24_b",
                              "mergeLevel": 1, "chessInfo": {"charName": "异客", "chessLevel": 4,
                              "charLevel": 60, "evolvePhase": 2}, "equips": [
                                  {"equipId": "chess_item_1_01_e_b"},
                                  {"equipId": "chess_item_2_04_e_a"}]}]}
    older = {"id": "favourite-2", "completeState": 0, "gameEndTs": 1100}
    data = {"career": {}, "history": {"records": [record], "favourRecords": [record, older]}}
    if encoded:
        data = {"content": b64encode(json.dumps(data, ensure_ascii=False).encode("utf-8")).decode("ascii")}
    real = httpx.AsyncClient
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"code": 0, "data": data})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(**kw, transport=httpx.MockTransport(handle)))
    monkeypatch.setattr(SklandAPI, "get_sign_header", AsyncMock(return_value={"cred": "cred"}))
    result = await SklandAPI.garrison_history(CRED("cred", "token"), "12345678")
    assert [item.id for item in result.records] == ["battle-1", "favourite-2"]
    assert build_garrison_pages(result, "博士")[0].records[0]["squad"][0]["equips"] == [
        {"id": "chess_item_1_01_e_b"}, {"id": "chess_item_2_04_e_a"},
    ]
    assert len(requests) == 1
    assert requests[0].url.path == "/api/ms/game/arknights/garrison/info"


@pytest.mark.asyncio
@pytest.mark.parametrize("data,match", [
    ({"content": "not-valid-base64"}, "解码失败"),
    ({"content": b64encode(b"not json").decode()}, "解码失败"),
    ({"records": []}, "缺少 history"),
    ({"history": []}, "history 格式无效"),
    ({"history": {"records": {}}}, "记录列表格式无效"),
    ({"history": {"records": [None]}}, "单局战绩格式无效"),
])
async def test_official_battle_api_rejects_bad_decoding_and_history_shapes(monkeypatch, data, match):
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(**kw, transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"code": 0, "data": data}))))
    monkeypatch.setattr(SklandAPI, "get_sign_header", AsyncMock(return_value={}))
    with pytest.raises(RequestException, match=match):
        await SklandAPI.garrison_history(CRED("cred", "token"), "12345678")


@pytest.mark.asyncio
async def test_garrison_rejects_missing_role_uid_before_request(monkeypatch):
    client = Mock(side_effect=AssertionError("request without a role"))
    monkeypatch.setattr(httpx, "AsyncClient", client)
    with pytest.raises(RequestException, match="角色 UID"):
        await SklandAPI.garrison_history(CRED("cred", "token"), "")
    client.assert_not_called()


@pytest.mark.asyncio
async def test_garrison_service_selects_account_and_refreshes_credentials(tmp_path, monkeypatch):
    store = SklandStore(tmp_path / "db")
    await store.initialize()
    account = Account("bot:u", "access", "cred", "old-token", "remote")
    role = Character("bot:u", "binding", "role", "arknights", "1", "博士",
                     is_skland_default=True, server_name="China")
    await store.commit_binding(account, [role], await store.owner_version("bot:u"))
    service = SklandService(store)
    calls = []
    async def api(cred, uid):
        calls.append((cred.token, uid))
        if cred.token == "old-token":
            raise UnauthorizedException("expired")
        return history()
    monkeypatch.setattr(service_module.SklandAPI, "garrison_history", api)
    monkeypatch.setattr(service_module.SklandLoginAPI, "refresh_token", AsyncMock(return_value="new-token"))
    selected, result = await service.garrison_history("bot:u", 1)
    assert selected.uid == role.uid and len(result.records) == 1
    assert calls == [("old-token", role.uid), ("new-token", role.uid)]


@pytest.mark.asyncio
@pytest.mark.parametrize("identity,expected_uid,expected_cred", [
    (None, "221", "cred-second"),
    (1, "111", "cred-first"),
    (2, "112", "cred-first"),
    (3, "221", "cred-second"),
])
async def test_garrison_request_uses_selected_role_and_its_account(
    tmp_path, monkeypatch, identity, expected_uid, expected_cred,
):
    store = SklandStore(tmp_path / "db")
    await store.initialize()
    first = Account("bot:u", "access-first", "cred-first", "token-first", "remote-first")
    first_roles = [
        Character("bot:u", "111", None, "arknights", "1", "一号", is_skland_default=True),
        Character("bot:u", "112", None, "arknights", "2", "二号"),
    ]
    second = Account("bot:u", "access-second", "cred-second", "token-second", "remote-second")
    second_role = Character("bot:u", "221", None, "arknights", "1", "三号")
    await store.commit_binding(first, first_roles, await store.owner_version("bot:u"))
    await store.commit_binding(second, [second_role], await store.owner_version("bot:u"))
    await store.set_default("bot:u", "arknights", second_role.id)
    seen = []

    def handle(request):
        seen.append(request)
        if request.url.params.get("uid") != expected_uid:
            return httpx.Response(200, json={"code": 10001, "message": "未查询到游戏角色，请前往绑定"})
        if request.headers.get("cred") != expected_cred:
            return httpx.Response(200, json={"code": 10002, "message": "用户未登录"})
        return httpx.Response(200, json={"code": 0, "data": {"history": {"records": []}}})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(**kw, transport=httpx.MockTransport(handle)))
    selected, result = await SklandService(store).garrison_history("bot:u", identity)
    assert selected.uid == expected_uid and result.records == []
    assert len(seen) == 1
    assert dict(seen[0].url.params) == {"activityId": "act3autochess", "uid": expected_uid}
    assert seen[0].headers["cred"] == expected_cred


def test_garrison_projection_sorts_before_selecting_latest_20():
    pages = build_garrison_pages(history(36), "博士", now=2000, max_height=4096)
    assert [len(page.records) for page in pages] == [20]
    assert pages[0].number == pages[0].total == 1
    assert pages[0].record_total == 20
    assert [item["id"] for page in pages for item in page.records] == [str(i) for i in range(35, 15, -1)]
    assert [item["index"] for page in pages for item in page.records] == list(range(1, 21))
    assert pages[0].records[0]["status"] == "模拟成功"
    assert pages[0].records[0]["squad"][0]["badge"] == "VI"
    assert pages[0].records[0]["empty_slots"] == 9
    assert len(build_garrison_pages(history(18), "博士")[0].records) == 18
    assert relative_time(1957, 2000) == "刚刚"
    assert relative_time(1800, 2000) == "3分钟前"
    assert relative_time(0, 2000) == "时间未知"
    assert build_garrison_pages(GarrisonHistory(), "博士")[0].records == []
    unusual = GarrisonHistory.model_validate({"records": [{"completeState": 7, "modeType": "SINGLE",
        "modeDifficulty": "NEW", "gameEndTs": "1999"}]})
    item = build_garrison_pages(unusual, "博士", now=2000)[0].records[0]
    assert item["status"] == "模拟状态 7" and item["team_count"] == 1
    assert item["difficulty"] == "NEW"


def test_garrison_grid_accounts_for_extra_squad_rows_and_height_limit():
    data = history(36)
    for record in data.records:
        record.chessSquad *= 15
    pages = build_garrison_pages(data, "博士", max_height=1400)
    assert [len(page.records) for page in pages] == [4, 4, 4, 4, 4]
    assert sum(len(page.records) for page in pages) == 20
    assert all(len(record["squad"]) == 15 for page in pages for record in page.records)


def test_garrison_operator_levels_and_first_two_equipment_slots():
    data = GarrisonHistory.model_validate({"records": [{"completeState": 1, "chessSquad": [
        {"charId": "char_472_pasngr", "mergeLevel": 1,
         "chessInfo": {"chessLevel": 4, "charLevel": "60", "evolvePhase": "2"},
         "equips": [{"equipId": key} for key in ("chess_item_2_04_e_a", "chess_item_2_04_e_a", "chess_item_3_07_e_a", "chess_item_1_01_e_a")]},
        {"charId": "char_4146_nymph", "mergeLevel": 0,
         "chessInfo": {"chessLevel": 6, "charLevel": 1, "evolvePhase": 2}, "equips": []},
        {"charId": "char_4054_malist", "chessInfo": {"chessLevel": 3}},
    ]}]})
    record = build_garrison_pages(data, "博士")[0].records[0]
    first, base, legacy = record["squad"]
    assert first["badge"] == "IV" and first["char_level"] == 60 and first["evolve_phase"] == 2 and first["merge_level"] == 1
    assert first["equips"] == [{"id": "chess_item_2_04_e_a"}, {"id": "chess_item_2_04_e_a"}]
    assert len(data.records[0].chessSquad[0].equips) == 4
    assert base["char_level"] == 1 and base["merge_level"] == 0 and base["equips"] == []
    assert legacy["char_level"] is None and legacy["evolve_phase"] is None and legacy["equips"] is None
    assert record["unit_height"] == 76


def test_garrison_overview_counts_all_matches_before_recent_limit():
    data = history(36)
    for i, record in enumerate(data.records):
        record.gameStartTs = record.gameEndTs - 60
        record.trophyAfter = i
        record.bondInfo = [GarrisonBond(bondId="preciShip", layer=i, activate=1),
                           GarrisonBond(bondId="yanShip", layer=9999, activate=0)]
    # These oldest-match values do not appear in the recent 20 cards.
    data.records[0].trophyAfter = 900
    data.records[0].bandId = "band_older"
    data.records[0].bondInfo[0].layer = 999
    data.records[0].acBossInfo = [
        GarrisonBoss(bossId="boss_1", isKilled=True),
        GarrisonBoss(bossId="boss_2", isKilled=True, isHidden=True),
        GarrisonBoss(bossId="boss_3", isKilled=False, isHidden=True),
    ]
    result = build_garrison_overview(data)
    assert result["total"] == 36 and result["completed"] == 18
    assert result["trophies"] == 35  # Current cups, not the maximum or a sum.
    assert result["boss_kills"] == 2 and result["hidden_boss_kills"] == 1
    assert result["completion_rate"] == "50%"
    assert result["duration_seconds"] == 2160 and result["duration"] == "36分钟"
    assert result["strategies"] == [
        {"id": "band_ducklord", "count": 35, "wins": 18, "win_rate": 51},
        {"id": "band_older", "count": 1, "wins": 0, "win_rate": 0},
    ]
    assert result["bonds"] == [{"id": "preciShip", "count": 36, "max_layer": 999}]
    pages = build_garrison_pages(data, "博士", max_height=1400)
    assert sum(len(page.records) for page in pages) == 20
    assert all(page.overview == result for page in pages)


def test_garrison_preferences_rank_by_matches_and_count_each_bond_once_per_match():
    data = history(6)
    for i, record in enumerate(data.records):
        record.bandId = f"band_{i}"
        record.bondInfo = [
            GarrisonBond(bondId="preciShip", layer=i, activate=1),
            GarrisonBond(bondId="preciShip", layer=i + 20, activate=1),
            GarrisonBond(bondId=f"unique{i}", layer=i + 1, activate=1),
        ]
    result = build_garrison_overview(data)
    assert [item["id"] for item in result["strategies"]] == [f"band_{i}" for i in range(5, 0, -1)]
    assert result["bonds"][0] == {"id": "preciShip", "count": 6, "max_layer": 25}
    assert [item["id"] for item in result["bonds"][1:]] == [f"unique{i}" for i in range(5, 1, -1)]


def test_garrison_overview_handles_empty_missing_and_invalid_fields():
    result = build_garrison_overview(GarrisonHistory())
    assert result["trophies"] == result["total"] == result["boss_kills"] == 0
    assert result["completion_rate"] == "—" and result["duration"] == "0分钟"
    assert result["strategies"] == result["bonds"] == []
    data = history(1)
    data.records[0].gameStartTs = data.records[0].gameEndTs + 1
    result = build_garrison_overview(data)
    assert result["trophies"] is None
    assert result["duration_seconds"] is None and result["duration"] == "—"
    data.records[0].gameStartTs = 1
    data.records[0].gameEndTs = 3 * 3600 + 25 * 60 + 59 + 1
    assert build_garrison_overview(data)["duration"] == "3小时25分"


def test_common_bonds_exclude_zero_maximum_but_keep_zero_layer_match_icons():
    data = history(1)
    data.records[0].bondInfo = [GarrisonBond(bondId="suntShip", layer=0, activate=1),
                               GarrisonBond(bondId="preciShip", layer=50, activate=1)]
    assert build_garrison_overview(data)["bonds"] == [{"id": "preciShip", "count": 1, "max_layer": 50}]
    assert [bond["id"] for bond in build_garrison_pages(data, "博士")[0].records[0]["bonds"]] == ["preciShip", "suntShip"]


def test_garrison_completion_and_win_rates_round_half_up():
    data = history(8)
    for record in data.records:
        record.completeState = 0
    data.records[0].completeState = 1
    result = build_garrison_overview(data)
    assert result["completion_rate"] == "13%"
    assert result["strategies"][0]["win_rate"] == 13


@pytest.mark.asyncio
async def test_garrison_overview_resolves_icons_used_only_before_recent_limit(monkeypatch):
    from skland import cards
    data = history(36)
    data.records[0].bandId = "band_older"
    data.records[0].bondInfo = [GarrisonBond(bondId="olderBond", layer=123, activate=1)]
    bands = AsyncMock(return_value={})
    bonds = AsyncMock(return_value={})
    render = AsyncMock(return_value=b"image")
    monkeypatch.setattr(cards, "resolve_band_resources", bands)
    monkeypatch.setattr(cards, "resolve_bond_resources", bonds)
    monkeypatch.setattr(cards, "template_to_pic", render)
    assert await render_garrison_history(data, "博士") == [b"image"]
    assert bands.await_args.args == ({"band_ducklord", "band_older"},)
    assert bonds.await_args.args == ({"olderBond"},)
    assert render.await_args.kwargs["templates"]["page"].overview["total"] == 36


def test_garrison_bonds_sort_by_layer_keep_ties_stable_and_keep_zero_layers():
    data = GarrisonHistory.model_validate({"records": [{"completeState": 1, "bondInfo": [
        {"bondId": "swiftShip", "activate": 1, "layer": 79},
        {"bondId": "suntShip", "activate": "1", "layer": "0"},
        {"bondId": "yanShip", "activate": 0, "layer": 100},
        {"bondId": "preciShip", "layer": 20},
        {"bondId": "", "activate": 1, "layer": 3},
        {"bondId": "visiShip", "activate": 1, "layer": 100},
        {"bondId": "arcaneShip", "activate": 1, "layer": 79},
    ]}]})
    assert build_garrison_pages(data, "博士")[0].records[0]["bonds"] == [
        {"id": "visiShip", "layer": 100}, {"id": "swiftShip", "layer": 79},
        {"id": "arcaneShip", "layer": 79}, {"id": "suntShip", "layer": 0},
    ]
    assert build_garrison_pages(history(), "博士")[0].records[0]["bonds"] == []


def test_garrison_height_limit_accounts_for_wrapping_bonds():
    data = history(20)
    for record in data.records:
        record.bondInfo = [GarrisonBond(bondId=f"bond{i}", layer=i, activate=1) for i in range(26)]
    pages = build_garrison_pages(data, "博士", max_height=1400)
    assert [len(page.records) for page in pages] == [4, 4, 4, 4, 4]
    assert all(len(record["bonds"]) == 26 for page in pages for record in page.records)


@pytest.mark.parametrize("mode,difficulty,label", [
    ("MULTI", "FUNNY", "标准模拟"),
    ("MULTI", "NORMAL", "险境模拟"),
    ("MULTI", "HARD", "绝境模拟"),
    ("MULTI", "ABYSS", "终极模拟"),
    ("SINGLE", "FUNNY", "规程等级 1–4"),
    ("SINGLE", "NORMAL", "规程等级 5–8"),
    ("SINGLE", "HARD", "规程等级 9–14"),
    ("SINGLE", "ABYSS", "规程等级 15–20"),
    ("FUTURE", "ABYSS", "ABYSS"),
])
def test_garrison_difficulty_label_depends_on_mode(mode, difficulty, label):
    data = history()
    data.records[0].modeType = mode
    data.records[0].modeDifficulty = difficulty
    projected = build_garrison_pages(data, "博士")[0].records[0]
    assert projected["difficulty"] == label
    assert projected["difficulty_key"] == difficulty.lower()


@pytest.mark.asyncio
async def test_garrison_card_renders_success_failure_and_empty(tmp_path, monkeypatch):
    from skland import cards
    placeholder = (cards.RES_DIR / "images/garrison/operator_empty.svg").as_uri()
    monkeypatch.setattr(cards, "garrison_avatar_url", lambda _: placeholder)
    monkeypatch.setattr(cards, "garrison_portrait_url", lambda _: placeholder)
    renderer.configure(tmp_path)
    try:
        mixed = history(2)
        mixed.records[0].modeType = "SINGLE"
        image = (await render_garrison_history(mixed, "博士"))[0]
        (tmp_path / "garrison.png").write_bytes(image)
        with Image.open(BytesIO(image)) as decoded:
            assert decoded.width == 2200 and decoded.height > 390
        empty = (await render_garrison_history(GarrisonHistory(), "博士"))[0]
        (tmp_path / "garrison-empty.png").write_bytes(empty)
        with Image.open(BytesIO(empty)) as decoded:
            assert decoded.width == 2200 and decoded.height >= 190
        full = history(36)
        for record in full.records:
            record.chessSquad *= 8
            record.bondInfo = [GarrisonBond(bondId=key, layer=0 if i == 0 else i * 50, activate=1)
                               for i, key in enumerate(("swiftShip", "suntShip", "preciShip", "sargonShip",
                                                       "arcaneShip", "yanShip", "docShip", "visiShip"))]
        pages = await render_garrison_history(full, "博士", max_height=4096)
        assert len(pages) == 1
        image = pages[0]
        (tmp_path / "garrison-grid-20.png").write_bytes(image)
        with Image.open(BytesIO(image)) as decoded:
            assert decoded.width == 2200 and 2500 <= decoded.height < 2850
        tall = history(18)
        for record in tall.records:
            record.chessSquad *= 15
        for image in await render_garrison_history(tall, "博士", max_height=1400):
            with Image.open(BytesIO(image)) as decoded:
                assert decoded.width == 2200 and decoded.height <= 1400
    finally:
        await renderer.close()


@pytest.mark.asyncio
async def test_garrison_bond_images_and_layers_fit_below_squad(tmp_path, monkeypatch):
    from skland import cards

    placeholder = (cards.RES_DIR / "images/garrison/operator_empty.svg").as_uri()
    monkeypatch.setattr(cards, "garrison_avatar_url", lambda _: placeholder)
    monkeypatch.setattr(cards, "garrison_portrait_url", lambda _: placeholder)
    data = history(1)
    keys = ["swiftShip", "suntShip", "preciShip", "sargonShip", "arcaneShip",
            "yanShip", "docShip", "visiShip", "skillfulShip"]
    data.records[0].bondInfo = [GarrisonBond(bondId=key, layer=i * 123, activate=1) for i, key in enumerate(keys)]
    data.records[0].bondInfo.extend([
        GarrisonBond(bondId="../missing", layer=9999, activate=1),
        GarrisonBond(bondId="egirShip", layer=10, activate=0),
    ])
    renderer.configure(tmp_path)
    try:
        await renderer.start()
        original_context = renderer._browser.new_context

        async def new_context(**kwargs):
            context = await original_context(**kwargs)
            original_page = context.new_page

            async def new_page():
                page = await original_page()
                original_screenshot = page.screenshot

                async def screenshot(**options):
                    assert await page.locator(".bond").count() == 10
                    assert await page.locator(".records .bond-icon").evaluate_all(
                        "images => images.length === 9 && images.every(image => image.complete && image.naturalWidth > 0)")
                    assert await page.locator(".bond-layer").all_text_contents() == ["9999"] + [
                        str(i * 123) for i in range(8, -1, -1)
                    ]
                    assert await page.locator(".records .bond-ring").evaluate_all(
                        "rings => rings.map(ring => Number(ring.dataset.segments))") == [1, 2, 2, 2, 3, 2, 3, 2, 2, 2]
                    assert await page.locator(".records .bond-ring").evaluate_all("""rings => rings.every(ring => {
                        const style = getComputedStyle(ring);
                        return style.color === 'rgb(0, 255, 196)' && style.filter.includes('drop-shadow');
                    })""")
                    assert await page.locator(".records .bond-icon").evaluate_all(
                        "images => images.every(image => getComputedStyle(image).filter === 'invert(1)')")
                    assert await page.locator('.records .bond[data-bond-id="swiftShip"] circle').get_attribute("stroke-dasharray") == "47.5 2.5"
                    assert await page.locator('.records .bond[data-bond-id="../missing"] circle').get_attribute("stroke-dasharray") is None
                    assert await page.locator(".records .bond-missing:not([hidden])").inner_text() == "../missing"
                    assert await page.locator('.bond[data-bond-id="egirShip"]').count() == 0
                    squad = await page.locator(".squad").bounding_box()
                    bonds = await page.locator(".bonds").bounding_box()
                    meta = await page.locator(".meta").bounding_box()
                    assert squad["y"] + squad["height"] < bonds["y"]
                    assert bonds["y"] + bonds["height"] < meta["y"]
                    assert await page.locator(".bond").evaluate_all("""items => items.every(item => {
                        const box = item.getBoundingClientRect();
                        const parent = item.closest('.record').getBoundingClientRect();
                        const layer = item.querySelector('.bond-layer').getBoundingClientRect();
                        const icon = item.querySelector('.bond-icon');
                        return box.left >= parent.left && box.right <= parent.right && box.bottom <= parent.bottom
                            && (!icon || icon.getBoundingClientRect().bottom < layer.top);
                    })""")
                    return await original_screenshot(**options)

                monkeypatch.setattr(page, "screenshot", screenshot)
                return page

            monkeypatch.setattr(context, "new_page", new_page)
            return context

        monkeypatch.setattr(renderer._browser, "new_context", new_context)
        (tmp_path / "garrison-bonds-wrapped.png").write_bytes((await render_garrison_history(data, "博士"))[0])
    finally:
        await renderer.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["test", "qq_official"])
async def test_garrison_command_routes_role_and_all_images(tmp_path, platform):
    store = SklandStore(tmp_path / "db")
    await store.initialize()
    plugin, namespace = make_plugin(store)
    role = Character("bot:u", "binding", "role", "arknights", "1", "博士")
    plugin.service.garrison_history = AsyncMock(return_value=(role, history(8)))
    namespace["render_garrison_history"] = AsyncMock(return_value=[b"page1", b"page2"])
    namespace["send_images"] = AsyncMock()
    event = Event(group="group", name=platform)
    assert await run(plugin, event, ["garrison", "-r", "1"]) == []
    plugin.service.garrison_history.assert_awaited_once_with("bot:u", 1)
    namespace["render_garrison_history"].assert_awaited_once()
    from skland.config import config
    assert namespace["render_garrison_history"].await_args.kwargs == (
        {"max_height": config.qq_image_max_side} if platform == "qq_official" else {})
    assert namespace["send_images"].await_args.args[1] == [b"page1", b"page2"]
    assert "用法" in texts(await run(plugin, event, ["garrison", "extra"]))


def test_garrison_avatar_reuses_shared_cache_and_rejects_unsafe_ids(tmp_path, monkeypatch):
    from skland import config as paths
    from skland.filters import garrison_avatar_url
    from skland.image_cache import collect_missing_images, write_cached_image

    monkeypatch.setattr(paths, "CACHE_DIR", tmp_path / "cache")
    image_path = paths.CACHE_DIR / "avatar/char_4054_malist.png"
    with collect_missing_images() as pending:
        url = garrison_avatar_url("char_4054_malist")
    assert pending == {(url, image_path)}
    stream = BytesIO()
    Image.new("RGB", (10, 10), "teal").save(stream, format="PNG")
    write_cached_image(image_path, stream.getvalue())
    with collect_missing_images() as pending:
        assert garrison_avatar_url("char_4054_malist") == image_path.as_uri()
        assert garrison_avatar_url("../../outside").endswith("operator_empty.svg")
    assert pending == set()


@pytest.mark.asyncio
@pytest.mark.parametrize("first_band_status", [200, 404])
async def test_garrison_render_caches_real_images_and_retries_failed_downloads(tmp_path, monkeypatch, first_band_status):
    from skland import config as paths
    from skland.garrison_assets import BAND_ICON_BASE, BOND_ICON_BASE, RESOURCE_URL

    monkeypatch.setattr(paths, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(paths, "RES_DIR", tmp_path / "resources")
    monkeypatch.setattr(paths.config, "ark_portrait_cache_enabled", False)
    decoration_url = "https://assets.skland.com/_static_assets/game-tools/test-card-background.png"
    decoration_index = paths.RES_DIR / "images/garrison/decorations.json"
    decoration_index.parent.mkdir(parents=True)
    decoration_index.write_text(json.dumps({"assets": {
        "card_background": {"url": decoration_url}
    }}), encoding="utf-8")
    avatar = BytesIO()
    Image.new("RGB", (76, 76), "teal").save(avatar, format="PNG")
    requests = []
    resource_requests = []
    band_url = f"{BAND_ICON_BASE}band_download_test.png"
    band_path = paths.CACHE_DIR / "garrison/band/band_download_test.png"
    bond_url = f"{BOND_ICON_BASE}bond_download_test.png"
    bond_path = paths.CACHE_DIR / "garrison/bond/bond_download_test.png"
    avatar_url = "https://web.hycdn.cn/arknights/game/assets/char/avatar/char_4054_malist.png"
    avatar_path = paths.CACHE_DIR / "avatar/char_4054_malist.png"
    gear_url = "https://web.hycdn.cn/arknights/game/assets/char_skill/gear_download_test.png"
    gear_path = paths.CACHE_DIR / "garrison/equipment/chess_item_download_test.png"
    real = httpx.AsyncClient

    def resource(request):
        resource_requests.append(request)
        assert str(request.url) == RESOURCE_URL and "cred" not in request.headers
        return httpx.Response(200, json={"code": 0, "data": {"strategies": [
            {"strategyId": "band_download_test", "iconUrl": band_url}
        ], "bonds": [{"bondId": "bond_download_test", "iconUrl": bond_url, "name": "测试盟约"}],
            "items": [{"itemId": "chess_item_download_test", "iconUrl": gear_url, "name": "测试装备", "shopLevel": 2}]}})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(**kw, transport=httpx.MockTransport(resource)))
    data = history(1)
    data.records[0].bandId = "band_download_test"
    data.records[0].bondInfo = [GarrisonBond(bondId="bond_download_test", layer=72, activate=1)]
    data.records[0].chessSquad[0].equips = [GarrisonEquipment(equipId="chess_item_download_test")]
    renderer.configure(tmp_path)
    try:
        await renderer.start()
        create_context = renderer._browser.new_context

        async def new_context(**kwargs):
            context = await create_context(**kwargs)

            async def handle(route):
                requests.append(route.request.url)
                if route.request.url in {band_url, bond_url, avatar_url, gear_url} and requests.count(route.request.url) == 1 and first_band_status != 200:
                    await route.fulfill(status=first_band_status, body="Not Found")
                    return
                await route.fulfill(status=200, content_type="image/png", body=avatar.getvalue())

            await context.route("https://web.hycdn.cn/arknights/game/assets/**", handle)
            await context.route("https://assets.skland.com/_static_assets/game-tools/**", handle)
            return context

        monkeypatch.setattr(renderer._browser, "new_context", new_context)
        first = (await render_garrison_history(data, "博士"))[0]
        (tmp_path / f"garrison-band-{first_band_status}.png").write_bytes(first)
        if first_band_status != 200:
            assert not avatar_path.exists() and not gear_path.exists()
            assert not band_path.exists()
            assert not bond_path.exists()
            await render_garrison_history(data, "博士")
        assert band_path.read_bytes() == avatar.getvalue()
        assert bond_path.read_bytes() == avatar.getvalue()
        assert avatar_path.read_bytes() == avatar.getvalue()
        assert gear_path.read_bytes() == avatar.getvalue()
        await render_garrison_history(data, "博士")
        assert len(resource_requests) == 3
        assert requests.count(band_url) == (1 if first_band_status == 200 else 2)
        assert requests.count(bond_url) == (1 if first_band_status == 200 else 2)
        assert requests.count(avatar_url) == (1 if first_band_status == 200 else 2)
        assert requests.count(gear_url) == (1 if first_band_status == 200 else 2)
        assert requests.count(decoration_url) == 1
        assert len(list((paths.CACHE_DIR / "garrison/decorations").glob("card_background-*.png"))) == 1
        assert not any("/char/portrait/" in url for url in requests)
    finally:
        await renderer.close()
