import asyncio
import shlex
from collections.abc import AsyncGenerator
from contextlib import suppress
from datetime import datetime, timedelta
from io import BytesIO

import qrcode
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At, Image, Plain
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.star.filter.command import GreedyStr

from .skland.access import CredentialCommandAccessPolicy
from .skland.api import SklandLoginAPI
from .skland.cards import (
    render_ark_card,
    render_clue_board,
    render_ef_card,
    render_ef_gacha_history,
    render_gacha_history,
    render_rogue_card,
    render_rogue_info,
)
from .skland.config import RES_DIR, configure_paths
from .skland.config import config as runtime_config
from .skland.exception import SklandError
from .skland.gacha import group_arknights_records, group_endfield_records
from .skland.renderer import RenderError, renderer
from .skland.resourcesync import game_data, sync_images
from .skland.service import ARKNIGHTS, ENDFIELD, SklandService
from .skland.store import SklandStore

HELP = """森空岛助手（sk / skland）
绑定：森空岛绑定 <token|cred>｜扫码绑定｜森空岛解绑 确认
角色：/sk card｜/sk efcard [-a] [-s]｜角色更新
签到：明日方舟签到｜签到详情｜终末地签到｜终末地签到详情
肉鸽：界园/萨卡兹/萨米/水月/傀影肉鸽｜战绩详情 <序号> [-f]
抽卡：方舟抽卡记录 [-b 起始] [-l 结束]｜导入抽卡记录 <URL>
      终末地抽卡记录｜终末地抽卡更新
其他：/sk clue｜资源更新｜/sk help
管理员：全体签到｜全体签到详情｜终末地全体签到｜全体角色更新"""


