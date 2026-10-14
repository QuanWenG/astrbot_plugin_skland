from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from html import escape
from typing import Any


logger = logging.getLogger(__name__)
QQ_OFFICIAL_PLATFORMS = {"qq_official", "qq_official_webhook"}


class PrivateDeliveryError(ValueError):
    def __init__(self, message: str, *, blocked: bool = False):
        super().__init__(message)
        self.blocked = blocked


def _private_error(code) -> PrivateDeliveryError:
    try:
        code = int(code)
    except (ValueError, TypeError):
        code = None
    if code == 40054004:
        return PrivateDeliveryError("无法发送私信：请先将 Bot 添加到私聊，再执行 /理智提醒测试。", blocked=True)
    if code in {40054013, 40034105}:
        return PrivateDeliveryError("私信被拒绝或无主动消息权限：请开启允许主动发送，再执行 /理智提醒测试。", blocked=True)
    if code == 40034100:
        return PrivateDeliveryError("私信发送达到平台频率限制，将稍后重试。")
    return PrivateDeliveryError("私信发送失败，请检查 Bot 私聊设置或稍后重试。")


def _exception_code(exc):
    code = getattr(exc, "code", None) or getattr(exc, "error_code", None)
    if code is not None:
        return code
    for payload in (getattr(exc, "data", None), *exc.args):
        if isinstance(payload, dict):
            return payload.get("code")
    return None


async def _qq_private_request(http, route, payload):
    # botpy's response handler drops QQ's business error code when raising an
    # HTTP error. Reuse its authenticated session but read the response here so
    # "no friend relationship" and "user refused" can pause delivery reliably.
    check_session = getattr(http, "check_session", None)
    if not callable(check_session):
        return await http.request(route, json=payload)
    await check_session()
    route.is_sandbox = getattr(http, "is_sandbox", False)
    async with http._session.request(method=route.method, url=route.url,
                                     headers=http._headers, json=payload) as response:
        try:
            result = await response.json(content_type=None)
        except (ValueError, TypeError):
            raise _private_error(None) from None
        if response.status not in {200, 201, 202}:
            raise _private_error(result.get("code") if isinstance(result, dict) else None)
        return result


async def send_private_text(context, private_umo: str, content: str) -> None:
    parts = private_umo.split(":", 2)
    if len(parts) != 3 or parts[1] != "FriendMessage" or not parts[0] or not parts[2]:
        raise PrivateDeliveryError("私聊目标无效，请在私聊执行 /理智提醒测试。", blocked=True)
    platform_id, _, recipient = parts
    platform = context.get_platform_inst(platform_id)
    if platform is None:
        raise PrivateDeliveryError("平台实例未连接，将稍后重试。")
    if platform.meta().name in QQ_OFFICIAL_PLATFORMS:
        client = getattr(platform, "client", None)
        if client is None:
            raise PrivateDeliveryError("QQ 官 Bot 尚未连接，将稍后重试。")
        route = _make_route("POST", "/v2/users/{openid}/messages", openid=recipient)
        try:
            response = await _qq_private_request(client.api._http, route, {
                "msg_type": 0, "content": content, "msg_seq": secrets.randbelow(1_000_000_000) + 1,
            })
        except PrivateDeliveryError:
            raise
        except Exception as exc:
            raise _private_error(_exception_code(exc)) from None
        if isinstance(response, dict) and response.get("code"):
            raise _private_error(response["code"])
        if not _response_message_id(response):
            raise _private_error(None)
        return
    from astrbot.api.event import MessageChain
    from astrbot.api.message_components import Plain
    try:
        found = await context.send_message(private_umo, MessageChain([Plain(content)]))
    except Exception:
        raise _private_error(None) from None
    if not found:
        raise PrivateDeliveryError("未找到可投递的私聊平台，将稍后重试。")


