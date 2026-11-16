import json
import hashlib
import logging
from urllib.parse import quote
from datetime import datetime, timedelta

from . import config as paths
from .image_cache import register_missing_image
from .garrison_assets import (
    BAND_ICON_BASE, BOND_ICON_BASE, TROPHY_ICON_BASE, DECORATION_FILES, trophy_icon_id,
    valid_band_id, valid_band_icon_url, valid_bond_id, valid_bond_icon_url, valid_decoration_url,
    valid_equipment_id, valid_equipment_icon_url,
)

logger = logging.getLogger("astrbot")
RES_DIR = paths.RES_DIR


def format_timestamp(timestamp: float) -> str:
    delta = timedelta(seconds=timestamp)
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes = remainder // 60

    if days > 0:
        return f"{days}天{hours}小时{minutes}分钟"
    elif hours > 0:
        return f"{hours}小时{minutes}分钟"
    else:
        return f"{minutes}分钟"


def time_to_next_monday_4am(now_ts: float) -> str:
    now = datetime.fromtimestamp(now_ts)
    days_until_monday = (7 - now.weekday()) % 7
    next_monday = now + timedelta(days=days_until_monday)
    next_monday_4am = next_monday.replace(hour=4, minute=0, second=0, microsecond=0)
    if now > next_monday_4am:
        next_monday_4am += timedelta(weeks=1)
    return format_timestamp((next_monday_4am - now).total_seconds())


def time_to_next_4am(now_ts: float) -> str:
    now = datetime.fromtimestamp(now_ts)
    next_4am = now.replace(hour=4, minute=0, second=0, microsecond=0)
    if now > next_4am:
        next_4am += timedelta(days=1)
    return format_timestamp((next_4am - now).total_seconds())


def format_timestamp_str(stamp_str: str) -> str:
    return datetime.fromtimestamp(float(stamp_str)).strftime("%Y-%m-%d %H:%M:%S")


def format_timestamp_md(ms: int) -> str:
    if ms > 1e10:
        return datetime.fromtimestamp(ms / 1000).strftime("%m-%d")
    else:
        return datetime.fromtimestamp(ms).strftime("%m-%d")


def format_war_echoes_date(timestamp: str | float) -> str:
    try:
        return datetime.fromtimestamp(float(timestamp)).strftime("%Y/%m/%d")
    except (TypeError, ValueError, OSError):
        return "--"


def format_war_echoes_duration(duration: str | float) -> str:
    try:
        seconds = int(float(duration))
    except (TypeError, ValueError):
        return "--"
    if seconds <= 0:
        return "--"
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}分{seconds}秒"


WAR_ECHOES_ASSET_DIR = "../images/endfield/war_echoes"
WAR_ECHOES_RATING_ASSETS = {
    "S+": "rating_s_plus.png",
    "S": "rating_s.png",
    "A": "rating_a.png",
    "B": "rating_b.png",
    "C": "rating_c.png",
    "D": "rating_d.png",
    "--": "rating_empty.png",
}


def war_echoes_asset(filename: str) -> str:
    return f"{WAR_ECHOES_ASSET_DIR}/{filename}"


def war_echoes_rating_asset(rating: str) -> str:
    filename = WAR_ECHOES_RATING_ASSETS.get(rating, WAR_ECHOES_RATING_ASSETS["--"])
    return war_echoes_asset(filename)


def war_echoes_stage_asset(stars: int, plus_task: bool) -> str:
    filename = "stage_plus.png" if stars >= 3 and plus_task else f"stage_{max(0, min(stars, 3))}.png"
    return war_echoes_asset(filename)


def war_echoes_potential_asset(level: int) -> str:
    normalized = max(0, min(level, 5))
    return f"../images/endfield/potential/potential_{normalized}.png"


def charId_to_avatarUrl(charId: str) -> str:
    avatar_id = next(
        (charId.replace(symbol, "_", 1) for symbol in ["@", "#"] if symbol in charId),
        charId,
    )
    img_path = paths.CACHE_DIR / "avatar" / f"{avatar_id}.png"
    if not img_path.exists():
        img_url = f"https://web.hycdn.cn/arknights/game/assets/char/avatar/{charId}.png"
        logger.debug(f"Avatar not found locally, using URL: {img_url}")
        return img_url
    return img_path.as_uri()


