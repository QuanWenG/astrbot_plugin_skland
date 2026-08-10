import asyncio

import pytest

from skland.card_cache import ArkCardCache


@pytest.mark.asyncio
async def test_card_cache_coalesces_concurrent_loads_and_invalidates():
    cache = ArkCardCache()
    calls = 0

    async def loader():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return {"value": calls}

    first, second = await asyncio.gather(
        cache.get("owner", "uid", loader), cache.get("owner", "uid", loader)
    )
    assert first == second == {"value": 1}
    assert calls == 1
    await cache.invalidate_owner("owner")
    assert await cache.get("owner", "uid", loader) == {"value": 2}
