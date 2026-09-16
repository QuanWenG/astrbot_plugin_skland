"""Load the real skland entrypoint with no support plugin import available."""
import asyncio
import importlib.abc
import importlib.util
import logging
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from plugin_harness import At, Event, Image, MessageChain, Plain, Reply


@pytest.mark.asyncio
async def test_real_plugin_loads_and_initializes_without_arksupport(tmp_path):
    class Star:
        def __init__(self, context, config): self.context = context

    class RejectSupport(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split(".")[0] in {"arksupport", "astrbot_plugin_arksupport"}:
                raise AssertionError(f"Unexpected mandatory dependency: {fullname}")

    def module(name, **attributes):
        item = ModuleType(name)
        item.__dict__.update(attributes)
        return item

    decorate = lambda *a, **k: lambda fn: fn
    modules = {
        "astrbot": module("astrbot"),
        "astrbot.api": module("astrbot.api", AstrBotConfig=dict, logger=logging.getLogger("test-plugin")),
        "astrbot.api.event": module("astrbot.api.event", AstrMessageEvent=Event, MessageChain=MessageChain,
                                    filter=SimpleNamespace(command=decorate, event_message_type=decorate, EventMessageType=SimpleNamespace(ALL=0))),
        "astrbot.api.message_components": module("astrbot.api.message_components", At=At, Image=Image, Plain=Plain, Reply=Reply),
        "astrbot.api.star": module("astrbot.api.star", Star=Star, Context=object, register=decorate,
                                   StarTools=SimpleNamespace(get_data_dir=lambda name: tmp_path)),
        "astrbot.core": module("astrbot.core"),
        "astrbot.core.star": module("astrbot.core.star"),
        "astrbot.core.star.filter": module("astrbot.core.star.filter"),
        "astrbot.core.star.filter.command": module("astrbot.core.star.filter.command", GreedyStr=str),
    }
    name = "standalone_skland"
    package = module(name)
    package.__path__ = [str(Path(__file__).parents[1])]
    modules[name] = package
    blocker = RejectSupport()
    sys.meta_path.insert(0, blocker)
    try:
        with patch.dict(sys.modules, modules):
            spec = importlib.util.spec_from_file_location(f"{name}.main", Path(package.__path__[0]) / "main.py")
            entry = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = entry
            spec.loader.exec_module(entry)
            context = SimpleNamespace(get_registered_star=Mock(return_value=None))
            obj = entry.SklandPlugin(context, {"auto_update_resources": False})
            obj._startup_resources = AsyncMock()
            entry.game_data.load_cached = Mock()
            await obj.initialize()
            try:
                await asyncio.sleep(0)
                assert await obj.store.list_accounts("bot:u") == []
                await obj._import_support_after_login(Event(group="B"), "bot:u")
                context.get_registered_star.assert_called_once_with("astrbot_plugin_arksupport")
            finally:
                await obj.terminate()
    finally:
        sys.meta_path.remove(blocker)