def garrison_band_icon(band_id: str, icon_url: str = "") -> str:
    """Use the real strategy image, downloading missing icons into the cache."""
    if not valid_band_id(band_id):
        return ""
    root = paths.RES_DIR / "images" / "garrison"
    cached = paths.CACHE_DIR / "garrison/band" / f"{band_id}.png"
    if cached.is_file():
        return cached.as_uri()
    bundled = root / "band" / f"{band_id}.png"
    if bundled.is_file():
        return bundled.as_uri()
    url = icon_url if valid_band_icon_url(icon_url) else f"{BAND_ICON_BASE}{band_id}.png"
    register_missing_image(url, cached)
    return url


def garrison_avatar_url(char_id: str) -> str:
    """Reuse the shared avatar cache and cache successful first-time downloads."""
    if not char_id or len(char_id) > 160 or not all(c.isalnum() or c in "_-@#" for c in char_id):
        return (paths.RES_DIR / "images/garrison/operator_empty.svg").as_uri()
    avatar_id = char_id.replace("@", "_").replace("#", "_")
    image_path = paths.CACHE_DIR / "avatar" / f"{avatar_id}.png"
    if image_path.is_file():
        return image_path.as_uri()
    image_url = f"https://web.hycdn.cn/arknights/game/assets/char/avatar/{quote(char_id, safe='')}.png"
    register_missing_image(image_url, image_path)
    return image_url


def garrison_bond_icon(bond_id: str, icon_url: str = "") -> str:
    """Reuse official bond artwork locally, caching any missing image."""
    if not valid_bond_id(bond_id):
        return ""
    cached = paths.CACHE_DIR / "garrison/bond" / f"{bond_id}.png"
    if cached.is_file():
        return cached.as_uri()
    bundled = paths.RES_DIR / "images/garrison/bond" / f"{bond_id}.png"
    if bundled.is_file():
        return bundled.as_uri()
    url = icon_url if valid_bond_icon_url(icon_url) else f"{BOND_ICON_BASE}{bond_id}.png"
    register_missing_image(url, cached)
    return url


def garrison_portrait_url(char_id: str) -> str:
    if not char_id or len(char_id) > 160 or not all(c.isalnum() or c in "_-@#" for c in char_id):
        return (paths.RES_DIR / "images/garrison/operator_empty.svg").as_uri()
    return charId_to_portraitUrl(char_id)


def garrison_operator_badge(phase: int | None, merge: int | None) -> str:
    if phase not in (0, 1, 2):
        return ""
    filename = "promoted.png" if merge is not None and merge >= 1 else f"phase_{phase}.png"
    bundled = paths.RES_DIR / "images/garrison/operator" / filename
    if bundled.is_file():
        return bundled.as_uri()
    fallback = paths.RES_DIR / "images/ark_card/elite" / f"elite_{phase}.png"
    return fallback.as_uri() if fallback.is_file() else ""


def garrison_equipment_icon(item_id: str, icon_url: str = "") -> str:
    if not valid_equipment_id(item_id):
        return ""
    cached = paths.CACHE_DIR / "garrison/equipment" / f"{item_id}.png"
    if cached.is_file():
        return cached.as_uri()
    bundled = paths.RES_DIR / "images/garrison/equipment" / f"{item_id}.png"
    if bundled.is_file():
        return bundled.as_uri()
    if not valid_equipment_icon_url(icon_url):
        return ""
    register_missing_image(icon_url, cached)
    return icon_url


def garrison_trophy_icon(trophies: int | None) -> str:
    icon = trophy_icon_id(trophies)
    if not icon:
        return ""
    cached = paths.CACHE_DIR / "garrison/trophy" / f"{icon}.png"
    if cached.is_file():
        return cached.as_uri()
    bundled = paths.RES_DIR / "images/garrison/trophy" / f"{icon}.png"
    if bundled.is_file():
        return bundled.as_uri()
    url = f"{TROPHY_ICON_BASE}{icon}.png"
    register_missing_image(url, cached)
    return url


def garrison_decoration(name: str) -> str:
    """Read bundled official artwork, or download and cache the registered URL."""
    filename = DECORATION_FILES.get(name)
    if not filename:
        return ""
    root = paths.RES_DIR / "images/garrison"
    bundled = root / "decorations" / filename
    if bundled.is_file():
        return bundled.as_uri()
    try:
        metadata = json.loads((root / "decorations.json").read_text(encoding="utf-8"))
        url = metadata["assets"][name]["url"]
        if not isinstance(url, str) or not valid_decoration_url(url):
            return ""
    except (OSError, ValueError, KeyError, TypeError):
        return ""
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    stem, extension = filename.rsplit(".", 1)
    cached = paths.CACHE_DIR / "garrison/decorations" / f"{stem}-{digest}.{extension}"
    if cached.is_file():
        return cached.as_uri()
    register_missing_image(url, cached)
    return url


