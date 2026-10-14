import asyncio
import shlex
from pathlib import Path
from collections.abc import AsyncGenerator
from contextlib import suppress
from datetime import datetime, timedelta

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import At, Image, Plain, Reply
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.star.filter.command import GreedyStr

from .skland.support_integration import import_support_after_login
from .skland.api import SklandLoginAPI
from .skland.cards import (
    render_bound_roles_card, render_ef_war_echoes,
    render_ark_card,
    render_clue_board,
    render_ef_card,
    render_ef_gacha_history,
    render_gacha_history,
    render_operator_roster,
    render_rogue_card,
    render_rogue_info,
)
from .skland.box_pagination import parse_box_page_arguments
from .skland.config import RES_DIR, configure_paths
from .skland.config import config as runtime_config
from .skland.exception import SklandError
from .skland.gacha import group_arknights_records, group_endfield_records
from .skland.renderer import RenderError, renderer
from .skland.image_output import ImageDeliveryError, image_output
from .skland.operator_snapshot import build_operator_snapshot
from .skland.resourcesync import game_data, sync_images
from .skland.qrcode_card import fetch_user_avatar, render_qrcode_card
from .skland.qq_message import send_with_receipt, send_private_text
from .skland.sanity import SanityMonitor, GAME_NAMES, GAME_ALIASES, PRIVATE_HINT, sanity_details, format_sanity_time, recovered_data_error
from .skland.service import ARKNIGHTS, ENDFIELD, SklandService
from .skland.background import resolve_background, background_bytes
from .skland.commands import take_role, take_option, validate_shortcut
from .skland.interactions import wait_choice, react, send_images
from .skland.schemas import Clue, RogueData
from .skland.card_cache import ark_card_cache
from pydantic import TypeAdapter
from .skland.store import SklandStore

PLUGIN_VERSION = "2.7.6"

HELP = """森空岛助手（sk / skland）
绑定：森空岛绑定 <token|cred>｜扫码绑定/扫码登录｜森空岛解绑（所有群聊、私聊可用，绑定成功后直接保存，解绑需确认）
理智：理智订阅 [方舟|终末地] [提前分钟] [-r 序号]（默认提前10分钟）
      理智取消 [方舟|终末地] [-r 序号|--all]｜理智订阅状态｜理智提醒测试
角色：/sk char｜/sk char set ark|ef <序号>｜角色更新
查询：/sk card｜/sk efcard [-a] [-s]；查询、签到、导入可加 -r <角色序号>
签到：明日方舟签到｜签到详情｜终末地签到｜终末地签到详情
肉鸽：树海/界园/萨卡兹/萨米/水月/傀影肉鸽｜战绩详情 <序号> [-f]
抽卡：方舟抽卡记录 [-b 起始] [-l 结束]｜导入抽卡记录 <URL>
      终末地抽卡记录 [-b 起始] [-l 结束]（自动更新，可用负数切片）
战争回响：/sk efwar [-s 赛季] [-w 轮换] [-r 角色]（负数赛季回溯）
其他：/sk clue｜/sk background（支持引用查询图片）｜资源更新｜/sk help｜/sk version（部署检查）
干员：方舟干员 [@用户] [筛选词...]｜/sk box [筛选词] [页码]
管理员：全体签到｜全体签到详情｜终末地全体签到｜全体角色更新
动态快捷指令：/sk shortcut add <名称> <子命令及参数>｜list｜remove <名称>"""

BUILTIN_COMMANDS = {
    "sk", "skland", "bind", "qrcode", "unbind", "char", "card", "efcard", "box", "clue",
    "rogue", "rginfo", "gacha", "efgacha", "efwar", "import", "arksign", "efsign", "sync", "background", "help",
    "sanity", "理智订阅", "理智取消", "理智订阅状态", "理智提醒测试",
    "version", "版本",
    "森空岛绑定", "扫码绑定", "扫码登录", "森空岛解绑", "明日方舟签到", "签到详情", "全体签到", "全体签到详情",
    "终末地签到", "终末地签到详情", "终末地全体签到", "终末地全体签到详情", "角色更新", "全体角色更新", "资源更新",
    "方舟抽卡记录", "导入抽卡记录", "终末地抽卡记录", "终末地抽卡更新", "ef", "zmd", "战绩详情", "方舟干员", "收藏战绩详情",
    "树海肉鸽", "界园肉鸽", "萨卡兹肉鸽", "萨米肉鸽", "水月肉鸽", "傀影肉鸽", "战争回响", "森空岛角色", "切换方舟角色", "切换终末地角色", "线索",
}


