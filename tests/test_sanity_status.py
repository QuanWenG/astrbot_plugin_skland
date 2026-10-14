from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from plugin_harness import Event, make_plugin, run, texts
from skland.sanity import SanityState, format_sanity_time, sanity_details, reminder_text
from skland.store import SklandStore
from test_account_migration import add_account

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc).timestamp()


@pytest_asyncio.fixture
async def status_plugin(tmp_path):
    store = SklandStore(tmp_path / "db")
    await store.initialize()
    _, role = await add_account(store)
    await store.subscribe_sanity("bot:u", role.id, "bot:FriendMessage:u")
    obj, ns = make_plugin(store)
    ns["react"] = AsyncMock()
    obj._sanity_monitor.clock = lambda: NOW
    obj.service.sanity = AsyncMock(return_value=SanityState(100, 135, NOW + 2220, 2220))
    obj._sanity_monitor.send_private = AsyncMock()
    return obj, role


@pytest.mark.asyncio
async def test_first_status_fetches_current_stamina_and_full_time(status_plugin):
    obj, role = status_plugin
    result = texts(await run(obj, Event(group="g"), ["sanity", "status"]))
    assert "当前理智：100/135" in result
    assert "预计 37 分钟后满" in result
    assert "预计满值时间：10-10 20:37" in result
    assert "香港" not in result and "UTC" not in result and "HKT" not in result
    obj.service.sanity.assert_awaited_once()
    assert obj.service.sanity.await_args.args[0].id == role.id
    sub, = await obj.store.list_sanity_subscriptions("bot:u")
    assert sub.current == 100 and sub.maximum == 135 and sub.last_checked == NOW
    obj._sanity_monitor.send_private.assert_not_awaited()


@pytest.mark.asyncio
async def test_status_ignores_polling_backoff_and_preserves_delivery_flags(status_plugin):
    obj, _ = status_plugin
    sub, = await obj.store.list_sanity_subscriptions("bot:u")
    await obj.store.update_sanity_subscription(sub, current=1, maximum=135, last_checked=NOW - 60,
        notified=True, blocked=True, retry_at=NOW + 300, last_error="请添加 Bot")
    result = texts(await run(obj, Event(), ["sanity", "status"]))
    assert "当前理智：100/135" in result and "私信投递暂停" in result and "请添加 Bot" in result
    sub, = await obj.store.list_sanity_subscriptions("bot:u")
    assert sub.notified and sub.blocked and sub.retry_at == NOW + 300 and sub.last_error == "请添加 Bot"
    obj._sanity_monitor.send_private.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("notified", [False, True])
async def test_successful_status_clears_recovered_data_error_and_retry(status_plugin, notified):
    obj, _ = status_plugin
    sub, = await obj.store.list_sanity_subscriptions("bot:u")
    await obj.store.update_sanity_subscription(sub, notified=notified, retry_at=NOW + 300,
        last_error="理智数据获取失败（RequestException），请检查登录或稍后重试。")
    result = texts(await run(obj, Event(), ["sanity", "status"]))
    assert "当前理智：100/135" in result
    assert "最近失败" not in result and "等待重试" not in result
    assert ("本轮已提醒" if notified else "等待接近满值") in result
    sub, = await obj.store.list_sanity_subscriptions("bot:u")
    assert not sub.last_error and sub.retry_at == 0 and sub.notified == notified
    obj._sanity_monitor.send_private.assert_not_awaited()


@pytest.mark.asyncio
async def test_successful_status_keeps_unresolved_private_delivery_retry(status_plugin):
    obj, _ = status_plugin
    sub, = await obj.store.list_sanity_subscriptions("bot:u")
    await obj.store.update_sanity_subscription(sub, retry_at=NOW + 300, last_error="私信发送失败，请稍后重试。")
    result = texts(await run(obj, Event(), ["sanity", "status"]))
    assert "当前理智：100/135" in result and "等待重试" in result and "私信发送失败" in result
    sub, = await obj.store.list_sanity_subscriptions("bot:u")
    assert sub.retry_at == NOW + 300 and sub.last_error == "私信发送失败，请稍后重试。"


@pytest.mark.asyncio
async def test_failed_status_keeps_unresolved_data_error(status_plugin):
    obj, _ = status_plugin
    sub, = await obj.store.list_sanity_subscriptions("bot:u")
    await obj.store.update_sanity_subscription(sub, retry_at=NOW + 300, last_error="理智数据获取失败（RequestException）")
    obj.service.sanity.side_effect = ValueError("invalid data")
    result = texts(await run(obj, Event(), ["sanity", "status"]))
    assert "暂时无法获取" in result and "最近失败" in result and "等待重试" in result
    sub, = await obj.store.list_sanity_subscriptions("bot:u")
    assert sub.last_error and sub.retry_at == NOW + 300


