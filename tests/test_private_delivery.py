from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from skland import qq_message
from skland.qq_message import PrivateDeliveryError, send_private_text, send_with_receipt
from plugin_harness import At, Event, Image, MessageChain, Plain


@pytest.fixture
def boundary(monkeypatch):
    definitions = {
        "astrbot": {}, "astrbot.api": {},
        "astrbot.api.event": {"MessageChain": MessageChain},
        "astrbot.api.message_components": {"At": At, "Image": Image, "Plain": Plain},
    }
    import sys
    for name, members in definitions.items():
        module = ModuleType(name)
        module.__dict__.update(members)
        module.__path__ = []
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(qq_message, "_make_route", lambda method, path, **parameters:
        SimpleNamespace(method=method, path=path, parameters=parameters, url=path.format(**parameters)))


def context(name, *, response=None, error=None):
    http = SimpleNamespace(request=AsyncMock(return_value=response, side_effect=error))
    platform = SimpleNamespace(meta=lambda: SimpleNamespace(name=name), client=SimpleNamespace(api=SimpleNamespace(_http=http)))
    ctx = SimpleNamespace(get_platform_inst=Mock(return_value=platform), send_message=AsyncMock(return_value=True))
    return ctx, http


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["qq_official", "qq_official_webhook"])
async def test_official_private_delivery_uses_proactive_endpoint_and_receipt(boundary, name):
    ctx, http = context(name, response={"id": "actual-response"})
    await send_private_text(ctx, "instance:FriendMessage:user-openid", "理智快满了")
    route = http.request.await_args.args[0]
    assert route.method == "POST" and route.path == "/v2/users/{openid}/messages"
    assert route.parameters == {"openid": "user-openid"}
    payload = http.request.await_args.kwargs["json"]
    assert payload["msg_type"] == 0 and payload["content"] == "理智快满了"
    assert "msg_id" not in payload and "event_id" not in payload
    ctx.send_message.assert_not_awaited()
    ctx.get_platform_inst.assert_called_once_with("instance")


@pytest.mark.asyncio
@pytest.mark.parametrize("response,blocked", [
    (None, False), ({}, False), ({"code": 40054004, "message": "sensitive"}, True),
    ({"code": 40054013}, True), ({"code": 40034105}, True), ({"code": 40034100}, False),
    ({"code": 40054004, "id": "misleading"}, True),
])
async def test_errors_or_missing_receipts_never_count_as_success(boundary, response, blocked):
    ctx, _ = context("qq_official", response=response)
    with pytest.raises(PrivateDeliveryError) as error:
        await send_private_text(ctx, "bot:FriendMessage:u", "reminder")
    assert error.value.blocked == blocked
    assert "sensitive" not in str(error.value)


@pytest.mark.asyncio
async def test_structured_sdk_error_is_sanitized(boundary):
    error = RuntimeError({"code": 40054004, "message": "private-token"})
    ctx, _ = context("qq_official", error=error)
    with pytest.raises(PrivateDeliveryError) as raised:
        await send_private_text(ctx, "bot:FriendMessage:u", "reminder")
    assert raised.value.blocked and "private-token" not in str(raised.value)


class Response:
    def __init__(self, status, data):
        self.status, self.data = status, data
    async def json(self, **kwargs):
        return self.data
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        return False


@pytest.mark.asyncio
@pytest.mark.parametrize("status,data,blocked", [
    (403, {"code": 40054004, "message": "raw-sensitive-message"}, True),
    (403, {"code": 40054013}, True),
    (429, {"code": 40034100}, False),
    (200, {"id": "delivered"}, None),
    (500, {"id": "not-a-success"}, False),
])
async def test_native_botpy_http_response_retains_business_error_code(boundary, status, data, blocked):
    ctx, http = context("qq_official")
    http.check_session = AsyncMock()
    http._headers = {"Authorization": "secret"}
    http.is_sandbox = True
    http._session = SimpleNamespace(request=Mock(return_value=Response(status, data)))
    if blocked is None:
        await send_private_text(ctx, "bot:FriendMessage:u", "reminder")
    else:
        with pytest.raises(PrivateDeliveryError) as error:
            await send_private_text(ctx, "bot:FriendMessage:u", "reminder")
        assert error.value.blocked == blocked and "raw-sensitive" not in str(error.value)
    http.check_session.assert_awaited_once()
    http.request.assert_not_awaited()
    assert http._session.request.call_args.kwargs["headers"] == http._headers


