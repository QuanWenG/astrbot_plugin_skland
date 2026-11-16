import asyncio
import json
import sqlite3
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
import httpx

from skland.api import SklandAPI
from skland.garrison import build_garrison_overview, build_garrison_pages
from skland.models import Account, Character
from skland.schemas import GarrisonHistory
from skland.service import SklandService
from skland.store import DDL, SUBSCRIPTION_DDL, SCHEMA_VERSION, SklandStore


def matches(*ids):
    return GarrisonHistory.model_validate({"records": [
        {"id": str(i), "completeState": i % 2, "gameStartTs": 1000 + i * 100,
         "gameEndTs": 1060 + i * 100, "trophyAfter": i * 10,
         "modeType": "MULTI", "modeDifficulty": "ABYSS", "bandId": "band_ducklord",
         "bondInfo": [{"bondId": "preciShip", "activate": 1, "layer": i * 20}],
         "acBossInfo": [{"bossId": "boss_1", "isKilled": True, "isHidden": i % 2 == 1}]}
        for i in ids
    ]})


@pytest_asyncio.fixture
async def archive(tmp_path):
    store = SklandStore(tmp_path / "archive.sqlite3")
    await store.initialize()
    first = Account("bot:u", None, "cred-one", "token-one", "remote-one")
    roles = [Character("bot:u", "111", None, "arknights", "1", "博士", is_skland_default=True),
             Character("bot:u", "222", None, "arknights", "2", "另一个区服")]
    await store.commit_binding(first, roles, await store.owner_version("bot:u"))
    second = Account("bot:u", None, "cred-two", "token-two", "remote-two")
    role = Character("bot:u", "111", None, "arknights", "1", "另一个账号")
    await store.commit_binding(second, [role], await store.owner_version("bot:u"))
    return store, roles + [role], first


@pytest.mark.asyncio
async def test_overlapping_windows_and_restart_keep_expired_records(archive):
    store, roles, _ = archive
    first = await store.merge_garrison_history("bot:u", roles[0].id, matches(1, 2, 3))
    assert first.is_archived and first.new_count == 3
    second = await store.merge_garrison_history("bot:u", roles[0].id, matches(2, 3, 4))
    assert second.new_count == 1
    assert [record.id for record in second.records] == ["4", "3", "2", "1"]
    repeated = await store.merge_garrison_history("bot:u", roles[0].id, matches(4, 4))
    assert repeated.new_count == 0 and len(repeated.records) == 4
    restarted = SklandStore(store.path)
    await restarted.initialize()
    retained = await restarted.merge_garrison_history("bot:u", roles[0].id, GarrisonHistory())
    stats = build_garrison_overview(retained)
    assert stats["total"] == stats["boss_kills"] == 4
    assert stats["trophies"] == 40 and stats["duration_seconds"] == 240
    assert stats["completion_rate"] == "50%" and stats["is_archived"]
    assert build_garrison_pages(retained, "博士")[0].record_total == 4


@pytest.mark.asyncio
async def test_updated_matches_replace_statistics_without_double_counting(archive):
    store, roles, _ = archive
    await store.merge_garrison_history("bot:u", roles[0].id, matches(2))
    changed = matches(2)
    changed.records[0].completeState = 1
    changed.records[0].bondInfo[0].layer = 500
    updated = await store.merge_garrison_history("bot:u", roles[0].id, changed)
    assert updated.new_count == 0
    stats = build_garrison_overview(updated)
    assert stats["total"] == 1 and stats["completion_rate"] == "100%"
    assert stats["bonds"][0]["max_layer"] == 500
    # Omitted fields in a later response must not erase the fuller saved record.
    partial = GarrisonHistory.model_validate({"records": [{"id": "2", "completeState": 1}]})
    merged = await store.merge_garrison_history("bot:u", roles[0].id, partial)
    assert merged.records[0].trophyAfter == 20
    assert merged.records[0].gameEndTs == 1260
    assert merged.records[0].bondInfo[0].layer == 500


