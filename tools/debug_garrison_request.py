"""Fetch the official battle page's info/resource using a Skland access token.

Standalone Python 3 script; no plugin installation or third-party modules needed.
Credentials are prompted, used in memory, and excluded from the saved responses.
"""

import argparse
import base64
import getpass
import hashlib
import hmac
import json
import math
import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


HOST = "https://zonai.skland.com"
USER_AGENT = "Skland/1.32.1 (com.hypergryph.skland; build:103201004; Android 33; ) Okhttp/4.11.0"


def response_time(payload, headers=None):
    value = payload.get("timestamp") if isinstance(payload, dict) else None
    try:
        seconds = float(value) if not isinstance(value, bool) else 0
        if math.isfinite(seconds) and seconds > 0:
            return int(seconds / 1000 if seconds >= 100_000_000_000 else seconds)
    except (ValueError, TypeError, OverflowError):
        pass
    try:
        value = parsedate_to_datetime((headers or {}).get("Date", "")).timestamp()
        return int(value) if math.isfinite(value) and value > 0 else None
    except (ValueError, TypeError, OverflowError, AttributeError):
        return None


class ServerClock:
    """Track the server's clock without depending on the local wall clock."""
    def __init__(self, monotonic=time.monotonic):
        self._monotonic = monotonic
        self._server = None
        self._observed = None

    def observe(self, payload, headers=None):
        seconds = response_time(payload, headers)
        if seconds is not None:
            self._server = seconds
            self._observed = self._monotonic()

    def now(self):
        if self._server is None:
            return None
        return self._server + int(max(0, self._monotonic() - self._observed))


SERVER_CLOCK = ServerClock()


def sign_headers(cred, signing_token, url, timestamp=None, *, battle=False):
    timestamp = int(time.time()) - 1 if timestamp is None else timestamp
    headers = {"platform": "3" if battle else "", "timestamp": str(timestamp),
               "dId": "", "vName": "1.0.0" if battle else ""}
    parsed = urlparse(url)
    text = parsed.path + parsed.query + str(timestamp) + json.dumps(headers, separators=(",", ":"))
    digest = hmac.new(signing_token.encode(), text.encode(), hashlib.sha256).hexdigest()
    return {"User-Agent": USER_AGENT, "cred": cred, **headers,
            "sign": hashlib.md5(digest.encode()).hexdigest()}


class APIError(RuntimeError):
    def __init__(self, url, status, payload, secrets=(), headers=None):
        payload = payload if isinstance(payload, dict) else {}
        self.code = payload.get("code", payload.get("status"))
        self.server_time = response_time(payload, headers)
        detail = payload.get("message") or payload.get("msg") or ""
        if not isinstance(detail, str):
            detail = ""
        for value in secrets:
            if isinstance(value, str) and value:
                detail = detail.replace(value, "[hidden]")
        detail = " ".join(detail.split())[:300]
        text = f"HTTP {status}"
        if self.code is not None:
            text += f", API code {self.code}"
        text += f": {urlparse(url).path}"
        if detail:
            text += f" - {detail}"
        super().__init__(text)


def decode_data(payload):
    data = payload.get("data")
    if isinstance(data, dict) and "content" in data:
        return json.loads(base64.b64decode(data["content"], validate=True).decode("utf-8"))
    return data


def request_json(url, *, body=None, headers=None, secrets=()):
    headers = {"User-Agent": USER_AGENT, **(headers or {})}
    secrets = (*secrets, *(headers.get(key) for key in ("cred", "sign")),
               *((body or {}).get(key) for key in ("token", "code", "cred")))
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
            response_headers = getattr(response, "headers", {})
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read(1024 * 1024))
        except (ValueError, UnicodeError):
            payload = None
        SERVER_CLOCK.observe(payload, exc.headers)
        raise APIError(url, exc.code, payload, secrets, exc.headers) from None
    if not isinstance(payload, dict):
        raise RuntimeError("Response is not a JSON object")
    SERVER_CLOCK.observe(payload, response_headers)
    codes = [payload.get(key) for key in ("code", "status")]
    if any(code not in (None, 0) for code in codes):
        raise APIError(url, 200, payload, secrets, response_headers)
    data = decode_data(payload)
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid response data: {urlparse(url).path}")
    return data


def get_credentials(access_token):
    grant = request_json("https://as.hypergryph.com/user/oauth2/v2/grant", body={
        "appCode": "4ca99fa6b56cc2ba", "token": access_token, "type": 0,
    })
    result = request_json(HOST + "/api/v1/user/auth/generate_cred_by_code", body={
        "code": grant["code"], "kind": 1,
    })
    return result["cred"], result["token"]