def charId_to_portraitUrl(charId: str) -> str:
    portrait_id = next(
        (charId.replace(symbol, "_", 1) for symbol in ["@", "#"] if symbol in charId),
        charId,
    )
    img_path = paths.CACHE_DIR / "portrait" / f"{portrait_id}.png"
    if not img_path.exists():
        encoded_id = quote(charId, safe="")
        img_url = f"https://web.hycdn.cn/arknights/game/assets/char/portrait/{encoded_id}.png"
        logger.debug(f"Portrait not found locally, using URL: {img_url}")
        register_missing_image(img_url, img_path)
        return img_url
    return img_path.as_uri()


ARK_PROFESSION_SLUGS = {
    "先锋": "pioneer",
    "近卫": "warrior",
    "重装": "tank",
    "狙击": "sniper",
    "术师": "caster",
    "医疗": "medic",
    "辅助": "support",
    "特种": "special",
}

ARK_ROSTER_LH_URLS = {
    0: "https://media.prts.wiki/0/0b/干员图鉴_lh_0%2C1%2C2.png",
    1: "https://media.prts.wiki/0/0b/干员图鉴_lh_0%2C1%2C2.png",
    2: "https://media.prts.wiki/0/0b/干员图鉴_lh_0%2C1%2C2.png",
    3: "https://media.prts.wiki/a/a5/干员图鉴_lh_3.png",
    4: "https://media.prts.wiki/9/9e/干员图鉴_lh_4.png",
    5: "https://media.prts.wiki/a/a5/干员图鉴_lh_5.png",
}

ARK_ROSTER_LIGHT_URLS = {
    0: "https://media.prts.wiki/a/a7/干员图鉴_稀有度_亮光_0.png",
    1: "https://media.prts.wiki/9/9c/干员图鉴_稀有度_亮光_1.png",
    2: "https://media.prts.wiki/b/b0/干员图鉴_稀有度_亮光_2.png",
    3: "https://media.prts.wiki/0/0d/干员图鉴_稀有度_亮光_3.png",
    4: "https://media.prts.wiki/f/f7/干员图鉴_稀有度_亮光_4.png",
    5: "https://media.prts.wiki/1/19/干员图鉴_稀有度_亮光_5.png",
}


def ark_skin_portrait_url(skin_id: str) -> str:
    portrait_id = next(
        (skin_id.replace(symbol, "_", 1) for symbol in ("@", "#") if symbol in skin_id),
        skin_id,
    )
    image_path = paths.CACHE_DIR / "portrait" / f"{portrait_id}.png"
    if image_path.exists():
        return image_path.as_uri()
    encoded_id = quote(skin_id, safe="")
    image_url = f"https://web.hycdn.cn/arknights/game/assets/char_skin/portrait/{encoded_id}.png"
    register_missing_image(image_url, image_path)
    return image_url


def ark_skill_icon_url(skill_id: str) -> str:
    image_path = paths.CACHE_DIR / "skill" / f"skill_icon_{skill_id}.png"
    if image_path.exists():
        return image_path.as_uri()
    encoded_id = quote(skill_id, safe="")
    return f"https://web.hycdn.cn/arknights/game/assets/char_skill/{encoded_id}.png"


def ark_potential_icon_url(rank: int) -> str:
    image_path = RES_DIR / "images" / "ark_card" / "potential" / f"potential_{rank}.png"
    return image_path.as_uri() if image_path.exists() else ""


def ark_elite_icon_url(phase: int) -> str:
    image_path = RES_DIR / "images" / "ark_card" / "elite" / f"elite_{phase}.png"
    return image_path.as_uri() if image_path.exists() else ""


def ark_profession_icon_url(profession: str) -> str:
    slug = ARK_PROFESSION_SLUGS.get(profession)
    if slug is None:
        return ""
    return (RES_DIR / "images" / "profession" / f"icon_profession_{slug}.png").as_uri()


def ark_rarity_icon_url(rarity: int) -> str:
    return (RES_DIR / "images" / "rarity" / f"rarity_yellow_{rarity}.png").as_uri()


def ark_uniequip_icon_url(type_icon: str | None) -> str:
    if not type_icon:
        return ""
    encoded_id = quote(type_icon.lower(), safe="")
    return f"https://torappu.prts.wiki/assets/uniequip_direction/{encoded_id}.png"


def ark_roster_light_url(rarity: int) -> str:
    return ARK_ROSTER_LIGHT_URLS.get(rarity, "")


