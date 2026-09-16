"""Load the actual plugin class with a small AstrBot event boundary for unit tests."""
import ast
import asyncio
import shlex
from collections.abc import AsyncGenerator
from contextlib import suppress
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pydantic

from skland import cards
from skland import config as paths
from skland import service
from skland.access import CredentialCommandAccessPolicy, validate_group_sid
from skland.support_integration import import_support_after_login
from skland.commands import take_role, take_option, validate_shortcut
from skland.exception import SklandError
from skland.renderer import RenderError
from skland.image_output import image_output, ImageDeliveryError
from skland.models import Character
from skland.operator_snapshot import build_operator_snapshot
from skland.background import resolve_background, background_bytes
from skland.box_pagination import parse_box_page_arguments
from skland.qq_message import send_with_receipt
from skland.interactions import wait_choice, react, send_images
from skland.qrcode_card import fetch_user_avatar, render_qrcode_card
from skland.schemas import Clue, RogueData
from skland.gacha import group_arknights_records


class Plain:
    def __init__(self, text):
        self.text = text


class Image:
    def __init__(self, data):
        self.data = data
    fromBytes = classmethod(lambda cls, data: cls(data))
    fromURL = fromBytes
    fromBase64 = fromBytes
    fromFileSystem = fromBytes


class At:
    def __init__(self, qq):
        self.qq = qq


class Reply:
    def __init__(self, message_id):
        self.id = message_id


class MessageChain:
    def __init__(self, chain):
        self.chain = chain


class Event:
    def __init__(self, *, owner="u", platform="bot", group="", name="test", message="", admin=False, reply=""):
        self.owner, self.platform, self.group, self.name = owner, platform, group, name
        self.message, self.admin = message, admin
        self.unified_msg_origin = f"{platform}:{'GroupMessage' if group else 'FriendMessage'}:{group or owner}"
        self.message_obj = SimpleNamespace(message_id="incoming", raw_message=None)
        self.segments = [Reply(reply)] if reply else []
        self.is_at_or_wake_command = True
        self.send = AsyncMock()
        self.stopped = False
    def get_sender_id(self): return self.owner
    def get_platform_id(self): return self.platform
    def get_group_id(self): return self.group
    def get_platform_name(self): return self.name
    def get_self_id(self): return "bot-id"
    def get_sender_name(self): return "用户"
    def get_message_str(self): return self.message
    def get_messages(self): return self.segments
    def is_admin(self): return self.admin
    def chain_result(self, chain): return MessageChain(chain)
    def plain_result(self, text): return MessageChain([Plain(text)])
    def stop_event(self): self.stopped = True


def make_plugin(store):
    source = ast.parse((Path(__file__).parents[1] / "main.py").read_text("utf-8"))
    selected = []
    for node in source.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in {"HELP", "BUILTIN_COMMANDS"} for t in node.targets):
            selected.append(node)
        elif isinstance(node, ast.ClassDef) and node.name == "SklandPlugin":
            node.decorator_list = []
            for method in node.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method.decorator_list = [d for d in method.decorator_list if isinstance(d, ast.Name)]
            selected.append(node)
    ns = dict(globals(), Star=object, Context=object, AstrBotConfig=dict, AstrMessageEvent=Event, GreedyStr=str,
        RES_DIR=paths.RES_DIR, runtime_config=paths.config, ark_card_cache=service.ark_card_cache,
        game_data=service.game_data, ARKNIGHTS=service.ARKNIGHTS, ENDFIELD=service.ENDFIELD,
        TypeAdapter=pydantic.TypeAdapter, SklandLoginAPI=service.SklandLoginAPI, logger=service.logger)
    ns.update({name: getattr(cards, name) for name in dir(cards) if name.startswith("render_")})
    exec(compile(ast.Module(body=selected, type_ignores=[]), "main.py", "exec"), ns)
    plugin = ns["SklandPlugin"].__new__(ns["SklandPlugin"])
    plugin.store, plugin.service = store, service.SklandService(store)
    plugin.config, plugin._shortcuts, plugin._interaction_tasks = {}, {}, set()
    plugin.context = SimpleNamespace(get_registered_star=lambda name: None)
    return plugin, ns


async def run(plugin, event, argv):
    return [result async for result in plugin._run(event, argv)]


def texts(results):
    return "\n".join(item.text for result in results for item in result.chain if isinstance(item, Plain))