def signed_get(path, params, cred, signing_token):
    url = HOST + path + "?" + urlencode(params)
    battle = path.startswith("/api/ms/game/arknights/garrison/")
    timestamp = None
    for attempt in range(2):
        request_time = timestamp if timestamp is not None else SERVER_CLOCK.now()
        headers = sign_headers(cred, signing_token, url, request_time, battle=battle)
        headers["x-transformed"] = "base64"
        if battle:
            headers["Origin"] = "https://act.skland.com"
            headers["Referer"] = "https://act.skland.com/arknights/auto-chess?activityId=act3autochess"
        try:
            return request_json(url, headers=headers, secrets=(cred, signing_token))
        except APIError as exc:
            if attempt:
                raise
            if exc.code == 10000:
                print("Refreshing signing token and retrying once...")
                refreshed = request_json(HOST + "/api/v1/auth/refresh", headers={"cred": cred},
                                         secrets=(cred, signing_token))
                signing_token = refreshed["token"]
            elif exc.code == 10003 and exc.server_time is not None:
                print("Correcting request time and retrying once...")
                timestamp = SERVER_CLOCK.now() or exc.server_time
            else:
                raise


def choose_uid(cred, signing_token):
    data = signed_get("/api/v1/game/player/binding", {}, cred, signing_token)
    roles = [role for app in data.get("list", []) if app.get("appCode") == "arknights"
             for role in app.get("bindingList", [])]
    if not roles:
        raise RuntimeError("No Arknights role found in this Skland account")
    for index, role in enumerate(roles, 1):
        print(f"{index}. {role.get('nickName', '')}  UID={role.get('uid', '')}")
    choice = input("Role number [1]: ").strip() or "1"
    if not choice.isdigit() or not 1 <= int(choice) <= len(roles):
        raise RuntimeError("Invalid role number")
    return roles[int(choice) - 1]["uid"]


def history_for_sharing(data):
    """Keep battle/character fields needed for diagnosis, excluding account data."""
    history = data.get("history") or {}
    keys = ("id", "versionId", "activityId", "completeState", "passRound", "gameEndTs",
            "gameStartTs", "modeType", "modeDifficulty", "chessSquad")
    return {"code": 0, "data": {"history": {
        name: [{key: record[key] for key in keys if key in record}
               for record in (history.get(name) or [])]
        for name in ("records", "favourRecords")
    }}}


def resources_for_sharing(data):
    return {"code": 0, "data": {"resources": [
        {key: resource[key] for key in ("versionInfo", "equipInfo", "chessInfo") if key in resource}
        for resource in data.get("resources", [])
    ]}}


def main():
    parser = argparse.ArgumentParser(description="Fetch official garrison data; enter secrets interactively.")
    parser.add_argument("--uid", help="Role UID; omitted to choose from the bound roles")
    parser.add_argument("--output", type=Path, help="Directory for the decoded, account-redacted JSON files")
    args = parser.parse_args()
    access_token = getpass.getpass("Skland access token (hidden): ").strip()
    if not access_token:
        raise RuntimeError("Token is empty")
    print("Getting credentials...")
    cred, signing_token = get_credentials(access_token)
    if SERVER_CLOCK.now() is not None:
        print("Request signing time synchronized from server response.")
    uid = args.uid or choose_uid(cred, signing_token)
    print("Fetching official battle info (platform=3, vName=1.0.0)...")
    info = signed_get("/api/ms/game/arknights/garrison/info",
                      {"activityId": "act3autochess", "uid": uid}, cred, signing_token)
    output = args.output or Path.cwd() / ("garrison-debug-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    output.mkdir(parents=True, exist_ok=True)
    info_file = output / "garrison-info-share.json"
    info_file.write_text(json.dumps(history_for_sharing(info), ensure_ascii=False, indent=2), encoding="utf-8")
    print("Saved:", info_file.resolve())
    print("Fetching official equipment resources...")
    resources = signed_get("/api/ms/game/arknights/garrison/resource",
                           {"activityId": "act3autochess"}, cred, signing_token)
    resource_file = output / "garrison-resource-share.json"
    resource_file.write_text(json.dumps(resources_for_sharing(resources), ensure_ascii=False, indent=2), encoding="utf-8")
    print("Saved:", resource_file.resolve())
    print("Done. Credentials were not written to either JSON file.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, KeyError, ValueError, UnicodeError, URLError, OSError) as exc:
        print("Request failed:", exc)
        raise SystemExit(1) from None
    except (EOFError, KeyboardInterrupt):
        raise SystemExit(1) from None
