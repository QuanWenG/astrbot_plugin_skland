import json
import sqlite3
from dataclasses import asdict, replace

import pytest

from skland.models import Account, Character, GachaRecord
from skland.store import SklandStore, SCHEMA_VERSION
from skland.service import SklandService


async def add_account(store, remote="remote-1", *, owner="bot:u", uid="100", server="1"):
    account = Account(owner, "access", "cred", "token", remote)
    role = Character(owner, uid, None, "arknights", server, "博士", is_skland_default=True)
    await store.commit_binding(account, [role], await store.owner_version(owner))
    return account, (await store.get_characters(owner))[-1]


def record(role):
    return GachaRecord(role.owner_id, role.uid, role.app_code, "char", "pool", "寻访", "c", "干员", 5, True, False, 123, 1, role.id)


@pytest.mark.asyncio
async def test_multi_account_default_reconcile_and_selective_unbind(tmp_path):
    store = SklandStore(tmp_path / "db")
    await store.initialize()
    a, first = await add_account(store)
    b, second = await add_account(store, "remote-2")
    service = SklandService(store)
    assert (await service.require_character("bot:u", "arknights")).id == first.id
    await store.save_sign_result("arknights", "bot:u", "博士", "ok", "now", character_id=second.id)
    await store.save_gacha_records([record(first), record(second)])
    await store.set_default("bot:u", "arknights", second.id)
    assert (await service.require_character("bot:u", "arknights")).account_id == b.id
    assert (await service.require_character("bot:u", "arknights", 1)).account_id == a.id
    refreshed = replace(second, nickname="新博士", isdefault=False)
    await store.replace_characters("bot:u", [refreshed], b.id)
    assert refreshed.id == second.id
    assert (await service.require_character("bot:u", "arknights")).id == second.id
    assert len(await store.get_sign_results("arknights", "bot:u")) == 1
    await store.delete_account("bot:u", [a.id])
    assert [c.id for c in await store.get_characters("bot:u")] == [second.id]
    assert len(await store.get_gacha_records("bot:u", "100", "arknights")) == 1
    await store.replace_characters("bot:u", [], b.id)
    assert await store.get_characters("bot:u") == []
    assert (await store.get_characters("bot:u", include_unavailable=True))[0].id == second.id
    assert len(await store.get_gacha_records("bot:u", "100", "arknights")) == 1


@pytest.mark.asyncio
async def test_binding_revision_conflict_rolls_back_and_cannot_resurrect(tmp_path):
    store = SklandStore(tmp_path / "db")
    await store.initialize()
    a, role = await add_account(store)
    version = await store.owner_version("bot:u")
    candidate = Account("bot:u", None, "new", "newtoken", "remote-1")
    await store.set_default("bot:u", "arknights", role.id)
    with pytest.raises(ValueError, match="已变化"):
        await store.commit_binding(candidate, [role], version)
    assert (await store.get_account("bot:u")).cred == "cred"
    await store.commit_binding(candidate, [role], await store.owner_version("bot:u"))
    assert candidate.id == a.id
    assert len(await store.list_accounts("bot:u")) == 1
    await store.delete_account("bot:u")
    with pytest.raises(ValueError, match="已解绑"):
        await store.save_account(candidate)
    with pytest.raises(ValueError):
        await store.save_refreshed_credentials(candidate, ("new", "newtoken"))
    assert await store.list_accounts("bot:u") == []


@pytest.mark.asyncio
async def test_cross_platform_and_account_identity_are_isolated(tmp_path):
    store = SklandStore(tmp_path / "db")
    await store.initialize()
    a, first = await add_account(store)
    b, second = await add_account(store, owner="other:u")
    await store.save_gacha_records([record(first), record(second)])
    with pytest.raises(ValueError):
        await store.set_default("bot:u", "arknights", second.id)
    with pytest.raises(ValueError):
        await store.replace_characters("bot:u", [first], b.id)
    assert await store.get_account("bot:u", b.id) is None
    await store.delete_account("bot:u")
    assert len(await store.get_gacha_records("other:u", "100", "arknights")) == 1


