from types import SimpleNamespace

import httpx
import pytest

import skland.api.request as request_module
from skland.api import SklandAPI, SklandLoginAPI, server_clock
from skland.exception import RequestException
from skland.schemas import CRED


@pytest.fixture(autouse=True)
def fresh_clock(monkeypatch):
    clock = server_clock.ServerClock(timer=lambda: 20)
    monkeypatch.setattr(server_clock, "BATTLE_CLOCK", clock)
    return clock


@pytest.mark.parametrize("timestamp", [1700000000, "1700000000", 1700000000000, "1700000000000"])
def test_login_response_seeds_battle_clock(timestamp, fresh_clock):
    response = httpx.Response(200, request=httpx.Request("POST", "https://zonai.skland.com/api/v1/user/auth/generate_cred_by_code"),
                              json={"code": 0, "timestamp": timestamp, "data": {"cred": "cred", "token": "token"}})
    assert SklandLoginAPI._auth_response(response, "登录")["cred"] == "cred"
    assert fresh_clock.now() == 1700000000


@pytest.mark.asyncio
async def test_only_battle_request_uses_calibrated_time(monkeypatch, fresh_clock):
    fresh_clock.observe({"timestamp": "1700000000"})
    monkeypatch.setattr(request_module, "datetime", SimpleNamespace(now=lambda: SimpleNamespace(timestamp=lambda: 123)))
    requests = []
    real = httpx.AsyncClient

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"code": 0, "data": {"history": {"records": []}}})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(**kw, transport=httpx.MockTransport(handle)))
    cred = CRED("cred", "token")
    await SklandAPI.garrison_history(cred, "12345678")
    assert requests[0].headers["timestamp"] == "1700000000"
    assert requests[0].headers["platform"] == "3"
    normal = await SklandAPI.get_sign_header(cred, "https://zonai.skland.com/api/v1/game/player/binding", "get")
    assert normal["timestamp"] == "122" and normal["platform"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("timestamp,headers", [
    ("1700000000", {}), ("1700000000000", {}),
    (None, {"Date": "Tue, 14 Nov 2023 22:13:20 GMT"}),
])
async def test_battle_time_error_resigns_once_using_server_clock(monkeypatch, timestamp, headers):
    requests = []
    real = httpx.AsyncClient

    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(401, headers=headers, json={"code": 10003, "timestamp": timestamp,
                                                            "message": "请勿修改设备本地时间"})
        assert request.headers["timestamp"] == "1700000000"
        assert request.headers["sign"] != requests[0].headers["sign"]
        assert request.url == requests[0].url
        return httpx.Response(200, json={"code": 0, "data": {"history": {"records": []}}})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(**kw, transport=httpx.MockTransport(handle)))
    result = await SklandAPI.garrison_history(CRED("cred", "token"), "12345678")
    assert result.records == [] and len(requests) == 2


@pytest.mark.asyncio
async def test_battle_time_error_does_not_loop_or_become_token_expiry(monkeypatch):
    requests = []
    real = httpx.AsyncClient

    def handle(request):
        requests.append(request)
        return httpx.Response(401, json={"code": 10003, "timestamp": "1700000000"})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(**kw, transport=httpx.MockTransport(handle)))
    with pytest.raises(RequestException, match="10003.*时间校准失败"):
        await SklandAPI.garrison_history(CRED("cred", "token"), "12345678")
    assert len(requests) == 2


def test_calibration_tracks_elapsed_monotonic_time_and_ignores_bad_samples():
    timer = [20.0]
    clock = server_clock.ServerClock(timer=lambda: timer[0])
    assert clock.now() is None
    clock.observe({"timestamp": "1700000000"})
    timer[0] += 3.8
    for value in (None, "bad", "nan", "inf", True, False, 0, -1):
        assert not clock.observe({"timestamp": value})
    assert clock.now() == 1700000003
