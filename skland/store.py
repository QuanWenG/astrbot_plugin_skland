import json
from pathlib import Path
from typing import Any

import aiosqlite

from .models import Account, Character, GachaRecord


class SklandStore:
    """SQLite repository for accounts, characters, records and reply state."""

    def __init__(self, path: Path) -> None:
        self.path = path

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS accounts (
                    owner_id TEXT PRIMARY KEY,
                    access_token TEXT,
                    cred TEXT NOT NULL,
                    cred_token TEXT NOT NULL,
                    user_id TEXT
                );
                CREATE TABLE IF NOT EXISTS characters (
                    owner_id TEXT NOT NULL,
                    uid TEXT NOT NULL,
                    role_id TEXT NOT NULL DEFAULT '',
                    app_code TEXT NOT NULL,
                    channel_master_id TEXT NOT NULL,
                    nickname TEXT NOT NULL,
                    is_default INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (owner_id, uid, role_id, app_code)
                );
                CREATE TABLE IF NOT EXISTS sign_results (
                    game TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    nickname TEXT NOT NULL,
                    result TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (game, owner_id, nickname)
                );
                CREATE TABLE IF NOT EXISTS gacha_records (
                    owner_id TEXT NOT NULL,
                    char_uid TEXT NOT NULL,
                    app_code TEXT NOT NULL,
                    item_type TEXT NOT NULL DEFAULT 'char',
                    pool_id TEXT NOT NULL,
                    pool_name TEXT NOT NULL,
                    char_id TEXT NOT NULL,
                    char_name TEXT NOT NULL,
                    rarity INTEGER NOT NULL,
                    is_new INTEGER NOT NULL,
                    is_free INTEGER NOT NULL DEFAULT 0,
                    gacha_ts INTEGER NOT NULL,
                    pos INTEGER NOT NULL,
                    PRIMARY KEY (owner_id, char_uid, app_code, gacha_ts, pos)
                );
                CREATE INDEX IF NOT EXISTS idx_gacha_owner_char
                    ON gacha_records(owner_id, char_uid, app_code);
                CREATE TABLE IF NOT EXISTS rogue_cache (
                    owner_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            await db.commit()

    async def save_account(self, account: Account) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO accounts(owner_id, access_token, cred, cred_token, user_id)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(owner_id) DO UPDATE SET
                    access_token=excluded.access_token,
                    cred=excluded.cred,
                    cred_token=excluded.cred_token,
                    user_id=excluded.user_id""",
                (
                    account.owner_id,
                    account.access_token,
                    account.cred,
                    account.cred_token,
                    account.user_id,
                ),
            )
            await db.commit()

    async def get_account(self, owner_id: str) -> Account | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            row = await (
                await db.execute(
                    "SELECT * FROM accounts WHERE owner_id = ?", (owner_id,)
                )
            ).fetchone()
        return Account(**dict(row)) if row else None

    async def list_accounts(self) -> list[Account]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute("SELECT * FROM accounts")).fetchall()
        return [Account(**dict(row)) for row in rows]

    async def delete_account(self, owner_id: str) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "DELETE FROM accounts WHERE owner_id = ?", (owner_id,)
            )
            for table in ("characters", "sign_results", "gacha_records", "rogue_cache"):
                await db.execute(f"DELETE FROM {table} WHERE owner_id = ?", (owner_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def replace_characters(self, owner_id: str, chars: list[Character]) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM characters WHERE owner_id = ?", (owner_id,))
            await db.executemany(
                """INSERT INTO characters(owner_id, uid, role_id, app_code,
                channel_master_id, nickname, is_default) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        c.owner_id,
                        c.uid,
                        c.role_id or "",
                        c.app_code,
                        c.channel_master_id,
                        c.nickname,
                        int(c.isdefault),
                    )
                    for c in chars
                ],
            )
            await db.commit()

    async def get_characters(
        self, owner_id: str, game: str | None = None
    ) -> list[Character]:
        sql = "SELECT * FROM characters WHERE owner_id = ?"
        params: tuple[str, ...] = (owner_id,)
        if game:
            sql += " AND app_code = ?"
            params += (game,)
        sql += " ORDER BY is_default DESC, nickname"
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute(sql, params)).fetchall()
        return [
            Character(
                row["owner_id"],
                row["uid"],
                row["role_id"] or None,
                row["app_code"],
                row["channel_master_id"],
                row["nickname"],
                bool(row["is_default"]),
            )
            for row in rows
        ]

    async def save_sign_result(
        self, game: str, owner_id: str, nickname: str, result: str, updated_at: str
    ) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO sign_results(game, owner_id, nickname, result, updated_at)
                VALUES (?, ?, ?, ?, ?) ON CONFLICT(game, owner_id, nickname) DO UPDATE SET
                result=excluded.result, updated_at=excluded.updated_at""",
                (game, owner_id, nickname, result, updated_at),
            )
            await db.commit()

    async def get_sign_results(
        self, game: str, owner_id: str | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM sign_results WHERE game = ?"
        params: tuple[str, ...] = (game,)
        if owner_id is not None:
            sql += " AND owner_id = ?"
            params += (owner_id,)
        sql += " ORDER BY updated_at DESC, nickname"
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute(sql, params)).fetchall()
        return [dict(row) for row in rows]

    async def save_gacha_records(self, records: list[GachaRecord]) -> int:
        if not records:
            return 0
        inserted = 0
        async with aiosqlite.connect(self.path) as db:
            for record in records:
                cursor = await db.execute(
                    """INSERT OR IGNORE INTO gacha_records(owner_id, char_uid, app_code,
                    item_type, pool_id, pool_name, char_id, char_name, rarity, is_new,
                    is_free, gacha_ts, pos) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        record.owner_id,
                        record.char_uid,
                        record.app_code,
                        record.item_type,
                        record.pool_id,
                        record.pool_name,
                        record.char_id,
                        record.char_name,
                        record.rarity,
                        int(record.is_new),
                        int(record.is_free),
                        record.gacha_ts,
                        record.pos,
                    ),
                )
                inserted += max(0, cursor.rowcount)
            await db.commit()
        return inserted

    async def get_gacha_records(
        self, owner_id: str, char_uid: str, app_code: str
    ) -> list[GachaRecord]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    """SELECT * FROM gacha_records WHERE owner_id = ? AND char_uid = ?
                AND app_code = ? ORDER BY gacha_ts DESC, pos DESC""",
                    (owner_id, char_uid, app_code),
                )
            ).fetchall()
        return [
            GachaRecord(
                row["owner_id"],
                row["char_uid"],
                row["app_code"],
                row["item_type"],
                row["pool_id"],
                row["pool_name"],
                row["char_id"],
                row["char_name"],
                row["rarity"],
                bool(row["is_new"]),
                bool(row["is_free"]),
                row["gacha_ts"],
                row["pos"],
            )
            for row in rows
        ]

    async def save_rogue_cache(self, owner_id: str, payload: dict[str, Any]) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO rogue_cache(owner_id, payload, updated_at) VALUES (?, ?, datetime('now'))
                ON CONFLICT(owner_id) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at""",
                (owner_id, json.dumps(payload, ensure_ascii=False)),
            )
            await db.commit()

    async def get_rogue_cache(self, owner_id: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.path) as db:
            row = await (
                await db.execute(
                    "SELECT payload FROM rogue_cache WHERE owner_id = ?", (owner_id,)
                )
            ).fetchone()
        return json.loads(row[0]) if row else None
