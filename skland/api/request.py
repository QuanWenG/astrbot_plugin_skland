import contextlib
import hashlib
import hmac
import json
import logging
from base64 import b64decode
from datetime import datetime
from typing import Literal
from urllib.parse import urlparse, urlencode

import httpx
from pydantic import TypeAdapter

from ..exception import LoginException, RequestException, UnauthorizedException
from ..models import Character
from . import server_clock
from ..schemas import (
    CRED,
    ArkCard,
    ArkSignResponse,
    BindingApp,
    EfCharGachaResponse,
    EfGachaContentResponse,
    EfWeaponGachaResponse,
    EndfieldCard,
    EndfieldPoolType,
    EndfieldSignResponse,
    GachaCate,
    GachaResponse,
    RogueData,
    GarrisonHistory,
    WarEchoes,
)

logger = logging.getLogger("astrbot")

base_url = "https://zonai.skland.com/api/v1"


class SklandAPI:
    _headers = {
        "User-Agent": (
            "Skland/1.32.1 (com.hypergryph.skland; build:103201004; Android 33; ) Okhttp/4.11.0"
        ),
        "Accept-Encoding": "gzip",
        "Connection": "close",
    }

    _header_for_sign = {"platform": "", "timestamp": "", "dId": "", "vName": ""}

    @classmethod
    async def get_binding(cls, cred: CRED) -> list[BindingApp]:
        """获取绑定的角色"""
        binding_url = f"{base_url}/game/player/binding"
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    binding_url,
                    headers=await cls.get_sign_header(cred, binding_url, method="get"),
                )
                if status := response.json().get("code"):
                    if status == 10000:
                        raise UnauthorizedException(
                            f"获取绑定角色失败：{response.json().get('message')}"
                        )
                    elif status == 10002:
                        raise LoginException(
                            f"获取绑定角色失败：{response.json().get('message')}"
                        )
                    if status != 0:
                        raise RequestException(
                            f"获取绑定角色失败：{response.json().get('message')}"
                        )
                return TypeAdapter(list[BindingApp]).validate_python(
                    response.json()["data"]["list"]
                )
            except httpx.HTTPError as e:
                raise RequestException(f"获取绑定角色失败: {e}")

    @classmethod
    async def get_sign_header(
        cls,
        cred: CRED,
        url: str,
        method: Literal["get", "post"],
        query_body: dict | None = None,
        use_did: bool = False,
        battle_profile: bool = False,
        timestamp_override: int | None = None,
    ) -> dict:
        """获取带sign请求头

        Args:
            cred: 认证凭据。
            url: 请求 URL。
            method: 请求方法。
            query_body: POST 请求体。
            use_did: 是否获取并使用设备 ID 参与签名计算。
            battle_profile: 使用官方卫戍战绩网页的签名头，沿用已有 cred 和 token。
            timestamp_override: 使用已经校准的服务器时间生成请求签名。
        """
        timestamp = int(datetime.now().timestamp()) - 1 if timestamp_override is None else timestamp_override
        header_for_sign = {**cls._header_for_sign}
        if battle_profile:
            header_for_sign.update(platform="3", vName="1.0.0")
        if use_did:
            from .dId import get_dId

            header_for_sign["dId"] = await get_dId()
        header_ca = {**header_for_sign, "timestamp": str(timestamp)}
        parsed_url = urlparse(url)
        if method == "post":
            query_params = json.dumps(query_body) if query_body is not None else ""
        else:
            query_params = parsed_url.query
        header_ca_str = json.dumps(
            {**header_for_sign, "timestamp": str(timestamp)},
            separators=(",", ":"),
        )
        secret = f"{parsed_url.path}{query_params}{timestamp}{header_ca_str}"
        hex_secret = hmac.new(
            cred.token.encode("utf-8"), secret.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        signature = hashlib.md5(hex_secret.encode("utf-8")).hexdigest()
        return {"cred": cred.cred, **cls._headers, "sign": signature, **header_ca}

    @classmethod
    async def ark_sign(
        cls, cred: CRED, uid: str, channel_master_id: str
    ) -> ArkSignResponse:
        """进行明日方舟签到"""
        body = {"uid": uid, "gameId": channel_master_id}
        json_body = json.dumps(
            body, ensure_ascii=False, separators=(", ", ": "), allow_nan=False
        )
        sign_url = f"{base_url}/game/attendance"
        headers = await cls.get_sign_header(
            cred,
            sign_url,
            method="post",
            query_body=body,
        )
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    sign_url,
                    headers={**headers, "Content-Type": "application/json"},
                    content=json_body,
                )
                logger.debug(f"签到回复：{response.json()}")
                if status := response.json().get("code"):
                    if status == 10000:
                        raise UnauthorizedException(
                            f"角色 {uid} 签到失败：{response.json().get('message')}"
                        )
                    elif status == 10002:
                        raise LoginException(
                            f"角色 {uid} 签到失败：{response.json().get('message')}"
                        )
                    elif status != 0:
                        raise RequestException(
                            f"角色 {uid} 签到失败：{response.json().get('message')}"
                        )
            except httpx.HTTPError as e:
                raise RequestException(f"角色 {uid} 签到失败: {e}")
            return ArkSignResponse(**response.json()["data"])

    @classmethod
    async def get_user_ID(cls, cred: CRED) -> str:
        uid_url = f"{base_url}/user/teenager"
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    uid_url,
                    headers=await cls.get_sign_header(cred, uid_url, method="get"),
                )
                if status := response.json().get("code"):
                    if status == 10000:
                        raise UnauthorizedException(
                            f"获取账号 userId 失败：{response.json().get('message')}"
                        )
                    elif status == 10002:
                        raise LoginException(
                            f"获取账号 userId 失败：{response.json().get('message')}"
                        )
                    if status != 0:
                        raise RequestException(
                            f"获取账号 userId 失败：{response.json().get('message')}"
                        )
                return response.json()["data"]["teenager"]["userId"]
            except httpx.HTTPError as e:
                raise RequestException(f"获取账号 userId 失败: {e}")

    @classmethod
    async def ark_card(cls, cred: CRED, uid: str) -> ArkCard:
        """获取明日方舟角色信息"""
        return ArkCard(**await cls.ark_card_raw(cred, uid))

    @classmethod
    async def ark_card_raw(cls, cred: CRED, uid: str) -> dict:
        """Fetch JSON without resource, rendering, or display-model validation."""
        game_info_url = f"{base_url}/game/player/info?{urlencode({'uid': uid})}"
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    game_info_url,
                    headers=await cls.get_sign_header(
                        cred, game_info_url, method="get"
                    ),
                )
                # Preserve authentication errors before raise_for_status turns
                # HTTP 401 into a generic request failure that cannot refresh.
                try:
                    payload = response.json()
                except ValueError:
                    payload = None
                status = payload.get("code") if isinstance(payload, dict) else None
                if status == 10002:
                    raise LoginException("森空岛登录凭证已失效，需要重新获取认证。")
                if response.status_code == 401 or status == 10000:
                    raise UnauthorizedException("森空岛认证已失效，需要刷新签名 token。")
                response.raise_for_status()
                if not isinstance(payload, dict):
                    raise RequestException("森空岛返回的角色数据格式无效")
                if status:
                    if not isinstance(status, int):
                        raise RequestException("森空岛返回的角色错误码格式无效")
                    raise RequestException(f"获取角色数据失败（森空岛错误码 {status}）")
                data = payload.get("data")
                if not isinstance(data, dict):
                    raise RequestException("角色数据格式无效")
                return data
            except httpx.HTTPStatusError as e:
                raise RequestException(f"获取角色数据失败（HTTP {e.response.status_code}），请稍后重试。") from e
            except httpx.HTTPError as e:
                raise RequestException(f"获取角色数据失败：网络连接异常（{type(e).__name__}），请稍后重试。") from e

    @classmethod
    async def get_rogue(cls, cred: CRED, uid: str, topic_id: str) -> RogueData:
        """获取肉鸽数据"""
        rogue_url = f"{base_url}/game/arknights/rogue?uid={uid}&targetUserId={cred.userId}&topicId={topic_id}"
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    rogue_url,
                    headers=await cls.get_sign_header(cred, rogue_url, method="get"),
                )
                if status := response.json().get("code"):
                    if status == 10000:
                        raise UnauthorizedException(
                            f"获取肉鸽数据失败：{response.json().get('message')}"
                        )
                    elif status == 10002:
                        raise LoginException(
                            f"获取肉鸽数据失败：{response.json().get('message')}"
                        )
                    if status != 0:
                        raise RequestException(
                            f"获取肉鸽数据失败：{response.json().get('message')}"
                        )
                return RogueData(**response.json()["data"])
            except httpx.HTTPError as e:
                raise RequestException(f"获取肉鸽数据失败: {e}") from e

    @classmethod
    async def garrison_history(cls, cred: CRED, uid: str) -> GarrisonHistory:
        """Use the official battle-history API, retaining promoted equipment IDs."""
        if not uid:
            raise RequestException("缺少明日方舟角色 UID，请先使用“角色更新”")
        query = urlencode({"activityId": "act3autochess", "uid": uid})
        request_url = f"{base_url.removesuffix('/v1')}/ms/game/arknights/garrison/info?{query}"
        extra_headers = {
            "x-transformed": "base64", "Origin": "https://act.skland.com",
            "Referer": "https://act.skland.com/arknights/auto-chess?activityId=act3autochess",
        }
        async with httpx.AsyncClient() as client:
            try:
                for attempt in range(2):
                    sign_options = {"method": "get", "battle_profile": True}
                    timestamp = server_clock.BATTLE_CLOCK.now()
                    if timestamp is not None:
                        sign_options["timestamp_override"] = timestamp
                    headers = await cls.get_sign_header(cred, request_url, **sign_options)
                    response = await client.get(request_url, headers={**headers, **extra_headers})
                    try:
                        payload = response.json()
                    except ValueError as exc:
                        if response.status_code == 401:
                            raise UnauthorizedException("获取卫戍协议战绩失败：森空岛认证已失效") from exc
                        response.raise_for_status()
                        raise RequestException("卫戍协议战绩响应不是有效 JSON") from exc
                    if not isinstance(payload, dict):
                        raise RequestException("卫戍协议战绩响应格式无效")
                    calibrated = server_clock.BATTLE_CLOCK.observe(payload, response.headers)
                    if payload.get("code") != 10003:
                        break
                    if attempt or not calibrated:
                        raise RequestException("获取卫戍协议战绩失败（错误码 10003）：请求时间校准失败，请稍后重试")
                code = payload.get("code")
                if code == 10002:
                    raise LoginException("获取卫戍协议战绩失败：森空岛登录已失效")
                if response.status_code == 401 or code == 10000:
                    raise UnauthorizedException("获取卫戍协议战绩失败：森空岛认证已失效")
                if code != 0:
                    detail = payload.get("message") or payload.get("msg")
                    if isinstance(detail, str):
                        for secret in (cred.cred, cred.token):
                            if secret:
                                detail = detail.replace(secret, "[已隐藏]")
                        detail = " ".join(detail.split())[:200]
                    else:
                        detail = ""
                    reason = f"错误码 {code}" if code is not None else "响应缺少错误码"
                    if response.is_error:
                        reason += f"，HTTP {response.status_code}"
                    raise RequestException(f"获取卫戍协议战绩失败（{reason}）" + (f"：{detail}" if detail else ""))
                response.raise_for_status()
                data = payload.get("data")
                # OneFetch on the official page decodes data.content when this
                # header is set. Accept plain JSON too, as some responses omit it.
                if isinstance(data, dict) and "content" in data:
                    try:
                        data = json.loads(b64decode(data["content"], validate=True).decode("utf-8"))
                    except (ValueError, TypeError, UnicodeError) as exc:
                        raise RequestException("卫戍协议战绩响应解码失败") from exc
                if not isinstance(data, dict) or "history" not in data:
                    raise RequestException("卫戍协议战绩响应缺少 history 数据")
                history = data["history"]
                if history is None:
                    return GarrisonHistory()
                if not isinstance(history, dict):
                    raise RequestException("卫戍协议战绩 history 格式无效")
                records = []
                seen = set()
                for field in ("records", "favourRecords"):
                    entries = history.get(field)
                    if entries is None:
                        continue
                    if not isinstance(entries, list):
                        raise RequestException("卫戍协议战绩记录列表格式无效")
                    for record in entries:
                        if not isinstance(record, dict):
                            raise RequestException("卫戍协议单局战绩格式无效")
                        key = record.get("id")
                        if not key or key not in seen:
                            records.append(record)
                        if key:
                            seen.add(key)
                return GarrisonHistory.model_validate({"records": records})
            except httpx.HTTPStatusError as exc:
                raise RequestException(f"获取卫戍协议战绩失败（HTTP {exc.response.status_code}）") from exc
            except httpx.HTTPError as exc:
                raise RequestException(f"获取卫戍协议战绩失败：{type(exc).__name__}") from exc
            except (TypeError, ValueError) as exc:
                raise RequestException("卫戍协议战绩数据格式无效") from exc

    @classmethod
    async def get_gacha_categories(
        cls, uid: str, role_token: str, token: str, ak_cookie: str
    ) -> list[GachaCate]:
        """获取卡池类别"""
        gacha_categories_url = (
            f"https://ak.hypergryph.com/user/api/inquiry/gacha/cate?uid={uid}"
        )
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    gacha_categories_url,
                    headers={"X-Account-Token": token, "X-Role-Token": role_token},
                    cookies={"ak-user-center": ak_cookie},
                )
                if status := response.json().get("code"):
                    if status == 10000:
                        raise UnauthorizedException(
                            f"获取抽卡类别失败：{response.json().get('message')}"
                        )
                    elif status == 10002:
                        raise LoginException(
                            f"获取抽卡类别失败：{response.json().get('message')}"
                        )
                    if status != 0:
                        raise RequestException(
                            f"获取抽卡类别失败：{response.json().get('message')}"
                        )
                return [GachaCate(**item) for item in response.json().get("data", [])]
            except httpx.HTTPError as e:
                raise RequestException(f"获取抽卡类别失败: {e}") from e

    @classmethod
    async def get_gacha_history(
        cls,
        uid: str,
        role_token: str,
        token: str,
        ak_cookie: str,
        category: str,
        size: int = 100,
        gachaTs: str | None = None,
        pos: int | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> GachaResponse:
        """获取明日方舟抽卡记录"""
        gacha_history_url = "https://ak.hypergryph.com/user/api/inquiry/gacha/history"
        query_params = {
            "uid": uid,
            "category": category,
            "size": size,
        }
        if gachaTs is not None and pos is not None:
            query_params["gachaTs"] = gachaTs
            query_params["pos"] = pos
        async with (
            httpx.AsyncClient()
            if client is None
            else contextlib.nullcontext(client) as client
        ):
            try:
                response = await client.get(
                    gacha_history_url,
                    headers={"X-Account-Token": token, "X-Role-Token": role_token},
                    cookies={"ak-user-center": ak_cookie},
                    params=query_params,
                )
                if status := response.json().get("code"):
                    if status == 10000:
                        raise UnauthorizedException(
                            f"获取抽卡记录失败：{response.json().get('message')}"
                        )
                    elif status == 10002:
                        raise LoginException(
                            f"获取抽卡记录失败：{response.json().get('message')}"
                        )
                    if status != 0:
                        raise RequestException(
                            f"获取抽卡记录失败：{response.json().get('message')}"
                        )
                return GachaResponse(**response.json()["data"])
            except httpx.HTTPError as e:
                raise RequestException(f"获取抽卡记录失败: {e}") from e

    @classmethod
    async def get_ef_gacha_history(
        cls,
        pool_type: EndfieldPoolType,
        server_id: str,
        role_token: str,
        seq_id: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> EfCharGachaResponse | EfWeaponGachaResponse:
        """获取终末地抽卡记录"""
        is_weapon = pool_type == EndfieldPoolType.WEAPON
        pool_url = "weapon" if is_weapon else "char"
        ef_gacha_url = f"https://ef-webview.hypergryph.com/api/record/{pool_url}"
        query_params: dict = {
            "token": role_token,
            "server_id": server_id,
            "lang": "zh-cn",
        }
        if not is_weapon:
            query_params["pool_type"] = pool_type.value
        if seq_id is not None:
            query_params["seq_id"] = seq_id
        async with (
            httpx.AsyncClient()
            if client is None
            else contextlib.nullcontext(client) as client
        ):
            try:
                response = await client.get(
                    ef_gacha_url,
                    params=query_params,
                )
                if status := response.json().get("code"):
                    if status == 10000:
                        raise UnauthorizedException(
                            f"获取终末地抽卡记录失败：{response.json().get('message')}"
                        )
                    elif status == 10002:
                        raise LoginException(
                            f"获取终末地抽卡记录失败：{response.json().get('message')}"
                        )
                    if status != 0:
                        raise RequestException(
                            f"获取终末地抽卡记录失败：{response.json().get('message')}"
                        )
                data = response.json()["data"]
                if is_weapon:
                    return EfWeaponGachaResponse(**data)
                return EfCharGachaResponse(**data)
            except httpx.HTTPError as e:
                raise RequestException(f"获取终末地抽卡记录失败: {e}") from e

    @classmethod
    async def get_ef_gacha_content(
        cls,
        pool_id: str,
        server_id: str,
    ) -> EfGachaContentResponse:
        """获取终末地卡池内容（UP角色信息）

        Args:
            pool_id: 卡池ID（如 special_xxx）。
            server_id: 服务器ID。
        """
        content_url = "https://ef-webview.hypergryph.com/api/content"
        query_params = {
            "pool_id": pool_id,
            "server_id": server_id,
            "lang": "zh-cn",
        }
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(content_url, params=query_params)
                resp_json = response.json()
                if status := resp_json.get("code"):
                    if status != 0:
                        raise RequestException(
                            f"获取终末地卡池内容失败：{resp_json.get('message')}"
                        )
                return EfGachaContentResponse(**resp_json["data"])
            except httpx.HTTPError as e:
                raise RequestException(f"获取终末地卡池内容失败: {e}") from e

    @classmethod
    async def endfield_sign(
        cls, cred: CRED, role_id: str, server_id: str
    ) -> EndfieldSignResponse:
        """进行明日方舟：终末地签到"""
        sign_url = "https://zonai.skland.com/web/v1/game/endfield/attendance"
        headers = await cls.get_sign_header(
            cred,
            sign_url,
            method="post",
            query_body=None,
        )
        game_role = f"3_{role_id}_{server_id}"
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    sign_url,
                    headers={
                        **headers,
                        "Content-Type": "application/json",
                        "sk-game-role": game_role,
                    },
                )
                logger.debug(f"终末地签到回复：{response.json()}")
                if status := response.json().get("code"):
                    if status == 10000:
                        raise UnauthorizedException(
                            f"角色 {role_id} 终末地签到失败：{response.json().get('message')}"
                        )
                    elif status == 10002:
                        raise LoginException(
                            f"角色 {role_id} 终末地签到失败：{response.json().get('message')}"
                        )
                    elif status != 0:
                        raise RequestException(
                            f"角色 {role_id} 终末地签到失败：{response.json().get('message')}"
                        )
            except httpx.HTTPError as e:
                raise RequestException(f"角色 {role_id} 终末地签到失败: {e}") from e
            return EndfieldSignResponse(**response.json()["data"])

    @classmethod
    async def endfield_card(cls, cred: CRED, uid: str, char: Character) -> EndfieldCard:
        """获取终末地角色信息"""
        game_info_url = f"https://zonai.skland.com/web/v1/game/endfield/card/detail?roleId={char.role_id}&serverId={char.channel_master_id}&userId={uid}"
        headers = await cls.get_sign_header(
            cred,
            game_info_url,
            method="get",
        )
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    game_info_url,
                    headers=headers,
                )
                if status := response.json().get("code"):
                    if status == 10000:
                        raise UnauthorizedException(
                            f"获取终末地角色卡片失败：{response.json().get('message')}"
                        )
                    elif status == 10002:
                        raise LoginException(
                            f"获取终末地角色卡片失败：{response.json().get('message')}"
                        )
                    if status != 0:
                        raise RequestException(
                            f"获取终末地角色卡片失败：{response.json().get('message')}"
                        )
                return EndfieldCard(**response.json()["data"]["detail"])
            except httpx.HTTPError as e:
                raise RequestException(f"获取终末地角色卡片失败: {e}") from e


    @classmethod
    async def endfield_war_echoes(
        cls,
        cred: CRED,
        *,
        user_id: str,
        role_id: str,
        server_id: str,
        season_id: str | int | None = None,
    ) -> WarEchoes:
        """Fetch Endfield War Echoes data using scalar account identity."""
        query = {
            "roleId": role_id,
            "serverId": server_id,
            "userId": user_id,
        }
        if season_id is not None:
            query["seasonId"] = str(season_id)
        request_url = f"{base_url}/game/endfield/card/war-echoes?{urlencode(query)}"
        headers = await cls.get_sign_header(cred, request_url, method="get")
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(request_url, headers=headers)
                data = response.json()
                if status := data.get("code"):
                    if status == 10000:
                        raise UnauthorizedException(f"获取终末地战争回响失败：{data.get('message')}")
                    if status == 10002:
                        raise LoginException(f"获取终末地战争回响失败：{data.get('message')}")
                    raise RequestException(f"获取终末地战争回响失败：{data.get('message')}")
                return WarEchoes(**data["data"]["warEchoes"])
            except httpx.HTTPError as e:
                raise RequestException(f"获取终末地战争回响失败: {e}") from e
