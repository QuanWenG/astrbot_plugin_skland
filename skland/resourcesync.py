"""Persistent game-data synchronization, independent from NoneBot."""

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from . import config as paths
from .exception import RequestException
from .download import GitHubDataClient
from .schemas.endfield.gacha.base import EfGachaContentPool
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
        self._update_lock = asyncio.Lock()
        self.last_update_messages: list[str] = []
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

    async def load(self, force: bool = False, *, refresh_metadata: bool = False) -> tuple[str | None, int, int]:
        if self._update_lock.locked():
            raise RequestException("数据资源正在更新，请稍后再试")
        async with self._update_lock:
            return await self._load(force, refresh_metadata=refresh_metadata)

    async def _load(self, force: bool, *, refresh_metadata: bool):
        root = paths.DATA_DIR
        version_file = root / "version"
        local = version_file.read_text("utf-8").strip() if version_file.exists() else None
        downloaded = failed = 0
        messages = []
        async with GitHubDataClient() as client:
            try:
                commit = await client.resolve_commit("yuanyan3060", "ArknightsGameResource", "main")
                base = f"https://raw.githubusercontent.com/yuanyan3060/ArknightsGameResource/{commit}/"
                remote = await client.get_text(base + "version")
                changed = force or local != remote or not self._has_valid_core_cache(root)
                batch = {}
                if changed:
                    tables = {route: await client.get_json(base + route) for route in self.FILES}
                    # Validate the complete staged generation before replacing any current file.
                    self._validate_core(tables)
                    batch = {root / route: json.dumps(value, ensure_ascii=False).encode() for route, value in tables.items()}
                    batch[version_file] = remote.encode()
                if changed or force or not self.variant_groups_loaded:
                    try:
                        variants = await client.get_json(base + self.CHAR_META_FILE)
                        if not self._valid_variant_table(variants):
                            raise ValueError("异格分组结构无效")
                        batch[root / self.CHAR_META_FILE] = json.dumps(variants).encode()
                    except (RequestException, ValueError) as exc:
                        messages.append(f"异格关系更新失败，保留可用缓存：{exc}")
                        failed += 1
                if batch:
                    replace_batch(batch)
                    downloaded += sum(path != version_file for path in batch)
                local = remote
                messages.append("明日方舟数据资源更新成功" if batch else "明日方舟数据资源已是最新")
            except (RequestException, ValueError, KeyError, TypeError, OSError) as exc:
                failed += 1
                messages.append(f"明日方舟更新失败，保留原缓存：{exc}")
            try:
                details = await client.get_json(self.DETAILS)
                for value in details["gachaPoolClient"]:
                    GachaDetails(**value)
                replace_batch({root / "gacha_details.json": json.dumps(details, ensure_ascii=False).encode()})
                messages.append("方舟卡池详情更新成功")
            except (RequestException, ValueError, KeyError, OSError) as exc:
                failed += 1
                messages.append(f"方舟卡池详情更新失败，保留原缓存：{exc}")
            try:
                commit = await client.resolve_commit("FrostN0v0", "EndfieldGachaPoolTable", "master")
                pools = await client.get_json(f"https://raw.githubusercontent.com/FrostN0v0/EndfieldGachaPoolTable/{commit}/GachaPoolTable.json")
                for value in pools.values():
                    EfGachaContentPool(**value)
                if not pools and self.ef_pool_table:
                    raise ValueError("卡池表为空")
                payload = json.dumps(pools, ensure_ascii=False).encode()
                target = root / "endfield" / "GachaPoolTable.json"
                changed = not target.exists() or target.read_bytes() != payload
                if changed:
                    replace_batch({target: payload})
                self.ef_pool_table = pools
                messages.append(f"终末地卡池{'更新成功' if changed else '已是最新'}：{len(pools)} 个")
            except (RequestException, ValueError, OSError) as exc:
                failed += 1
                messages.append(f"终末地卡池更新失败，保留原缓存：{exc}")
        self.last_update_messages = messages
        for message in messages:
            logger.info(message)
        if not all((root / route).exists() for route in self.FILES):
            raise RequestException("；".join(messages))
        metadata = self._load_operator_metadata()
        if force or refresh_metadata or downloaded or metadata is None:
            try:
                metadata = await self._refresh_operator_metadata()
                messages.append("干员筛选元数据更新完成")
            except RequestException as exc:
                failed += 1
                messages.append(f"干员筛选元数据更新失败，保留官方数据：{exc}")
                logger.warning("干员筛选元数据更新失败，保留官方数据：%s", exc)
        self._parse(metadata)
        self.variant_groups_checked = self.variant_groups_loaded
        return local, downloaded, failed

    def load_cached(self) -> bool:
        self._load_ef_cache()
        if not all((paths.DATA_DIR / route).exists() for route in self.FILES):
            return False
        self._parse(self._load_operator_metadata())
        self.variant_groups_checked = self.variant_groups_loaded
        return True

    @classmethod
    def _validate_core(cls, tables):
        if not all(isinstance(tables[route], dict) for route in cls.FILES) or not tables[cls.FILES[1]]:
            raise ValueError("官方数据表为空或结构无效")
        for value in tables[cls.FILES[1]].values():
            CharTable(**value)
        for value in tables[cls.FILES[0]]["gachaPoolClient"]:
            GachaTable(**value)
        OperatorCatalog.from_game_tables(*(tables[route] for route in cls.FILES[1:]))

    @classmethod
    def _has_valid_core_cache(cls, root):
        try:
            cls._validate_core({route: json.loads((root / route).read_text("utf-8")) for route in cls.FILES})
            return True
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def _load_ef_cache(self):
        ef_file = paths.DATA_DIR / "endfield" / "GachaPoolTable.json"
        if ef_file.exists():
            try:
                pools = json.loads(ef_file.read_text("utf-8"))
                if not isinstance(pools, dict):
                    raise ValueError("卡池表结构无效")
                for value in pools.values():
                    EfGachaContentPool(**value)
                self.ef_pool_table = pools
            except (OSError, ValueError, TypeError) as exc:
                logger.warning("终末地卡池缓存无效：%s", type(exc).__name__)

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
            self._load_ef_cache()
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
    if game_data._update_lock.locked():
        raise RequestException("资源正在更新，请稍后再试")
    async with game_data._update_lock:
        return await _sync_images(force, update)


