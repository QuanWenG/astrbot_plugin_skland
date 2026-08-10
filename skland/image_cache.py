"""Render-time cache bridge for deterministic upstream portrait URLs."""

from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator
from urllib.parse import urlsplit
from uuid import uuid4

from . import config as paths

PendingImage = tuple[str, Path]
_pending_images: ContextVar[set[PendingImage] | None] = ContextVar(
    "skland_pending_images", default=None
)
_ALLOWED_PREFIXES = (
    "/arknights/game/assets/char/portrait/",
    "/arknights/game/assets/char_skin/portrait/",
)


def register_missing_image(url: str, path: Path) -> None:
    pending = _pending_images.get()
    if pending is not None and _is_allowed(url, path):
        pending.add((url, path))


@contextmanager
def collect_missing_images() -> Iterator[set[PendingImage]]:
    pending: set[PendingImage] = set()
    token = _pending_images.set(pending)
    try:
        yield pending
    finally:
        _pending_images.reset(token)


def _is_allowed(url: str, path: Path) -> bool:
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname == "web.hycdn.cn"
            and any(parsed.path.startswith(prefix) for prefix in _ALLOWED_PREFIXES)
            and path.resolve().is_relative_to(paths.CACHE_DIR.resolve())
        )
    except (OSError, ValueError):
        return False


def write_cached_image(path: Path, body: bytes) -> None:
    """Atomically store a validated image response inside the plugin cache."""
    if not body or len(body) > 10 * 1024 * 1024 or path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    temporary.write_bytes(body)
    os.replace(temporary, path)
