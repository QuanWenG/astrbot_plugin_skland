import json
from pathlib import Path

import httpx
import pytest

from skland.download import GitHubDataClient
from skland import resourcesync
from skland.config import config
from skland.exception import RequestException


@pytest.mark.asyncio
async def test_proxy_failure_direct_retry_and_token_only_official_api(monkeypatch):
    monkeypatch.setattr(config, "github_proxy_url", "https://proxy.invalid/")
    monkeypatch.setattr(config, "github_token", "test-token")
    requests = []
    def handler(request):
        requests.append(request)
        if request.url.host == "proxy.invalid":
            return httpx.Response(502)
        if request.url.host == "api.github.com":
            return httpx.Response(200, json={"object": {"sha": "a" * 40, "type": "commit"}})
        return httpx.Response(200, json={"ok": True})
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(**kw, transport=httpx.MockTransport(handler)))
    async with GitHubDataClient() as client:
        assert await client.resolve_commit("owner", "repo", "main") == "a" * 40
        assert await client.get_json("https://raw.githubusercontent.com/owner/repo/" + "a" * 40 + "/data.json") == {"ok": True}
        await client.get_json("https://weedy.prts.wiki/gacha_table.json")
    assert sum(r.url.host == "proxy.invalid" for r in requests) == 1
    for request in requests:
        assert request.headers.get("authorization") == ("Bearer test-token" if request.url.host == "api.github.com" else None)
    assert config.github_proxy_url == "https://proxy.invalid/"


@pytest.mark.asyncio
async def test_html_proxy_response_falls_back_without_token(monkeypatch):
    monkeypatch.setattr(config, "github_proxy_url", "https://proxy.invalid")
    monkeypatch.setattr(config, "github_token", "test-token")
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, content=b"<html>blocked</html>" if request.url.host == "proxy.invalid" else b'{"data": {}}')
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(**kw, transport=httpx.MockTransport(handler)))
    async with GitHubDataClient() as client:
        assert await client.get_json("https://raw.githubusercontent.com/repo/data.json") == {"data": {}}
    assert len(requests) == 2
    assert all("authorization" not in r.headers for r in requests)


def test_batch_replace_restores_old_generation_on_midway_failure(tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    first.write_bytes(b"old-first")
    second.write_bytes(b"old-second")
    original = resourcesync.os.replace
    def fail_second(source, destination):
        if Path(destination) == second:
            raise OSError("injected replacement failure")
        return original(source, destination)
    monkeypatch.setattr(resourcesync.os, "replace", fail_second)
    with pytest.raises(OSError, match="injected"):
        resourcesync.replace_batch({first: b"new-first", second: b"new-second"})
    assert first.read_bytes() == b"old-first" and second.read_bytes() == b"old-second"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["first", "second"]


@pytest.mark.asyncio
async def test_all_update_entrypoints_share_mutex(monkeypatch):
    repository = resourcesync.GameDataRepository()
    monkeypatch.setattr(resourcesync, "game_data", repository)
    async with repository._update_lock:
        with pytest.raises(RequestException, match="正在更新"):
            await repository.load()
        with pytest.raises(RequestException, match="正在更新"):
            await resourcesync.sync_images()


@pytest.mark.asyncio
async def test_invalid_image_download_keeps_existing_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(resourcesync.paths, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(config, "github_proxy_url", "")
    target = tmp_path / "avatar" / "char.png"
    target.parent.mkdir()
    target.write_bytes(b"old-valid-cache")
    def handler(request):
        if "/git/ref/" in request.url.path:
            return httpx.Response(200, json={"object": {"sha": "a" * 40, "type": "commit"}})
        if "/git/trees/" in request.url.path:
            return httpx.Response(200, json={"tree": [{"path": "avatar/char.png", "type": "blob"}]})
        return httpx.Response(200, content=b"<html>proxy error</html>")
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(**kw, transport=httpx.MockTransport(handler)))
    assert await resourcesync.sync_images(update=True) == (0, 1)
    assert target.read_bytes() == b"old-valid-cache"
