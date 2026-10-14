import ast
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from PIL import Image as PillowImage
import pytest

from skland.models import Account
from skland.store import SklandStore
from skland.support_integration import import_support_after_login
from plugin_harness import Event, make_plugin, Plain, Image


def receipt(count=4):
    buffer = BytesIO()
    PillowImage.new("RGB", (8, 8), "white").save(buffer, format="PNG")
    return {"schema_version": 1, "text": "完整角色摘要", "short_text": "账号：4｜干员：200｜待补齐：2", "role_count": count, "images": [buffer.getvalue()]}


KWARGS = dict(owner_id="bot:u", group_umo="bot:GroupMessage:B", platform_id="bot", group_id="B", group_name="群B", member_nickname="昵称")


@pytest.mark.asyncio
async def test_new_receipt_preferred_without_reimport_and_legacy_fallback():
    structured = AsyncMock(return_value=receipt())
    legacy = AsyncMock(return_value="旧文字")
    plugin = SimpleNamespace(import_skland_after_login_receipt=structured, import_skland_after_login=legacy)
    context = SimpleNamespace(get_registered_star=lambda _: SimpleNamespace(activated=True, star_cls=plugin))
    assert await import_support_after_login(context, **KWARGS) == structured.return_value
    structured.assert_awaited_once_with(**KWARGS)
    legacy.assert_not_awaited()
    structured.side_effect = RuntimeError("failed")
    with pytest.raises(RuntimeError):
        await import_support_after_login(context, **KWARGS)
    legacy.assert_not_awaited()
    del plugin.import_skland_after_login_receipt
    assert await import_support_after_login(context, **KWARGS) == "旧文字"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [{"schema_version": 2}, {**receipt(), "images": ["not-bytes"]}, {**receipt(), "role_count": True}])
async def test_invalid_receipt_contract_rejected(bad):
    context = SimpleNamespace(get_registered_star=lambda _: SimpleNamespace(activated=True,
        star_cls=SimpleNamespace(import_skland_after_login_receipt=AsyncMock(return_value=bad))))
    with pytest.raises(ValueError, match="回执契约"):
        await import_support_after_login(context, **KWARGS)


@pytest.mark.asyncio
async def test_login_images_use_platform_adapter_and_send_failure_short_fallback(tmp_path):
    store = SklandStore(tmp_path / "db")
    await store.initialize()
    plugin, namespace = make_plugin(store)
    callback = AsyncMock(return_value=receipt())
    plugin.context = SimpleNamespace(get_registered_star=lambda _: SimpleNamespace(activated=True,
        star_cls=SimpleNamespace(import_skland_after_login_receipt=callback)))
    event = Event(group="B")
    await plugin._import_support_after_login(event, "bot:u")
    event.send.assert_awaited_once()
    assert isinstance(event.send.await_args.args[0].chain[0], Image)
    callback.assert_awaited_once()
    event = Event(group="B")
    sends = []
    async def fail_image(chain):
        sends.append(chain)
        if isinstance(chain.chain[0], Image):
            raise RuntimeError("transport")
    event.send = fail_image
    await plugin._import_support_after_login(event, "bot:u")
    assert len(sends) == 2 and isinstance(sends[1].chain[0], Plain)
    assert sends[1].chain[0].text == receipt()["short_text"]
    assert "完整角色摘要" not in sends[1].chain[0].text


@pytest.mark.asyncio
async def test_three_roles_text_and_render_failure_short_fallback(tmp_path):
    store = SklandStore(tmp_path / "db")
    await store.initialize()
    plugin, _ = make_plugin(store)
    callback = AsyncMock(return_value=receipt(3))
    plugin.context = SimpleNamespace(get_registered_star=lambda _: SimpleNamespace(activated=True,
        star_cls=SimpleNamespace(import_skland_after_login_receipt=callback)))
    event = Event(group="B")
    await plugin._import_support_after_login(event, "bot:u")
    assert event.send.await_args.args[0].chain[0].text == "完整角色摘要"
    callback.return_value = {**receipt(), "images": []}
    event = Event(group="B")
    await plugin._import_support_after_login(event, "bot:u")
    assert event.send.await_args.args[0].chain[0].text == receipt()["short_text"]


@pytest.mark.asyncio
async def test_bound_filter_is_scoped_ordered_deduplicated_and_chunked(tmp_path):
    store = SklandStore(tmp_path / "db")
    await store.initialize()
    await store.save_account(Account("bot:u1", None, "cred", "token", "remote1"))
    await store.save_account(Account("other:u1", None, "cred", "token", "remote2"))
    await store.save_account(Account("bot:u999", None, "cred", "token", "remote3"))
    candidates = [f"bot:u{index}" for index in range(1000)] + ["bot:u1"]
    assert await store.filter_bound_owners(candidates) == ["bot:u1", "bot:u999"]
    assert await store.filter_bound_owners([]) == []
    with pytest.raises(ValueError):
        await store.filter_bound_owners([None])
    tree = ast.parse((Path(__file__).parents[1] / "main.py").read_text("utf-8"))
    method = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "filter_skland_bound_owners")
    namespace = {}
    exec(compile(ast.Module(body=[method], type_ignores=[]), "main.py", "exec"), namespace)
    assert await namespace[method.name](SimpleNamespace(store=store), candidates) == ["bot:u1", "bot:u999"]