def _qq_mentions(event, message_chain):
    """QQ's serializer ignores At; preserve mentions in the text payload."""
    components = getattr(message_chain, "chain", ())
    if not any(hasattr(item, "qq") for item in components):
        return message_chain
    from astrbot.api.event import MessageChain
    from astrbot.api.message_components import At, Plain
    source = getattr(getattr(event, "message_obj", None), "raw_message", None)
    channel = getattr(source, "channel_id", None) and not getattr(source, "group_openid", None)
    converted = []
    for item in components:
        if isinstance(item, At) and str(item.qq) != "all":
            user_id = escape(str(item.qq), quote=True)
            converted.append(Plain(f"<@!{user_id}>" if channel else f'<qqbot-at-user id="{user_id}" />'))
        else:
            converted.append(item)
    if callable(getattr(message_chain, "derive", None)):
        return message_chain.derive(converted)
    result = MessageChain(converted)
    for key in ("use_markdown_", "use_t2i_", "type"):
        if hasattr(message_chain, key):
            setattr(result, key, getattr(message_chain, key))
    return result


async def _qq_group_media_prompt(event, message_chain):
    """Keep group mentions out of msg_type=7, which displays them literally."""
    components = getattr(message_chain, "chain", ())
    if not any(hasattr(item, "qq") for item in components):
        return message_chain
    source = getattr(getattr(event, "message_obj", None), "raw_message", None)
    if getattr(source, "channel_id", None) and not getattr(source, "group_openid", None):
        return message_chain
    group_id = getattr(source, "group_openid", None)
    if not group_id and callable(getattr(event, "get_group_id", None)):
        group_id = event.get_group_id()
    if not group_id:
        return message_chain
    from astrbot.api.event import MessageChain
    from astrbot.api.message_components import At, Image, Plain
    if not any(isinstance(item, At) for item in components) or not any(isinstance(item, Image) for item in components):
        return message_chain
    prompt = _qq_mentions(event, MessageChain([item for item in components if isinstance(item, (At, Plain))]))
    content = "".join(item.text for item in prompt.chain if isinstance(item, Plain))
    payload = {"msg_type": 2, "markdown": {"content": content},
               "msg_seq": 10001 + secrets.randbelow(1_000_000)}
    message_id = getattr(event.message_obj, "message_id", None)
    if message_id:
        payload["msg_id"] = str(message_id)
    sent = False
    try:
        route = _make_route("POST", "/v2/groups/{group_openid}/messages", group_openid=str(group_id))
        # Bypass AstrBot's automatic Markdown-to-text fallback: it would expose
        # the mention tag again. The image still uses its original sender.
        response = await event.bot.api._http.request(route, json=payload)
        sent = bool(_response_message_id(response)) and not (
            isinstance(response, dict) and response.get("code")
        )
        if not sent:
            logger.warning("QQ 官方 Markdown @ 提示未取得成功回执；二维码使用普通说明。")
    except Exception as exc:
        logger.warning("QQ 官方 Markdown @ 提示发送失败（%s）；二维码使用无标签普通说明，请检查 Markdown 权限。", type(exc).__name__)
    # When the prompt fails, retain its instructions but remove all At tags.
    remaining = [item for item in components if not isinstance(item, At) and not (sent and isinstance(item, Plain))]
    if callable(getattr(message_chain, "derive", None)):
        return message_chain.derive(remaining)
    result = MessageChain(remaining)
    for key in ("use_markdown_", "use_t2i_", "type"):
        if hasattr(message_chain, key):
            setattr(result, key, getattr(message_chain, key))
    return result


def _make_route(method: str, path: str, **parameters: str):
    # botpy is provided by AstrBot and imported lazily so the portable core
    # remains importable without a QQ adapter installed.
    from botpy.http import Route

    return Route(method, path, **parameters)