@register(
    "astrbot_plugin_skland", "AoiNyanko", "森空岛明日方舟/终末地查询与签到插件", PLUGIN_VERSION
)
class SklandPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context, config)
        self.config = config
        self.data_dir = StarTools.get_data_dir("astrbot_plugin_skland")
        self.store = SklandStore(self.data_dir / "skland.sqlite3")
        self.service = SklandService(self.store)
        self._daily_task: asyncio.Task | None = None
        self._resource_task: asyncio.Task | None = None
        self._startup_task: asyncio.Task | None = None
        self._sanity_task: asyncio.Task | None = None
        self._sanity_monitor = SanityMonitor(self.store, self.service, self._send_private_reminder)
        self._interaction_tasks: set[asyncio.Task] = set()
        self._shortcuts: dict[str, list[str]] = {}

    async def initialize(self) -> None:
        configure_paths(self.data_dir)
        runtime_config.background_source = self.config.get("background_source", "default")
        runtime_config.rogue_background_source = self.config.get("rogue_background_source", "rogue")
        runtime_config.context_ttl = max(1, int(self.config.get("context_ttl", 300)))
        runtime_config.endfield_background_simple = bool(
            self.config.get("endfield_background_simple", False)
        )
        runtime_config.github_proxy_url = str(self.config.get("github_proxy_url", ""))
        runtime_config.github_token = str(self.config.get("github_token", ""))
        runtime_config.gacha_render_max = max(
            1, int(self.config.get("gacha_render_max", 30))
        )
        runtime_config.ef_gacha_render_max = max(
            1, int(self.config.get("ef_gacha_render_max", 5))
        )
        runtime_config.render_timeout = max(1, int(self.config.get("render_timeout", 180000)))
        runtime_config.ark_portrait_cache_enabled = bool(
            self.config.get("ark_portrait_cache_enabled", False)
        )
        runtime_config.ark_card_cache_ttl = max(1, int(self.config.get("ark_card_cache_ttl", 120)))
        runtime_config.ark_card_cache_max_entries = max(
            1, int(self.config.get("ark_card_cache_max_entries", 64))
        )
        runtime_config.roster_render_max = max(1, int(self.config.get("roster_render_max", 16)))
        runtime_config.roster_render_format = str(
            self.config.get("roster_render_format", "jpeg")
        ).lower()
        if runtime_config.roster_render_format not in {"png", "jpeg"}:
            runtime_config.roster_render_format = "jpeg"
        runtime_config.roster_jpeg_quality = max(
            1, min(100, int(self.config.get("roster_jpeg_quality", 90)))
        )
        runtime_config.qq_image_max_bytes = max(
            1024, int(self.config.get("qq_image_max_bytes", 4194304))
        )
        runtime_config.qq_image_max_side = max(
            256, int(self.config.get("qq_image_max_side", 4096))
        )
        runtime_config.qq_image_jpeg_quality = max(
            1, min(100, int(self.config.get("qq_image_jpeg_quality", 88)))
        )
        renderer.configure(
            self.data_dir, str(self.config.get("browser_executable", ""))
        )
        await self.store.initialize()
        try:
            await asyncio.to_thread(game_data.load_cached)
        except (SklandError, ValueError, OSError) as exc:
            logger.warning("本地资源缓存需要更新：%s", exc)
        self._shortcuts = await self.store.shortcuts()
        if self.config.get("startup_update_resources", False):
            self._startup_task = asyncio.create_task(self._startup_resources())
        self._sanity_task = asyncio.create_task(self._sanity_monitor.run())
        if self.config.get("auto_update_resources", True):
            self._resource_task = asyncio.create_task(self._daily_resource_loop())
        if self.config.get("auto_sign", False):
            self._daily_task = asyncio.create_task(self._daily_sign_loop())
        logger.info("astrbot_plugin_skland initialized with image rendering")

    async def terminate(self) -> None:
        tasks = {t for t in (self._daily_task, self._resource_task, self._startup_task, self._sanity_task) if t}
        tasks.update(self._interaction_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await renderer.close()

    async def export_arknights_raw_rosters(self, owner_id: str, *, allow_empty: bool = False) -> dict:
        """Public v1 raw roster API. No credentials or game-resource dependency."""
        return await self.service.raw_operator_rosters(owner_id, allow_empty=allow_empty)

    async def filter_skland_bound_owners(self, owner_ids: list[str]) -> list[str]:
        """Public caller-scoped filter; return IDs only, never credential data."""
        return await self.store.filter_bound_owners(owner_ids)

    async def export_arknights_operator_snapshot(self, owner_id: str) -> dict:
        """Export the default Arknights role as a credential-free snapshot."""
        role = await self.service.require_character(owner_id, ARKNIGHTS)
        roster = await self.service.operator_roster(owner_id, character=role)
        return build_operator_snapshot(
            role,
            roster,
            variant_metadata_complete=game_data.variant_groups_loaded,
        )

    async def export_arknights_operator_snapshots(
        self, owner_id: str, *, allow_empty: bool = False,
    ) -> list[dict]:
        """Export a consistent role set; opt-in empty means no available roles."""
        version = await self.service.store.owner_version(owner_id)
        snapshots = [
            build_operator_snapshot(
                role,
                roster,
                variant_metadata_complete=game_data.variant_groups_loaded,
            )
            for role, roster in await self.service.operator_rosters(owner_id, allow_empty=allow_empty)
        ]
        if await self.service.store.owner_version(owner_id) != version:
            raise ValueError("账号或角色数据在导出期间已变化，请重试同步。")
        return snapshots

    @filter.command("skland", alias={"sk"})
    async def skland(self, event: AstrMessageEvent, args: GreedyStr):
        """森空岛助手；不带参数时查询明日方舟角色卡。"""
        try:
            argv = shlex.split(str(args)) if str(args).strip() else ["card"]
        except ValueError as exc:
            yield event.plain_result(f"参数格式错误：{exc}")
            return
        if argv and argv[0].isdigit():
            argv.insert(0, "card")
        async for result in self._run(event, argv):
            yield result

    @filter.command("理智订阅")
    async def shortcut_sanity_subscribe(self, event: AstrMessageEvent, args: GreedyStr):
        async for result in self._run(event, ["sanity", "subscribe", *shlex.split(str(args))]):
            yield result

    @filter.command("理智取消")
    async def shortcut_sanity_unsubscribe(self, event: AstrMessageEvent, args: GreedyStr):
        async for result in self._run(event, ["sanity", "unsubscribe", *shlex.split(str(args))]):
            yield result

    @filter.command("理智订阅状态")
    async def shortcut_sanity_status(self, event: AstrMessageEvent):
        async for result in self._run(event, ["sanity", "status"]):
            yield result

    @filter.command("理智提醒测试")
    async def shortcut_sanity_test(self, event: AstrMessageEvent):
        async for result in self._run(event, ["sanity", "test"]):
            yield result

    # 中文快捷指令：绑定相关
    @filter.command("森空岛绑定")
    async def shortcut_bind(self, event: AstrMessageEvent, args: GreedyStr):
        async for result in self._run(event, ["bind", *shlex.split(str(args))]):
            yield result

    @filter.command("扫码绑定")
    async def shortcut_qrcode(self, event: AstrMessageEvent):
        async for result in self._run(event, ["qrcode"]):
            yield result

    @filter.command("扫码登录")
    async def shortcut_qrcode_login(self, event: AstrMessageEvent):
        async for result in self._run(event, ["qrcode"]):
            yield result

    @filter.command("森空岛解绑")
    async def shortcut_unbind(self, event: AstrMessageEvent, args: GreedyStr):
        async for result in self._run(event, ["unbind", *shlex.split(str(args))]):
            yield result

    # 中文快捷指令：签到、角色和资源
    @filter.command("明日方舟签到")
    async def shortcut_arksign(self, event: AstrMessageEvent, args: GreedyStr):
        options = shlex.split(str(args)) if str(args).strip() else ["--all"]
        async for result in self._run(event, ["arksign", "sign", *options]):
            yield result

    @filter.command("签到详情")
    async def shortcut_sign_status(self, event: AstrMessageEvent, args: GreedyStr):
        async for result in self._run(event, ["arksign", "status", *shlex.split(str(args))]):
            yield result

    @filter.command("全体签到")
    async def shortcut_sign_all(self, event: AstrMessageEvent):
        async for result in self._run(event, ["arksign", "all"]):
            yield result

    @filter.command("全体签到详情")
    async def shortcut_sign_status_all(self, event: AstrMessageEvent):
        async for result in self._run(event, ["arksign", "status", "--all"]):
            yield result

    @filter.command("终末地签到")
    async def shortcut_efsign(self, event: AstrMessageEvent, args: GreedyStr):
        options = shlex.split(str(args)) if str(args).strip() else ["--all"]
        async for result in self._run(event, ["efsign", "sign", *options]):
            yield result

    @filter.command("终末地签到详情")
    async def shortcut_efsign_status(self, event: AstrMessageEvent, args: GreedyStr):
        async for result in self._run(event, ["efsign", "status", *shlex.split(str(args))]):
            yield result

    @filter.command("终末地全体签到")
    async def shortcut_efsign_all(self, event: AstrMessageEvent):
        async for result in self._run(event, ["efsign", "all"]):
            yield result

    @filter.command("终末地全体签到详情")
    async def shortcut_efsign_status_all(self, event: AstrMessageEvent):
        async for result in self._run(event, ["efsign", "status", "--all"]):
            yield result

    @filter.command("角色更新")
    async def shortcut_char_update(self, event: AstrMessageEvent, args: GreedyStr):
        async for result in self._run(event, ["char", "update", *shlex.split(str(args))]):
            yield result

    @filter.command("全体角色更新")
    async def shortcut_char_update_all(self, event: AstrMessageEvent):
        async for result in self._run(event, ["char", "update", "--all"]):
            yield result

    @filter.command("资源更新")
    async def shortcut_sync(self, event: AstrMessageEvent, args: GreedyStr):
        async for result in self._run(event, ["sync", *shlex.split(str(args))]):
            yield result

    # 中文快捷指令：抽卡和详情
    @filter.command("方舟抽卡记录")
    async def shortcut_gacha(self, event: AstrMessageEvent, args: GreedyStr):
        async for result in self._run(event, ["gacha", *shlex.split(str(args))]):
            yield result

    @filter.command("导入抽卡记录")
    async def shortcut_import(self, event: AstrMessageEvent, args: GreedyStr):
        async for result in self._run(event, ["import", *shlex.split(str(args))]):
            yield result

    @filter.command("终末地抽卡记录")
    async def shortcut_efgacha(self, event: AstrMessageEvent, args: GreedyStr):
        async for result in self._run(event, ["efgacha", *shlex.split(str(args))]):
            yield result

    @filter.command("终末地抽卡更新")
    async def shortcut_efgacha_update(self, event: AstrMessageEvent, args: GreedyStr):
        async for result in self._run(event, ["efgacha", "-u", *shlex.split(str(args))]):
            yield result

    @filter.command("ef")
    async def shortcut_efcard(self, event: AstrMessageEvent, args: GreedyStr):
        async for result in self._run(event, ["efcard", *shlex.split(str(args))]):
            yield result

    @filter.command("zmd")
    async def shortcut_zmd_card(self, event: AstrMessageEvent, args: GreedyStr):
        async for result in self._run(event, ["efcard", *shlex.split(str(args))]):
            yield result

    @filter.command("战绩详情")
    async def shortcut_rginfo(self, event: AstrMessageEvent, args: GreedyStr):
        async for result in self._run(event, ["rginfo", *shlex.split(str(args))]):
            yield result

    @filter.command("方舟干员")
    async def shortcut_box(self, event: AstrMessageEvent, args: GreedyStr):
        async for result in self._run(event, ["box", *shlex.split(str(args))]):
            yield result

    @filter.command("收藏战绩详情")
    async def shortcut_rginfo_favored(self, event: AstrMessageEvent, args: GreedyStr):
        async for result in self._run(event, ["rginfo", *shlex.split(str(args)), "-f"]):
            yield result

    # 中文肉鸽快捷指令
    @filter.command("树海肉鸽")
    async def shortcut_rogue_6(self, event: AstrMessageEvent, args: GreedyStr):
        async for result in self._run(event, ["rogue", "--topic", "黑流树海", *shlex.split(str(args))]):
            yield result

    @filter.command("界园肉鸽")
    async def shortcut_rogue_5(self, event: AstrMessageEvent, args: GreedyStr):
        async for result in self._run(event, ["rogue", "--topic", "界园", *shlex.split(str(args))]):
            yield result

    @filter.command("萨卡兹肉鸽")
    async def shortcut_rogue_4(self, event: AstrMessageEvent, args: GreedyStr):
        async for result in self._run(event, ["rogue", "--topic", "萨卡兹", *shlex.split(str(args))]):
            yield result

    @filter.command("萨米肉鸽")
    async def shortcut_rogue_3(self, event: AstrMessageEvent, args: GreedyStr):
        async for result in self._run(event, ["rogue", "--topic", "萨米", *shlex.split(str(args))]):
            yield result

    @filter.command("水月肉鸽")
    async def shortcut_rogue_2(self, event: AstrMessageEvent, args: GreedyStr):
        async for result in self._run(event, ["rogue", "--topic", "水月", *shlex.split(str(args))]):
            yield result

    @filter.command("傀影肉鸽")
    async def shortcut_rogue_1(self, event: AstrMessageEvent, args: GreedyStr):
        async for result in self._run(event, ["rogue", "--topic", "傀影", *shlex.split(str(args))]):
            yield result

    @filter.command("森空岛角色")
    async def shortcut_roles(self, event: AstrMessageEvent, args: GreedyStr):
        async for result in self._run(event, ["char", *shlex.split(str(args))]):
            yield result

    @filter.command("切换方舟角色")
    async def shortcut_set_ark(self, event: AstrMessageEvent, args: GreedyStr):
        async for result in self._run(event, ["char", "set", "ark", *shlex.split(str(args))]):
            yield result

    @filter.command("切换终末地角色")
    async def shortcut_set_ef(self, event: AstrMessageEvent, args: GreedyStr):
        async for result in self._run(event, ["char", "set", "ef", *shlex.split(str(args))]):
            yield result

    @filter.command("战争回响")
    async def shortcut_efwar(self, event: AstrMessageEvent, args: GreedyStr):
        async for result in self._run(event, ["efwar", *shlex.split(str(args))]):
            yield result

    @filter.command("background")
    async def shortcut_background(self, event: AstrMessageEvent):
        async for result in self._run(event, ["background"]):
            yield result

    @filter.command("clue", alias={"线索"})
    async def shortcut_clue(self, event: AstrMessageEvent, args: GreedyStr):
        async for result in self._run(event, ["clue", *shlex.split(str(args))]):
            yield result

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def dynamic_shortcut(self, event: AstrMessageEvent):
        if not event.is_at_or_wake_command:
            return
        try:
            argv = shlex.split(event.get_message_str().strip())
        except ValueError:
            return
        if argv and argv[0] in self._shortcuts and argv[0] not in BUILTIN_COMMANDS:
            expanded = self._shortcuts[argv[0]]
            validate_shortcut(argv[0], expanded, BUILTIN_COMMANDS, self._shortcuts)
            async for result in self._run(event, [*expanded, *argv[1:]]):
                yield result
            event.stop_event()

    async def _run(self, event: AstrMessageEvent, argv: list[str]) -> AsyncGenerator:
        owner_id = self._owner_id(event)
        try:
            if not event.get_group_id():
                async with self._sanity_monitor.owner_lock(owner_id):
                    await self.store.remember_private_session(owner_id, str(event.unified_msg_origin))
            command = argv[0].lower() if argv else "card"
            role_index = None
            if command in {"card", "efcard", "rogue", "box", "gacha", "efgacha", "efwar", "import", "clue", "rginfo", "arksign", "efsign"}:
                role_index, remainder = take_role(argv[1:])
                argv = [command, *remainder]
            await react(event, "processing")
            if command in {"help", "帮助", "-h", "--help"}:
                yield event.plain_result(HELP)
            elif command in {"version", "版本"}:
                if len(argv) != 1:
                    raise ValueError("用法：/sk version")
                yield event.plain_result(self._deployment_info(event))
            elif command == "sanity":
                yield event.plain_result(await self._sanity_command(event, owner_id, argv[1:]))
            elif command in {"bind", "qrcode"}:
                if command == "bind" and len(argv) < 2:
                    raise ValueError("用法：森空岛绑定 <24位token|32位cred>")
                async with self.service.bindings.exclusive(owner_id):
                    if command == "bind":
                        saved = await self._bind_account(event, owner_id, argv[-1])
                    else:
                        saved = await self._qrcode_login(event, owner_id)
                if saved:
                    await self._import_support_after_login(event, owner_id)
            elif command == "unbind":
                async with self.service.bindings.exclusive(owner_id):
                    await self._unbind_interaction(event, owner_id)
            elif command == "char":
                async for result in self._char_update(event, owner_id, argv):
                    yield result
            elif command in {"card", "efcard"}:
                await self._card_result(event, owner_id, command, argv[1:], role_index=role_index)
            elif command == "clue":
                cached = await self._query_context(event, "clue") if self._reply_id(event) else None
                if self._reply_id(event) and not cached:
                    raise ValueError("该角色卡的线索上下文已过期或无法匹配")
                if cached:
                    await self._check_context_role(owner_id, ARKNIGHTS, role_index, cached)
                    clue = Clue.model_validate(cached["data"])
                else:
                    _, card = await self.service.card(owner_id, ARKNIGHTS, role_index)
                    clue = card.building.meeting.clue
                yield await self._image_result(event, await render_clue_board(clue), "clue")
            elif command == "background":
                cached = await self._query_context(event, "background")
                if not cached:
                    raise ValueError("暂无背景上下文，请先查询角色卡或肉鸽战绩")
                yield await self._image_result(event, await background_bytes(cached["uri"]), "background")
            elif command == "rogue":
                topic, args = take_option(argv[1:], {"-t", "--topic", "topic"})
                target_owner, args = self._target_owner(event, args)
                if topic is None and args:
                    topic, args = args[0], args[1:]
                if args:
                    raise ValueError("肉鸽参数无效，请使用 --topic 主题 -r 角色")
                self._check_role_target(event, target_owner, role_index)
                char, data = await self.service.rogue(target_owner, topic, role_index)
                background = await resolve_background(topic=data.topic)
                await self._send_context_image(event, await render_rogue_card(data, background, server_name=char.display_server), "rogue",
                    {"rogue": {"data": data.model_dump(mode="json"), "role_id": char.id}, "background": {"uri": background}})
            elif command == "box":
                target_owner, args = self._box_target_owner(event, argv[1:])
                self._check_role_target(event, target_owner, role_index)
                page_args = parse_box_page_arguments(args)
                filters, options = self._box_arguments(list(page_args.query_args))
                roster = await self.service.operator_roster(
                    target_owner, identity=role_index, filters=tuple(filters), options=options
                )
                if not roster.cards:
                    yield event.plain_result(
                        f"没有匹配的干员 · {roster.summary}"
                    )
                    return
                page_size = runtime_config.roster_render_max
                total_pages = (len(roster.cards) + page_size - 1) // page_size
                if page_args.page > total_pages:
                    raise ValueError(
                        f"Box 页码超出范围：共 {total_pages} 页，"
                        f"当前请求第 {page_args.page} 页"
                    )
                start = (page_args.page - 1) * page_size
                cards = roster.cards[start : start + page_size]
                if total_pages > 1:
                    yield event.plain_result(
                        f"Box 第 {page_args.page}/{total_pages} 页 · "
                        f"共 {len(roster.cards)} 名干员"
                    )
                image = await render_operator_roster(
                    props=roster.with_cards(cards), background_image=await resolve_background(box=True)
                )
                yield await self._image_result(
                    event, image, f"box-{page_args.page}"
                )
            elif command == "rginfo":
                cached = await self._query_context(event, "rogue")
                if not cached:
                    raise ValueError("暂无有效战绩上下文，请先查询肉鸽战绩")
                await self._check_context_role(owner_id, ARKNIGHTS, role_index, cached)
                data = TypeAdapter(RogueData).validate_python(cached["data"])
                detail_args = [arg for arg in argv[1:] if arg not in {"-f", "--favored"}]
                record_id = int(detail_args[0]) if detail_args else 1
                background = await resolve_background(topic=data.topic)
                image = await render_rogue_info(data, background, record_id, any(x in argv for x in ("-f", "--favored")))
                await self._send_context_image(event, image, "rogue-info", {"background": {"uri": background}})
            elif command == "gacha":
                target_owner, args = self._target_owner(event, argv[1:])
                begin, limit = self._range_options(args)
                self._check_role_target(event, target_owner, role_index)
                char, records, added = await self.service.arknights_gacha(target_owner, role_index)
                _, card = await self.service.card(target_owner, ARKNIGHTS, role_index)
                grouped = group_arknights_records(records)
                pages = self._page_ranges(
                    begin, limit, len(grouped.pools), runtime_config.gacha_render_max
                )
                if len(pages) > 1:
                    yield event.plain_result(
                        f"抽卡记录较多，将发送 {len(pages)} 张图片"
                    )
                images = []
                for page, (start, end) in enumerate(pages, 1):
                    image = await render_gacha_history(
                        grouped, char, card.status, start, end
                    )
                    images.append(image)
                await send_images(event, images, prepare=image_output.prepare, title=char.nickname)
            elif command == "import":
                if len(argv) < 2:
                    raise ValueError("用法：导入抽卡记录 <小黑盒导出URL>")
                total, added = await self.service.import_heybox(owner_id, argv[1], role_index)
                yield event.plain_result(f"导入成功：读取 {total} 条，新增 {added} 条")
            elif command == "efgacha":
                target_owner, args = self._target_owner(event, argv[1:])
                self._check_role_target(event, target_owner, role_index)
                begin, limit = self._range_options(args)
                view = await self.service.endfield_history_view(target_owner, identity=role_index, begin=begin, limit=limit)
                await send_images(event, await render_ef_gacha_history(view), prepare=image_output.prepare, title=view.nickname)
            elif command == "efwar":
                target_owner, args = self._target_owner(event, argv[1:])
                self._check_role_target(event, target_owner, role_index)
                season, args = take_option(args, {"-s", "--season", "season"}, integer=True)
                week, args = take_option(args, {"-w", "--week", "week"}, integer=True)
                if args:
                    raise ValueError("战争回响参数无效，请使用 -s 赛季 -w 轮换 -r 角色")
                view = await self.service.war_echoes(target_owner, identity=role_index, season_id=season, week_id=week)
                yield await self._image_result(event, await render_ef_war_echoes(view), "war-echoes")
            elif command in {"shortcut", "--shortcut"}:
                yield event.plain_result(await self._manage_shortcuts(event, argv[1:]))
            elif command in {"arksign", "efsign"}:
                game = ARKNIGHTS if command == "arksign" else ENDFIELD
                async for result in self._sign_command(event, owner_id, game, argv[1:], role_index=role_index):
                    yield result
            elif command == "sync":
                if not event.is_admin():
                    raise ValueError("资源更新仅 AstrBot 管理员可用")
                force = any(x in argv for x in ("-f", "--force"))
                image_only = "--img" in argv
                data_only = "--data" in argv
                update_existing = any(x in argv for x in ("-u", "--update"))
                messages = []
                if data_only or not image_only:
                    version, downloaded, failed = await game_data.load(force=force, refresh_metadata=True)
                    messages.extend(game_data.last_update_messages)
                    messages.append(f"数据版本 {version or '本地缓存'}，下载 {downloaded}，失败 {failed}")
                if image_only or not data_only:
                    downloaded, failed = await sync_images(
                        force=force, update=update_existing
                    )
                    messages.append(f"图片资源：下载 {downloaded}，失败 {failed}")
                messages.append(
                    f"内置模板与静态资源：{len(list(RES_DIR.rglob('*.*')))} 个"
                )
                yield event.plain_result("资源更新完成\n" + "\n".join(messages))
            else:
                yield event.plain_result(f"未知子命令：{argv[0]}\n\n{HELP}")
            await react(event, "done")
        except (ValueError, SklandError, RenderError) as exc:
            await react(event, "fail")
            yield event.plain_result(f"操作失败：{exc}")
        except Exception as exc:
            logger.exception("Skland command failed")
            yield event.plain_result(f"森空岛请求失败：{type(exc).__name__}: {exc}")

    async def _card_result(self, event, owner_id, command, args, *, role_index=None):
        game = ARKNIGHTS if command == "card" else ENDFIELD
        target_owner, args = self._target_owner(event, args)
        self._check_role_target(event, target_owner, role_index)
        char, card = await self.service.card(target_owner, game, role_index)
        background = await resolve_background(game)
        contexts = {"background": {"uri": background}}
        if game == ARKNIGHTS:
            image = await render_ark_card(card, background)
            contexts["clue"] = {"data": card.building.meeting.clue.model_dump(mode="json"), "role_id": char.id}
        else:
            image = await render_ef_card(card, background,
                show_all=bool(set(args) & {"-a", "--all"}),
                is_simple=bool(set(args) & {"-s", "--simple"}))
        await self._send_context_image(event, image, command, contexts)

    async def _char_update(self, event, owner_id, argv):
        if len(argv) == 1:
            card = await self.service.bindings.overview(owner_id)
            yield await self._image_result(event, await render_bound_roles_card(card), "roles")
            yield event.plain_result("切换默认角色：/sk char set ark|ef <角色序号>；单次查询使用 -r <角色序号>")
        elif argv[1] == "set":
            if len(argv) != 4 or argv[2] not in {"ark", "ef"}:
                raise ValueError("用法：/sk char set ark|ef <角色序号>")
            game = ARKNIGHTS if argv[2] == "ark" else ENDFIELD
            index = int(argv[3])
            if index < 1:
                raise ValueError("角色序号必须从 1 开始")
            async with self.service.bindings.exclusive(owner_id):
                role = await self.service.require_character(owner_id, game, index)
                await self.store.set_default(owner_id, game, role.id)
                await ark_card_cache.invalidate_owner(owner_id)
            yield event.plain_result(f"默认角色已切换：{role.nickname}（{role.display_server}）")
        elif argv[1] in {"update", "更新"}:
            if "--all" in argv:
                self._require_admin(event)
                messages = []
                owners = dict.fromkeys(a.owner_id for a in await self.store.list_accounts())
                for owner in owners:
                    try:
                        chars = await self.service.sync_characters(owner)
                        messages.append(f"✅ {owner}: {len(chars)} 个角色")
                    except Exception as exc:
                        messages.append(f"❌ {owner}: {exc}")
                yield event.plain_result("全体角色更新完成\n" + "\n".join(messages))
            else:
                yield event.plain_result(self._sync_message("角色更新成功", await self.service.sync_characters(owner_id)))
        else:
            raise ValueError("用法：/sk char [update | set ark|ef <序号>]")

    async def _sign_command(self, event, owner_id, game, argv, *, role_index=None):
        action = argv[0].lower() if argv else "sign"
        if role_index is not None and ("--all" in argv or action == "all"):
            raise ValueError("指定角色与全体签到不能同时使用")
        if action in {"status", "状态"}:
            all_users = "--all" in argv
            if all_users:
                self._require_admin(event)
            role = await self.service.require_character(owner_id, game, role_index) if role_index else None
            rows = await self.store.get_sign_results(game, None if all_users else owner_id,
                character_id=role.id if role else None)
            lines = [f"{r['nickname']}（账号 {r['account_id']} / {r['channel_master_id']}）：{r['result']}（{r['updated_at']}）" for r in rows]
            yield event.plain_result("签到记录\n" + ("\n".join(lines) or "暂无签到记录"))
            return
        if action == "all":
            self._require_admin(event)
            results = await self.service.sign_all_accounts(game)
            lines = [f"{owner} / {char.nickname}（{char.display_server}）：{text}" for owner, char, text in results]
            yield event.plain_result("全体签到完成\n" + ("\n".join(lines) or "没有绑定账号"))
            return
        if action not in {"sign", "签到"}:
            raise ValueError("用法：arksign|efsign sign [--all] [-r 序号] | status")
        identity = role_index or next((arg for arg in argv[1:] if not arg.startswith("-")), None)
        results = await self.service.sign(owner_id, game, all_roles="--all" in argv, identity=identity)
        yield event.plain_result("签到结果\n" + "\n".join(
            f"{char.nickname}（账号 {char.account_id} / {char.display_server}）：{text}" for char, text in results))

    async def _bind_account(self, event, owner_id, secret):
        prepared = await self.service.bindings.prepare(owner_id, secret)
        await self.service.bindings.commit(prepared)
        await ark_card_cache.invalidate_owner(owner_id)
        try:
            await event.send(MessageChain([Plain(self._sync_message("绑定成功", prepared.characters))]))
        except Exception:
            logger.warning("账号已保存，但绑定成功消息发送失败。")
        return True

    async def _import_support_after_login(self, event, owner_id):
        group_id = str(event.get_group_id() or "").strip()
        if not group_id:
            return
        group = getattr(event.message_obj, "group", None)
        try:
            message = await import_support_after_login(
                self.context, owner_id=owner_id, group_umo=str(event.unified_msg_origin),
                platform_id=str(event.get_platform_id()), group_id=group_id,
                group_name=str(getattr(group, "group_name", "") or ""),
                member_nickname=str(event.get_sender_name() or ""),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Do not forward third-party exceptions, which may contain secrets.
            logger.warning("登录成功后的助战导入失败，账号绑定已保留。")
            message = "登录已成功，助战自动导入失败，旧快照已保留。可在本群发送 /助战 森空岛导入 重试。"
        if message:
            if isinstance(message, dict):
                images = message["images"] if message["role_count"] > 3 else []
                if images:
                    try:
                        components = []
                        for content in images:
                            result = await self._image_result(event, content, "support-import")
                            components.extend(result.chain)
                        await event.send(MessageChain(components))
                        return
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.warning("登录后的助战导入图片发送失败，回退短摘要。")
                message = message["short_text"] if message["role_count"] > 3 else message["text"]
            try:
                await event.send(MessageChain([Plain(message)]))
            except Exception:
                logger.warning("登录后的助战导入回执发送失败。")

    async def _unbind_interaction(self, event, owner_id):
        version = await self.store.owner_version(owner_id)
        accounts = await self.store.list_accounts(owner_id)
        if not accounts:
            raise ValueError("尚未绑定账号")
        card = await self.service.bindings.overview(owner_id, mode="unbind_selection")
        result = await self._image_result(event, await render_bound_roles_card(card), "unbind")
        choice = await wait_choice(event, (*[str(i) for i in range(1, len(accounts) + 1)], "全部", "取消"),
            MessageChain([*result.chain, Plain("请在 60 秒内回复账号序号、全部或取消。")]), tasks=self._interaction_tasks)
        if choice is None or choice == "取消":
            await event.send(MessageChain([Plain("已取消解绑。")]))
            return
        ids = [a.id for a in accounts] if choice == "全部" else [accounts[int(choice) - 1].id]
        card = await self.service.bindings.overview(owner_id, mode="unbind_confirmation", unbind_ids=ids)
        result = await self._image_result(event, await render_bound_roles_card(card), "unbind-confirm")
        choice = await wait_choice(event, ("确认", "取消"), MessageChain([*result.chain,
            Plain("确认解绑所选账号及其角色历史？请在 60 秒内回复 确认 或 取消。")]), tasks=self._interaction_tasks)
        if choice != "确认":
            await event.send(MessageChain([Plain("已取消解绑。")]))
            return
        await self.service.unbind(owner_id, ids, expected_version=version)
        await event.send(MessageChain([Plain(f"已解绑 {len(ids)} 个账号。")]))

    async def _qrcode_login(self, event, owner_id):
        raw_message = getattr(event.message_obj, "raw_message", None)
        author = getattr(raw_message, "author", None)
        avatar = await fetch_user_avatar(getattr(author, "avatar", None))
        scan_id = await SklandLoginAPI.get_scan()
        qr_image = render_qrcode_card(f"hypergryph://scan_login?scanId={scan_id}", avatar)
        qr_receipt = None
        tasks = set()
        async def poll():
            deadline = asyncio.get_running_loop().time() + 100
            while asyncio.get_running_loop().time() < deadline:
                try:
                    return await SklandLoginAPI.get_scan_status(scan_id)
                except SklandError:
                    await asyncio.sleep(2)
            return None
        try:
            prefix = [At(qq=event.get_sender_id())] if event.get_group_id() else []
            qr_receipt = await send_with_receipt(event, MessageChain([
                *prefix, Plain(" 请使用森空岛 App 扫码，二维码约 100 秒内有效；回复 取消 可中止。"), Image.fromBytes(qr_image)]))
            scan = asyncio.create_task(poll())
            cancel = asyncio.create_task(wait_choice(event, ("取消",), timeout=100, tasks=self._interaction_tasks))
            tasks.update((scan, cancel))
            self._interaction_tasks.update(tasks)
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if cancel in done:
                await event.send(event.plain_result("已取消扫码绑定。" if cancel.result() == "取消" else "二维码已超时，请重新执行 扫码绑定"))
                return False
            scan_code = scan.result()
            cancel.cancel()
            await asyncio.gather(cancel, return_exceptions=True)
            if not scan_code:
                await event.send(event.plain_result("二维码已超时，请重新执行 扫码绑定"))
                return False
            token = await SklandLoginAPI.get_token_by_scan_code(scan_code)
            return await self._bind_account(event, owner_id, token)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._interaction_tasks.difference_update(tasks)
            if qr_receipt is not None:
                try:
                    await qr_receipt.recall()
                except Exception:
                    logger.warning("扫码流程已结束，二维码撤回失败。")

    @staticmethod
    def _reply_id(event):
        return next((str(item.id) for item in event.get_messages() if isinstance(item, Reply)), "")

    async def _query_context(self, event, kind):
        owner, session, reply = self._owner_id(event), str(event.unified_msg_origin), self._reply_id(event)
        exact = await self.store.get_context(owner, session, kind, reply)
        if exact or not reply:
            return exact
        latest = await self.store.get_context(owner, session, kind)
        return latest if latest and latest.get("_has_receipt") is False else None

    async def _check_context_role(self, owner, game, index, cached):
        if index is not None:
            char = await self.service.require_character(owner, game, index)
            if char.id != cached.get("role_id"):
                raise ValueError("引用内容与所选角色不一致，请重新查询该角色")

    @staticmethod
    def _background_component(uri):
        from urllib.parse import urlparse
        from urllib.request import url2pathname
        if uri.startswith("data:image/"):
            return Image.fromBase64(uri.split(",", 1)[1])
        if uri.startswith("file://"):
            return Image.fromFileSystem(url2pathname(urlparse(uri).path))
        return Image.fromURL(uri)

    async def _send_context_image(self, event, content, prefix, contexts):
        result = await self._image_result(event, content, prefix)
        receipt = await send_with_receipt(event, MessageChain(result.chain))
        for kind, payload in contexts.items():
            await self.store.save_context(self._owner_id(event), str(event.unified_msg_origin), kind, {**payload, "_has_receipt": receipt is not None},
                message_id=receipt.message_id if receipt else "", ttl=runtime_config.context_ttl)

    @classmethod
    def _check_role_target(cls, event, target, index):
        if index is not None and target != cls._owner_id(event):
            raise ValueError("查询他人仅使用其默认角色，不能指定角色序号")

    async def _manage_shortcuts(self, event, argv):
        if not argv or argv == ["list"]:
            return "动态快捷指令\n" + ("\n".join(f"{name} → /sk {shlex.join(args)}" for name, args in self._shortcuts.items()) or "暂无")
        self._require_admin(event)
        if argv[0] == "add" and len(argv) >= 3:
            name, args = argv[1], argv[2:]
            validate_shortcut(name, args, BUILTIN_COMMANDS, self._shortcuts)
            await self.store.save_shortcut(name, args)
        elif argv[0] == "remove" and len(argv) == 2:
            name = argv[1]
            if name not in self._shortcuts:
                raise ValueError("快捷指令不存在")
            await self.store.save_shortcut(name, None)
        else:
            raise ValueError("用法：/sk shortcut add <名称> <子命令及参数> | list | remove <名称>")
        self._shortcuts = await self.store.shortcuts()
        return "快捷指令已更新，全局生效。"

    async def _startup_resources(self):
        try:
            await game_data.load()
            if self.config.get("check_res_update", False):
                await sync_images()
        except Exception as exc:
            logger.warning("启动资源更新失败：%s", exc)

    async def _daily_resource_loop(self):
        while True:
            now = datetime.now()
            target = now.replace(hour=9, minute=0, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            await asyncio.sleep((target - now).total_seconds())
            try:
                await game_data.load(refresh_metadata=True)
            except Exception as exc:
                logger.warning("每日数据更新失败：%s", exc)

    async def _image_result(self, event: AstrMessageEvent, content: bytes, prefix: str):
        try:
            prepared = image_output.prepare(event.get_platform_name(), content, prefix)
        except ImageDeliveryError as exc:
            raise ValueError(str(exc)) from exc
        return event.chain_result([Image.fromBytes(prepared)])

    @staticmethod
    def _box_arguments(args: list[str]) -> tuple[list[str], dict[str, str]]:
        aliases = {
            "-o": "ownership", "--ownership": "ownership", "ownership": "ownership",
            "--rarity": "rarities", "rarity": "rarities",
            "-p": "professions", "--profession": "professions", "profession": "professions",
            "-b": "branches", "--branch": "branches", "branch": "branches",
            "--position": "positions", "position": "positions",
            "--gender": "genders", "gender": "genders",
            "-f": "factions", "--faction": "factions", "faction": "factions",
            "--race": "races", "race": "races",
            "--potential": "potentials", "potential": "potentials",
            "-s": "sort", "--sort": "sort", "sort": "sort",
            "-n": "name", "--name": "name", "name": "name",
        }
        filters: list[str] = []
        options: dict[str, str] = {}
        index = 0
        while index < len(args):
            key = aliases.get(args[index])
            if key is None:
                filters.append(args[index])
                index += 1
                continue
            if index + 1 >= len(args):
                raise ValueError(f"参数 {args[index]} 缺少值")
            options[key] = args[index + 1]
            index += 2
        return filters, options

    @classmethod
    def _box_target_owner(
        cls, event: AstrMessageEvent, args: list[str]
    ) -> tuple[str, list[str]]:
        platform = event.get_platform_id()
        for segment in event.get_messages():
            if isinstance(segment, At) and str(segment.qq) not in {
                "all",
                str(event.get_self_id()),
            }:
                return f"{platform}:{segment.qq}", args

        target_markers = {"用户", "user", "--user", "--target", "目标"}
        if len(args) >= 2 and args[0].casefold() in target_markers:
            if not args[1].isdigit():
                raise ValueError(f"{args[0]} 后需要填写平台数字用户 ID")
            return f"{platform}:{args[1]}", args[2:]
        return cls._owner_id(event), args

    def _rogue_background(self, topic: str) -> str:
        mapping = {
            "rogue_1": "pic_rogue_1_KV1.png",
            "rogue_2": "pic_rogue_2_50.png",
            "rogue_3": "pic_rogue_3_KV2.png",
            "rogue_4": "pic_rogue_4_47.png",
            "rogue_5": "pic_rogue_5_KV1.png",
            "rogue_6": "pic_rogue_6_kv1.png",
        }
        filename = mapping.get(topic, "kv_epoque14.png")
        return (RES_DIR / "images" / "background" / "rogue" / filename).as_uri()

    async def _daily_sign_loop(self) -> None:
        hour = max(0, min(23, int(self.config.get("auto_sign_hour", 0))))
        minute = max(0, min(59, int(self.config.get("auto_sign_minute", 15))))
        while True:
            now = datetime.now()
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            await asyncio.sleep((target - now).total_seconds())
            for game in (ARKNIGHTS, ENDFIELD):
                with suppress(Exception):
                    await self.service.sign_all_accounts(game)

    @staticmethod
    def _range_options(args: list[str]) -> tuple[int | None, int | None]:
        def value(flags: set[str]) -> int | None:
            for index, arg in enumerate(args[:-1]):
                if arg in flags:
                    return int(args[index + 1])
            return None

        return value({"-b", "--begin", "begin"}), value({"-l", "--limit", "limit"})

    @staticmethod
    def _page_ranges(
        begin: int | None, limit: int | None, total: int, page_size: int
    ) -> list[tuple[int, int]]:
        start, end, _ = slice(begin, limit).indices(total)
        if start >= end:
            raise ValueError("抽卡卡池范围为空，请检查 begin/limit 参数")
        return [
            (index, min(index + page_size, end))
            for index in range(start, end, page_size)
        ]

    @staticmethod
    def _option_or_positional(args: list[str], flags: set[str]) -> str | None:
        for index, arg in enumerate(args[:-1]):
            if arg in flags:
                return args[index + 1]
        return next((arg for arg in args if not arg.startswith("-")), None)

    @staticmethod
    def _owner_id(event: AstrMessageEvent) -> str:
        return f"{event.get_platform_id()}:{event.get_sender_id()}"

    @classmethod
    def _target_owner(
        cls, event: AstrMessageEvent, args: list[str]
    ) -> tuple[str, list[str]]:
        platform = event.get_platform_id()
        for segment in event.get_messages():
            if isinstance(segment, At) and str(segment.qq) not in {
                "all",
                str(event.get_self_id()),
            }:
                return f"{platform}:{segment.qq}", args
        if args and args[0].isdigit():
            return f"{platform}:{args[0]}", args[1:]
        return cls._owner_id(event), args

    def _deployment_info(self, event) -> str:
        # Inspect the actual loaded functions, not metadata.yaml on disk: a
        # partial manual copy can leave the API or service from an old release.
        service_function = getattr(self.service._with_refresh, "__func__", self.service._with_refresh)
        service_globals = getattr(service_function, "__globals__", {})
        api = service_globals.get("SklandAPI")
        api_method = getattr(api, "ark_card_raw", None)
        api_function = getattr(api_method, "__func__", api_method)
        api_code = getattr(api_function, "__code__", None)
        service_code = getattr(service_function, "__code__", None)
        api_loaded = api_code is not None and 401 in api_code.co_consts and "UnauthorizedException" in api_code.co_names
        service_loaded = service_code is not None and "credentials_regenerated" in service_code.co_varnames
        lines = [f"森空岛助手运行版本：{PLUGIN_VERSION}",
                 f"角色接口 401 恢复：{'已加载' if api_loaded else '未加载或无法确认'}",
                 f"凭证自动恢复链：{'已加载' if service_loaded else '未加载或无法确认'}"]
        if not api_loaded or not service_loaded:
            lines.append("请完整覆盖实际插件目录中的 skland 文件夹，再重启 AstrBot。")
        if event.is_admin():
            for label, function in (("角色接口文件", api_function), ("凭证服务文件", service_function)):
                path = getattr(function, "__globals__", {}).get("__file__")
                if path:
                    lines.append(f"{label}：{Path(path).resolve()}")
        return "\n".join(lines)

    async def _send_private_reminder(self, private_umo: str, content: str) -> None:
        await send_private_text(self.context, private_umo, content)

    async def _private_target(self, event, owner_id: str) -> str:
        remembered = await self.store.private_session(owner_id)
        if remembered:
            return remembered
        source = getattr(getattr(event, "message_obj", None), "raw_message", None)
        user_id = getattr(getattr(source, "author", None), "user_openid", None) or event.get_sender_id()
        return f"{event.get_platform_id()}:FriendMessage:{user_id}"

    async def _sanity_command(self, event, owner_id: str, argv: list[str]) -> str:
        action, args = (argv[0].lower(), argv[1:]) if argv else ("status", [])
        async with self._sanity_monitor.owner_lock(owner_id):
            if action == "status":
                if args:
                    raise ValueError("用法：/理智订阅状态")
                subscriptions = await self.store.list_sanity_subscriptions(owner_id)
                if not subscriptions:
                    return "尚无理智订阅。使用 /理智订阅 [方舟|终末地] [提前分钟] [-r 序号]。"
                roles = {r.id: r for r in await self.store.get_characters(owner_id, include_unavailable=True)}
                lines = ["理智订阅状态："]
                for sub in subscriptions:
                    role = roles.get(sub.character_id)
                    if role is None:
                        continue
                    lines.append(f"{GAME_NAMES[role.app_code]} · {role.nickname}（{role.display_server}）")
                    state, read_error = None, None
                    if role.is_available:
                        now = self._sanity_monitor.clock()
                        version = await self.store.owner_version(owner_id)
                        try:
                            state = await asyncio.wait_for(self.service.sanity(role, now=now), timeout=30)
                            recovery = recovered_data_error(sub)
                            if await self.store.owner_version(owner_id) != version:
                                state, read_error = None, "账号或角色已变化，请重新查询。"
                            elif not await self.store.update_sanity_subscription(sub, current=state.current,
                                    maximum=state.maximum, full_at=state.full_at, last_checked=now, **recovery):
                                state, read_error = None, "订阅已变化，请重新查询。"
                            else:
                                for key, value in recovery.items():
                                    setattr(sub, key, value)
                        except Exception:
                            state, read_error = None, "暂时无法获取当前理智，请稍后重试。"
                    else:
                        read_error = "角色暂不可用。"
                    if state is not None:
                        lines.extend(sanity_details(state))
                    else:
                        lines.append(f"当前理智：{read_error}")
                        if sub.last_checked and sub.current is not None:
                            checked = format_sanity_time(sub.last_checked)
                            lines.append(f"上次记录理智：{sub.current}/{sub.maximum}（{checked}）")
                            if sub.full_at is not None:
                                lines.append(f"上次预计满值时间：{format_sanity_time(sub.full_at)}")
                    status = ("角色暂不可用" if not role.is_available else "私信投递暂停，请执行 /理智提醒测试" if sub.blocked
                              else "等待重试" if sub.retry_at > self._sanity_monitor.clock() else "本轮已提醒" if sub.notified else "等待接近满值")
                    lines.append(f"提醒：提前 {sub.advance_minutes} 分钟，{status}")
                    if sub.last_error:
                        lines.append(f"  最近失败：{sub.last_error}")
                return "\n".join(lines)
            if action == "test":
                if args:
                    raise ValueError("用法：/理智提醒测试")
                await self._sanity_monitor.test_private(owner_id, await self._private_target(event, owner_id))
                return "测试私信已发送；暂停的订阅已恢复。"
            if action not in {"subscribe", "unsubscribe"}:
                raise ValueError("用法：/sk sanity subscribe|unsubscribe|status|test")
            role_index, args = take_role(args)
            game = ARKNIGHTS
            if args and args[0].lower() in GAME_ALIASES:
                game = GAME_ALIASES[args.pop(0).lower()]
            if action == "unsubscribe" and args == ["--all"]:
                if role_index is not None:
                    raise ValueError("--all 不能与 -r 同时使用")
                count = await self.store.unsubscribe_sanity(owner_id, game)
                return f"已取消 {GAME_NAMES[game]} 的 {count} 项理智订阅。"
            if action == "subscribe":
                if len(args) > 1:
                    raise ValueError("用法：/理智订阅 [方舟|终末地] [提前分钟] [-r 序号]")
                try:
                    minutes = int(args[0]) if args else 10
                except ValueError:
                    raise ValueError("提前分钟须为非负整数，游戏可填 方舟/终末地/ark/ef") from None
                if minutes < 0:
                    raise ValueError("提前分钟须为非负整数")
                role = await self.service.require_character(owner_id, game, role_index)
                await self.store.subscribe_sanity(owner_id, role.id, await self._private_target(event, owner_id), minutes)
                return f"已订阅 {GAME_NAMES[game]} · {role.nickname}（{role.display_server}），提前 {minutes} 分钟私信提醒。\n{PRIVATE_HINT}"
            if args:
                raise ValueError("用法：/理智取消 [方舟|终末地] [-r 序号|--all]")
            role = await self.service.require_character(owner_id, game, role_index)
            count = await self.store.unsubscribe_sanity(owner_id, game, role.id)
            return f"已取消 {role.nickname} 的理智订阅。" if count else "该角色尚未订阅。"

    @staticmethod
    def _require_admin(event: AstrMessageEvent) -> None:
        if not event.is_admin():
            raise ValueError("该指令仅 AstrBot 管理员可用")

    @staticmethod
    def _sync_message(title: str, chars) -> str:
        lines = [
            f"- {char.app_code}: {char.nickname} ({char.role_id or char.uid})"
            for char in chars
        ]
        return f"{title}，已同步 {len(chars)} 个角色\n" + (
            "\n".join(lines) or "账号下没有角色"
        )
