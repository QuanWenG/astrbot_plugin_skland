import pytest

from skland.models import Account, Character, GachaRecord
from skland.store import SklandStore


@pytest.mark.asyncio
async def test_account_and_characters_round_trip(tmp_path):
    store = SklandStore(tmp_path / "skland.sqlite3")
    await store.initialize()
    account = Account("platform:user", "t" * 24, "c" * 32, "token", "42")
    await store.save_account(account)
    await store.replace_characters(
        account.owner_id,
        [Character(account.owner_id, "100", None, "arknights", "1", "博士", True)],
    )

    assert await store.get_account(account.owner_id) == account
    chars = await store.get_characters(account.owner_id, "arknights")
    assert len(chars) == 1
    assert chars[0].nickname == "博士"


@pytest.mark.asyncio
async def test_delete_account_cascades_plugin_data(tmp_path):
    store = SklandStore(tmp_path / "skland.sqlite3")
    await store.initialize()
    account = Account("platform:user", None, "c" * 32, "token", "42")
    await store.save_account(account)
    await store.replace_characters(
        account.owner_id,
        [Character(account.owner_id, "100", None, "arknights", "1", "博士", True)],
    )
    await store.save_sign_result("arknights", account.owner_id, "博士", "✅", "now")

    assert await store.delete_account(account.owner_id)
    assert await store.get_account(account.owner_id) is None
    assert await store.get_characters(account.owner_id) == []
    assert await store.get_sign_results("arknights", account.owner_id) == []


@pytest.mark.asyncio
async def test_gacha_deduplication_and_rogue_cache(tmp_path):
    store = SklandStore(tmp_path / "skland.sqlite3")
    await store.initialize()
    record = GachaRecord(
        "platform:user",
        "100",
        "arknights",
        "char",
        "pool",
        "测试寻访",
        "char_1",
        "测试干员",
        5,
        True,
        False,
        123,
        1,
    )
    assert await store.save_gacha_records([record]) == 1
    assert await store.save_gacha_records([record]) == 0
    assert await store.get_gacha_records("platform:user", "100", "arknights") == [
        record
    ]

    payload = {"topic": "rogue_5", "career": {}}
    await store.save_rogue_cache("platform:user", payload)
    assert await store.get_rogue_cache("platform:user") == payload
