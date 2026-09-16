"""Resolve configured backgrounds at request time, using runtime data paths."""
import json
import base64
from io import BytesIO
import logging
import random
from pathlib import Path
from urllib.parse import urlparse, unquote_to_bytes
from urllib.request import url2pathname
from PIL import Image

import httpx

from . import config as paths

logger = logging.getLogger("astrbot")
ROGUE_BACKGROUNDS = {"rogue_1": "pic_rogue_1_KV1.png", "rogue_2": "pic_rogue_2_50.png",
    "rogue_3": "pic_rogue_3_KV2.png", "rogue_4": "pic_rogue_4_47.png",
    "rogue_5": "pic_rogue_5_KV1.png", "rogue_6": "pic_rogue_6_kv1.png"}


def custom_uri(value) -> str:
    if isinstance(value, str) and value.lstrip().startswith("{"):
        value = json.loads(value)
    uri = str(value.get("uri", "")) if isinstance(value, dict) else str(value)
    if uri.startswith(("https://", "http://", "data:image/", "file://")):
        return uri
    path = Path(uri)
    if not path.is_absolute():
        path = paths.PLUGIN_DATA_DIR / path
    if path.is_dir():
        images = [p for p in path.iterdir() if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}]
        if not images:
            raise ValueError("背景目录没有图片")
        path = random.choice(images)
    if not path.is_file():
        raise ValueError("背景图片不存在")
    return path.resolve().as_uri()


async def background_bytes(uri: str) -> bytes:
    if uri.startswith("data:image/"):
        header, payload = uri.split(",", 1)
        content = base64.b64decode(payload, validate=True) if ";base64" in header else unquote_to_bytes(payload)
    elif uri.startswith("file://"):
        content = Path(url2pathname(urlparse(uri).path)).read_bytes()
    else:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            response = await client.get(uri)
            response.raise_for_status()
            content = response.content
    with Image.open(BytesIO(content)) as image:
        image.verify()
    return content


async def validated_uri(uri: str) -> str:
    content = await background_bytes(uri)
    if uri.startswith("file://"):
        return uri
    with Image.open(BytesIO(content)) as image:
        mime = Image.MIME[image.format]
    return f"data:{mime};base64," + base64.b64encode(content).decode("ascii")


async def resolve_background(game="arknights", *, topic=None, box=False) -> str | None:
    root = paths.RES_DIR / "images" / "background"
    fallback = root / "bg.jpg"
    source = paths.config.background_source
    if game == "endfield":
        root = root / "endfield"
        fallback = root / "default_bg.jpg"
    if topic:
        root = root / "rogue"
        source = paths.config.rogue_background_source
        fallback = root / ROGUE_BACKGROUNDS.get(topic, "kv_epoque14.png")
    try:
        if source == "default":
            if box:
                return None
            return (root / "kv_epoque14.png").as_uri() if topic else fallback.as_uri()
        if source == "rogue":
            return fallback.as_uri()
        if source == "random":
            return custom_uri({"uri": str(root)})
        if source == "Lolicon":
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get("https://api.lolicon.app/setu/v2", params={"tag": "endfield" if game == "endfield" else "arknights", "r18": 0})
                response.raise_for_status()
                return await validated_uri(response.json()["data"][0]["urls"]["original"])
        return await validated_uri(custom_uri(source))
    except (ValueError, OSError, httpx.HTTPError, KeyError, IndexError, TypeError) as exc:
        logger.warning("自定义背景不可用，使用内置背景：%s", type(exc).__name__)
        return None if box else fallback.as_uri()
