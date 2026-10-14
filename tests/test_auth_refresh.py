import json
from dataclasses import replace

import httpx
import pytest
import pytest_asyncio

from skland.api import SklandAPI, SklandLoginAPI
from skland.exception import LoginException, RequestException, UnauthorizedException
from skland.schemas import CRED
from skland.service import SklandService
from skland.store import SklandStore
from test_account_migration import add_account

INFO = "/api/v1/game/player/info"
REFRESH = "/api/v1/auth/refresh"
GRANT = "/user/oauth2/v2/grant"
GENERATE = "/api/v1/user/auth/generate_cred_by_code"
PROFILE = "/api/v1/user/teenager"
DATA = {"status": {"ap": {"current": 15, "max": 210}}}


def transport(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs:
        original(**kwargs, transport=httpx.MockTransport(handler)))


@pytest_asyncio.fixture
async def bound(tmp_path):
    store = SklandStore(tmp_path / "db")
    await store.initialize()
    account, role = await add_account(store, uid="620197154", server="2")
    await store.subscribe_sanity("bot:u", role.id, "bot:FriendMessage:u")
    await store.save_sign_result("arknights", "bot:u", role.nickname, "ok", "today", character_id=role.id)
    return store, account, role, SklandService(store)


@pytest.mark.asyncio
@pytest.mark.parametrize("body,expected", [
    ({"code": 10000, "message": "expired"}, UnauthorizedException),
    ({"code": 10002, "message": "expired"}, LoginException),
    (None, UnauthorizedException),
])
async def test_http_401_preserves_authentication_error_type(monkeypatch, body, expected):
    transport(monkeypatch, lambda request: httpx.Response(401, json=body) if body is not None else httpx.Response(401, text="Unauthorized"))
    with pytest.raises(expected):
        await SklandAPI.ark_card_raw(CRED("old-cred", "old-signing"), "620197154")


