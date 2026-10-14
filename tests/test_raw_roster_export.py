import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from skland.api import SklandAPI, SklandLoginAPI
from skland.exception import RosterExportError, UnauthorizedException, LoginException, RequestException
from skland.models import Account, Character
from skland.service import SklandService
from skland.resourcesync import game_data


def payload():
    return {"chars": [{"charId": "char_future", "evolvePhase": 2, "level": 90, "potentialRank": 5,
                       "skills": [{"id": "skill_new", "specializeLevel": 3}], "equip": []}],
            "charInfoMap": {"char_future": {"id": "char_future", "name": "新干员"}},
            "equipmentInfoMap": {}, "building": {"private": "do-not-export"}, "cred": "do-not-export"}


def service(roles=None, versions=None):
    roles = roles if roles is not None else [Character("bot:u", "100", None, "arknights", "1", "博士", account_id=1)]
    store = SimpleNamespace(get_characters=AsyncMock(return_value=roles), owner_version=AsyncMock(side_effect=versions) if versions else AsyncMock(return_value=3),
                            save_refreshed_credentials=AsyncMock())
    instance = SklandService(store)
    account = Account("bot:u", "access", "cred", "token", "remote", id=1)
    instance.require_account = AsyncMock(return_value=account)
    return instance


@pytest.mark.asyncio
async def test_export_does_not_touch_catalog_resources_or_display_cache(monkeypatch):
    instance = service()
    monkeypatch.setattr(game_data, "load", AsyncMock(side_effect=AssertionError("Resources called")))
    monkeypatch.setattr(game_data, "operator_catalog", SimpleNamespace(entries=[], by_id={}))
    request = AsyncMock(return_value=payload())
    monkeypatch.setattr(SklandAPI, "ark_card_raw", request)
    result = await instance.raw_operator_rosters("bot:u", allow_empty=True)
    assert result["schema_version"] == 1 and result["complete_role_set"] is True
    assert result["binding_revision"] == 3
    assert result["roles"][0]["chars"][0]["charId"] == "char_future"
    assert "building" not in result["roles"][0] and "cred" not in result["roles"][0]
    game_data.load.assert_not_awaited()
    request.assert_awaited_once()


@pytest.mark.asyncio
async def test_role_deduplication_multi_server_and_fresh_requests(monkeypatch):
    roles = [Character("bot:u", uid, None, "arknights", server, "博士", account_id=account)
             for uid, server, account in [("100", "1", 1), ("100", "1", 2), ("100", "2", 3)]]
    instance = service(roles)
    request = AsyncMock(return_value=payload())
    monkeypatch.setattr(SklandAPI, "ark_card_raw", request)
    for _ in range(2):
        result = await instance.raw_operator_rosters("bot:u", allow_empty=True)
        assert len(result["roles"]) == 2
    assert request.await_count == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("empty", [False, True])
async def test_binding_revision_checked_even_for_empty(monkeypatch, empty):
    instance = service([] if empty else None, [1, 2])
    monkeypatch.setattr(SklandAPI, "ark_card_raw", AsyncMock(return_value=payload()))
    with pytest.raises(RosterExportError) as caught:
        await instance.raw_operator_rosters("bot:u", allow_empty=True)
    assert caught.value.code == "binding_changed"


@pytest.mark.asyncio
async def test_empty_is_explicit_and_failures_are_not_empty(monkeypatch):
    instance = service([])
    with pytest.raises(RosterExportError) as caught:
        await instance.raw_operator_rosters("bot:u")
    assert caught.value.code == "not_bound"
    assert (await instance.raw_operator_rosters("bot:u", allow_empty=True))["roles"] == []
    instance = service()
    monkeypatch.setattr(SklandAPI, "ark_card_raw", AsyncMock(side_effect=RequestException("SECRET")))
    with pytest.raises(RosterExportError) as caught:
        await instance.raw_operator_rosters("bot:u", allow_empty=True)
    assert caught.value.code == "network_failed" and "SECRET" not in str(caught.value)