@dataclass(frozen=True, slots=True)
class QQMessageReceipt:
    bot: Any
    message_id: str
    group_openid: str | None = None
    user_openid: str | None = None

    async def recall(self) -> bool:
        """Recall the recorded QQ Official message without exposing payloads."""
        try:
            if self.group_openid:
                route = _make_route(
                    "DELETE",
                    "/v2/groups/{group_openid}/messages/{message_id}",
                    group_openid=self.group_openid,
                    message_id=self.message_id,
                )
            elif self.user_openid:
                route = _make_route(
                    "DELETE",
                    "/v2/users/{openid}/messages/{message_id}",
                    openid=self.user_openid,
                    message_id=self.message_id,
                )
            else:
                return False
            await self.bot.api._http.request(route)
            return True
        except Exception as exc:
            logger.warning("QQ 官方二维码消息撤回失败: %s", exc)
            return False


@dataclass(frozen=True, slots=True)
class OneBotMessageReceipt:
    bot: Any
    message_id: str

    async def recall(self) -> bool:
        try:
            await self.bot.call_action("delete_msg", message_id=int(self.message_id))
            return True
        except Exception as exc:
            logger.warning("OneBot 消息撤回失败：%s", type(exc).__name__)
            return False


def _response_message_id(response: Any) -> str | None:
    if isinstance(response, dict):
        value = response.get("id")
    else:
        value = getattr(response, "id", None)
    return str(value) if value else None


def _receipt_from_event(event: Any, response: Any) -> QQMessageReceipt | None:
    message_id = _response_message_id(response)
    bot = getattr(event, "bot", None)
    source = getattr(getattr(event, "message_obj", None), "raw_message", None)
    if not message_id or bot is None or source is None:
        return None

    group_openid = str(getattr(source, "group_openid", "") or "") or None
    author = getattr(source, "author", None)
    user_openid = str(getattr(author, "user_openid", "") or "") or None
    if not group_openid and not user_openid:
        return None
    return QQMessageReceipt(
        bot=bot,
        message_id=message_id,
        group_openid=group_openid,
        user_openid=None if group_openid else user_openid,
    )


async def send_with_receipt(event: Any, message_chain: Any) -> QQMessageReceipt | OneBotMessageReceipt | None:
    """Send a QQ message while preserving the response ID for later recall.

    AstrBot 4.26's public ``event.send`` method returns ``None``. Its QQ event
    sender does return the qq-botpy response, so this small compatibility layer
    uses that path when available and falls back to the public API otherwise.
    """
    platform = event.get_platform_name() if hasattr(event, "get_platform_name") else "qq_official"
    if platform == "aiocqhttp":
        bot = getattr(event, "bot", None)
        parse = getattr(event, "_parse_onebot_json", None)
        if bot is not None and callable(parse):
            content = await parse(message_chain)
            group_id = event.get_group_id()
            route = {"group_id": int(group_id)} if group_id else {"user_id": int(event.get_sender_id())}
            response = await bot.call_action("send_group_msg" if group_id else "send_private_msg", message=content, **route)
            message_id = response.get("message_id") if isinstance(response, dict) else None
            return OneBotMessageReceipt(bot, str(message_id)) if message_id else None
    if platform not in QQ_OFFICIAL_PLATFORMS:
        await event.send(message_chain)
        return None
    message_chain = await _qq_group_media_prompt(event, message_chain)
    message_chain = _qq_mentions(event, message_chain)
    post_send = getattr(event, "_post_send", None)
    if not callable(post_send) or not hasattr(event, "send_buffer"):
        await event.send(message_chain)
        logger.warning("当前 AstrBot QQ 适配器不提供消息回执，二维码将无法自动撤回")
        return None

    event.send_buffer = message_chain
    try:
        response = await post_send()
    except Exception:
        event.send_buffer = None
        raise
    receipt = _receipt_from_event(event, response)
    if receipt is None:
        logger.warning("QQ 官方二维码发送成功但未取得消息 ID，无法自动撤回")
    return receipt
