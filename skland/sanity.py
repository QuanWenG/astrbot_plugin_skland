"""Fresh stamina snapshots and persistent, once-per-recovery private reminders."""
from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .qq_message import PrivateDeliveryError

logger = logging.getLogger("astrbot")
GAME_NAMES = {"arknights": "明日方舟", "endfield": "终末地"}
GAME_ALIASES = {"ark": "arknights", "arknights": "arknights", "方舟": "arknights", "明日方舟": "arknights",
                "ef": "endfield", "endfield": "endfield", "终末地": "endfield"}
PRIVATE_HINT = "请将 Bot 添加到私聊，并开启允许主动发送；可使用 /理智提醒测试 验证。"
DISPLAY_TIMEZONE = timezone(timedelta(hours=8))
DATA_ERROR_PREFIX = "理智数据获取失败"


@dataclass(frozen=True, slots=True)
class SanityState:
    current: int
    maximum: int
    full_at: float
    remaining_seconds: float


def _integer(value) -> int:
    if isinstance(value, bool):
        raise ValueError("理智数据格式异常")
    return int(str(value))


def _timestamp(value) -> float:
    timestamp = float(value)
    if not math.isfinite(timestamp) or timestamp <= 0:
        raise ValueError("恢复时间缺失或异常")
    # The API normally uses seconds; tolerate millisecond timestamps.
    return timestamp / 1000 if timestamp >= 1_000_000_000_000 else timestamp


def sanity_state(game: str, card, *, now: float) -> SanityState:
    if game == "arknights":
        ap = card.status.ap
        current, maximum = _integer(ap.current), _integer(ap.max)
    elif game == "endfield":
        current, maximum = _integer(card.dungeon.curStamina), _integer(card.dungeon.maxStamina)
    else:
        raise ValueError("不支持的游戏")
    if current < 0 or maximum <= 0:
        raise ValueError("理智数据缺失或异常")
    if current >= maximum:
        return SanityState(current, maximum, now, 0)
    if game == "arknights":
        full_at = _timestamp(ap.completeRecoveryTime)
        remaining = max(0, full_at - now)
        current = max(current, maximum - math.ceil(remaining / 360))
    else:
        # Anchor the API's remaining duration to local time to tolerate clock skew.
        remaining = max(0, _timestamp(card.dungeon.maxTs) - _timestamp(card.currentTs))
        full_at = now + remaining
        if remaining == 0:
            current = maximum
    return SanityState(current, maximum, full_at, remaining)


def format_sanity_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, DISPLAY_TIMEZONE).strftime("%m-%d %H:%M")


def sanity_details(state: SanityState) -> list[str]:
    minutes = math.ceil(max(0, state.remaining_seconds) / 60)
    return [f"当前理智：{state.current}/{state.maximum}",
            f"预计 {minutes} 分钟后满" if minutes else "距离满值：0 分钟（理智已满）",
            f"预计满值时间：{format_sanity_time(state.full_at)}" if minutes else "预计满值时间：已满"]


def recovered_data_error(subscription) -> dict:
    """A successful fetch resolves only the previous data-fetch failure."""
    if subscription.last_error.startswith(DATA_ERROR_PREFIX):
        return {"last_error": "", "retry_at": 0}
    return {}


def reminder_text(role, state: SanityState) -> str:
    when = format_sanity_time(state.full_at)
    status = "理智已满" if state.remaining_seconds == 0 else f"理智将在约 {math.ceil(state.remaining_seconds / 60)} 分钟后恢复满"
    return (f"{GAME_NAMES[role.app_code]} · {role.nickname}（{role.display_server}）\n"
            f"{status}，当前 {state.current}/{state.maximum}。\n预计恢复时间：{when}")


