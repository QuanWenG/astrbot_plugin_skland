from io import BytesIO

from PIL import Image

from skland.config import config
from skland.image_output import image_output


def _png(size=(1200, 900), mode="RGB") -> bytes:
    image = Image.new(mode, size, (30, 80, 130, 128) if mode == "RGBA" else (30, 80, 130))
    stream = BytesIO()
    image.save(stream, "PNG")
    return stream.getvalue()


def test_non_qq_image_is_unchanged():
    source = _png()
    assert image_output.prepare("aiocqhttp", source, "rogue") is source


def test_qrcode_stays_png_on_qq():
    source = _png((256, 256))
    assert image_output.prepare("qq_official", source, "qrcode") == source


def test_qq_rendered_png_becomes_bounded_jpeg():
    previous = (config.qq_image_max_bytes, config.qq_image_max_side)
    config.qq_image_max_bytes = 200_000
    config.qq_image_max_side = 600
    try:
        output = image_output.prepare("qq_official", _png((1600, 1200), "RGBA"), "rogue")
    finally:
        config.qq_image_max_bytes, config.qq_image_max_side = previous
    assert output.startswith(b"\xff\xd8")
    assert len(output) <= 200_000
    with Image.open(BytesIO(output)) as image:
        assert image.mode == "RGB"
        assert max(image.size) <= 600