@pytest.mark.asyncio
@pytest.mark.parametrize("session", ["instance:GroupMessage:g", "bad", ":FriendMessage:u", "bot:FriendMessage:"])
async def test_reminders_cannot_be_routed_to_a_group(boundary, session):
    ctx, http = context("qq_official", response={"id": "sent"})
    with pytest.raises(PrivateDeliveryError):
        await send_private_text(ctx, session, "reminder")
    http.request.assert_not_awaited()
    ctx.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_onebot_uses_private_session_and_missing_platform_is_failure(boundary):
    ctx, _ = context("aiocqhttp")
    await send_private_text(ctx, "bot:FriendMessage:123", "test")
    args = ctx.send_message.await_args.args
    assert args[0] == "bot:FriendMessage:123" and args[1].chain[0].text == "test"
    ctx.send_message.return_value = False
    with pytest.raises(PrivateDeliveryError):
        await send_private_text(ctx, "bot:FriendMessage:123", "test")
    ctx.get_platform_inst.return_value = None
    with pytest.raises(PrivateDeliveryError):
        await send_private_text(ctx, "bot:FriendMessage:123", "test")


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["qq_official", "qq_official_webhook"])
async def test_qq_group_prompt_uses_markdown_and_keeps_qrcode_receipt(boundary, name):
    event = Event(owner="member-openid", name=name, group="group-openid")
    http = SimpleNamespace(request=AsyncMock(return_value={"id": "mention-message"}))
    event.bot = SimpleNamespace(api=SimpleNamespace(_http=http))
    event.message_obj.raw_message = SimpleNamespace(group_openid="group-openid")
    event.send_buffer = None
    sent = []
    async def post():
        sent.append(event.send_buffer)
        event.send_buffer = None
        return {"id": "qr-message"}
    event._post_send = post
    original = MessageChain([At(qq="member-openid"), Plain(" 请扫码"), Image.fromBytes(b"png")])
    original.use_markdown_ = False
    receipt = await send_with_receipt(event, original)
    assert receipt.message_id == "qr-message" and receipt.group_openid == "group-openid"
    prompt = http.request.await_args.kwargs["json"]
    assert prompt["msg_type"] == 2 and prompt["markdown"]["content"] == '<qqbot-at-user id="member-openid" /> 请扫码'
    assert prompt["msg_id"] == "incoming" and prompt["msg_seq"] > 10000
    assert "content" not in prompt and "media" not in prompt
    assert http.request.await_args.args[0].path == "/v2/groups/{group_openid}/messages"
    # The image becomes msg_type=7 in AstrBot: no mention tag can be in its text.
    assert not any(isinstance(item, At) for item in sent[0].chain)
    assert not any(isinstance(item, Plain) for item in sent[0].chain)
    assert sent[0].chain[-1].data == b"png" and sent[0].use_markdown_ is False
    assert isinstance(original.chain[0], At)
    await receipt.recall()
    assert http.request.await_args.args[0].path == "/v2/groups/{group_openid}/messages/{message_id}"
    assert http.request.await_args.args[0].parameters["message_id"] == "qr-message"


@pytest.mark.asyncio
async def test_qq_fallback_public_sender_gets_image_after_markdown_prompt(boundary):
    event = Event(name="qq_official", group="g")
    http = SimpleNamespace(request=AsyncMock(return_value={"id": "prompt"}))
    event.bot = SimpleNamespace(api=SimpleNamespace(_http=http))
    await send_with_receipt(event, MessageChain([At(qq="u"), Plain("scan"), Image.fromBytes(b"qr")]))
    chain = event.send.await_args.args[0].chain
    assert len(chain) == 1 and isinstance(chain[0], Image)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["no-permission", "no-receipt", "error-response"])
async def test_failed_markdown_prompt_never_exposes_tag_or_prevents_qrcode(boundary, failure):
    event = Event(name="qq_official", group="g")
    http = SimpleNamespace(request=AsyncMock(return_value=None))
    event.bot = SimpleNamespace(api=SimpleNamespace(_http=http))
    if failure == "no-permission":
        http.request.side_effect = RuntimeError("不允许发送原生 markdown")
    elif failure == "error-response":
        http.request.return_value = {"code": 304061, "id": "not-success"}
    await send_with_receipt(event, MessageChain([At(qq="u"), Plain(" 请扫码"), Image.fromBytes(b"qr")]))
    chain = event.send.await_args.args[0].chain
    assert len(chain) == 2 and chain[0].text == " 请扫码" and chain[1].data == b"qr"
    assert not any(isinstance(item, At) for item in chain)


@pytest.mark.asyncio
async def test_private_qrcode_keeps_single_original_message(boundary):
    event = Event(name="qq_official")
    http = SimpleNamespace(request=AsyncMock())
    event.bot = SimpleNamespace(api=SimpleNamespace(_http=http))
    original = MessageChain([Plain("请扫码"), Image.fromBytes(b"qr")])
    await send_with_receipt(event, original)
    http.request.assert_not_awaited()
    assert event.send.await_args.args[0] is original


@pytest.mark.asyncio
async def test_other_platforms_keep_native_at(boundary):
    event = Event(name="other", group="g")
    message = MessageChain([At(qq="u"), Plain("scan")])
    await send_with_receipt(event, message)
    assert event.send.await_args.args[0] is message