@pytest.mark.asyncio
async def test_credential_refresh_is_reused(monkeypatch):
    instance = service()
    monkeypatch.setattr(SklandAPI, "ark_card_raw", AsyncMock(side_effect=[UnauthorizedException("expired"), payload()]))
    refresh = AsyncMock(return_value="refreshed")
    monkeypatch.setattr(SklandLoginAPI, "refresh_token", refresh)
    result = await instance.raw_operator_rosters("bot:u")
    assert len(result["roles"]) == 1
    refresh.assert_awaited_once_with("cred")
    instance.store.save_refreshed_credentials.assert_awaited_once()


@pytest.mark.asyncio
async def test_partial_role_failure_rejects_whole_result(monkeypatch):
    roles = [Character("bot:u", uid, None, "arknights", "1", "博士", account_id=1) for uid in ("100", "200")]
    instance = service(roles)
    monkeypatch.setattr(SklandAPI, "ark_card_raw", AsyncMock(side_effect=[payload(), RequestException("offline")]))
    with pytest.raises(RosterExportError):
        await instance.raw_operator_rosters("bot:u", allow_empty=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("code,exception", [(0, None), (10000, UnauthorizedException), (10002, LoginException), (123, RequestException)])
async def test_raw_api_returns_json_and_preserves_auth_errors(monkeypatch, code, exception):
    real = httpx.AsyncClient
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"code": code, "message": "status", "data": payload()})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(**kw, transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(SklandAPI, "get_sign_header", AsyncMock(return_value={"sign": "signed"}))
    if exception:
        with pytest.raises(exception):
            await SklandAPI.ark_card_raw(SimpleNamespace(cred="cred", token="token"), "100")
    else:
        assert (await SklandAPI.ark_card_raw(SimpleNamespace(cred="cred", token="token"), "100"))["chars"][0]["charId"] == "char_future"
    assert dict(requests[0].url.params) == {"uid": "100"}
    assert requests[0].headers["sign"] == "signed"


@pytest.mark.asyncio
async def test_raw_api_encodes_uid_and_http_errors_are_not_success(monkeypatch):
    real = httpx.AsyncClient
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(503, json={"data": payload()})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(**kw, transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(SklandAPI, "get_sign_header", AsyncMock(return_value={}))
    with pytest.raises(RequestException):
        await SklandAPI.ark_card_raw(SimpleNamespace(cred="cred", token="token"), "100&x=1")
    assert dict(requests[0].url.params) == {"uid": "100&x=1"}


@pytest.mark.asyncio
async def test_projection_does_not_export_unknown_fields_or_nested_secrets(monkeypatch):
    raw = payload()
    raw["chars"][0]["access_token"] = "SECRET"
    raw["chars"][0]["skills"][0]["unknown"] = "SECRET"
    raw["charInfoMap"]["char_future"]["unknown"] = "SECRET"
    monkeypatch.setattr(SklandAPI, "ark_card_raw", AsyncMock(return_value=raw))
    result = await service().raw_operator_rosters("bot:u")
    assert "SECRET" not in str(result)


@pytest.mark.asyncio
async def test_public_facade_forwards_contract_without_host_import():
    tree = ast.parse((Path(__file__).parents[1] / "main.py").read_text("utf-8"))
    method = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "export_arknights_raw_rosters")
    namespace = {}
    exec(compile(ast.Module(body=[method], type_ignores=[]), "main.py", "exec"), namespace)
    provider = SimpleNamespace(raw_operator_rosters=AsyncMock(return_value={"schema_version": 1}))
    assert await namespace[method.name](SimpleNamespace(service=provider), "bot:u", allow_empty=True) == {"schema_version": 1}
    provider.raw_operator_rosters.assert_awaited_once_with("bot:u", allow_empty=True)