@pytest.mark.asyncio
async def test_official_battle_response_updates_legacy_equipment_without_duplicate_matches(archive, monkeypatch):
    store, roles, _ = archive
    legacy = matches(2)
    record = legacy.records[0].model_dump(exclude_unset=True)
    record["chessSquad"] = [{"charId": "char_472_pasngr", "chessId": "chess_char_4_24_b",
                            "chessInfo": {"chessLevel": 4, "charLevel": 60, "evolvePhase": 2},
                            "equips": [{"equipId": "chess_item_1_01_e_a"}]}]
    await store.merge_garrison_history("bot:u", roles[0].id, GarrisonHistory(records=[record]))
    record["chessSquad"][0]["equips"] = [{"equipId": "chess_item_1_01_e_b"}]
    real = httpx.AsyncClient

    def handle(request):
        assert request.url.path == "/api/ms/game/arknights/garrison/info"
        assert request.url.params["uid"] == roles[0].uid
        return httpx.Response(200, json={"code": 0, "data": {"history": {"records": [record]}}})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(**kw, transport=httpx.MockTransport(handle)))
    _, updated = await SklandService(store).garrison_history("bot:u", 1)
    assert updated.new_count == 0 and len(updated.records) == 1
    assert build_garrison_overview(updated)["total"] == 1
    assert build_garrison_pages(updated, "博士")[0].records[0]["squad"][0]["equips"] == [
        {"id": "chess_item_1_01_e_b"}]
    async with store.connection() as db:
        row = await (await db.execute("SELECT payload FROM garrison_records")).fetchone()
    assert json.loads(row["payload"])["chessSquad"][0]["equips"] == [{"equipId": "chess_item_1_01_e_b"}]


@pytest.mark.asyncio
async def test_operator_details_survive_partial_responses_and_keep_all_equipment(archive):
    store, roles, _ = archive
    legacy = GarrisonHistory.model_validate({"records": [{"id": "old", "completeState": 1,
        "chessSquad": [{"charId": "char_472_pasngr", "chessInfo": {"chessLevel": 4}}]}]})
    saved = await store.merge_garrison_history("bot:u", roles[0].id, legacy)
    assert saved.records[0].chessSquad[0].equips is None
    assert saved.records[0].chessSquad[0].chessInfo.charLevel is None
    full = GarrisonHistory.model_validate({"records": [{"id": "old", "completeState": 1,
        "chessSquad": [{"chessId": "chess_pasngr", "charId": "char_472_pasngr", "mergeLevel": 1,
                        "chessInfo": {"chessLevel": 4, "charLevel": 60, "evolvePhase": 2},
                        "equips": [{"equipId": f"chess_item_{i}"} for i in range(4)]}]}]})
    saved = await store.merge_garrison_history("bot:u", roles[0].id, full)
    assert saved.new_count == 0 and len(saved.records[0].chessSquad[0].equips) == 4
    partial = GarrisonHistory.model_validate({"records": [{"id": "old", "completeState": 1,
        "chessSquad": [{"charId": "char_472_pasngr", "chessInfo": {"chessLevel": 6}}]}]})
    saved = await store.merge_garrison_history("bot:u", roles[0].id, partial)
    unit = saved.records[0].chessSquad[0]
    assert unit.chessInfo.charLevel == 60 and unit.chessInfo.chessLevel == 6
    assert unit.mergeLevel == 1 and len(unit.equips) == 4
    # An explicit empty list is a real equipment removal.
    partial.records[0].chessSquad[0].equips = []
    saved = await store.merge_garrison_history("bot:u", roles[0].id, partial)
    assert saved.records[0].chessSquad[0].equips == []


@pytest.mark.asyncio
async def test_archive_isolates_accounts_roles_and_seasons(archive):
    store, roles, _ = archive
    await store.merge_garrison_history("bot:u", roles[0].id, matches(1))
    for role in roles[1:]:
        assert not (await store.merge_garrison_history("bot:u", role.id, GarrisonHistory())).records
    assert not (await store.merge_garrison_history(
        "bot:u", roles[0].id, GarrisonHistory(), season_id="next-season")).records
    with pytest.raises(ValueError, match="角色"):
        await store.merge_garrison_history("other:user", roles[0].id, matches(9))
    assert len((await store.merge_garrison_history("bot:u", roles[0].id, GarrisonHistory())).records) == 1


