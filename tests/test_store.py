import sqlite3

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
async def test_same_uid_is_distinguished_by_server(tmp_path):
    store = SklandStore(tmp_path / "skland.sqlite3")
    await store.initialize()
    owner_id = "platform:user"

    await store.replace_characters(
        owner_id,
        [
            Character(owner_id, "100", None, "arknights", "1", "官服博士", True),
            Character(owner_id, "100", None, "arknights", "2", "B服博士"),
        ],
    )

    chars = await store.get_characters(owner_id, "arknights")
    assert [(char.channel_master_id, char.uid) for char in chars] == [
        ("1", "100"),
        ("2", "100"),
    ]


@pytest.mark.asyncio
async def test_initialize_migrates_legacy_character_primary_key(tmp_path):
    database_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database_path) as db:
        db.execute(
            """
            CREATE TABLE characters (
                owner_id TEXT NOT NULL,
                uid TEXT NOT NULL,
                role_id TEXT NOT NULL DEFAULT '',
                app_code TEXT NOT NULL,
                channel_master_id TEXT NOT NULL,
                nickname TEXT NOT NULL,
                is_default INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (owner_id, uid, role_id, app_code)
            )
            """
        )
        db.execute(
            """
            INSERT INTO characters (
                owner_id, uid, role_id, app_code,
                channel_master_id, nickname, is_default
            ) VALUES ('platform:user', '100', '', 'arknights', '1', '旧博士', 1)
            """
        )

    store = SklandStore(database_path)
    await store.initialize()
    await store.initialize()

    preserved = await store.get_characters("platform:user", "arknights")
    assert [(char.nickname, char.channel_master_id) for char in preserved] == [
        ("旧博士", "1")
    ]
    with sqlite3.connect(database_path) as db:
        columns = db.execute("PRAGMA table_info(characters)").fetchall()
    assert tuple(
        row[1] for row in sorted((row for row in columns if row[5]), key=lambda row: row[5])
    ) == (
        "owner_id",
        "uid",
        "role_id",
        "app_code",
        "channel_master_id",
    )

    await store.replace_characters(
        "platform:user",
        [
            Character("platform:user", "100", None, "arknights", "1", "官服", True),
            Character("platform:user", "100", None, "arknights", "2", "B服"),
        ],
    )
    assert len(await store.get_characters("platform:user", "arknights")) == 2


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
