"""Official strategy/bond image URLs and persistent resource metadata for garrison."""

import asyncio
import json
import logging
import os
import re
from contextlib import suppress
from pathlib import Path
from time import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from . import config as paths

logger = logging.getLogger("astrbot")
RESOURCE_URL = "https://zonai.skland.com/api/v1/game/garrison/guide/resource?seasonId=act3autochess"
BAND_ICON_BASE = "https://web.hycdn.cn/arknights/game/assets/auto_chess/band_icon/"
BOND_ICON_BASE = "https://web.hycdn.cn/arknights/game/assets/auto_chess/bond_icon/"
TROPHY_ICON_BASE = "https://raw.githubusercontent.com/ArknightsAssets/ArknightsAssets2/cn/assets/dyn/ui/autochess/%5Buc%5Dautochessouter/arts/medalicon/"
DECORATION_FILES = {
    "page_background": "page_background.jpg",
    "page_texture": "page_texture.jpg",
    "card_background": "card_background.png",
    "season_logo": "season_logo.png",
    "header_decoration": "header_decoration.png",
}
_resource_lock = asyncio.Lock()


def valid_band_id(band_id: str) -> bool:
    return bool(re.fullmatch(r"band_[A-Za-z0-9_-]{1,128}", band_id))


def valid_band_icon_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == "https" and parsed.hostname == "web.hycdn.cn"
                and parsed.port in (None, 443) and not parsed.username and not parsed.password
                and parsed.path.startswith("/arknights/game/assets/auto_chess/band_icon/"))
    except ValueError:
        return False


def valid_bond_id(bond_id: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", bond_id))


def valid_bond_icon_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == "https" and parsed.hostname == "web.hycdn.cn"
                and parsed.port in (None, 443) and not parsed.username and not parsed.password
                and parsed.path.startswith("/arknights/game/assets/auto_chess/bond_icon/")
                and parsed.path.lower().endswith(".png"))
    except ValueError:
        return False


def valid_equipment_id(item_id: str) -> bool:
    return bool(re.fullmatch(r"chess_item_[A-Za-z0-9_-]{1,128}", item_id))


def valid_equipment_icon_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == "https" and parsed.hostname == "web.hycdn.cn"
                and parsed.port in (None, 443) and not parsed.username and not parsed.password
                and parsed.path.startswith("/arknights/game/assets/char_skill/")
                and parsed.path.lower().endswith(".png"))
    except ValueError:
        return False


def valid_decoration_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == "https" and parsed.port in (None, 443)
                and not parsed.username and not parsed.password
                and parsed.path.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
                and ((parsed.hostname == "bbs.hycdn.cn" and parsed.path.startswith("/image/"))
                     or (parsed.hostname == "assets.skland.com" and parsed.path.startswith("/_static_assets/game-tools/"))))
    except ValueError:
        return False


def valid_trophy_id(icon_id: str) -> bool:
    return bool(re.fullmatch(r"trophy_level[1-9][0-9]?_icon", icon_id))


def valid_trophy_icon_url(url: str) -> bool:
    return url.startswith(TROPHY_ICON_BASE) and valid_trophy_id(url.removeprefix(TROPHY_ICON_BASE).removesuffix(".png")) and url.endswith(".png")


def trophy_icon_id(trophies: int | None) -> str:
    if trophies is None or trophies < 0:
        return ""
    try:
        payload = json.loads((paths.RES_DIR / "images/garrison/trophies.json").read_text(encoding="utf-8"))
        eligible = [item for item in payload["tiers"] if isinstance(item.get("minimum"), int)
                    and 0 <= item["minimum"] <= trophies and valid_trophy_id(item.get("iconId", ""))]
        return max(eligible, key=lambda item: item["minimum"])["iconId"] if eligible else ""
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return ""


