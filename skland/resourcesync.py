"""Persistent game-data synchronization, independent from NoneBot."""

import asyncio
import json
import logging
from typing import Any
from urllib.parse import quote

import httpx

from . import config as paths
from .exception import RequestException
from .schemas import CharTable, GachaDetails, GachaTable

logger = logging.getLogger("astrbot")


class GameDataRepository:
    RAW = "https://raw.githubusercontent.com/yuanyan3060/ArknightsGameResource/main/"
    VERSION = RAW + "version"
    DETAILS = "https://weedy.prts.wiki/gacha_table.json"
    EF_POOLS = "https://raw.githubusercontent.com/FrostN0v0/EndfieldGachaPoolTable/master/GachaPoolTable.json"
    FILES = ("gamedata/excel/gacha_table.json", "gamedata/excel/character_table.json")

    def __init__(self) -> None:
        self.gacha_table: list[GachaTable] = []
        self.gacha_details: list[GachaDetails] = []
        self.character_table: list[CharTable] = []
        self.ef_pool_table: dict[str, Any] = {}

    @staticmethod
    def proxy(url: str) -> str:
        prefix = paths.config.github_proxy_url
        return f"{prefix}{url}" if prefix else url

    async def load(self, force: bool = False) -> tuple[str | None, int, int]:
        root = paths.DATA_DIR
        version_file = root / "version"
        local = (
            version_file.read_text("utf-8").strip() if version_file.exists() else None
        )
        downloaded = failed = 0
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                version_response = await client.get(self.proxy(self.VERSION))
                version_response.raise_for_status()
                remote = version_response.text.strip()
                missing = any(not (root / route).exists() for route in self.FILES)
                if force or missing or local != remote:
                    for route in self.FILES:
                        response = await client.get(self.proxy(self.RAW + route))
                        response.raise_for_status()
                        target = root / route
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(response.content)
                        downloaded += 1
                    version_file.write_text(remote, "utf-8")
                await self._optional(client, root)
                local = remote
        except httpx.HTTPError as exc:
            failed = 1
            if not all((root / route).exists() for route in self.FILES):
                raise RequestException(
                    f"游戏数据下载失败：{type(exc).__name__}: {exc}"
                ) from exc
            logger.warning("游戏数据更新失败，使用本地缓存：%s", exc)
        self._parse()
        return local, downloaded, failed

    async def _optional(self, client: httpx.AsyncClient, root) -> None:
        details = await client.get(self.DETAILS)
        details.raise_for_status()
        (root / "gacha_details.json").write_bytes(details.content)
        try:
            response = await client.get(self.proxy(self.EF_POOLS))
            response.raise_for_status()
            target = root / "endfield" / "GachaPoolTable.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(response.content)
        except httpx.HTTPError as exc:
            logger.warning("终末地卡池表更新失败：%s", exc)

    def load_cached(self) -> bool:
        if not all((paths.DATA_DIR / route).exists() for route in self.FILES):
            return False
        self._parse()
        return True

    def _parse(self) -> None:
        try:
            root = paths.DATA_DIR
            chars = json.loads((root / self.FILES[1]).read_text("utf-8"))
            self.character_table = []
            for char_id, value in chars.items():
                char = CharTable(**value)
                char.char_id = char_id
                self.character_table.append(char)
            pools = json.loads((root / self.FILES[0]).read_text("utf-8"))
            self.gacha_table = [
                GachaTable(**item) for item in pools.get("gachaPoolClient", [])
            ]
            detail_file = root / "gacha_details.json"
            if detail_file.exists():
                data = json.loads(detail_file.read_text("utf-8"))
                self.gacha_details = [
                    GachaDetails(**item) for item in data.get("gachaPoolClient", [])
                ]
            ef_file = root / "endfield" / "GachaPoolTable.json"
            if ef_file.exists():
                self.ef_pool_table = json.loads(ef_file.read_text("utf-8"))
        except (OSError, ValueError, KeyError) as exc:
            raise RequestException(f"游戏数据解析失败：{exc}") from exc

    def char_id(self, name: str) -> str:
        name = "麒麟R夜刀" if name == "麒麟X夜刀" else name
        return next(
            (x.char_id for x in self.character_table if x.name == name),
            "char_601_cguard",
        )

    def pool_id(self, name: str, timestamp: int) -> str:
        special = {
            "中坚寻访": {4, 6, 10},
            "标准寻访": {0, 9},
            "中坚甄选": {6},
            "联合行动": {0},
            "常驻标准寻访": {0},
            "【联合行动】特选干员定向寻访": {0},
            "进攻-防守-战术交汇": {2},
            "前路回响": {0},
        }
        for pool in self.gacha_table:
            matches = pool.gachaPoolName == name or pool.gachaRuleType in special.get(
                name, set()
            )
            if pool.openTime <= timestamp <= pool.endTime and matches:
                return pool.gachaPoolId
        return "NORM_1_0_1"

    def pool_info(self, pool_id: str) -> tuple[int, int, int, list[str], list[str]]:
        table = next((x for x in self.gacha_table if x.gachaPoolId == pool_id), None)
        five: list[str] = []
        six: list[str] = []
        detail = next((x for x in self.gacha_details if x.gachaPoolId == pool_id), None)
        if detail:
            info = detail.gachaPoolDetail.detailInfo
            source = (
                info.upCharInfo.perCharList
                if info.upCharInfo
                else info.availCharInfo.perAvailList
                if info.availCharInfo
                else []
            )
            for item in source:
                if item.rarityRank == 4:
                    five = item.charIdList
                elif item.rarityRank == 5:
                    six = item.charIdList
        if table:
            return table.openTime, table.endTime, table.gachaRuleType, five, six
        return 0, 0, 0, five, six


async def sync_images(force: bool = False, update: bool = False) -> tuple[int, int]:
    """Synchronize avatar, portrait and skill caches from the upstream resource repo."""
    api = "https://api.github.com/repos/yuanyan3060/ArknightsGameResource/git/trees/main?recursive=1"
    headers = (
        {"Authorization": f"Bearer {paths.config.github_token}"}
        if paths.config.github_token
        else {}
    )
    try:
        async with httpx.AsyncClient(
            timeout=60, follow_redirects=True, headers=headers
        ) as client:
            response = await client.get(GameDataRepository.proxy(api))
            response.raise_for_status()
            routes = [
                item["path"]
                for item in response.json().get("tree", [])
                if item.get("type") == "blob"
                and item.get("path", "").split("/", 1)[0]
                in {"avatar", "portrait", "skill"}
            ]
            semaphore = asyncio.Semaphore(16)
            success = failed = 0

            async def download(route: str) -> None:
                nonlocal success, failed
                target = paths.CACHE_DIR / route
                if target.exists() and not (force or update):
                    return
                async with semaphore:
                    try:
                        url = GameDataRepository.proxy(
                            GameDataRepository.RAW + quote(route, safe="/")
                        )
                        payload = await client.get(url)
                        payload.raise_for_status()
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(payload.content)
                        success += 1
                    except httpx.HTTPError:
                        failed += 1

            await asyncio.gather(*(download(route) for route in routes))
            return success, failed
    except httpx.HTTPError as exc:
        raise RequestException(
            f"图片资源同步失败：{type(exc).__name__}: {exc}"
        ) from exc


game_data = GameDataRepository()