def ark_roster_lh_url(rarity: int) -> str:
    return ARK_ROSTER_LH_URLS.get(rarity, "")


def loads_json(json_str: str) -> dict:
    return json.loads(json_str)


def ef_charId_to_avatarUrl(item_id: str) -> str:
    """终末地角色/武器头像URL拼接

    角色ID以 chr_ 开头，使用 charremoteicon 路径；
    武器ID以 wpn_ 开头，使用 itemiconbig 路径。
    """
    ENDFIELD_ASSET_BASE = "https://endfieldtools.dev/assets/images/endfield"
    if item_id.startswith("wpn_"):
        return f"{ENDFIELD_ASSET_BASE}/itemiconbig/{item_id}.png"
    return f"{ENDFIELD_ASSET_BASE}/charremoteicon/icon_{item_id}.png"


def format_stamina_time(remaining_seconds: float) -> str:
    """将剩余秒数格式化为 XXh XXmin 格式"""
    if remaining_seconds <= 0:
        return "已满"
    hours = int(remaining_seconds // 3600)
    minutes = int((remaining_seconds % 3600) // 60)
    if hours > 0:
        return f"{hours}h {minutes}min"
    return f"{minutes}min"


def format_date_ymd(timestamp: str | float) -> str:
    """将时间戳格式化为 YYYY-MM-DD 格式（苏醒日）"""
    ts = float(timestamp) if isinstance(timestamp, str) else timestamp
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


# 据点映射表：domainId -> {name, gradient}
DOMAIN_MAP: dict[str, dict[str, str]] = {
    "domain_1": {"name": "四号谷地", "gradient": "green-linear"},
    "domain_2": {"name": "武陵", "gradient": "blue-linear"},
}


def get_domain_info(domain_id: str) -> dict[str, str]:
    """根据 domainId 获取据点信息（名称和渐变样式类）"""
    return DOMAIN_MAP.get(domain_id, {"name": "未知据点", "gradient": "gray-linear"})


# 稀有度到边框颜色映射
RARITY_COLOR_MAP: dict[str, str] = {
    "rarity_6": "#FF7101",  # 6星橙色
    "rarity_5": "#FFCC00",  # 5星金色
    "rarity_4": "#A85FD6",  # 4星紫色
    "rarity_3": "#00B0FF",  # 3星蓝色
}


def get_rarity_color(rarity_key: str) -> str:
    """根据稀有度 key 获取边框颜色"""
    return RARITY_COLOR_MAP.get(rarity_key, "#FFFFFF")


# 装备稀有度到边框颜色映射
EQUIP_RARITY_COLOR_MAP: dict[str, str] = {
    "equip_rarity_6": "#FF7101",
    "equip_rarity_5": "#FFCC00",
    "equip_rarity_4": "#A85FD6",
    "equip_rarity_3": "#00B0FF",
}


def get_equip_rarity_color(rarity_key: str) -> str:
    """根据装备稀有度 key 获取边框颜色"""
    return EQUIP_RARITY_COLOR_MAP.get(rarity_key, "#FFFFFF")


# 职业 key 到图片文件名映射
PROFESSION_MAP: dict[str, str] = {
    "profession_assault": "assault",
    "profession_supporter": "supporter",
    "profession_caster": "caster",
    "profession_vanguard": "vanguard",
    "profession_guard": "guard",
    "profession_defender": "defender",
    "profession_sniper": "sniper",
    "profession_medic": "medic",
}


def get_profession_icon(profession_key: str) -> str:
    """根据职业 key 获取图片文件名"""
    name = PROFESSION_MAP.get(profession_key, "unknown")
    return f"profession/{name}.png"


# 属性 key 到图片文件名映射
PROPERTY_MAP: dict[str, str] = {
    "char_property_fire": "fire",
    "char_property_cryst": "cryst",
    "char_property_natural": "natural",
    "char_property_pulse": "pulse",
    "char_property_physical": "physical",
}


def get_property_icon(property_key: str) -> str:
    """根据属性 key 获取图片文件名"""
    name = PROPERTY_MAP.get(property_key, "unknown")
    return f"property/{name}.png"


def format_money_wan(value: str | int | None) -> str:
    """将字符串或数字形式的货币数值格式化为：
    - >= 10000 时以万为单位显示整数部分（不保留小数），返回带单位的字符串
    - < 10000 时直接显示原值
    非法或空值返回空字符串
    """
    if value is None:
        return ""
    try:
        iv = int(float(value))
    except Exception:
        return ""
    if iv >= 10000:
        return f"{iv // 10000}万"
    return str(iv)
