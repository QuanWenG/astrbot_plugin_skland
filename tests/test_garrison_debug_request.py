import base64
import importlib.util
import json
import sys
from io import BytesIO, StringIO
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse
from urllib.error import HTTPError

import pytest

import skland.api.request as request_module
from skland.api import SklandAPI
from skland.schemas import CRED


@pytest.fixture
def debug_request():
    path = Path(__file__).resolve().parents[1] / "tools/debug_garrison_request.py"
    spec = importlib.util.spec_from_file_location("garrison_debug_request", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
@pytest.mark.parametrize("battle", [False, True])
async def test_standalone_signatures_match_plugin_headers(debug_request, monkeypatch, battle):
    now = SimpleNamespace(now=lambda: SimpleNamespace(timestamp=lambda: 1700000001))
    monkeypatch.setattr(request_module, "datetime", now)
    url = "https://zonai.skland.com/api/ms/game/arknights/garrison/info?activityId=act3autochess&uid=12345678"
    expected = await SklandAPI.get_sign_header(CRED("test-cred", "test-signing-token"), url, "get", battle_profile=battle)
    actual = debug_request.sign_headers("test-cred", "test-signing-token", url, timestamp=1700000000, battle=battle)
    for key in ("cred", "sign", "platform", "timestamp", "dId", "vName", "User-Agent"):
        assert actual[key] == expected[key]
    assert actual["platform"] == ("3" if battle else "")
    assert actual["vName"] == ("1.0.0" if battle else "")
    if battle:
        # The official SDK includes these four fields in this order in the
        # HMAC input. These are header values, not a different credential kind.
        legacy = debug_request.sign_headers("test-cred", "test-signing-token", url, timestamp=1700000000)
        assert actual["sign"] != legacy["sign"]


def test_standalone_request_flow_decodes_and_excludes_account_credentials(debug_request, monkeypatch, tmp_path, capsys):
    record = {"id": "battle-1", "completeState": 1, "versionId": "V3_1", "chessSquad": [
        {"charId": "char_472_pasngr", "equips": [{"equipId": "chess_item_1_01_e_b"}]}]}
    info = {"user": {"uid": "private-account-uid"}, "history": {"records": [record], "favourRecords": []}}
    resources = {"resources": [{"versionInfo": {"versionId": "V3_1"}, "equipInfo": {
        "chess_item_1_01_e_b": {"skillId": "sktok_acarm041_2"}}, "private": "omit"}]}
    calls = []
    monkeypatch.setattr(debug_request, "SERVER_CLOCK", debug_request.ServerClock(monotonic=lambda: 20))

    def fake_open(request, **kwargs):
        path = urlparse(request.full_url).path
        calls.append((path, request))
        if "/api/ms/" in path:
            headers = {key.lower(): value for key, value in request.header_items()}
            assert headers["platform"] == "3" and headers["vname"] == "1.0.0"
            assert headers["x-transformed"] == "base64"
            assert headers["timestamp"] == "1700000005"
        if path.endswith("/oauth2/v2/grant"):
            assert json.loads(request.data)["token"] == "private-access-token"
            result = {"status": 0, "data": {"code": "private-grant-code"}}
        elif path.endswith("/generate_cred_by_code"):
            assert json.loads(request.data)["code"] == "private-grant-code"
            result = {"code": 0, "timestamp": "1700000005",
                      "data": {"cred": "private-cred", "token": "private-signing-token"}}
        elif path.endswith("/garrison/info"):
            result = {"code": 0, "data": {"content": base64.b64encode(json.dumps(info).encode()).decode()}}
        else:
            assert path.endswith("/garrison/resource")
            result = {"code": 0, "data": resources}
        return StringIO(json.dumps(result))

    monkeypatch.setattr(debug_request, "urlopen", fake_open)
    monkeypatch.setattr(debug_request.getpass, "getpass", lambda _: "private-access-token")
    monkeypatch.setattr(sys, "argv", ["debug_garrison_request.py", "--uid", "12345678", "--output", str(tmp_path)])
    debug_request.main()
    assert len(calls) == 4
    decoded = json.loads((tmp_path / "garrison-info-share.json").read_text(encoding="utf-8"))
    assert decoded["data"]["history"]["records"][0] == record
    resource = json.loads((tmp_path / "garrison-resource-share.json").read_text(encoding="utf-8"))
    assert resource["data"]["resources"][0]["equipInfo"]["chess_item_1_01_e_b"]["skillId"] == "sktok_acarm041_2"
    text = capsys.readouterr().out + "".join(path.read_text(encoding="utf-8") for path in tmp_path.glob("*.json"))
    assert "private-" not in text


def test_standalone_role_selection_uses_arknights_bindings(debug_request, monkeypatch):
    monkeypatch.setattr(debug_request, "signed_get", lambda *args: {"list": [
        {"appCode": "endfield", "bindingList": [{"uid": "wrong-game"}]},
        {"appCode": "arknights", "bindingList": [
            {"uid": "111", "nickName": "甲"}, {"uid": "222", "nickName": "乙"}]},
    ]})
    monkeypatch.setattr("builtins.input", lambda _: "2")
    assert debug_request.choose_uid("cred", "signing-token") == "222"


def test_http_401_reports_business_reason_without_credentials(debug_request, monkeypatch):
    url = debug_request.HOST + "/api/ms/game/arknights/garrison/info"
    payload = {"code": 10002, "message": "登录失效 private-cred private-token"}

    def fail(request, **kwargs):
        raise HTTPError(request.full_url, 401, "Unauthorized", {}, BytesIO(json.dumps(payload).encode()))

    monkeypatch.setattr(debug_request, "urlopen", fail)
    with pytest.raises(debug_request.APIError) as error:
        debug_request.request_json(url, headers={"cred": "private-cred"}, secrets=("private-token",))
    assert "HTTP 401" in str(error.value) and "10002" in str(error.value) and "登录失效" in str(error.value)
    assert "private-" not in str(error.value)


@pytest.mark.parametrize("code,server_time", [
    (10000, 1700000000), (10003, 1700000000), (10003, "1700000000"),
    (10003, "1700000000000"), (10003, 1700000000000),
])
def test_official_request_refreshes_or_resyncs_once_on_auth_error(debug_request, monkeypatch, code, server_time):
    calls = []

    def respond(request, **kwargs):
        calls.append(request)
        headers = {key.lower(): value for key, value in request.header_items()}
        if len(calls) == 1:
            payload = {"code": code, "message": "expired", "timestamp": server_time}
            raise HTTPError(request.full_url, 401, "Unauthorized", {}, BytesIO(json.dumps(payload).encode()))
        if request.full_url.endswith("/api/v1/auth/refresh"):
            assert headers["cred"] == "cred"
            return StringIO(json.dumps({"code": 0, "data": {"token": "new-token"}}))
        assert headers["platform"] == "3" and headers["vname"] == "1.0.0"
        if code == 10003:
            assert headers["timestamp"] == "1700000000"
        else:
            expected = debug_request.sign_headers("cred", "new-token", request.full_url,
                                                  int(headers["timestamp"]), battle=True)
            assert headers["sign"] == expected["sign"]
        return StringIO(json.dumps({"code": 0, "data": {"history": {"records": []}}}))

    monkeypatch.setattr(debug_request, "urlopen", respond)
    data = debug_request.signed_get("/api/ms/game/arknights/garrison/info",
                                    {"uid": "123", "activityId": "act3autochess"}, "cred", "old-token")
    assert data == {"history": {"records": []}}
    assert len(calls) == (3 if code == 10000 else 2)


def test_time_error_can_calibrate_from_http_date_header(debug_request, monkeypatch):
    calls = []

    def respond(request, **kwargs):
        calls.append(request)
        if len(calls) == 1:
            body = {"code": 10003, "message": "请勿修改设备本地时间"}
            raise HTTPError(request.full_url, 401, "Unauthorized",
                            {"Date": "Tue, 14 Nov 2023 22:13:20 GMT"}, BytesIO(json.dumps(body).encode()))
        headers = {key.lower(): value for key, value in request.header_items()}
        assert headers["timestamp"] == "1700000000"
        return StringIO(json.dumps({"code": 0, "data": {"history": {"records": []}}}))

    monkeypatch.setattr(debug_request, "urlopen", respond)
    assert debug_request.signed_get("/api/ms/game/arknights/garrison/info", {}, "cred", "token") == {
        "history": {"records": []}}
    assert len(calls) == 2


def test_signing_clock_uses_elapsed_monotonic_time_when_local_clock_is_wrong(debug_request, monkeypatch):
    elapsed = [20.0]
    clock = debug_request.ServerClock(monotonic=lambda: elapsed[0])
    monkeypatch.setattr(debug_request, "SERVER_CLOCK", clock)
    monkeypatch.setattr(debug_request, "time", SimpleNamespace(time=lambda: 123))
    clock.observe({"timestamp": "1700000000"})
    elapsed[0] += 3.8
    assert clock.now() == 1700000003

    def respond(request, **kwargs):
        headers = {key.lower(): value for key, value in request.header_items()}
        assert headers["timestamp"] == "1700000003"
        expected = debug_request.sign_headers("cred", "token", request.full_url, 1700000003, battle=True)
        assert headers["sign"] == expected["sign"]
        return StringIO(json.dumps({"code": 0, "data": {"history": {"records": []}}}))

    monkeypatch.setattr(debug_request, "urlopen", respond)
    debug_request.signed_get("/api/ms/game/arknights/garrison/info", {}, "cred", "token")


@pytest.mark.parametrize("value", [None, "", "bad time", "nan", "inf", True, False, 0, -1])
def test_bad_server_time_does_not_corrupt_signing_clock(debug_request, value):
    clock = debug_request.ServerClock(monotonic=lambda: 20)
    clock.observe({"timestamp": "1700000000"})
    clock.observe({"timestamp": value})
    assert clock.now() == 1700000000


def test_unstructured_401_does_not_leak_body_or_retry_indefinitely(debug_request, monkeypatch):
    calls = []

    def fail(request, **kwargs):
        calls.append(request)
        raise HTTPError(request.full_url, 401, "Unauthorized", {}, BytesIO(b"<html>private-token</html>"))

    monkeypatch.setattr(debug_request, "urlopen", fail)
    with pytest.raises(debug_request.APIError) as error:
        debug_request.signed_get("/api/ms/game/arknights/garrison/info", {}, "cred", "private-token")
    assert "HTTP 401" in str(error.value) and "private-token" not in str(error.value)
    assert len(calls) == 1
