import asyncio
import math
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from skland.models import Account, Character
from skland.qq_message import PrivateDeliveryError
from skland.sanity import SanityMonitor, SanityState, sanity_state
from skland.service import SklandService, SklandAPI
from skland.store import SklandStore
from plugin_harness import Event, make_plugin, run, texts
from test_account_migration import add_account

NOW = 1_800_000_000


def card(game, remaining, *, current=None, maximum=120):
    current = (maximum if not remaining else maximum - math.ceil(remaining / 360)) if current is None else current
    if game == "arknights":
        return SimpleNamespace(status=SimpleNamespace(ap=SimpleNamespace(current=current, max=maximum,
            completeRecoveryTime=NOW + remaining, lastApAddTime=NOW)))
    return SimpleNamespace(currentTs=str(NOW), dungeon=SimpleNamespace(curStamina=str(current),
        maxStamina=str(maximum), maxTs=str(NOW + remaining)))


@pytest.mark.parametrize("game", ["arknights", "endfield"])
@pytest.mark.parametrize("remaining", [0, 599, 600, 601])
def test_recovery_time_is_normalized(game, remaining):
    state = sanity_state(game, card(game, remaining), now=NOW)
    assert state.remaining_seconds == remaining
    assert state.maximum == 120
    assert state.full_at == NOW + remaining


@pytest.mark.parametrize("game", ["arknights", "endfield"])
def test_full_or_overfilled_snapshot_needs_no_recovery_timestamp(game):
    data = card(game, 0, current=135)
    if game == "arknights":
        data.status.ap.completeRecoveryTime = 0
    else:
        data.dungeon.maxTs = data.currentTs = ""
    state = sanity_state(game, data, now=NOW)
    assert state.current == 135 and state.remaining_seconds == 0


@pytest.mark.parametrize("value", ["", "NaN", "inf", "0", "-1", "invalid"])
@pytest.mark.parametrize("game", ["arknights", "endfield"])
def test_missing_or_malformed_timestamps_do_not_mean_full(game, value):
    data = card(game, 600)
    if game == "arknights":
        data.status.ap.completeRecoveryTime = value
    else:
        data.dungeon.maxTs = value
    with pytest.raises((ValueError, TypeError)):
        sanity_state(game, data, now=NOW)


def test_endfield_uses_api_duration_with_clock_skew_and_milliseconds():
    data = card("endfield", 600)
    data.currentTs = str(NOW * 1000)
    data.dungeon.maxTs = str((NOW + 600) * 1000)
    state = sanity_state("endfield", data, now=NOW + 120)
    assert state.remaining_seconds == 600 and state.full_at == NOW + 720


@pytest.mark.parametrize("game", ["arknights", "endfield"])
@pytest.mark.parametrize("current,maximum", [(0, 0), (-1, 120), ("", 120), (1, "")])
def test_invalid_stamina_counts_are_rejected(game, current, maximum):
    with pytest.raises((ValueError, TypeError)):
        sanity_state(game, card(game, 600, current=current, maximum=maximum), now=NOW)


@pytest_asyncio.fixture
async def setup(tmp_path):
    store = SklandStore(tmp_path / "db")
    await store.initialize()
    account, role = await add_account(store)
    service = SklandService(store)
    service.sanity = AsyncMock(return_value=SanityState(118, 120, NOW + 600, 600))
    sent = AsyncMock()
    clock = [NOW]
    monitor = SanityMonitor(store, service, sent, clock=lambda: clock[0])
    return SimpleNamespace(store=store, account=account, role=role, service=service,
                           sent=sent, clock=clock, monitor=monitor)


@pytest.mark.asyncio
@pytest.mark.parametrize("game", ["arknights", "endfield"])
@pytest.mark.parametrize("minutes", [0, 10])
async def test_once_per_window_restart_repeat_subscribe_and_rearm(setup, game, minutes):
    obj = setup
    if game == "endfield":
        obj.role = replace(obj.role, app_code=game, id=None, is_skland_default=True)
        await obj.store.replace_characters("bot:u", [obj.role], obj.account.id)
    await obj.store.subscribe_sanity("bot:u", obj.role.id, "bot:FriendMessage:u", minutes)
    def state(remaining):
        obj.service.sanity.return_value = SanityState(120 if not remaining else 118, 120, NOW + remaining, remaining)
    state(minutes * 60 + 1)
    await obj.monitor.check_once()
    obj.sent.assert_not_awaited()
    state(minutes * 60)
    await obj.monitor.check_once()
    assert obj.sent.await_count == 1
    assert obj.sent.await_args.args[0] == "bot:FriendMessage:u"
    assert "博士" in obj.sent.await_args.args[1] and "120" in obj.sent.await_args.args[1]
    await obj.store.subscribe_sanity("bot:u", obj.role.id, "bot:FriendMessage:u", minutes)
    restarted = SanityMonitor(SklandStore(obj.store.path), obj.service, obj.sent, clock=lambda: obj.clock[0])
    state(0)
    await restarted.check_once()
    await restarted.check_once()
    assert obj.sent.await_count == 1
    state(minutes * 60 + 1)
    await restarted.check_once()
    state(minutes * 60)
    await restarted.check_once()
    assert obj.sent.await_count == 2


