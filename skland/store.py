"""SQLite persistence with stable account/role identities and legacy upgrades."""
import json
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import aiosqlite

from .access import validate_group_sid
from .models import Account, Character, GachaRecord

logger = logging.getLogger("astrbot")
SCHEMA_VERSION = 4
DDL = (
    """CREATE TABLE accounts (id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id TEXT NOT NULL,
    access_token TEXT, cred TEXT NOT NULL, cred_token TEXT NOT NULL, user_id TEXT, UNIQUE(owner_id,user_id))""",
    """CREATE TABLE characters (id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE, owner_id TEXT NOT NULL,
    uid TEXT NOT NULL, role_id TEXT NOT NULL, app_code TEXT NOT NULL, channel_master_id TEXT NOT NULL,
    nickname TEXT NOT NULL, server_name TEXT NOT NULL DEFAULT '', level INTEGER,
    is_available INTEGER NOT NULL DEFAULT 1, is_skland_default INTEGER NOT NULL DEFAULT 0,
    UNIQUE(account_id,app_code,channel_master_id,role_id))""",
    """CREATE TABLE character_defaults (owner_id TEXT NOT NULL, app_code TEXT NOT NULL,
    character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE, PRIMARY KEY(owner_id,app_code))""",
    """CREATE TABLE sign_results (character_id INTEGER PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,
    game TEXT NOT NULL, owner_id TEXT NOT NULL, nickname TEXT NOT NULL, result TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE gacha_records (character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    owner_id TEXT NOT NULL, char_uid TEXT NOT NULL, app_code TEXT NOT NULL, item_type TEXT NOT NULL,
    pool_id TEXT NOT NULL, pool_name TEXT NOT NULL, char_id TEXT NOT NULL, char_name TEXT NOT NULL,
    rarity INTEGER NOT NULL, is_new INTEGER NOT NULL, is_free INTEGER NOT NULL, gacha_ts INTEGER NOT NULL,
    pos INTEGER NOT NULL, PRIMARY KEY(character_id,item_type,gacha_ts,pos))""",
    """CREATE TABLE unresolved_records (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, owner_id TEXT NOT NULL,
    payload TEXT NOT NULL, reason TEXT NOT NULL, UNIQUE(kind,owner_id,payload))""",
    "CREATE TABLE rogue_cache (owner_id TEXT PRIMARY KEY,payload TEXT NOT NULL,updated_at TEXT NOT NULL)",
    """CREATE TABLE query_context (owner_id TEXT NOT NULL,session_id TEXT NOT NULL,message_id TEXT NOT NULL,
    kind TEXT NOT NULL,payload TEXT NOT NULL,expires_at REAL NOT NULL,PRIMARY KEY(owner_id,session_id,message_id,kind))""",
    "CREATE TABLE shortcuts (name TEXT PRIMARY KEY,argv TEXT NOT NULL)",
    "CREATE TABLE owner_versions (owner_id TEXT PRIMARY KEY,version INTEGER NOT NULL)",
)


REGISTRATION_DDL = """CREATE TABLE credential_groups (
    target_sid TEXT PRIMARY KEY, legacy_match INTEGER NOT NULL DEFAULT 0,
    entry_umo TEXT NOT NULL, registered_by TEXT NOT NULL, registered_at TEXT NOT NULL
)"""


class SklandStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.migration_summary = {}

    @asynccontextmanager
    async def connection(self):
        async with aiosqlite.connect(self.path, timeout=30) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys=ON")
            try:
                yield db
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    async def initialize(self, *, legacy_credential_groups=()) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with self.connection() as db:
            version = (await (await db.execute("PRAGMA user_version")).fetchone())[0]
            if version == SCHEMA_VERSION:
                return
            if version not in (0, 3):
                raise RuntimeError(f"不支持的数据库版本：{version}，请保留原库")
            tables = {row[0] for row in await (await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )).fetchall()}
            if version == 0 and tables - {"accounts", "characters", "gacha_records", "sign_results", "rogue_cache"}:
                raise RuntimeError("未知数据库结构，未执行迁移")
            if tables:
                backup = self.path.with_name(
                    self.path.name + ".pre-v4-" + datetime.now().strftime("%Y%m%d%H%M%S%f") + ".bak"
                )
                async with aiosqlite.connect(backup) as destination:
                    await db.backup(destination)
                self.migration_summary["backup"] = str(backup)
            await db.execute("BEGIN IMMEDIATE")
            # A second initializer may have finished while we backed up.
            version = (await (await db.execute("PRAGMA user_version")).fetchone())[0]
            if version == SCHEMA_VERSION:
                return
            if version == 0:
                old = {}
                for table in tables:
                    old[table] = [dict(row) for row in await (await db.execute(f"SELECT * FROM {table}")).fetchall()]
                    await db.execute(f"DROP TABLE {table}")
                for statement in DDL:
                    await db.execute(statement)
                if old:
                    await self._migrate(db, old)
            await db.execute(REGISTRATION_DDL)
            # user_version is the durable one-time marker, including an empty
            # migration. Old configuration must never resurrect revoked rows.
            legacy = sorted({str(value).strip() for value in (legacy_credential_groups or ()) if str(value).strip()})
            await db.executemany(
                "INSERT INTO credential_groups VALUES (?,1,'','legacy-config',?)",
                [(sid, datetime.now().isoformat()) for sid in legacy],
            )
            self.migration_summary["credential_groups"] = len(legacy)
            await db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        if self.migration_summary:
            logger.info("森空岛数据库迁移：%s", self.migration_summary)

    async def credential_group_allowed(self, group_id: str, umo: str) -> bool:
        async with self.connection() as db:
            row = await (await db.execute(
                "SELECT 1 FROM credential_groups WHERE target_sid=? OR (legacy_match=1 AND target_sid=?)",
                (umo, group_id),
            )).fetchone()
            return row is not None

    async def register_credential_group(self, target_sid: str, *, entry_umo: str, registered_by: str) -> bool:
        sid = validate_group_sid(target_sid)
        group_id = sid.split(":", 2)[2]
        async with self.connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            existing = await (await db.execute(
                "SELECT 1 FROM credential_groups WHERE target_sid=? OR (legacy_match=1 AND target_sid=?)",
                (sid, group_id),
            )).fetchone()
            if existing:
                return False
            await db.execute(
                "INSERT INTO credential_groups VALUES (?,0,?,?,?)",
                (sid, entry_umo, registered_by, datetime.now().isoformat()),
            )
            return True

    async def list_credential_groups(self) -> list[dict]:
        async with self.connection() as db:
            return [dict(row) for row in await (await db.execute(
                "SELECT * FROM credential_groups ORDER BY registered_at,target_sid"
            )).fetchall()]

    async def revoke_credential_group(self, target_sid: str) -> bool:
        target = target_sid.strip()
        # Removing a full SID must also remove a legacy bare-ID rule that
        # would otherwise keep granting access to the same target.
        try:
            legacy_id = validate_group_sid(target).split(":", 2)[2]
        except ValueError:
            legacy_id = target
        async with self.connection() as db:
            cursor = await db.execute(
                "DELETE FROM credential_groups WHERE target_sid=? OR (legacy_match=1 AND target_sid=?)",
                (target, legacy_id),
            )
            return cursor.rowcount > 0

    async def _migrate(self, db, old: dict) -> None:
        accounts = {r["owner_id"]: r for r in old.get("accounts", [])}
        for role in old.get("characters", []):
            accounts.setdefault(role["owner_id"], dict(owner_id=role["owner_id"], access_token=None, cred="", cred_token="", user_id=None))
        ids = {}
        for owner, row in accounts.items():
            cursor = await db.execute("INSERT INTO accounts(owner_id,access_token,cred,cred_token,user_id) VALUES (?,?,?,?,?)",
                (owner, row.get("access_token"), row["cred"], row["cred_token"], row.get("user_id")))
            ids[owner] = cursor.lastrowid
        defaults = set()
        for row in sorted(old.get("characters", []), key=lambda r: (-r["is_default"], r["nickname"])):
            role = Character(row["owner_id"], row["uid"], row["role_id"] or None, row["app_code"],
                row["channel_master_id"], row["nickname"], bool(row["is_default"]), account_id=ids[row["owner_id"]],
                is_skland_default=bool(row["is_default"]))
            await self._upsert_role(db, role)
            key = (role.owner_id, role.app_code)
            if key not in defaults:
                await db.execute("INSERT INTO character_defaults VALUES (?,?,?)", (*key, role.id))
                defaults.add(key)
        unresolved = 0
        for kind, table in (("gacha", "gacha_records"), ("sign", "sign_results")):
            for row in old.get(table, []):
                game = row.get("app_code", row.get("game"))
                column, value = ("uid", row["char_uid"]) if kind == "gacha" else ("nickname", row["nickname"])
                matches = await (await db.execute(f"SELECT id FROM characters WHERE owner_id=? AND app_code=? AND {column}=?",
                    (row["owner_id"], game, value))).fetchall()
                if len(matches) != 1:
                    await self._unresolved(db, kind, row, "旧数据无法唯一确定区服或角色")
                    unresolved += 1
                elif kind == "gacha":
                    await self._insert_gacha(db, GachaRecord(**row, character_id=matches[0][0]))
                else:
                    await db.execute("INSERT INTO sign_results VALUES (?,?,?,?,?,?)",
                        (matches[0][0], row["game"], row["owner_id"], row["nickname"], row["result"], row["updated_at"]))
        for row in old.get("rogue_cache", []):
            await db.execute("INSERT INTO rogue_cache VALUES (?,?,?)", (row["owner_id"], row["payload"], row["updated_at"]))
        self.migration_summary.update(accounts=len(ids), roles=len(old.get("characters", [])), unresolved=unresolved)

    @staticmethod
    async def _unresolved(db, kind, payload, reason):
        await db.execute("INSERT OR IGNORE INTO unresolved_records(kind,owner_id,payload,reason) VALUES (?,?,?,?)",
            (kind, payload["owner_id"], json.dumps(payload, ensure_ascii=False, sort_keys=True), reason))

    @staticmethod
    async def _bump(db, owner):
        await db.execute("INSERT INTO owner_versions VALUES (?,1) ON CONFLICT(owner_id) DO UPDATE SET version=version+1", (owner,))

    async def owner_version(self, owner: str, db=None) -> int:
        if db is None:
            async with self.connection() as connection:
                return await self.owner_version(owner, connection)
        row = await (await db.execute("SELECT version FROM owner_versions WHERE owner_id=?", (owner,))).fetchone()
        return row[0] if row else 0

    async def _check_version(self, db, owner, expected):
        if expected is not None and await self.owner_version(owner, db) != expected:
            raise ValueError("账号或角色数据已变化，请重新查看并确认")

    async def _save_account(self, db, account: Account):
        if account.id is None and account.user_id:
            row = await (await db.execute("SELECT id FROM accounts WHERE owner_id=? AND user_id=?", (account.owner_id, account.user_id))).fetchone()
            if row:
                account.id = row[0]
        values = (account.access_token, account.cred, account.cred_token, account.user_id)
        if account.id is not None:
            cursor = await db.execute("UPDATE accounts SET access_token=?,cred=?,cred_token=?,user_id=? WHERE id=? AND owner_id=?", (*values, account.id, account.owner_id))
            if not cursor.rowcount:
                raise ValueError("账号已解绑，请重新绑定")
        else:
            cursor = await db.execute("INSERT INTO accounts(access_token,cred,cred_token,user_id,owner_id) VALUES (?,?,?,?,?)", (*values, account.owner_id))
            account.id = cursor.lastrowid

    async def save_account(self, account: Account) -> None:
        async with self.connection() as db:
            await self._save_account(db, account)
            await self._bump(db, account.owner_id)

    async def save_refreshed_credentials(self, account: Account, original: tuple[str, str]) -> None:
        async with self.connection() as db:
            cursor = await db.execute("UPDATE accounts SET cred=?,cred_token=? WHERE id=? AND owner_id=? AND cred=? AND cred_token=?",
                (account.cred, account.cred_token, account.id, account.owner_id, *original))
            if not cursor.rowcount:
                raise ValueError("账号凭证已变更，请重试查询")

    async def get_account(self, owner_id: str, account_id: int | None = None) -> Account | None:
        async with self.connection() as db:
            sql, params = "SELECT * FROM accounts WHERE owner_id=?", [owner_id]
            if account_id is not None:
                sql += " AND id=?"
                params.append(account_id)
            row = await (await db.execute(sql + " ORDER BY id LIMIT 1", params)).fetchone()
            return Account(**dict(row)) if row else None

    async def list_accounts(self, owner_id: str | None = None) -> list[Account]:
        async with self.connection() as db:
            rows = await (await db.execute("SELECT * FROM accounts" + (" WHERE owner_id=?" if owner_id is not None else "") + " ORDER BY id",
                (owner_id,) if owner_id is not None else ())).fetchall()
            return [Account(**dict(row)) for row in rows]

    async def delete_account(self, owner_id: str, account_ids: list[int] | None = None, *, expected_version=None) -> bool:
        async with self.connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await self._check_version(db, owner_id, expected_version)
            sql, params = "DELETE FROM accounts WHERE owner_id=?", [owner_id]
            if account_ids is not None:
                if not account_ids:
                    return False
                sql += " AND id IN (" + ",".join("?" for _ in account_ids) + ")"
                params.extend(account_ids)
            cursor = await db.execute(sql, params)
            await db.execute("DELETE FROM query_context WHERE owner_id=?", (owner_id,))
            await db.execute("DELETE FROM rogue_cache WHERE owner_id=?", (owner_id,))
            if not await (await db.execute("SELECT 1 FROM accounts WHERE owner_id=?", (owner_id,))).fetchone():
                await db.execute("DELETE FROM unresolved_records WHERE owner_id=?", (owner_id,))
            await self._bump(db, owner_id)
            return cursor.rowcount > 0

    @staticmethod
    async def _upsert_role(db, role: Character):
        cursor = await db.execute("""INSERT INTO characters(account_id,owner_id,uid,role_id,app_code,
            channel_master_id,nickname,server_name,level,is_available,is_skland_default) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(account_id,app_code,channel_master_id,role_id) DO UPDATE SET uid=excluded.uid,
            nickname=excluded.nickname,server_name=excluded.server_name,level=excluded.level,
            is_available=excluded.is_available,is_skland_default=excluded.is_skland_default RETURNING id""",
            (role.account_id, role.owner_id, role.uid, role.role_id or role.uid, role.app_code, role.channel_master_id,
             role.nickname, role.server_name, role.level, int(role.is_available), int(role.is_skland_default or role.isdefault)))
        role.id = (await cursor.fetchone())[0]

    async def _reconcile(self, db, owner_id, account_id, chars):
        before = {r[0] for r in await (await db.execute("SELECT DISTINCT app_code FROM characters WHERE owner_id=? AND is_available=1", (owner_id,))).fetchall()}
        await db.execute("UPDATE characters SET is_available=0 WHERE account_id=?", (account_id,))
        seen = set()
        for role in chars:
            if role.owner_id != owner_id:
                raise ValueError("角色归属不匹配")
            key = (role.app_code, role.channel_master_id, role.role_id or role.uid)
            if key in seen:
                raise ValueError("接口返回了重复角色")
            seen.add(key)
            role.account_id = account_id
            await self._upsert_role(db, role)
        await db.execute("DELETE FROM character_defaults WHERE character_id IN (SELECT id FROM characters WHERE is_available=0)")
        for game in ("arknights", "endfield"):
            default = await (await db.execute("SELECT 1 FROM character_defaults WHERE owner_id=? AND app_code=?", (owner_id, game))).fetchone()
            if default or game in before:
                continue
            roles = await (await db.execute("SELECT id,is_skland_default FROM characters WHERE owner_id=? AND app_code=? AND is_available=1 ORDER BY id", (owner_id, game))).fetchall()
            remote = [r for r in roles if r[1]]
            chosen = remote[0] if len(remote) == 1 else roles[0] if len(roles) == 1 else None
            if chosen:
                await db.execute("INSERT INTO character_defaults VALUES (?,?,?)", (owner_id, game, chosen[0]))

    async def commit_binding(self, account: Account, chars: list[Character], expected_version: int) -> None:
        async with self.connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await self._check_version(db, account.owner_id, expected_version)
            await self._save_account(db, account)
            await self._reconcile(db, account.owner_id, account.id, chars)
            await self._bump(db, account.owner_id)

    async def replace_characters(self, owner_id: str, chars: list[Character], account_id: int | None = None, *, expected_version=None) -> None:
        if account_id is None:
            accounts = await self.list_accounts(owner_id)
            if len(accounts) != 1:
                raise ValueError("同步角色需要明确所属账号")
            account_id = accounts[0].id
        async with self.connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await self._check_version(db, owner_id, expected_version)
            if not await (await db.execute("SELECT 1 FROM accounts WHERE id=? AND owner_id=?", (account_id, owner_id))).fetchone():
                raise ValueError("账号不存在")
            await self._reconcile(db, owner_id, account_id, chars)
            await self._bump(db, owner_id)

    async def get_characters(self, owner_id: str, game: str | None = None, *, include_unavailable=False) -> list[Character]:
        sql = """SELECT c.*,COALESCE(d.character_id=c.id,0) AS isdefault FROM characters c LEFT JOIN
            character_defaults d ON d.owner_id=c.owner_id AND d.app_code=c.app_code WHERE c.owner_id=?"""
        params = [owner_id]
        if game:
            sql += " AND c.app_code=?"
            params.append(game)
        if not include_unavailable:
            sql += " AND c.is_available=1"
        async with self.connection() as db:
            rows = await (await db.execute(sql + " ORDER BY c.account_id,c.app_code,c.channel_master_id,c.role_id", params)).fetchall()
            return [Character(**dict(row)) for row in rows]

    async def set_default(self, owner_id: str, game: str, character_id: int) -> None:
        async with self.connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            if not await (await db.execute("SELECT 1 FROM characters WHERE id=? AND owner_id=? AND app_code=? AND is_available=1", (character_id, owner_id, game))).fetchone():
                raise ValueError("角色序号无效，请查看最新角色卡")
            await db.execute("INSERT INTO character_defaults VALUES (?,?,?) ON CONFLICT(owner_id,app_code) DO UPDATE SET character_id=excluded.character_id", (owner_id, game, character_id))
            await self._bump(db, owner_id)

    async def save_sign_result(self, game, owner_id, nickname, result, updated_at, *, character_id=None):
        async with self.connection() as db:
            if character_id is None:
                rows = await (await db.execute("SELECT id FROM characters WHERE owner_id=? AND app_code=? AND nickname=?", (owner_id, game, nickname))).fetchall()
                character_id = rows[0][0] if len(rows) == 1 else None
            if character_id is None:
                await self._unresolved(db, "sign", dict(game=game, owner_id=owner_id, nickname=nickname, result=result, updated_at=updated_at), "无法唯一确定角色")
                return
            await db.execute("""INSERT INTO sign_results VALUES (?,?,?,?,?,?) ON CONFLICT(character_id) DO UPDATE SET
                nickname=excluded.nickname,result=excluded.result,updated_at=excluded.updated_at""", (character_id, game, owner_id, nickname, result, updated_at))

    async def get_sign_results(self, game: str, owner_id: str | None = None, *, character_id=None) -> list[dict]:
        sql, params = "SELECT s.*,c.channel_master_id,c.server_name,c.account_id FROM sign_results s JOIN characters c ON c.id=s.character_id WHERE s.game=?", [game]
        if owner_id is not None:
            sql += " AND s.owner_id=?"
            params.append(owner_id)
        if character_id is not None:
            sql += " AND s.character_id=?"
            params.append(character_id)
        async with self.connection() as db:
            return [dict(r) for r in await (await db.execute(sql + " ORDER BY s.updated_at DESC,s.character_id", params)).fetchall()]

    @staticmethod
    async def _insert_gacha(db, record):
        data = asdict(record)
        cursor = await db.execute("INSERT OR IGNORE INTO gacha_records(" + ",".join(data) + ") VALUES (" + ",".join("?" for _ in data) + ")", tuple(data.values()))
        return max(cursor.rowcount, 0)

    async def save_gacha_records(self, records: list[GachaRecord]) -> int:
        inserted = 0
        async with self.connection() as db:
            for record in records:
                if record.character_id is None:
                    rows = await (await db.execute("SELECT id FROM characters WHERE owner_id=? AND uid=? AND app_code=?", (record.owner_id, record.char_uid, record.app_code))).fetchall()
                    if len(rows) != 1:
                        before = db.total_changes
                        await self._unresolved(db, "gacha", asdict(record), "无法唯一确定角色")
                        inserted += db.total_changes - before
                        continue
                    record.character_id = rows[0][0]
                inserted += await self._insert_gacha(db, record)
        return inserted

    async def get_gacha_records(self, owner_id, char_uid, app_code, *, character_id=None) -> list[GachaRecord]:
        sql, params = "SELECT * FROM gacha_records WHERE owner_id=? AND app_code=?", [owner_id, app_code]
        if character_id is not None:
            sql += " AND character_id=?"
            params.append(character_id)
        else:
            sql += " AND char_uid=?"
            params.append(char_uid)
        async with self.connection() as db:
            return [GachaRecord(**dict(r)) for r in await (await db.execute(sql + " ORDER BY gacha_ts DESC,pos DESC", params)).fetchall()]

    async def save_rogue_cache(self, owner_id: str, payload: dict) -> None:
        async with self.connection() as db:
            await db.execute("INSERT INTO rogue_cache VALUES (?,?,datetime('now')) ON CONFLICT(owner_id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at", (owner_id, json.dumps(payload, ensure_ascii=False)))

    async def get_rogue_cache(self, owner_id: str) -> dict | None:
        async with self.connection() as db:
            row = await (await db.execute("SELECT payload FROM rogue_cache WHERE owner_id=?", (owner_id,))).fetchone()
            return json.loads(row[0]) if row else None

    async def save_context(self, owner, session, kind, payload, *, message_id="", ttl=300):
        async with self.connection() as db:
            await db.execute("DELETE FROM query_context WHERE expires_at<=?", (time.time(),))
            for key in {"", str(message_id)}:
                await db.execute("INSERT OR REPLACE INTO query_context VALUES (?,?,?,?,?,?)", (owner, session, key, kind, json.dumps(payload, ensure_ascii=False), time.time() + ttl))

    async def get_context(self, owner, session, kind, message_id="") -> dict | None:
        async with self.connection() as db:
            row = await (await db.execute("SELECT payload FROM query_context WHERE owner_id=? AND session_id=? AND kind=? AND message_id=? AND expires_at>?", (owner, session, kind, str(message_id), time.time()))).fetchone()
            return json.loads(row[0]) if row else None

    async def shortcuts(self) -> dict[str, list[str]]:
        async with self.connection() as db:
            return {r[0]: json.loads(r[1]) for r in await (await db.execute("SELECT name,argv FROM shortcuts ORDER BY name")).fetchall()}

    async def save_shortcut(self, name: str, argv: list[str] | None) -> None:
        async with self.connection() as db:
            if argv is None:
                await db.execute("DELETE FROM shortcuts WHERE name=?", (name,))
            else:
                await db.execute("INSERT OR REPLACE INTO shortcuts VALUES (?,?)", (name, json.dumps(argv, ensure_ascii=False)))
