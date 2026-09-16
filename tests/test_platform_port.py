import asyncio
import sys
from types import SimpleNamespace, ModuleType
from unittest.mock import AsyncMock

import pytest

from skland.interactions import wait_choice, send_images, react
from skland.qq_message import send_with_receipt
from skland.exception import RequestException
from skland.store import SklandStore
from plugin_harness import Event, Plain, Image, MessageChain, make_plugin, run


@pytest.fixture
def astrbot_boundary(monkeypatch):
    sessions = {}
    class SessionFilter:
        pass
    def session_waiter(*, timeout, record_history_chains):
        def decorate(callback):
            async def wait(event, session_filter):
                key = session_filter.filter(event)
                future = asyncio.get_running_loop().create_future()
                controller = SimpleNamespace(stop=lambda: future.set_result(None) if not future.done() else None)
                sessions[key] = (session_filter, callback, controller)
                try:
                    await asyncio.wait_for(future, timeout)
                finally:
                    sessions.pop(key, None)
            return wait
        return decorate
    class BaseEvent:
        async def react(self, emoji):
            raise AssertionError("fallback text reaction must not be used")
    class Node:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)
    class Nodes:
        def __init__(self, nodes): self.nodes = nodes
    definitions = {
        "astrbot": {}, "astrbot.api": {}, "astrbot.core": {}, "astrbot.core.utils": {},
        "astrbot.api.event": {"MessageChain": MessageChain, "AstrMessageEvent": BaseEvent},
        "astrbot.api.message_components": {"Plain": Plain, "Image": Image, "Node": Node, "Nodes": Nodes},
        "astrbot.core.utils.session_waiter": {"SessionFilter": SessionFilter, "session_waiter": session_waiter},
    }
    for name, values in definitions.items():
        module = ModuleType(name)
        module.__dict__.update(values)
        module.__path__ = []
        monkeypatch.setitem(sys.modules, name, module)
    return sessions, BaseEvent


async def trigger(sessions, event):
    for key, (session_filter, callback, controller) in list(sessions.items()):
        if session_filter.filter(event) == key:
            await callback(controller, event)


@pytest.mark.asyncio
async def test_confirmation_waits_for_same_user_session_and_cleans_up(astrbot_boundary):
    sessions, _ = astrbot_boundary
    tasks = set()
    initial = Event(group="g")
    waiting = asyncio.create_task(wait_choice(initial, ("确认", "取消"), "prompt", timeout=1, tasks=tasks))
    for _ in range(3): await asyncio.sleep(0)
    assert initial.send.await_count == 1 and len(sessions) == 1
    for event in [Event(owner="other", group="g", message="确认"), Event(group="else", message="确认"), Event(platform="other", group="g", message="确认")]:
        await trigger(sessions, event)
    assert not waiting.done()
    await trigger(sessions, Event(group="g", message="确认"))
    assert await waiting == "确认"
    assert not sessions and not tasks
    assert await wait_choice(Event(), ("确认",), timeout=0.01, tasks=tasks) is None
    assert not sessions and not tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["cancel", "timeout", "success", "render-failure"])
async def test_qrcode_recall_on_every_exit(tmp_path, monkeypatch, mode):
    store = SklandStore(tmp_path / "db")
    await store.initialize()
    obj, ns = make_plugin(store)
    ns["react"] = AsyncMock()
    ns["fetch_user_avatar"] = AsyncMock(return_value=None)
    ns["render_qrcode_card"] = lambda *args: b"raw-png"
    receipt = SimpleNamespace(recall=AsyncMock(return_value=True))
    ns["send_with_receipt"] = AsyncMock(return_value=receipt)
    monkeypatch.setattr(ns["SklandLoginAPI"], "get_scan", AsyncMock(return_value="scan"))
    monkeypatch.setattr(ns["SklandLoginAPI"], "get_token_by_scan_code", AsyncMock(return_value="access"))
    if mode in {"cancel", "timeout"}:
        monkeypatch.setattr(ns["SklandLoginAPI"], "get_scan_status", AsyncMock(side_effect=RequestException("waiting")))
        ns["wait_choice"] = AsyncMock(return_value="取消" if mode == "cancel" else None)
    else:
        monkeypatch.setattr(ns["SklandLoginAPI"], "get_scan_status", AsyncMock(return_value="code"))
        async def pending(*args, **kwargs): await asyncio.Future()
        ns["wait_choice"] = pending
    obj._confirm_binding = AsyncMock(side_effect=ValueError("render failed") if mode == "render-failure" else None)
    await run(obj, Event(), ["qrcode"])
    receipt.recall.assert_awaited_once()
    assert not obj._interaction_tasks
    assert await store.list_accounts("bot:u") == []
    if mode in {"success", "render-failure"}:
        obj._confirm_binding.assert_awaited_once()
    else:
        obj._confirm_binding.assert_not_awaited()
    sent = ns["send_with_receipt"].await_args.args[1]
    assert sent.chain[1].data == b"raw-png"


@pytest.mark.asyncio
@pytest.mark.parametrize("group", ["123", ""])
async def test_onebot_receipt_uses_exact_message_for_recall(group):
    event = Event(owner="456", group=group, name="aiocqhttp")
    event.bot = SimpleNamespace(call_action=AsyncMock(return_value={"message_id": 999}))
    event._parse_onebot_json = AsyncMock(return_value=[{"type": "text", "data": {"text": "test"}}])
    receipt = await send_with_receipt(event, MessageChain([Plain("test")]))
    assert receipt.message_id == "999"
    route = "send_group_msg" if group else "send_private_msg"
    assert event.bot.call_action.await_args.args[0] == route
    assert await receipt.recall()
    event.bot.call_action.assert_awaited_with("delete_msg", message_id=999)


@pytest.mark.asyncio
async def test_merged_forward_and_unsupported_native_reaction(astrbot_boundary):
    _, base = astrbot_boundary
    event = Event(name="aiocqhttp")
    await send_images(event, [b"one", b"two"], prepare=lambda p, b, purpose: b)
    assert len(event.send.await_args.args[0].chain[0].nodes) == 2
    other = Event(name="other")
    await send_images(other, [b"one", b"two"], prepare=lambda p, b, purpose: b)
    assert other.send.await_count == 2
    class Unsupported(Event, base): pass
    unsupported = Unsupported()
    await react(unsupported, "done")
    unsupported.send.assert_not_awaited()