@pytest.mark.asyncio
async def test_first_subscription_already_full_is_reminded(setup):
    await setup.store.subscribe_sanity("bot:u", setup.role.id, "bot:FriendMessage:u")
    setup.service.sanity.return_value = SanityState(120, 120, NOW, 0)
    await setup.monitor.check_once()
    assert "理智已满" in setup.sent.await_args.args[1]


@pytest.mark.asyncio
async def test_temporary_failure_retries_only_after_fresh_eligibility_check(setup):
    obj = setup
    await obj.store.subscribe_sanity("bot:u", obj.role.id, "bot:FriendMessage:u")
    obj.sent.side_effect = [PrivateDeliveryError("频率限制"), None]
    await obj.monitor.check_once()
    sub, = await obj.store.list_sanity_subscriptions("bot:u")
    assert not sub.notified and sub.retry_at == NOW + 300
    obj.clock[0] += 299
    await obj.monitor.check_once()
    assert obj.sent.await_count == obj.service.sanity.await_count == 1
    obj.clock[0] += 1
    obj.service.sanity.return_value = SanityState(100, 120, NOW + 1200, 1200)
    await obj.monitor.check_once()
    assert obj.sent.await_count == 1
    obj.service.sanity.return_value = SanityState(118, 120, NOW + 600, 600)
    await obj.monitor.check_once()
    assert obj.sent.await_count == 2
    assert (await obj.store.list_sanity_subscriptions("bot:u"))[0].notified


@pytest.mark.asyncio
async def test_permission_failure_pauses_until_successful_test(setup):
    obj = setup
    await obj.store.subscribe_sanity("bot:u", obj.role.id, "bot:FriendMessage:u")
    obj.sent.side_effect = PrivateDeliveryError("请添加 Bot", blocked=True)
    await obj.monitor.check_once()
    obj.clock[0] += 600
    await obj.monitor.check_once()
    sub, = await obj.store.list_sanity_subscriptions("bot:u")
    assert sub.blocked and not sub.notified
    assert obj.sent.await_count == 1
    with pytest.raises(PrivateDeliveryError):
        await obj.monitor.test_private("bot:u", "bot:FriendMessage:u")
    obj.sent.side_effect = None
    await obj.monitor.test_private("bot:u", "bot:FriendMessage:u")
    sub, = await obj.store.list_sanity_subscriptions("bot:u")
    assert not sub.blocked and not sub.notified and not sub.last_error
    await obj.monitor.check_once()
    assert obj.sent.await_count == 4
    await obj.monitor.test_private("bot:u", "bot:FriendMessage:u")
    await obj.monitor.check_once()
    assert obj.sent.await_count == 5  # A test does not reset an already delivered reminder.


@pytest.mark.asyncio
async def test_bad_api_data_and_credentials_never_send_or_leak_secrets(setup):
    obj = setup
    await obj.store.subscribe_sanity("bot:u", obj.role.id, "bot:FriendMessage:u")
    obj.service.sanity.side_effect = ValueError("secret-token")
    await obj.monitor.check_once()
    sub, = await obj.store.list_sanity_subscriptions("bot:u")
    assert "数据获取失败" in sub.last_error and "secret" not in sub.last_error
    obj.sent.assert_not_awaited()