@pytest.mark.asyncio
async def test_http_401_refreshes_signing_token_and_preserves_existing_binding(bound, monkeypatch):
    store, account, role, service = bound
    calls, credentials = [], []
    original_sign = SklandAPI.get_sign_header
    async def signed(cred, *args, **kwargs):
        credentials.append((cred.cred, cred.token))
        return await original_sign(cred, *args, **kwargs)
    monkeypatch.setattr(SklandAPI, "get_sign_header", signed)
    def handler(request):
        calls.append(request)
        if request.url.path == REFRESH:
            assert request.headers["cred"] == "cred"
            return httpx.Response(200, json={"code": 0, "data": {"token": "refreshed-signing"}})
        assert request.url.path == INFO and request.url.params["uid"] == "620197154"
        if len(credentials) == 1:
            return httpx.Response(401, json={"code": 10000})
        return httpx.Response(200, json={"code": 0, "data": DATA})
    transport(monkeypatch, handler)
    version = await store.owner_version("bot:u")
    result = await service._with_refresh(account, lambda cred: SklandAPI.ark_card_raw(cred, role.uid))
    assert result == DATA and credentials == [("cred", "token"), ("cred", "refreshed-signing")]
    assert [request.url.path for request in calls] == [INFO, REFRESH, INFO]
    assert calls[0].headers["sign"] != calls[2].headers["sign"]
    saved = await store.get_account("bot:u")
    assert saved.id == account.id and saved.cred_token == "refreshed-signing" and saved.access_token == "access"
    assert (await store.get_characters("bot:u"))[0].id == role.id
    assert (await store.list_sanity_subscriptions("bot:u"))[0].character_id == role.id
    assert (await store.get_sign_results("arknights", "bot:u"))[0]["result"] == "ok"
    assert await store.owner_version("bot:u") == version


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["expired-cred", "retry-401", "business-login-expired"])
async def test_saved_login_token_regenerates_expired_credentials(bound, monkeypatch, mode):
    store, account, role, service = bound
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if request.url.path == REFRESH:
            if mode == "expired-cred":
                return httpx.Response(401, json={"code": 10002})
            return httpx.Response(200, json={"code": 0, "data": {"token": "signing-only"}})
        if request.url.path == GRANT:
            assert json.loads(request.content)["token"] == "access"
            return httpx.Response(200, json={"status": 0, "data": {"code": "grant-code"}})
        if request.url.path == GENERATE:
            assert json.loads(request.content)["code"] == "grant-code"
            return httpx.Response(200, json={"code": 0, "data": {
                "cred": "regenerated-cred", "token": "regenerated-signing", "userId": "remote-1"}})
        assert request.url.path == INFO
        if request.headers["cred"] == "regenerated-cred":
            return httpx.Response(200, json={"code": 0, "data": DATA})
        return httpx.Response(401, json={"code": 10002 if mode == "business-login-expired" else 10000})
    transport(monkeypatch, handler)
    assert await service._with_refresh(account, lambda cred: SklandAPI.ark_card_raw(cred, role.uid)) == DATA
    saved = await store.get_account("bot:u")
    assert (saved.cred, saved.cred_token, saved.access_token) == ("regenerated-cred", "regenerated-signing", "access")
    assert len(await store.list_accounts("bot:u")) == len(await store.list_sanity_subscriptions("bot:u")) == 1
    assert calls.count(GRANT) == calls.count(GENERATE) == 1
    assert calls.count(REFRESH) == (0 if mode == "business-login-expired" else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id", ["different-owner", None])
async def test_regeneration_verifies_account_identity_before_saving(bound, monkeypatch, user_id):
    store, account, role, service = bound
    def handler(request):
        if request.url.path == INFO:
            return httpx.Response(401, json={"code": 10002})
        if request.url.path == GRANT:
            return httpx.Response(200, json={"status": 0, "data": {"code": "grant"}})
        if request.url.path == GENERATE:
            return httpx.Response(200, json={"code": 0, "data": {
                "cred": "unsafe-cred", "token": "unsafe-token", "userId": user_id}})
        assert request.url.path == PROFILE
        return httpx.Response(200, json={"code": 0, "data": {"teenager": {"userId": None}}})
    transport(monkeypatch, handler)
    with pytest.raises(RequestException, match="账号身份"):
        await service._with_refresh(account, lambda cred: SklandAPI.ark_card_raw(cred, role.uid))
    assert (await store.get_account("bot:u")).cred == account.cred == "cred"
    assert (await store.get_account("bot:u")).cred_token == account.cred_token == "token"


@pytest.mark.asyncio
async def test_missing_user_id_is_resolved_before_credential_save(bound, monkeypatch):
    store, account, role, service = bound
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if request.url.path == INFO:
            return httpx.Response(200, json={"code": 0, "data": DATA}) if request.headers["cred"] == "new-cred" else httpx.Response(401, json={"code": 10002})
        if request.url.path == GRANT:
            return httpx.Response(200, json={"status": 0, "data": {"code": "grant"}})
        if request.url.path == GENERATE:
            return httpx.Response(200, json={"code": 0, "data": {"cred": "new-cred", "token": "new-sign"}})
        assert request.url.path == PROFILE and request.headers["cred"] == "new-cred"
        return httpx.Response(200, json={"code": 0, "data": {"teenager": {"userId": "remote-1"}}})
    transport(monkeypatch, handler)
    assert await service._with_refresh(account, lambda cred: SklandAPI.ark_card_raw(cred, role.uid)) == DATA
    assert calls.count(PROFILE) == 1 and (await store.get_account("bot:u")).cred == "new-cred"


@pytest.mark.asyncio
async def test_repeated_authentication_failure_has_bounded_recovery(bound, monkeypatch):
    _, account, role, service = bound
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if request.url.path == REFRESH:
            return httpx.Response(200, json={"code": 0, "data": {"token": "new-sign"}})
        if request.url.path == GRANT:
            return httpx.Response(200, json={"status": 0, "data": {"code": "grant"}})
        if request.url.path == GENERATE:
            return httpx.Response(200, json={"code": 0, "data": {"cred": "new", "token": "new-token", "userId": "remote-1"}})
        assert request.url.path == INFO
        return httpx.Response(401, text="Unauthorized")
    transport(monkeypatch, handler)
    with pytest.raises(UnauthorizedException, match="重新扫码绑定"):
        await service._with_refresh(account, lambda cred: SklandAPI.ark_card_raw(cred, role.uid))
    assert calls.count(INFO) == 3 and calls.count(REFRESH) == calls.count(GRANT) == calls.count(GENERATE) == 1


@pytest.mark.asyncio
async def test_cred_only_expired_binding_gives_rebind_instruction_and_keeps_data(bound, monkeypatch):
    store, account, role, service = bound
    account.access_token = None
    await store.save_account(account)
    transport(monkeypatch, lambda request: httpx.Response(401, json={"code": 10002}))
    with pytest.raises(RequestException, match="重新扫码绑定"):
        await service._with_refresh(account, lambda cred: SklandAPI.ark_card_raw(cred, role.uid))
    assert (await store.get_account("bot:u")).cred == "cred"
    assert (await store.get_characters("bot:u"))[0].id == role.id
    assert len(await store.list_sanity_subscriptions("bot:u")) == 1


@pytest.mark.asyncio
async def test_valid_cred_only_binding_refreshes_without_login_token(bound, monkeypatch):
    store, account, role, service = bound
    account.access_token = None
    await store.save_account(account)
    paths = []
    def handler(request):
        paths.append(request.url.path)
        if request.url.path == REFRESH:
            return httpx.Response(200, json={"status": 0, "data": {"token": "refreshed-sign"}})
        assert request.url.path == INFO
        return httpx.Response(401, json={"code": 10000}) if paths.count(INFO) == 1 else httpx.Response(200, json={"code": 0, "data": DATA})
    transport(monkeypatch, handler)
    assert await service._with_refresh(account, lambda cred: SklandAPI.ark_card_raw(cred, role.uid)) == DATA
    assert paths == [INFO, REFRESH, INFO]
    assert (await store.get_account("bot:u")).access_token is None


@pytest.mark.asyncio
async def test_revoked_saved_login_token_gives_rebind_instruction_without_deletion(bound, monkeypatch):
    store, account, role, service = bound
    def handler(request):
        if request.url.path == INFO:
            return httpx.Response(401, json={"code": 10002})
        assert request.url.path == GRANT
        return httpx.Response(401, text="Unauthorized")
    transport(monkeypatch, handler)
    with pytest.raises(LoginException, match="重新扫码绑定"):
        await service._with_refresh(account, lambda cred: SklandAPI.ark_card_raw(cred, role.uid))
    assert (await store.get_account("bot:u")).cred == "cred"
    assert (await store.get_characters("bot:u"))[0].id == role.id
    assert len(await store.list_sanity_subscriptions("bot:u")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_path", [INFO, REFRESH])
async def test_server_errors_do_not_regenerate_or_replace_credentials(bound, monkeypatch, failure_path):
    store, account, role, service = bound
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if request.url.path == failure_path:
            return httpx.Response(503, json={"data": DATA, "message": "SECRET"})
        return httpx.Response(401, json={"code": 10000})
    transport(monkeypatch, handler)
    with pytest.raises(RequestException) as error:
        await service._with_refresh(account, lambda cred: SklandAPI.ark_card_raw(cred, role.uid))
    assert "HTTP 503" in str(error.value) and "SECRET" not in str(error.value)
    assert GRANT not in calls and (await store.get_account("bot:u")).cred_token == "token"


@pytest.mark.asyncio
async def test_concurrent_credentials_are_not_overwritten_or_mutated_in_memory(bound, monkeypatch):
    store, account, role, service = bound
    async def handler(request):
        if request.url.path == INFO:
            return httpx.Response(401, json={"code": 10000})
        assert request.url.path == REFRESH
        await store.save_account(replace(account, cred="concurrent-cred", cred_token="concurrent-token"))
        return httpx.Response(200, json={"code": 0, "data": {"token": "stale-refresh"}})
    transport(monkeypatch, handler)
    with pytest.raises(ValueError, match="凭证已变更"):
        await service._with_refresh(account, lambda cred: SklandAPI.ark_card_raw(cred, role.uid))
    saved = await store.get_account("bot:u")
    assert saved.cred == "concurrent-cred" and saved.cred_token == "concurrent-token"
    assert account.cred == "cred" and account.cred_token == "token"


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["code", "status"])
@pytest.mark.parametrize("status", [10000, 10002])
async def test_refresh_auth_errors_accept_both_response_code_fields(monkeypatch, key, status):
    transport(monkeypatch, lambda request: httpx.Response(200, json={key: status, "message": "secret-cred"}))
    with pytest.raises(LoginException) as error:
        await SklandLoginAPI.refresh_token("old-cred")
    assert "secret-cred" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [{}, {"token": None}, {"token": ""}])
async def test_invalid_refresh_response_is_not_used_as_a_signing_token(monkeypatch, data):
    transport(monkeypatch, lambda request: httpx.Response(200, json={"code": 0, "data": data}))
    with pytest.raises(RequestException, match="有效 token"):
        await SklandLoginAPI.refresh_token("old-cred")