async def _sync_images(force: bool = False, update: bool = False) -> tuple[int, int]:
    async with GitHubDataClient() as client:
        commit = await client.resolve_commit("yuanyan3060", "ArknightsGameResource", "main")
        tree = await client.get_json(f"https://api.github.com/repos/yuanyan3060/ArknightsGameResource/git/trees/{commit}?recursive=1")
        if tree.get("truncated"):
            raise RequestException("资源目录不完整，请稍后重试")
        routes = [x["path"] for x in tree.get("tree", []) if x.get("type") == "blob"
                  and x.get("path", "").split("/", 1)[0] in {"avatar", "portrait", "skill"}]
        async def download(route):
            target = paths.CACHE_DIR / route
            if not target.resolve().is_relative_to(paths.CACHE_DIR.resolve()):
                raise RequestException("无效资源路径")
            if target.exists() and not (force or update):
                return 0, 0
            try:
                payload = await client.fetch(f"https://raw.githubusercontent.com/yuanyan3060/ArknightsGameResource/{commit}/" + quote(route, safe="/"), _validate_image_payload)
                replace_batch({target: payload})
                return 1, 0
            except (RequestException, OSError):
                return 0, 1
        results = await asyncio.gather(*(download(route) for route in routes))
        return sum(x[0] for x in results), sum(x[1] for x in results)


def _validate_image_payload(payload):
    from io import BytesIO
    from PIL import Image
    try:
        with Image.open(BytesIO(payload)) as image:
            image.verify()
    except (OSError, ValueError) as exc:
        raise ValueError("资源图片格式无效") from exc
    return payload


def replace_batch(batch: dict[Path, bytes]) -> None:
    """Rollback on replacement failure; not a crash-atomic multi-file transaction."""
    originals = {path: path.read_bytes() if path.exists() else None for path in batch}
    staged = {}
    replaced = []
    try:
        for path, payload in batch.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(dir=path.parent, suffix=".stage")
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
            staged[path] = Path(name)
        for path, temporary in staged.items():
            os.replace(temporary, path)
            replaced.append(path)
    except BaseException:
        for path in reversed(replaced):
            payload = originals[path]
            if payload is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(payload)
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


game_data = GameDataRepository()