@pytest.mark.asyncio
async def test_paused_delivery_still_observes_new_cycles_without_erasing_reason(setup):
    obj = setup
    await obj.store.subscribe_sanity("bot:u", obj.role.id, "bot:FriendMessage:u")
    await obj.monitor.check_once()
    obj.sent.side_effect = PrivateDeliveryError("请添加 Bot", blocked=True)
    with pytest.raises(PrivateDeliveryError):
        await obj.monitor.test_private("bot:u", "bot:FriendMessage:u")
    obj.service.sanity.return_value = SanityState(100, 120, NOW + 1200, 1200)
    await obj.monitor.check_once()
    sub, = await obj.store.list_sanity_subscriptions("bot:u")
    assert sub.blocked and not sub.notified and sub.last_error == "请添加 Bot"
    obj.sent.side_effect = None
    await obj.monitor.test_private("bot:u", "bot:FriendMessage:u")
    obj.service.sanity.return_value = SanityState(118, 120, NOW + 600, 600)
    await obj.monitor.check_once()
    assert obj.sent.await_count == 4


@pytest.mark.asyncio
async def test_failed_refresh_keeps_timestamp_of_last_valid_snapshot(setup):
    obj = setup
    await obj.store.subscribe_sanity("bot:u", obj.role.id, "bot:FriendMessage:u")
    await obj.monitor.check_once()
    obj.clock[0] += 60
    obj.service.sanity.side_effect = ValueError("invalid data")
    await obj.monitor.check_once()
    sub, = await obj.store.list_sanity_subscriptions("bot:u")
    assert sub.last_checked == NOW and sub.current == 118


@pytest.mark.asyncio
async def test_fixed_role_unavailable_recovery_and_unbind_cascade(setup):
    obj = setup
    _, second = await add_account(obj.store, "remote-2", uid="200")
    await obj.store.subscribe_sanity("bot:u", obj.role.id, "bot:FriendMessage:u")
    await obj.store.set_default("bot:u", "arknights", second.id)
    await obj.store.replace_characters("bot:u", [], obj.account.id)
    await obj.monitor.check_once()
    obj.sent.assert_not_awaited()
    assert len(await obj.store.list_sanity_subscriptions("bot:u")) == 1
    await obj.store.replace_characters("bot:u", [obj.role], obj.account.id)
    await obj.monitor.check_once()
    assert obj.service.sanity.await_args.args[0].id == obj.role.id
    await obj.store.delete_account("bot:u", [obj.account.id])
    assert await obj.store.list_sanity_subscriptions("bot:u") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["unsubscribe", "unbind", "resubscribe", "version"])
async def test_binding_or_subscription_changes_during_fetch_prevent_stale_send(setup, mutation):
    obj = setup
    await obj.store.subscribe_sanity("bot:u", obj.role.id, "bot:FriendMessage:u")
    async def changed(role, **kwargs):
        if mutation == "unsubscribe":
            await obj.store.unsubscribe_sanity("bot:u", "arknights", role.id)
        elif mutation == "unbind":
            await obj.store.delete_account("bot:u")
        elif mutation == "resubscribe":
            await obj.store.subscribe_sanity("bot:u", role.id, "bot:FriendMessage:new-target")
        else:
            await obj.store.set_default("bot:u", "arknights", role.id)
        return SanityState(118, 120, NOW + 600, 600)
    obj.service.sanity.side_effect = changed
    await obj.monitor.check_once()
    obj.sent.assert_not_awaited()


@pytest.mark.asyncio
async def test_overlapping_checks_do_not_duplicate_delivery(setup):
    await setup.store.subscribe_sanity("bot:u", setup.role.id, "bot:FriendMessage:u")
    await asyncio.gather(setup.monitor.check_once(), setup.monitor.check_once())
    setup.sent.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancelling_monitor_during_send_preserves_undelivered_state(setup):
    await setup.store.subscribe_sanity("bot:u", setup.role.id, "bot:FriendMessage:u")
    started = asyncio.Event()
    async def wait(*args):
        started.set()
        await asyncio.Future()
    setup.sent.side_effect = wait
    task = asyncio.create_task(setup.monitor.run())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not (await setup.store.list_sanity_subscriptions("bot:u"))[0].notified


@pytest.mark.asyncio
@pytest.mark.parametrize("game,method", [("arknights", "ark_card"), ("endfield", "endfield_card")])
async def test_service_fetches_fresh_data_and_refreshes_credentials(setup, monkeypatch, game, method):
    from skland.exception import UnauthorizedException
    from skland.service import SklandLoginAPI
    service = SklandService(setup.store)
    role = replace(setup.role, app_code=game)
    api = AsyncMock(side_effect=[UnauthorizedException("expired"), card(game, 600), card(game, 0)])
    monkeypatch.setattr(SklandAPI, method, api)
    refresh = AsyncMock(return_value="new-token")
    monkeypatch.setattr(SklandLoginAPI, "refresh_token", refresh)
    assert (await service.sanity(role, now=NOW)).remaining_seconds == 600
    assert (await service.sanity(role, now=NOW)).remaining_seconds == 0
    assert api.await_count == 3
    refresh.assert_awaited_once()
    assert (await setup.store.get_account("bot:u")).cred_token == "new-token"