@pytest.mark.asyncio
async def test_successful_background_fetch_clears_data_error_after_notified_cycle(status_plugin):
    obj, _ = status_plugin
    sub, = await obj.store.list_sanity_subscriptions("bot:u")
    await obj.store.update_sanity_subscription(sub, notified=True, retry_at=NOW, last_error="理智数据获取失败（RequestException）")
    obj.service.sanity.return_value = SanityState(135, 135, NOW, 0)
    await obj._sanity_monitor.check_once()
    sub, = await obj.store.list_sanity_subscriptions("bot:u")
    assert not sub.last_error and sub.retry_at == 0 and sub.notified
    obj._sanity_monitor.send_private.assert_not_awaited()


@pytest.mark.asyncio
async def test_status_refreshes_only_requesters_subscriptions(status_plugin):
    obj, role = status_plugin
    _, other = await add_account(obj.store, "other-remote", owner="bot:other")
    await obj.store.subscribe_sanity("bot:other", other.id, "bot:FriendMessage:other")
    await run(obj, Event(group="g"), ["sanity", "status"])
    obj.service.sanity.assert_awaited_once()
    assert obj.service.sanity.await_args.args[0].id == role.id
    assert (await obj.store.list_sanity_subscriptions("bot:other"))[0].current is None
    obj.service.sanity.reset_mock()
    assert "尚无" in texts(await run(obj, Event(platform="other", group="g"), ["sanity", "status"]))
    obj.service.sanity.assert_not_awaited()


@pytest.mark.asyncio
async def test_failure_labels_old_snapshot_and_never_invents_current_stamina(status_plugin):
    obj, _ = status_plugin
    sub, = await obj.store.list_sanity_subscriptions("bot:u")
    await obj.store.update_sanity_subscription(sub, current=90, maximum=135, last_checked=NOW - 60, full_at=NOW + 3600)
    obj.service.sanity.side_effect = ValueError("secret-credential")
    result = texts(await run(obj, Event(), ["sanity", "status"]))
    assert "暂时无法获取当前理智" in result
    assert "上次记录理智：90/135（10-10 19:59）" in result
    assert "上次预计满值时间：10-10 21:00" in result
    assert "当前理智：90/135" not in result and "secret" not in result
    assert (await obj.store.list_sanity_subscriptions("bot:u"))[0].last_checked == NOW - 60


@pytest.mark.asyncio
async def test_failure_without_snapshot_displays_unavailable(status_plugin):
    obj, _ = status_plugin
    obj.service.sanity.side_effect = ValueError("invalid data")
    result = texts(await run(obj, Event(), ["sanity", "status"]))
    assert "当前理智：暂时无法获取" in result
    assert "0/0" not in result and "上次记录理智" not in result


@pytest.mark.asyncio
async def test_unavailable_role_does_not_fetch_data(status_plugin):
    obj, role = status_plugin
    await obj.store.replace_characters("bot:u", [], role.account_id)
    result = texts(await run(obj, Event(), ["sanity", "status"]))
    assert "角色暂不可用" in result
    obj.service.sanity.assert_not_awaited()


@pytest.mark.asyncio
async def test_binding_change_during_read_does_not_save_stale_snapshot(status_plugin):
    obj, role = status_plugin
    async def changed(*args, **kwargs):
        await obj.store.set_default("bot:u", "arknights", role.id)
        return SanityState(100, 135, NOW + 2220, 2220)
    obj.service.sanity.side_effect = changed
    result = texts(await run(obj, Event(), ["sanity", "status"]))
    assert "账号或角色已变化" in result
    assert "当前理智：100/135" not in result
    assert (await obj.store.list_sanity_subscriptions("bot:u"))[0].current is None


@pytest.mark.parametrize("seconds,expected", [(1, "预计 1 分钟后满"), (61, "预计 2 分钟后满"), (18000, "预计 300 分钟后满")])
def test_minutes_round_up_and_cross_day_times_are_explicit(seconds, expected):
    details = sanity_details(SanityState(100, 135, NOW + seconds, seconds))
    assert expected in details
    if seconds == 18000:
        assert "预计满值时间：10-11 01:00" in details


@pytest.mark.parametrize("current", [135, 140])
def test_already_full_does_not_invent_historical_completion_time(current):
    details = sanity_details(SanityState(current, 135, NOW, 0))
    assert f"当前理智：{current}/135" in details
    assert "距离满值：0 分钟（理智已满）" in details
    assert "预计满值时间：已满" in details


def test_time_format_is_fixed_to_utc_plus_eight_without_timezone_label():
    assert format_sanity_time(NOW) == "10-10 20:00"


@pytest.mark.asyncio
async def test_private_reminder_uses_same_time_display(status_plugin):
    _, role = status_plugin
    result = reminder_text(role, SanityState(100, 135, NOW + 2220, 2220))
    assert "预计恢复时间：10-10 20:37" in result
    assert "UTC" not in result and "香港" not in result and "HKT" not in result
