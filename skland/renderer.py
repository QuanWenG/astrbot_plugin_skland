import asyncio
import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader


class RenderError(RuntimeError):
    pass


class LocalHtmlRenderer:
    """Framework-independent Jinja2 + Playwright HTML screenshot adapter."""

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._launch_lock = asyncio.Lock()
        self.data_dir = Path(tempfile.gettempdir()) / "astrbot_plugin_skland"
        self.executable_path: str | None = None

    def configure(self, data_dir: Path, executable_path: str | None = None) -> None:
        self.data_dir = data_dir / "render"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.executable_path = executable_path or None

    async def start(self) -> None:
        if self._browser and self._browser.is_connected():
            return
        async with self._launch_lock:
            if self._browser and self._browser.is_connected():
                return
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:
                raise RenderError("缺少 playwright，请重新安装插件依赖") from exc

            self._playwright = await async_playwright().start()
            chromium = self._playwright.chromium
            candidates: list[dict[str, Any]] = []
            if self.executable_path:
                candidates.append({"executable_path": self.executable_path})
            candidates.append({})
            if os.name == "nt":
                edge_paths = (
                    Path(
                        "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
                    ),
                    Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
                )
                candidates.extend(
                    {"executable_path": str(path)}
                    for path in edge_paths
                    if path.exists()
                )
                candidates.extend(({"channel": "msedge"}, {"channel": "chrome"}))

            errors = []
            for kwargs in candidates:
                try:
                    self._browser = await chromium.launch(
                        headless=True,
                        args=[
                            "--allow-file-access-from-files",
                            "--disable-web-security",
                        ],
                        **kwargs,
                    )
                    return
                except Exception as exc:
                    errors.append(str(exc).splitlines()[0])
            await self.close()
            raise RenderError(
                "未找到可用的 Chromium/Edge。请配置 browser_executable，"
                "或执行 playwright install chromium。详情：" + " | ".join(errors)
            )

    async def close(self) -> None:
        if self._browser:
            with suppress(Exception):
                await self._browser.close()
        self._browser = None
        if self._playwright:
            with suppress(Exception):
                await self._playwright.stop()
        self._playwright = None

    async def template_to_pic(
        self,
        *,
        template_path: str,
        template_name: str,
        templates: dict[str, Any],
        filters: dict[str, Any] | None = None,
        pages: dict[str, Any] | None = None,
        device_scale_factor: float = 1,
        **_: Any,
    ) -> bytes:
        await self.start()
        root = Path(template_path).resolve()
        env = Environment(loader=FileSystemLoader(root), autoescape=False)
        if filters:
            env.filters.update(filters)
        html = env.get_template(template_name).render(**templates)
        base = root.as_uri().rstrip("/") + "/"
        if "<head>" in html:
            html = html.replace("<head>", f'<head><base href="{base}">', 1)
        else:
            html = f'<base href="{base}">' + html

        fd, filename = tempfile.mkstemp(suffix=".html", dir=self.data_dir)
        os.close(fd)
        html_path = Path(filename)
        html_path.write_text(html, encoding="utf-8")
        viewport = (pages or {}).get("viewport", {"width": 800, "height": 600})
        width = max(1, int(viewport.get("width", 800)))
        height = max(1, int(viewport.get("height", 600)))
        context = await self._browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=device_scale_factor,
        )
        page = await context.new_page()
        try:
            await page.goto(html_path.as_uri(), wait_until="load", timeout=30_000)
            with suppress(Exception):
                await page.wait_for_load_state("networkidle", timeout=10_000)
            await page.wait_for_timeout(300)
            return await page.screenshot(type="png", full_page=True)
        finally:
            await context.close()
            with suppress(OSError):
                html_path.unlink()


renderer = LocalHtmlRenderer()


async def template_to_pic(**kwargs: Any) -> bytes:
    return await renderer.template_to_pic(**kwargs)
