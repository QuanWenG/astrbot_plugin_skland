from io import BytesIO

from PIL import Image

from skland.qrcode_card import is_supported_avatar_url, render_qrcode_card


def test_avatar_url_rejects_local_and_credentialed_hosts():
    assert not is_supported_avatar_url("http://127.0.0.1/avatar.png")
    assert not is_supported_avatar_url("http://localhost/avatar.png")
    assert not is_supported_avatar_url("https://user:pass@example.com/avatar.png")
    assert is_supported_avatar_url("https://example.com/avatar.png")


def test_qrcode_card_has_png_fallback_without_avatar():
    payload = render_qrcode_card("hypergryph://scan_login?scanId=test", None)
    assert payload.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(BytesIO(payload)) as image:
        assert image.width >= 640
        assert image.height > image.width // 2
