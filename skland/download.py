"""Per-update HTTP connection pool, fixed Git revisions and bounded proxy fallback."""
import asyncio
import json
import logging
import re
from urllib.parse import quote, urlsplit

import httpx

from .config import config
from .exception import RequestException

logger = logging.getLogger("astrbot")


class GitHubDataClient:
    def __init__(self):
        self.proxy = config.github_proxy_url.strip().rstrip("/")
        self.token = config.github_token.strip()
        self.proxy_failed = False
        self.semaphore = asyncio.Semaphore(8)
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10),
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=8), follow_redirects=True,
            headers={"User-Agent": "astrbot-plugin-skland data updater", "Cache-Control": "no-cache"})

    async def __aenter__(self):
        await self.client.__aenter__()
        return self

    async def __aexit__(self, *args):
        await self.client.__aexit__(*args)

    async def fetch(self, url, parse=lambda value: value):
        host = urlsplit(url).hostname
        proxied = bool(self.proxy and host in {"github.com", "api.github.com", "raw.githubusercontent.com"})
        candidates = [(self.proxy + "/" + url, True), (url, False)] if proxied else [(url, False)]
        failure = "请求失败"
        for candidate, via_proxy in candidates:
            if via_proxy and self.proxy_failed:
                continue
            headers = {"Authorization": f"Bearer {self.token}"} if not via_proxy and host == "api.github.com" and self.token else {}
            for attempt in range(2):
                try:
                    async with self.semaphore:
                        response = await self.client.get(candidate, headers=headers)
                        response.raise_for_status()
                        return parse(response.content)
                except httpx.HTTPStatusError as exc:
                    failure = f"HTTP {exc.response.status_code}"
                    transient = exc.response.status_code in {408, 429} or exc.response.status_code >= 500
                except httpx.HTTPError as exc:
                    failure, transient = type(exc).__name__, True
                except (ValueError, KeyError, TypeError):
                    failure, transient = "数据格式无效", False
                if via_proxy:
                    self.proxy_failed = True
                    break
                if not transient or attempt:
                    break
                await asyncio.sleep(0.5)
        raise RequestException(f"资源下载失败：{failure}")

    async def resolve_commit(self, owner, repo, branch):
        def parse(raw):
            value = json.loads(raw)["object"]
            sha = value["sha"]
            if value.get("type") != "commit" or not re.fullmatch(r"[0-9a-f]{40}", sha):
                raise ValueError("无效提交")
            return sha
        return await self.fetch(f"https://api.github.com/repos/{owner}/{repo}/git/ref/heads/{quote(branch, safe='')}", parse)

    async def get_json(self, url):
        def parse(raw):
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("需要 JSON 对象")
            return value
        logger.info("正在下载资源：%s", urlsplit(url).path.rsplit("/", 1)[-1])
        return await self.fetch(url, parse)

    async def get_text(self, url):
        def parse(raw):
            value = raw.decode("utf-8-sig").strip()
            if not value or "<" in value:
                raise ValueError("版本号无效")
            return value
        return await self.fetch(url, parse)
