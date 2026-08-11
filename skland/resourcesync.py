"""Persistent game-data synchronization, independent from NoneBot."""

import asyncio
import json
import logging
import os
from typing import Any
from urllib.parse import quote

import httpx

from . import config as paths
from .exception import RequestException
from .schemas import (
    CharTable,
    GachaDetails,
    GachaTable,
    OperatorCatalog,
    OperatorMetadataSnapshot,
)

logger = logging.getLogger("astrbot")


class GameDataRepository:
    RAW = "https://raw.githubusercontent.com/yuanyan3060/ArknightsGameResource/main/"
    VERSION = RAW + "version"
    DETAILS = "https://weedy.prts.wiki/gacha_table.json"
    PRTS_API = "https://prts.wiki/api.php"
    EF_POOLS = "https://raw.githubusercontent.com/FrostN0v0/EndfieldGachaPoolTable/master/GachaPoolTable.json"
    CHAR_META_FILE = "gamedata/excel/char_meta_table.json"
    FILES = (
        "gamedata/excel/gacha_table.json",
        "gamedata/excel/character_table.json",
        "gamedata/excel/char_patch_table.json",
        "gamedata/excel/uniequip_table.json",
        "gamedata/excel/handbook_info_table.json",
        "gamedata/excel/handbook_team_table.json",
    )
    SYNC_FILES = FILES + (CHAR_META_FILE,)

    def __init__(self) -> None:
        self.gacha_table: list[GachaTable] = []
        self.gacha_details: list[GachaDetails] = []
        self.character_table: list[CharTable] = []
        self.operator_catalog = OperatorCatalog()
        self.variant_groups_loaded = False
        self.variant_groups_checked = False
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
                missing = any(
                    not (root / route).exists() for route in self.SYNC_FILES
                ) or not self.variant_groups_loaded
                if force or missing or local != remote:
                    for route in self.SYNC_FILES:
                        response = await client.get(self.proxy(self.RAW + route))
                        response.raise_for_status()
                        target = root / route
                        target.parent.mkdir(parents=True, exist_ok=True)
                        temporary = target.with_suffix(target.suffix + ".tmp")
                        try:
                            temporary.write_bytes(response.content)
                            os.replace(temporary, target)
                        finally:
                            temporary.unlink(missing_ok=True)
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
        metadata = self._load_operator_metadata()
        if force or downloaded or metadata is None:
            try:
                metadata = await self._refresh_operator_metadata()
            except RequestException as exc:
                logger.warning("干员筛选元数据更新失败，使用官方档案回退：%s", exc)
        self._parse(metadata)
        self.variant_groups_checked = self.variant_groups_loaded
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
        self._parse(self._load_operator_metadata())
        self.variant_groups_checked = self.variant_groups_loaded
        return True

    @staticmethod
    def _valid_variant_table(value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        groups = value.get("spCharGroups")
        if not isinstance(groups, dict) or not groups:
            return False
        return all(
            isinstance(group_id, str)
            and bool(group_id)
            and isinstance(member_ids, list)
            and bool(member_ids)
            and all(isinstance(member_id, str) and member_id for member_id in member_ids)
            for group_id, member_ids in groups.items()
        )

    @classmethod
    def _load_tables(cls, root: Any) -> dict[str, Any]:
        tables = {
            route.rsplit("/", 1)[-1]: json.loads((root / route).read_text("utf-8"))
            for route in cls.FILES
        }
        optional = root / cls.CHAR_META_FILE
        if optional.exists():
            try:
                variant_table = json.loads(optional.read_text("utf-8"))
                if not cls._valid_variant_table(variant_table):
                    raise ValueError("spCharGroups 结构不完整")
                tables[optional.name] = variant_table
            except (OSError, ValueError) as exc:
                logger.warning("异格分组缓存无效，已忽略：%s", exc)
        return tables

    def _parse(self, metadata: OperatorMetadataSnapshot | None = None) -> None:
        try:
            root = paths.DATA_DIR
            tables = self._load_tables(root)
            chars = tables["character_table.json"]
            self.character_table = []
            for char_id, value in chars.items():
                char = CharTable(**value)
                char.char_id = char_id
                self.character_table.append(char)
            pools = tables["gacha_table.json"]
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
            self.operator_catalog = OperatorCatalog.from_game_tables(
                chars,
                tables["char_patch_table.json"],
                tables["uniequip_table.json"],
                tables["handbook_info_table.json"],
                tables["handbook_team_table.json"],
                metadata,
                tables.get("char_meta_table.json"),
            )
            self.variant_groups_loaded = bool(self.operator_catalog.entries) and all(
                entry.variant_group_id for entry in self.operator_catalog.entries
            )
        except (OSError, ValueError, KeyError) as exc:
            raise RequestException(f"游戏数据解析失败：{exc}") from exc

    @staticmethod
    def _load_operator_metadata() -> OperatorMetadataSnapshot | None:
        path = paths.OPERATOR_METADATA_PATH
        if not path.exists():
            return None
        try:
            return OperatorMetadataSnapshot.model_validate_json(path.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("加载干员筛选元数据失败：%s", exc)
            return None

    async def _refresh_operator_metadata(self) -> OperatorMetadataSnapshot:
        rows: list[dict[str, Any]] = []
        offset = 0
        try:
            async with httpx.AsyncClient(
                timeout=30,
                headers={"User-Agent": "astrbot-plugin-skland operator metadata updater"},
            ) as client:
                while True:
                    response = await client.get(
                        self.PRTS_API,
                        params={
                            "action": "cargoquery",
                            "format": "json",
                            "tables": "chara,chara_extra_info",
                            "fields": (
                                "chara.charId=char_id,chara.subProfession=branch,"
                                "chara_extra_info.sex=gender,chara_extra_info.race=race"
                            ),
                            "join_on": "chara._pageName=chara_extra_info._pageName",
                            "where": "chara.charIndex>0",
                            "limit": 500,
                            "offset": offset,
                        },
                    )
                    response.raise_for_status()
                    page = response.json().get("cargoquery") or []
                    rows.extend(page)
                    if len(page) < 500:
                        break
                    offset += 500
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise RequestException(f"获取 PRTS 干员筛选元数据失败：{exc}") from exc
        if not rows:
            raise RequestException("获取 PRTS 干员筛选元数据失败：返回数据为空")
        snapshot = OperatorMetadataSnapshot.from_prts_rows(rows)
        root = paths.DATA_DIR
        tables = self._load_tables(root)
        catalog = OperatorCatalog.from_game_tables(
            tables["character_table.json"],
            tables["char_patch_table.json"],
            tables["uniequip_table.json"],
            tables["handbook_info_table.json"],
            tables["handbook_team_table.json"],
            snapshot,
            tables.get("char_meta_table.json"),
        )
        official_ids = {entry.char_id for entry in catalog.entries}
        matched = len(official_ids.intersection(snapshot.by_id))
        if matched < max(1, int(len(official_ids) * 0.75)):
            raise RequestException(
                f"PRTS 干员筛选元数据覆盖率过低：{matched}/{len(official_ids)}"
            )
        path = paths.OPERATOR_METADATA_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        try:
            temporary.write_text(snapshot.model_dump_json(), "utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return snapshot

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
