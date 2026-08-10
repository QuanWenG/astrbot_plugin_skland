"""Platform-specific image delivery policy, independent from AstrBot messages."""

from __future__ import annotations

import logging
from io import BytesIO

from PIL import Image as PillowImage
from PIL import UnidentifiedImageError

from .config import config

logger = logging.getLogger("astrbot")


class ImageDeliveryError(RuntimeError):
    pass


class ImageOutputAdapter:
    """Prepare rendered bytes for platform limits without knowing message APIs."""

    def prepare(self, platform: str, content: bytes, purpose: str) -> bytes:
        try:
            with PillowImage.open(BytesIO(content)) as source:
                source.load()
                original_format = (source.format or "unknown").lower()
                original_size = source.size
                if platform != "qq_official" or purpose == "qrcode":
                    self._log(platform, purpose, original_format, original_size, content, content, "unchanged")
                    return content

                needs_conversion = (
                    original_format == "png"
                    or len(content) > config.qq_image_max_bytes
                    or max(source.size) > config.qq_image_max_side
                )
                if not needs_conversion:
                    self._log(platform, purpose, original_format, original_size, content, content, "unchanged")
                    return content

                image = self._rgb(source)
                image.thumbnail(
                    (config.qq_image_max_side, config.qq_image_max_side),
                    PillowImage.Resampling.LANCZOS,
                )
                quality = config.qq_image_jpeg_quality
                output = self._jpeg(image, quality)
                attempts = 0
                while len(output) > config.qq_image_max_bytes and attempts < 10:
                    attempts += 1
                    if quality > 68:
                        quality = max(68, quality - 8)
                    else:
                        width = max(1, int(image.width * 0.85))
                        height = max(1, int(image.height * 0.85))
                        image = image.resize((width, height), PillowImage.Resampling.LANCZOS)
                    output = self._jpeg(image, quality)
                if len(output) > config.qq_image_max_bytes:
                    raise ImageDeliveryError(
                        f"图片压缩后仍超过 {config.qq_image_max_bytes} 字节，"
                        "请调低 qq_image_max_bytes 或减少单图内容"
                    )
                reason = f"jpeg-quality-{quality}"
                if image.size != original_size:
                    reason += "-resized"
                self._log(platform, purpose, original_format, original_size, content, output, reason, image.size)
                return output
        except ImageDeliveryError:
            raise
        except (OSError, UnidentifiedImageError, ValueError) as exc:
            raise ImageDeliveryError(f"图片输出处理失败：{exc}") from exc

    @staticmethod
    def _rgb(source: PillowImage.Image) -> PillowImage.Image:
        if source.mode in {"RGBA", "LA"} or "transparency" in source.info:
            rgba = source.convert("RGBA")
            background = PillowImage.new("RGB", rgba.size, "white")
            background.paste(rgba, mask=rgba.getchannel("A"))
            return background
        return source.convert("RGB")

    @staticmethod
    def _jpeg(image: PillowImage.Image, quality: int) -> bytes:
        stream = BytesIO()
        image.save(stream, "JPEG", quality=quality, optimize=True, progressive=True)
        return stream.getvalue()

    @staticmethod
    def _log(
        platform: str,
        purpose: str,
        original_format: str,
        original_size: tuple[int, int],
        original: bytes,
        output: bytes,
        reason: str,
        output_size: tuple[int, int] | None = None,
    ) -> None:
        logger.info(
            "Skland 图片输出 platform=%s purpose=%s source=%s/%sx%s/%dB "
            "output=%sx%s/%dB reason=%s",
            platform,
            purpose,
            original_format,
            original_size[0],
            original_size[1],
            len(original),
            (output_size or original_size)[0],
            (output_size or original_size)[1],
            len(output),
            reason,
        )


image_output = ImageOutputAdapter()
