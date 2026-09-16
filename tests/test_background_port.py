import base64
from io import BytesIO

import httpx
from PIL import Image
import pytest

from skland import config as paths
from skland.background import resolve_background, background_bytes


@pytest.mark.asyncio
async def test_local_directory_data_uri_and_failed_source_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "PLUGIN_DATA_DIR", tmp_path)
    folder = tmp_path / "背景"
    folder.mkdir()
    path = folder / "image.png"
    Image.new("RGB", (8, 8), "red").save(path)
    monkeypatch.setattr(paths.config, "background_source", {"uri": "背景"})
    assert await resolve_background() == path.as_uri()
    uri = "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()
    monkeypatch.setattr(paths.config, "background_source", uri)
    assert await background_bytes(await resolve_background()) == path.read_bytes()
    monkeypatch.setattr(paths.config, "background_source", {"uri": "不存在.png"})
    assert (await resolve_background()).endswith("/background/bg.jpg")
    monkeypatch.setattr(paths.config, "background_source", "default")
    assert await resolve_background(box=True) is None
    monkeypatch.setattr(paths.config, "background_source", "data:image/png;base64,invalid")
    assert (await resolve_background()).endswith("/background/bg.jpg")


@pytest.mark.asyncio
async def test_remote_and_lolicon_fetch_failure_falls_back(monkeypatch):
    requested = []
    def handler(request):
        requested.append(request)
        if request.url.host == "api.lolicon.app":
            return httpx.Response(200, json={"data": [{"urls": {"original": "https://image.invalid/a.png"}}]})
        return httpx.Response(503)
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(**kw, transport=httpx.MockTransport(handler)))
    for source in ("https://image.invalid/a.png", "Lolicon"):
        monkeypatch.setattr(paths.config, "background_source", source)
        assert (await resolve_background("endfield")).endswith("/endfield/default_bg.jpg")
    assert any(r.url.params.get("tag") == "endfield" for r in requested)