@register(
    "astrbot_plugin_skland", "AoiNyanko", "森空岛明日方舟/终末地查询与签到插件", "2.0.0"
)
class SklandPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context, config)
        self.config = config
        self.data_dir = StarTools.get_data_dir("astrbot_plugin_skland")
        self.store = SklandStore(self.data_dir / "skland.sqlite3")
        self.service = SklandService(self.store)
        self.credential_access = CredentialCommandAccessPolicy.from_values([])
        self._daily_task: asyncio.Task | None = None

    async def initialize(self) -> None:
        self.credential_access = CredentialCommandAccessPolicy.from_values(
            self.config.get("credential_group_whitelist", [])
        )
        configure_paths(self.data_dir)
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
        renderer.configure(
            self.data_dir, str(self.config.get("browser_executable", ""))
        )
        await self.store.initialize()
        game_data.load_cached()
        if self.config.get("auto_sign", False):
            self._daily_task = asyncio.create_task(self._daily_sign_loop())
        logger.info("astrbot_plugin_skland initialized with image rendering")

    async def terminate(self) -> None:
        if self._daily_task:
            self._daily_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._daily_task
        await renderer.close()

    @filter.command("skland", alias={"sk"})
    async def skland(self, event: AstrMessageEvent, args: GreedyStr = ""):
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

    # 中文快捷指令：绑定相关
    @filter.command("森空岛绑定")
    async def shortcut_bind(self, event: AstrMessageEvent, args: GreedyStr = ""):
        async for result in self._run(event, ["bind", *shlex.split(str(args))]):
            yield result

    @filter.command("扫码绑定")
    async def shortcut_qrcode(self, event: AstrMessageEvent):
        async for result in self._run(event, ["qrcode"]):
            yield result

    @filter.command("森空岛解绑")
    async def shortcut_unbind(self, event: AstrMessageEvent, confirm: str = ""):
        async for result in self._run(event, ["unbind", confirm]):
            yield result

    # 中文快捷指令：签到、角色和资源
    @filter.command("明日方舟签到")
    async def shortcut_arksign(self, event: AstrMessageEvent):
        async for result in self._run(event, ["arksign", "sign", "--all"]):
            yield result

    @filter.command("签到详情")
    async def shortcut_sign_status(self, event: AstrMessageEvent):
        async for result in self._run(event, ["arksign", "status"]):
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
    async def shortcut_efsign(self, event: AstrMessageEvent):
        async for result in self._run(event, ["efsign", "sign", "--all"]):
            yield result

    @filter.command("终末地签到详情")
    async def shortcut_efsign_status(self, event: AstrMessageEvent):
        async for result in self._run(event, ["efsign", "status"]):
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
    async def shortcut_char_update(self, event: AstrMessageEvent):
        async for result in self._run(event, ["char", "update"]):
            yield result

    @filter.command("全体角色更新")
    async def shortcut_char_update_all(self, event: AstrMessageEvent):
        async for result in self._run(event, ["char", "update", "--all"]):
            yield result

    @filter.command("资源更新")
    async def shortcut_sync(self, event: AstrMessageEvent, args: GreedyStr = ""):
        async for result in self._run(event, ["sync", *shlex.split(str(args))]):
            yield result

    # 中文快捷指令：抽卡和详情
    @filter.command("方舟抽卡记录")
    async def shortcut_gacha(self, event: AstrMessageEvent, args: GreedyStr = ""):
        async for result in self._run(event, ["gacha", *shlex.split(str(args))]):
            yield result

    @filter.command("导入抽卡记录")
    async def shortcut_import(self, event: AstrMessageEvent, url: str):
        async for result in self._run(event, ["import", url]):
            yield result

    @filter.command("终末地抽卡记录")
    async def shortcut_efgacha(self, event: AstrMessageEvent):
        async for result in self._run(event, ["efgacha"]):
            yield result

    @filter.command("终末地抽卡更新")
    async def shortcut_efgacha_update(self, event: AstrMessageEvent):
        async for result in self._run(event, ["efgacha", "-u"]):
            yield result

    @filter.command("ef")
    async def shortcut_efcard(self, event: AstrMessageEvent, args: GreedyStr = ""):
        async for result in self._run(event, ["efcard", *shlex.split(str(args))]):
            yield result

    @filter.command("zmd")
    async def shortcut_zmd_card(self, event: AstrMessageEvent, args: GreedyStr = ""):
        async for result in self._run(event, ["efcard", *shlex.split(str(args))]):
            yield result

    @filter.command("战绩详情")
    async def shortcut_rginfo(self, event: AstrMessageEvent, args: GreedyStr = ""):
        async for result in self._run(event, ["rginfo", *shlex.split(str(args))]):
            yield result

    @filter.command("收藏战绩详情")
    async def shortcut_rginfo_favored(self, event: AstrMessageEvent, record_id: int):
        async for result in self._run(event, ["rginfo", str(record_id), "-f"]):
            yield result

    # 中文肉鸽快捷指令
    @filter.command("界园肉鸽")
    async def shortcut_rogue_5(self, event: AstrMessageEvent):
        async for result in self._run(event, ["rogue", "界园"]):
            yield result

    @filter.command("萨卡兹肉鸽")
    async def shortcut_rogue_4(self, event: AstrMessageEvent):
        async for result in self._run(event, ["rogue", "萨卡兹"]):
            yield result

    @filter.command("萨米肉鸽")
    async def shortcut_rogue_3(self, event: AstrMessageEvent):
        async for result in self._run(event, ["rogue", "萨米"]):
            yield result

    @filter.command("水月肉鸽")
    async def shortcut_rogue_2(self, event: AstrMessageEvent):
        async for result in self._run(event, ["rogue", "水月"]):
            yield result

    @filter.command("傀影肉鸽")
    async def shortcut_rogue_1(self, event: AstrMessageEvent):
        async for result in self._run(event, ["rogue", "傀影"]):
            yield result

    async def _run(self, event: AstrMessageEvent, argv: list[str]) -> AsyncGenerator:
        owner_id = self._owner_id(event)
        try:
            command = argv[0].lower() if argv else "card"
            if command in {"help", "帮助", "-h", "--help"}:
                yield event.plain_result(HELP)
            elif command == "bind":
                self._require_credential_access(event)
                if len(argv) < 2:
                    raise ValueError("用法：森空岛绑定 <24位token|32位cred>")
                chars = await self.service.bind(owner_id, argv[1])
                yield event.plain_result(self._sync_message("绑定成功", chars))
            elif command == "qrcode":
                self._require_credential_access(event)
                async for result in self._qrcode_login(event, owner_id):
                    yield result
            elif command == "unbind":
                self._require_credential_access(event)
                if len(argv) < 2 or argv[1].lower() not in {"confirm", "确认"}:
                    raise ValueError(
                        "该操作会删除所有绑定数据，请发送：森空岛解绑 确认"
                    )
                deleted = await self.store.delete_account(owner_id)
                yield event.plain_result("解绑成功" if deleted else "当前账号尚未绑定")
            elif command == "char":
                async for result in self._char_update(event, owner_id, argv):
                    yield result
            elif command in {"card", "efcard"}:
                yield await self._card_result(event, owner_id, command, argv[1:])
            elif command == "clue":
                _, card = await self.service.card(owner_id, ARKNIGHTS)
                yield await self._image_result(
                    event, await render_clue_board(card.building.meeting.clue), "clue"
                )
            elif command == "rogue":
                target_owner, args = self._target_owner(event, argv[1:])
                topic = self._option_or_positional(args, {"-t", "--topic", "topic"})
                _, data = await self.service.rogue(target_owner, topic)
                if target_owner != owner_id:
                    await self.store.save_rogue_cache(
                        owner_id, data.model_dump(mode="json")
                    )
                background = self._rogue_background(data.topic)
                yield await self._image_result(
                    event, await render_rogue_card(data, background), "rogue"
                )
            elif command == "rginfo":
                if len(argv) < 2:
                    raise ValueError("用法：战绩详情 <序号> [-f]")
                data = await self.service.rogue_detail(owner_id)
                record_id = int(argv[1])
                favored = "-f" in argv or "--favored" in argv
                image = await render_rogue_info(
                    data, self._rogue_background(data.topic), record_id, favored
                )
                yield await self._image_result(event, image, "rogue-info")
            elif command == "gacha":
                target_owner, args = self._target_owner(event, argv[1:])
                begin, limit = self._range_options(args)
                char, records, added = await self.service.arknights_gacha(target_owner)
                _, card = await self.service.card(target_owner, ARKNIGHTS)
                grouped = group_arknights_records(records)
                pages = self._page_ranges(
                    begin, limit, len(grouped.pools), runtime_config.gacha_render_max
                )
                if len(pages) > 1:
                    yield event.plain_result(
                        f"抽卡记录较多，将发送 {len(pages)} 张图片"
                    )
                for page, (start, end) in enumerate(pages, 1):
                    image = await render_gacha_history(
                        grouped, char, card.status, start, end
                    )
                    yield await self._image_result(
                        event, image, f"gacha-{added}-new-{page}"
                    )
            elif command == "import":
                if len(argv) < 2:
                    raise ValueError("用法：导入抽卡记录 <小黑盒导出URL>")
                total, added = await self.service.import_heybox(owner_id, argv[1])
                yield event.plain_result(f"导入成功：读取 {total} 条，新增 {added} 条")
            elif command == "efgacha":
                target_owner, args = self._target_owner(event, argv[1:])
                begin, limit = self._range_options(args)
                update = any(flag in argv for flag in ("-u", "--update", "update"))
                char, records, added = await self.service.endfield_gacha(
                    target_owner, update=update
                )
                _, card = await self.service.card(target_owner, ENDFIELD)
                grouped = group_endfield_records(records)
                await self.service.enrich_endfield_pools(grouped, char)
                pages = self._page_ranges(
                    begin,
                    limit,
                    grouped.max_category_pool_count,
                    runtime_config.ef_gacha_render_max,
                )
                if len(pages) > 1:
                    yield event.plain_result(
                        f"抽卡记录较多，将发送 {len(pages)} 张图片"
                    )
                for page, (start, end) in enumerate(pages, 1):
                    image = await render_ef_gacha_history(
                        grouped, card.base, char, start, end
                    )
                    yield await self._image_result(
                        event, image, f"efgacha-{added}-new-{page}"
                    )
            elif command in {"arksign", "efsign"}:
                game = ARKNIGHTS if command == "arksign" else ENDFIELD
                async for result in self._sign_command(event, owner_id, game, argv[1:]):
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
                    version, downloaded, failed = await game_data.load(force=force)
                    messages.append(
                        f"数据资源：版本 {version or '本地缓存'}，下载 {downloaded}，失败 {failed}"
                    )
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
        except (ValueError, SklandError, RenderError) as exc:
            yield event.plain_result(f"操作失败：{exc}")
        except Exception as exc:
            logger.exception("Skland command failed")
            yield event.plain_result(f"森空岛请求失败：{type(exc).__name__}: {exc}")

    async def _card_result(self, event, owner_id: str, command: str, args: list[str]):
        game = ARKNIGHTS if command == "card" else ENDFIELD
        target_owner, args = self._target_owner(event, args)
        flags = {arg for arg in args if arg.startswith("-")}
        _, card = await self.service.card(target_owner, game)
        if game == ARKNIGHTS:
            background = RES_DIR / "images" / "background" / "bg.jpg"
            image = await render_ark_card(card, background.as_uri())
        else:
            background = (
                RES_DIR / "images" / "background" / "endfield" / "default_bg.jpg"
            )
            image = await render_ef_card(
                card,
                background.as_uri(),
                show_all=bool(flags & {"-a", "--all"}),
                is_simple=bool(flags & {"-s", "--simple"}),
            )
        return await self._image_result(event, image, command)

    async def _char_update(self, event, owner_id: str, argv: list[str]):
        if len(argv) < 2 or argv[1] not in {"update", "更新"}:
            raise ValueError("用法：角色更新")
        if "--all" in argv:
            self._require_admin(event)
            messages = []
            for account in await self.store.list_accounts():
                try:
                    messages.append(
                        f"✅ {account.owner_id}: {len(await self.service.sync_characters(account.owner_id))} 个角色"
                    )
                except Exception as exc:
                    messages.append(f"❌ {account.owner_id}: {exc}")
            yield event.plain_result("全体角色更新完成\n" + "\n".join(messages))
        else:
            yield event.plain_result(
                self._sync_message(
                    "角色更新成功", await self.service.sync_characters(owner_id)
                )
            )

    async def _sign_command(self, event, owner_id: str, game: str, argv: list[str]):
        action = argv[0].lower() if argv else "sign"
        if action in {"status", "状态"}:
            all_users = "--all" in argv
            if all_users:
                self._require_admin(event)
            rows = await self.store.get_sign_results(
                game, None if all_users else owner_id
            )
            text = "\n".join(
                f"{row['nickname']}：{row['result']}（{row['updated_at']}）"
                for row in rows
            )
            yield event.plain_result("签到记录\n" + (text or "暂无签到记录"))
            return
        if action == "all":
            self._require_admin(event)
            results = await self.service.sign_all_accounts(game)
            yield event.plain_result(
                "全体签到完成\n"
                + (
                    "\n".join(f"{char.nickname}：{text}" for _, char, text in results)
                    or "没有绑定账号"
                )
            )
            return
        if action not in {"sign", "签到"}:
            raise ValueError("用法：arksign|efsign sign [--all] [角色ID] | status")
        all_roles = "--all" in argv
        identity = next((arg for arg in argv[1:] if not arg.startswith("-")), None)
        results = await self.service.sign(
            owner_id, game, all_roles=all_roles, identity=identity
        )
        yield event.plain_result(
            "签到结果\n"
            + "\n".join(f"{char.nickname}：{text}" for char, text in results)
        )

    async def _qrcode_login(self, event: AstrMessageEvent, owner_id: str):
        scan_id = await SklandLoginAPI.get_scan()
        image = qrcode.make(f"hypergryph://scan_login?scanId={scan_id}")
        qr_buffer = BytesIO()
        image.save(qr_buffer, format="PNG")
        yield event.chain_result(
            [
                Plain("请使用森空岛 App 扫码，二维码约 100 秒内有效。"),
                Image.fromBytes(qr_buffer.getvalue()),
            ]
        )
        deadline = asyncio.get_running_loop().time() + 100
        scan_code = None
        while asyncio.get_running_loop().time() < deadline:
            try:
                scan_code = await SklandLoginAPI.get_scan_status(scan_id)
                break
            except SklandError:
                await asyncio.sleep(2)
        if not scan_code:
            yield event.plain_result("二维码已超时，请重新执行 扫码绑定")
            return
        token = await SklandLoginAPI.get_token_by_scan_code(scan_code)
        chars = await self.service.bind_scan_token(owner_id, token)
        yield event.plain_result(self._sync_message("扫码绑定成功", chars))

    async def _image_result(self, event: AstrMessageEvent, content: bytes, prefix: str):
        return event.chain_result([Image.fromBytes(content)])

    def _rogue_background(self, topic: str) -> str:
        mapping = {
            "rogue_1": "pic_rogue_1_KV1.png",
            "rogue_2": "pic_rogue_2_50.png",
            "rogue_3": "pic_rogue_3_KV2.png",
            "rogue_4": "pic_rogue_4_47.png",
            "rogue_5": "pic_rogue_5_KV1.png",
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
        start = max(0, begin or 0)
        end = min(total, limit) if limit is not None else total
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

    def _require_credential_access(self, event: AstrMessageEvent) -> None:
        group_id = str(event.get_group_id()).strip()
        unified_origin = str(getattr(event, "unified_msg_origin", "")).strip()
        if not self.credential_access.allows(group_id, unified_origin):
            raise ValueError(
                "绑定相关指令仅允许在私聊或白名单群中使用；"
                f"当前群 ID：{group_id}。请将群 ID 或 /sid 返回的会话 ID "
                "加入插件配置 credential_group_whitelist"
            )

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
