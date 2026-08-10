from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any


logger = logging.getLogger(__name__)


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


async def send_with_receipt(event: Any, message_chain: Any) -> QQMessageReceipt | None:
    """Send a QQ message while preserving the response ID for later recall.

    AstrBot 4.26's public ``event.send`` method returns ``None``. Its QQ event
    sender does return the qq-botpy response, so this small compatibility layer
    uses that path when available and falls back to the public API otherwise.
    """
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
