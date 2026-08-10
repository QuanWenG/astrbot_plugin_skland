"""Small async LRU cache for expensive ArkCard requests."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any

from .config import config

Loader = Callable[[], Awaitable[Any]]


class ArkCardCache:
    def __init__(self) -> None:
        self._values: OrderedDict[tuple[str, str, int], tuple[Any, float]] = OrderedDict()
        self._inflight: dict[tuple[str, str, int], asyncio.Task[Any]] = {}
        self._generations: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def _load(
        self, key: tuple[str, str, int], loader: Loader
    ) -> Any:
        try:
            value = await loader()
            async with self._lock:
                owner_id, _, generation = key
                if self._generations.get(owner_id, 0) == generation:
                    self._values[key] = (
                        value,
                        monotonic() + config.ark_card_cache_ttl,
                    )
                    self._values.move_to_end(key)
                    while len(self._values) > config.ark_card_cache_max_entries:
                        self._values.popitem(last=False)
            return value
        finally:
            async with self._lock:
                if self._inflight.get(key) is asyncio.current_task():
                    self._inflight.pop(key, None)

    async def get(self, owner_id: str, subject: str, loader: Loader) -> Any:
        async with self._lock:
            now = monotonic()
            stale = [name for name, (_, expires) in self._values.items() if now >= expires]
            for name in stale:
                self._values.pop(name, None)
            key = (owner_id, subject, self._generations.get(owner_id, 0))
            cached = self._values.get(key)
            if cached is not None:
                self._values.move_to_end(key)
                return cached[0]
            task = self._inflight.get(key)
            if task is None:
                task = asyncio.create_task(self._load(key, loader))
                self._inflight[key] = task
        return await asyncio.shield(task)

    async def invalidate_owner(self, owner_id: str) -> None:
        async with self._lock:
            self._generations[owner_id] = self._generations.get(owner_id, 0) + 1
            for key in [key for key in self._values if key[0] == owner_id]:
                self._values.pop(key, None)


ark_card_cache = ArkCardCache()