@pytest.mark.asyncio
async def test_commands_defaults_role_games_hint_and_owner_isolation(setup):
    obj, ns = make_plugin(setup.store)
    ns["react"] = AsyncMock()
    obj.service.sanity = setup.service.sanity
    result = texts(await run(obj, Event(group="any"), ["sanity", "subscribe"]))
    assert "10 分钟" in result and "添加到私聊" in result and "允许主动发送" in result
    first, = await setup.store.list_sanity_subscriptions("bot:u")
    assert first.character_id == setup.role.id and first.private_umo == "bot:FriendMessage:u"
    account = Account("bot:u", None, "cred", "token", "endfield-account")
    ef_role = Character("bot:u", "ef-uid", "ef-role", "endfield", "1", "管理员", is_skland_default=True)
    await setup.store.commit_binding(account, [ef_role], await setup.store.owner_version("bot:u"))
    assert "5 分钟" in texts(await run(obj, Event(group="other"), ["sanity", "subscribe", "ef", "5", "-r", "1"]))
    assert len(await setup.store.list_sanity_subscriptions("bot:u")) == 2
    assert "博士" in texts(await run(obj, Event(group="g"), ["sanity", "status"]))
    assert "尚无" in texts(await run(obj, Event(owner="other", group="g"), ["sanity", "status"]))
    assert "尚无" in texts(await run(obj, Event(platform="different", group="g"), ["sanity", "status"]))
    assert "未找到" in texts(await run(obj, Event(owner="other"), ["sanity", "subscribe"]))
    assert await setup.store.list_sanity_subscriptions("bot:other") == []
    assert "已取消" in texts(await run(obj, Event(), ["sanity", "unsubscribe", "终末地", "--all"]))
    assert len(await setup.store.list_sanity_subscriptions("bot:u")) == 1
    await run(obj, Event(), ["sanity", "unsubscribe", "ark", "-r", "1"])
    assert await setup.store.list_sanity_subscriptions("bot:u") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [["-1"], ["abc"], ["1.5"], ["10", "extra"], ["-r", "0"], ["-r", "99"]])
async def test_invalid_subscription_arguments_never_save(setup, args):
    obj, ns = make_plugin(setup.store)
    ns["react"] = AsyncMock()
    assert "操作失败" in texts(await run(obj, Event(group="g"), ["sanity", "subscribe", *args]))
    assert await setup.store.list_sanity_subscriptions("bot:u") == []


@pytest.mark.asyncio
async def test_private_commands_update_target_and_successful_test_preserves_cycle(setup):
    obj, ns = make_plugin(setup.store)
    ns["react"] = AsyncMock()
    ns["send_private_text"] = AsyncMock()
    await run(obj, Event(group="g"), ["sanity", "subscribe"])
    sub, = await setup.store.list_sanity_subscriptions("bot:u")
    await setup.store.update_sanity_subscription(sub, notified=True, blocked=True, last_error="请添加 Bot")
    event = Event()
    event.unified_msg_origin = "bot:FriendMessage:actual-session"
    await run(obj, event, ["help"])
    sub, = await setup.store.list_sanity_subscriptions("bot:u")
    assert sub.private_umo == event.unified_msg_origin and sub.blocked
    await run(obj, Event(group="g"), ["sanity", "subscribe", "20"])
    sub, = await setup.store.list_sanity_subscriptions("bot:u")
    assert sub.private_umo == event.unified_msg_origin and sub.notified
    assert "测试私信已发送" in texts(await run(obj, Event(group="g"), ["sanity", "test"]))
    assert ns["send_private_text"].await_args.args[1] == event.unified_msg_origin
    sub, = await setup.store.list_sanity_subscriptions("bot:u")
    assert sub.notified and not sub.blocked and not sub.last_error


@pytest.mark.asyncio
async def test_store_rejects_another_owners_role(setup):
    with pytest.raises(ValueError):
        await setup.store.subscribe_sanity("other:u", setup.role.id, "other:FriendMessage:u")
    assert await setup.store.list_sanity_subscriptions() == []