def _resources(payload: dict, kind: str) -> dict[str, dict]:
    field, validate_id, validate_url = {
        "strategies": ("strategyId", valid_band_id, valid_band_icon_url),
        "bonds": ("bondId", valid_bond_id, valid_bond_icon_url),
        "items": ("itemId", valid_equipment_id, valid_equipment_icon_url),
    }[kind]
    entries = payload.get(kind)
    if not isinstance(entries, list):
        return {}
    resources = {}
    for item in entries:
        if (not isinstance(item, dict) or not isinstance(item.get(field), str)
                or not validate_id(item[field]) or not isinstance(item.get("iconUrl"), str)
                or not validate_url(item["iconUrl"])):
            continue
        resource = {field: item[field], "iconUrl": item["iconUrl"], "name": item.get("name") or item[field]}
        if kind == "strategies" and isinstance(item.get("initiatorName"), str):
            resource["initiatorName"] = item["initiatorName"]
        if kind == "items":
            if isinstance(item.get("isGolden"), bool):
                resource["isGolden"] = item["isGolden"]
            try:
                resource["shopLevel"] = min(6, max(0, int(item.get("shopLevel", 0))))
            except (ValueError, TypeError, OverflowError):
                resource["shopLevel"] = 0
        if kind == "bonds" and "activeCount" in item:
            try:
                resource["activeCount"] = min(4, max(1, int(item.get("activeCount", 1))))
            except (ValueError, TypeError, OverflowError):
                resource["activeCount"] = 1
        resources[item[field]] = resource
    return resources


def _read_resources(path: Path, kind: str) -> dict[str, dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return _resources(payload, kind) if isinstance(payload, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


async def _resolve_resources(keys: set[str], kind: str) -> dict[str, dict]:
    """Reuse local metadata; refresh the public resource only for missing images."""
    directory, validate_id = {
        "strategies": ("band", valid_band_id), "bonds": ("bond", valid_bond_id),
        "items": ("equipment", valid_equipment_id),
    }[kind]
    root = paths.RES_DIR / "images/garrison"
    cache_root = paths.CACHE_DIR / "garrison"
    metadata = cache_root / f"{kind}.json"
    resources = _read_resources(root / f"{kind}.json", kind)
    for key, item in _read_resources(metadata, kind).items():
        resources[key] = resources.get(key, {}) | item
    missing = {key for key in keys if validate_id(key)
               and not (cache_root / directory / f"{key}.png").is_file()
               and not (root / directory / f"{key}.png").is_file()}
    if not missing:
        return resources
    async with _resource_lock:
        for key, item in _read_resources(metadata, kind).items():
            resources[key] = resources.get(key, {}) | item
        try:
            age = time() - metadata.stat().st_mtime
        except OSError:
            age = float("inf")
        if age < 60 or (age < 86400 and missing <= resources.keys()):
            return resources
        temporary = metadata.with_name(f".{metadata.name}.{uuid4().hex}.tmp")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(RESOURCE_URL)
                response.raise_for_status()
                if len(response.content) > 10 * 1024 * 1024:
                    raise ValueError("resource response too large")
                payload = response.json()
            if not isinstance(payload, dict) or payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
                raise ValueError("invalid garrison resource response")
            current = _resources(payload["data"], kind)
            if not current:
                raise ValueError(f"garrison resource has no usable {kind} icons")
            metadata.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps({kind: list(current.values())}, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, metadata)
            resources.update(current)
        except (httpx.HTTPError, OSError, ValueError, TypeError) as exc:
            logger.warning("卫戍素材索引更新失败（%s）：%s", kind, type(exc).__name__)
        finally:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
    return resources


async def resolve_band_icon_urls(band_ids: set[str]) -> dict[str, str]:
    resources = await _resolve_resources(band_ids, "strategies")
    return {key: item["iconUrl"] for key, item in resources.items()}


async def resolve_band_resources(band_ids: set[str]) -> dict[str, dict]:
    return await _resolve_resources(band_ids, "strategies")


async def resolve_bond_resources(bond_ids: set[str]) -> dict[str, dict]:
    return await _resolve_resources(bond_ids, "bonds")


async def resolve_equipment_resources(item_ids: set[str]) -> dict[str, dict]:
    return await _resolve_resources(item_ids, "items")
