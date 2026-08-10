"""Safe QR login card rendering copied from the upstream 0.7.1 behavior."""

from __future__ import annotations

import ipaddress
from io import BytesIO
from urllib.parse import urlsplit

import httpx
import qrcode
from PIL import Image, ImageDraw, ImageFilter, ImageOps, UnidentifiedImageError

_AVATAR_MAX_BYTES = 2 * 1024 * 1024
_AVATAR_MAX_PIXELS = 4_000_000


def is_supported_avatar_url(avatar_url: str) -> bool:
    try:
        parsed = urlsplit(avatar_url)
    except ValueError:
        return False
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.username or parsed.password:
        return False
    hostname = parsed.hostname.lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return True
    return address.is_global


async def fetch_user_avatar(avatar_url: str | None) -> Image.Image | None:
    if not avatar_url or not is_supported_avatar_url(avatar_url):
        return None
    try:
        async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
            async with client.stream("GET", avatar_url) as response:
                if not 200 <= response.status_code < 300:
                    return None
                content_type = response.headers.get("content-type", "").partition(";")[0].strip().lower()
                if not content_type.startswith("image/"):
                    return None
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > _AVATAR_MAX_BYTES:
                    return None
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > _AVATAR_MAX_BYTES:
                        return None
        with Image.open(BytesIO(body)) as image:
            if image.width * image.height > _AVATAR_MAX_PIXELS:
                return None
            return ImageOps.exif_transpose(image).convert("RGB")
    except (httpx.HTTPError, OSError, UnidentifiedImageError, ValueError):
        return None


def render_qrcode_card(scan_url: str, avatar: Image.Image | None) -> bytes:
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=4
    )
    qr.add_data(scan_url)
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    padding = 24
    panel_size = qr_image.width + padding * 2
    card_width = max(640, panel_size + 64)
    panel_top = 136 if avatar else 32
    card_height = panel_top + panel_size + 32
    if avatar:
        card = ImageOps.fit(avatar, (card_width, card_height), Image.Resampling.LANCZOS)
        card = card.filter(ImageFilter.GaussianBlur(24)).convert("RGBA")
    else:
        card = Image.new("RGBA", (card_width, card_height), (31, 38, 51, 255))
    card.alpha_composite(Image.new("RGBA", card.size, (5, 10, 18, 118)))
    left = (card_width - panel_size) // 2
    box = (left, panel_top, left + panel_size, panel_top + panel_size)
    shadow = Image.new("RGBA", card.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (box[0] + 6, box[1] + 10, box[2] + 6, box[3] + 10), radius=28, fill=(0, 0, 0, 90)
    )
    card.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(12)))
    ImageDraw.Draw(card).rounded_rectangle(box, radius=28, fill=(255, 255, 255, 255))
    card.paste(qr_image, (left + padding, panel_top + padding))
    if avatar:
        size = 88
        badge_left = (card_width - size) // 2
        badge_top = 24
        ImageDraw.Draw(card).ellipse(
            (badge_left - 5, badge_top - 5, badge_left + size + 5, badge_top + size + 5),
            fill=(255, 255, 255, 255),
        )
        badge = ImageOps.fit(avatar, (size, size), Image.Resampling.LANCZOS)
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
        card.paste(badge, (badge_left, badge_top), mask)
    stream = BytesIO()
    card.convert("RGB").save(stream, "PNG", optimize=True)
    return stream.getvalue()