def legacy_database(path):
    with sqlite3.connect(path) as db:
        db.executescript("""
        CREATE TABLE accounts(owner_id TEXT PRIMARY KEY,access_token TEXT,cred TEXT,cred_token TEXT,user_id TEXT);
        CREATE TABLE characters(owner_id TEXT,uid TEXT,role_id TEXT,app_code TEXT,channel_master_id TEXT,nickname TEXT,is_default INT);
        CREATE TABLE sign_results(game TEXT,owner_id TEXT,nickname TEXT,result TEXT,updated_at TEXT);
        CREATE TABLE gacha_records(owner_id TEXT,char_uid TEXT,app_code TEXT,item_type TEXT,pool_id TEXT,pool_name TEXT,char_id TEXT,char_name TEXT,rarity INT,is_new INT,is_free INT,gacha_ts INT,pos INT);
        CREATE TABLE rogue_cache(owner_id TEXT,payload TEXT,updated_at TEXT);
        INSERT INTO accounts VALUES ('bot:u','secret-access','secret-cred','secret-token','remote');
        INSERT INTO characters VALUES ('bot:u','100','','arknights','1','同名',0),('bot:u','100','','arknights','2','同名',1),('bot:u','200','','arknights','1','唯一',0);
        INSERT INTO sign_results VALUES ('arknights','bot:u','同名','ambiguous','today'),('arknights','bot:u','唯一','ok','today');
        INSERT INTO rogue_cache VALUES ('bot:u','{"topic":"rogue_5"}','today');
        """)
        for uid in ("100", "200"):
            values = asdict(record(Character("bot:u", uid, None, "arknights", "1", "n")))
            values.pop("character_id")
            db.execute("INSERT INTO gacha_records VALUES (" + ",".join("?" for _ in values) + ")", tuple(values.values()))


@pytest.mark.asyncio
async def test_legacy_backup_history_defaults_and_unresolved(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    legacy_database(path)
    store = SklandStore(path)
    await store.initialize()
    assert store.migration_summary["unresolved"] == 2
    assert store.migration_summary["roles"] == 3
    assert (await store.get_account("bot:u")).access_token == "secret-access"
    roles = await store.get_characters("bot:u")
    assert next(r for r in roles if r.isdefault).channel_master_id == "2"
    assert len(await store.get_sign_results("arknights", "bot:u")) == 1
    assert len(await store.get_gacha_records("bot:u", "200", "arknights")) == 1
    assert await store.get_rogue_cache("bot:u") == {"topic": "rogue_5"}
    with sqlite3.connect(path) as db:
        unresolved = db.execute("SELECT kind,payload FROM unresolved_records ORDER BY kind").fetchall()
        assert [r[0] for r in unresolved] == ["gacha", "sign"]
        assert json.loads(unresolved[1][1])["result"] == "ambiguous"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    await store.initialize()
    assert len(list(tmp_path.glob("*.bak"))) == 1
    with sqlite3.connect(store.migration_summary["backup"]) as backup:
        assert backup.execute("SELECT COUNT(*) FROM gacha_records").fetchone()[0] == 2
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_migration_failure_rolls_back_ddl_and_preserves_backup(tmp_path, monkeypatch):
    path = tmp_path / "legacy.sqlite3"
    legacy_database(path)
    store = SklandStore(path)
    async def fail(db, old):
        await db.execute("INSERT INTO shortcuts VALUES ('partial','[]')")
        raise RuntimeError("injected migration failure")
    monkeypatch.setattr(store, "_migrate", fail)
    with pytest.raises(RuntimeError, match="injected"):
        await store.initialize()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM characters").fetchone()[0] == 3
        assert db.execute("SELECT access_token FROM accounts").fetchone()[0] == "secret-access"
        assert db.execute("PRAGMA user_version").fetchone()[0] == 0
        assert db.execute("SELECT name FROM sqlite_master WHERE name='shortcuts'").fetchall() == []
    assert len(list(tmp_path.glob("*.bak"))) == 1
    await SklandStore(path).initialize()


@pytest.mark.asyncio
async def test_unknown_future_schema_refuses_changes(tmp_path):
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(RuntimeError, match="不支持"):
        await SklandStore(path).initialize()


@pytest.mark.asyncio
async def test_stable_role_keeps_history_after_binding_uid_changes(tmp_path):
    store = SklandStore(tmp_path / "db")
    await store.initialize()
    account = Account("bot:u", "access", "cred", "token", "remote")
    role = Character("bot:u", "old-binding", "game-role", "endfield", "1", "管理员", is_skland_default=True)
    await store.commit_binding(account, [role], 0)
    await store.save_gacha_records([record(role)])
    updated = replace(role, uid="new-binding")
    await store.replace_characters("bot:u", [updated], account.id)
    assert updated.id == role.id
    assert len(await store.get_gacha_records("bot:u", "new-binding", "endfield", character_id=role.id)) == 1
