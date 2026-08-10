import asyncio
from types import SimpleNamespace

from skland import qq_message


class FakeHTTP:
    def __init__(self):
        self.routes = []

    async def request(self, route):
        self.routes.append(route)


class FakeEvent:
    def __init__(self, source, response):
        self.bot = SimpleNamespace(api=SimpleNamespace(_http=FakeHTTP()))
        self.message_obj = SimpleNamespace(raw_message=source)
        self.send_buffer = None
        self.response = response

    async def _post_send(self):
        self.send_buffer = None
        return self.response


def test_group_qrcode_message_is_recalled_from_its_response_id(monkeypatch):
    routes = []
    monkeypatch.setattr(
        qq_message,
        "_make_route",
        lambda method, path, **parameters: routes.append(
            (method, path, parameters)
        )
        or routes[-1],
    )
    event = FakeEvent(SimpleNamespace(group_openid="group-1"), {"id": "message-1"})

    receipt = asyncio.run(qq_message.send_with_receipt(event, object()))
    assert receipt is not None
    assert asyncio.run(receipt.recall()) is True
    assert routes == [
        (
            "DELETE",
            "/v2/groups/{group_openid}/messages/{message_id}",
            {"group_openid": "group-1", "message_id": "message-1"},
        )
    ]


def test_c2c_qrcode_message_uses_user_recall_route(monkeypatch):
    routes = []
    monkeypatch.setattr(
        qq_message,
        "_make_route",
        lambda method, path, **parameters: routes.append(
            (method, path, parameters)
        )
        or routes[-1],
    )
    source = SimpleNamespace(
        group_openid=None,
        author=SimpleNamespace(user_openid="user-1"),
    )
    event = FakeEvent(source, SimpleNamespace(id="message-2"))

    receipt = asyncio.run(qq_message.send_with_receipt(event, object()))
    assert receipt is not None
    assert asyncio.run(receipt.recall()) is True
    assert routes[0][1] == "/v2/users/{openid}/messages/{message_id}"
    assert routes[0][2] == {"openid": "user-1", "message_id": "message-2"}


def test_missing_message_id_disables_recall():
    event = FakeEvent(SimpleNamespace(group_openid="group-1"), None)
    assert asyncio.run(qq_message.send_with_receipt(event, object())) is None