class SanityMonitor:
    def __init__(self, store, service, send_private, *, clock=time.time):
        self.store, self.service, self.send_private, self.clock = store, service, send_private, clock
        self._owners: dict[str, asyncio.Lock] = {}
        self._checking = asyncio.Lock()

    def owner_lock(self, owner_id: str) -> asyncio.Lock:
        return self._owners.setdefault(owner_id, asyncio.Lock())

    async def run(self) -> None:
        while True:
            started = asyncio.get_running_loop().time()
            try:
                await self.check_once()
            except Exception as exc:
                logger.warning("理智订阅检查失败：%s", type(exc).__name__)
            await asyncio.sleep(max(0, 60 - (asyncio.get_running_loop().time() - started)))

    async def check_once(self) -> None:
        async with self._checking:
            owners = {}
            for candidate in await self.store.list_sanity_subscriptions():
                owners.setdefault(candidate.owner_id, []).append(candidate)
            semaphore = asyncio.Semaphore(3)
            async def check_owner(owner_id, subscriptions):
                # Serialize an owner's credential refreshes; a slow account
                # must not delay every other user's reminder.
                async with semaphore, self.owner_lock(owner_id):
                    for candidate in subscriptions:
                        try:
                            await self._check(candidate)
                        except Exception as exc:
                            logger.warning("角色 %s 理智订阅检查失败：%s", candidate.character_id, type(exc).__name__)
            await asyncio.gather(*(check_owner(owner, subscriptions) for owner, subscriptions in owners.items()))

    async def _check(self, candidate) -> None:
        # Commands may have changed or removed a subscription while its turn waited.
        sub = next((s for s in await self.store.list_sanity_subscriptions(candidate.owner_id)
                    if s.character_id == candidate.character_id), None)
        now = self.clock()
        if sub is None or sub.retry_at > now:
            return
        version = await self.store.owner_version(sub.owner_id)
        roles = await self.store.get_characters(sub.owner_id)
        role = next((r for r in roles if r.id == sub.character_id), None)
        if role is None:
            return
        try:
            state = await asyncio.wait_for(self.service.sanity(role, now=now), timeout=30)
        except Exception as exc:
            await self.store.update_sanity_subscription(sub, retry_at=now + 300,
                last_error=f"{DATA_ERROR_PREFIX}（{type(exc).__name__}），请检查登录或稍后重试。")
            return
        if await self.store.owner_version(sub.owner_id) != version:
            return
        outside = state.remaining_seconds > sub.advance_minutes * 60
        changes = dict(last_checked=now, current=state.current, maximum=state.maximum, full_at=state.full_at)
        changes.update(recovered_data_error(sub))
        if outside:
            changes.update(notified=False, retry_at=0)
            if not sub.blocked:
                changes["last_error"] = ""
        if not await self.store.update_sanity_subscription(sub, **changes):
            return
        if outside or sub.notified or sub.blocked:
            return
        # Recheck binding validity immediately before submitting a private message.
        if await self.store.owner_version(sub.owner_id) != version:
            return
        try:
            await asyncio.wait_for(self.send_private(sub.private_umo, reminder_text(role, state)), timeout=30)
        except Exception as exc:
            error = exc if isinstance(exc, PrivateDeliveryError) else PrivateDeliveryError("私信发送失败，请稍后重试。")
            await self.store.update_sanity_subscription(sub, blocked=error.blocked,
                retry_at=0 if error.blocked else self.clock() + 300, last_error=str(error))
            return
        await self.store.update_sanity_subscription(sub, notified=True, retry_at=0, last_error="", last_notified=self.clock())

    async def test_private(self, owner_id: str, private_umo: str) -> None:
        try:
            await asyncio.wait_for(self.send_private(private_umo, "森空岛理智提醒测试成功。已确认可以向你发送私信。"), timeout=30)
        except Exception as exc:
            error = exc if isinstance(exc, PrivateDeliveryError) else PrivateDeliveryError("测试私信发送失败，请稍后重试。")
            for sub in await self.store.list_sanity_subscriptions(owner_id):
                await self.store.update_sanity_subscription(sub, blocked=error.blocked,
                    retry_at=0 if error.blocked else self.clock() + 300, last_error=str(error))
            raise error from None
        for sub in await self.store.list_sanity_subscriptions(owner_id):
            await self.store.update_sanity_subscription(sub, blocked=False, retry_at=0, last_error="")
