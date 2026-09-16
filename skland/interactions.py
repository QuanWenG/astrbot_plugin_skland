"""AstrBot session waiting and platform capabilities, outside domain services."""
import asyncio
import logging
from contextlib import suppress

logger = logging.getLogger("astrbot")


async def wait_choice(event, choices, prompt=None, *, timeout=60, tasks=None):
    from astrbot.core.utils.session_waiter import SessionFilter, session_waiter
    from astrbot.api.event import MessageChain
    from astrbot.api.message_components import Plain

    class OwnerSessionFilter(SessionFilter):
        def filter(self, current):
            return f"skland:{current.unified_msg_origin}:{current.get_sender_id()}"

    result = None
    attempts = 0

    @session_waiter(timeout=timeout, record_history_chains=False)
    async def receive(controller, current):
        nonlocal result, attempts
        value = current.get_message_str().strip()
        current.stop_event()
        if value in choices:
            result = value
            controller.stop()
        else:
            attempts += 1
            if attempts >= 3:
                controller.stop()
            else:
                await current.send(MessageChain([Plain("请输入：" + " / ".join(choices))]))

    task = asyncio.create_task(receive(event, OwnerSessionFilter()))
    if tasks is not None:
        tasks.add(task)
    try:
        await asyncio.sleep(0)
        if prompt:
            await event.send(MessageChain([Plain(prompt)]) if isinstance(prompt, str) else prompt)
        try:
            await task
        except TimeoutError:
            return None
        return result
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError, TimeoutError):
            await task
        if tasks is not None:
            tasks.discard(task)


async def react(event, state):
    """The base AstrBot react sends text, so only use native implementations."""
    try:
        from astrbot.api.event import AstrMessageEvent
        if type(event).react is not AstrMessageEvent.react:
            await event.react({"processing": "❤", "done": "🎉", "fail": "❌"}[state])
        elif event.get_platform_name() == "aiocqhttp":
            await event.bot.call_action("set_msg_emoji_like", message_id=event.message_obj.message_id,
                emoji_id={"processing": "66", "done": "144", "fail": "10060"}[state])
    except Exception as exc:
        logger.debug("平台未提供消息反应：%s", type(exc).__name__)


async def send_images(event, images, *, prepare, title="森空岛"):
    from astrbot.api.event import MessageChain
    from astrbot.api.message_components import Image, Node, Nodes
    images = [prepare(event.get_platform_name(), content, "card") for content in images]
    if len(images) > 1 and event.get_platform_name() in {"aiocqhttp", "satori"}:
        nodes = [Node(uin=event.get_self_id(), name=f"{title} · 第 {i} 页", content=[Image.fromBytes(content)])
                 for i, content in enumerate(images, 1)]
        try:
            await event.send(MessageChain([Nodes(nodes)]))
            return
        except Exception as exc:
            logger.warning("合并转发失败，改为逐图发送：%s", type(exc).__name__)
    for content in images:
        await event.send(MessageChain([Image.fromBytes(content)]))