@pytest.mark.asyncio
async def test_concurrent_window_merges_do_not_lose_or_duplicate_matches(archive):
    store, roles, _ = archive
    await store.merge_garrison_history("bot:u", roles[0].id, matches(1, 2, 3))
    results = await asyncio.gather(
        store.merge_garrison_history("bot:u", roles[0].id, matches(2, 3, 4)),
        store.merge_garrison_history("bot:u", roles[0].id, matches(3, 4, 5)),
    )
    assert sum(result.new_count for result in results) == 2
    saved = await store.merge_garrison_history("bot:u", roles[0].id, GarrisonHistory())
    assert [record.id for record in saved.records] == ["5", "4", "3", "2", "1"]


@pytest.mark.asyncio
async def test_idless_match_timestamp_identity_is_stable(archive):
    store, roles, _ = archive
    data = matches(1)
    data.records[0].id = ""
    await store.merge_garrison_history("bot:u", roles[0].id, data)
    data.records[0].bondInfo[0].layer = 300
    saved = await store.merge_garrison_history("bot:u", roles[0].id, data)
    assert saved.new_count == 0 and len(saved.records) == 1
    assert saved.records[0].bondInfo[0].layer == 300


@pytest.mark.asyncio
async def test_unbinding_cascades_archive_and_stale_requests_cannot_recreate_it(archive):
    store, roles, account = archive
    await store.merge_garrison_history("bot:u", roles[0].id, matches(1))
    await store.delete_account("bot:u", [account.id])
    with pytest.raises(ValueError, match="解绑"):
        await store.merge_garrison_history("bot:u", roles[0].id, matches(2))
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM garrison_records").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_service_returns_saved_history_after_server_window_shrinks(archive, monkeypatch):
    store, roles, _ = archive
    api = AsyncMock(side_effect=[matches(1, 2, 3), matches(3), GarrisonHistory()])
    monkeypatch.setattr(SklandAPI, "garrison_history", api)
    service = SklandService(store)
    _, first = await service.garrison_history("bot:u", 1)
    assert first.new_count == 3
    role, second = await service.garrison_history("bot:u", 1)
    assert role.id == roles[0].id and len(second.records) == 3 and second.new_count == 0
    _, other = await service.garrison_history("bot:u", 2)
    assert not other.records
    assert api.await_args.args[1] == roles[1].uid


@pytest.mark.asyncio
async def test_v5_upgrade_preserves_accounts_and_subscriptions_and_backs_up(tmp_path):
    path = tmp_path / "v5.sqlite3"
    with sqlite3.connect(path) as db:
        for statement in DDL + SUBSCRIPTION_DDL:
            db.execute(statement)
        db.execute("INSERT INTO accounts VALUES (1,'bot:u',NULL,'cred','token','remote')")
        db.execute("""INSERT INTO characters(id,account_id,owner_id,uid,role_id,app_code,channel_master_id,nickname)
                      VALUES (1,1,'bot:u','111','111','arknights','1','博士')""")
        db.execute("""INSERT INTO sanity_subscriptions(owner_id,character_id,private_umo,advance_minutes,revision)
                      VALUES ('bot:u',1,'private',15,7)""")
        db.execute("PRAGMA user_version=5")
    store = SklandStore(path)
    await store.initialize()
    await store.initialize()
    assert (await store.get_account("bot:u")).cred == "cred"
    subscriptions = await store.list_sanity_subscriptions("bot:u")
    assert len(subscriptions) == 1 and subscriptions[0].revision == 7
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 6
        assert db.execute("SELECT COUNT(*) FROM garrison_records").fetchone()[0] == 0
    backups = list(tmp_path.glob("*.pre-v6-*.bak"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 5
