from unittest.mock import AsyncMock

import httpx
import pytest

from skland.api import SklandAPI
from skland.schemas import CRED, WarEchoesView
from skland.exception import LoginException, RequestException, UnauthorizedException


@pytest.mark.asyncio
@pytest.mark.parametrize("code,exception", [(0, None), (10000, UnauthorizedException), (10002, LoginException), (123, RequestException)])
async def test_war_api_query_identity_signing_and_errors(monkeypatch, code, exception):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"code": code, "message": "status", "data": {"warEchoes": {"seasons": [], "achieves": []}}})
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(**kw, transport=httpx.MockTransport(handler)))
    signing = AsyncMock(return_value={"test-sign": "signed"})
    monkeypatch.setattr(SklandAPI, "get_sign_header", signing)
    async def fetch():
        return await SklandAPI.endfield_war_echoes(CRED("cred", "token"), user_id="remote-user", role_id="role&x", server_id="2", season_id=3)
    if exception:
        with pytest.raises(exception):
            await fetch()
    else:
        data = await fetch()
        with pytest.raises(ValueError, match="暂无战争回响赛季"):
            WarEchoesView.from_data(data)
    assert len(requests) == 1
    request = requests[0]
    assert request.url.path == "/api/v1/game/endfield/card/war-echoes"
    assert dict(request.url.params) == {"userId": "remote-user", "roleId": "role&x", "serverId": "2", "seasonId": "3"}
    assert request.headers["test-sign"] == "signed"
    assert signing.await_args.args[1] == str(request.url)
