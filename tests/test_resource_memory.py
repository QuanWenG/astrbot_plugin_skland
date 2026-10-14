import json
import tracemalloc
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from skland import resourcesync
from skland.exception import RequestException
from skland.resourcesync import GameDataRepository, replace_batch
from skland.schemas import OperatorCatalog
from test_resourcesync import _core_tables, _write_core_cache, data_paths


def test_large_legacy_tables_keep_catalog_behavior_without_unused_payload(data_paths):
    tables = _core_tables()
    tables["character_table.json"]["char_alt"].update(
        potentialItemId="potential", displayNumber="001", subProfessionId="lord",
        position="MELEE", mainPower={"nationId": "rhodes", "unused": "ignored"},
        skills=[{"skillId": "skill_a", "levelUpCostCond": ["ignored"]}],
        tagList=["输出"], unused="ignored",
    )
    tables["char_patch_table.json"] = {
        "patchChars": {"char_patch": {**tables["character_table.json"]["char_alt"], "name": "形态"}},
        "infos": {"char_alt": {"tmplIds": ["char_patch"], "unused": "ignored"}},
    }
    tables["uniequip_table.json"] = {
        "charEquip": {"char_alt": ["module_a"]},
        "equipDict": {"module_a": {"typeIcon": "x", "uniEquipName": "模组", "typeName2": "X", "unused": "ignored"}},
    }
    tables["handbook_info_table.json"] = {"handbookDict": {"char_alt": {
        "charID": "char_alt", "storyTextAudio": [{"stories": [
            {"storyText": "【性别】女\n【种族】萨卡兹\n" + "剧情" * 20000},
        ]}], "unused": "ignored",
    }}}
    tables["handbook_team_table.json"] = {"rhodes": {"powerName": "罗德岛", "unused": "ignored"}}
    for route in GameDataRepository.FILES:
        path = data_paths / route
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(tables[path.name], ensure_ascii=False), encoding="utf-8")
    def catalog(source):
        return OperatorCatalog.from_game_tables(*(source[Path(route).name] for route in GameDataRepository.FILES[1:]))
    compact = GameDataRepository._load_tables(data_paths)
    assert catalog(compact) == catalog(tables)
    assert len(json.dumps(compact)) < len(json.dumps(tables)) / 10
    repository = GameDataRepository()
    assert repository.load_cached()
    assert repository.operator_catalog == catalog(tables)


def test_old_file_backups_do_not_scale_memory_with_total_batch_size(tmp_path):
    payload = b"x" * (2 * 1024 * 1024)
    batch = {}
    for index in range(6):
        path = tmp_path / str(index)
        path.write_bytes(payload)
        batch[path] = b"new"
    tracemalloc.start()
    try:
        replace_batch(batch)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < len(payload) * 2
    assert all(path.read_bytes() == b"new" for path in batch)
    assert sorted(path.name for path in tmp_path.iterdir()) == [str(i) for i in range(6)]


@pytest.mark.asyncio
async def test_core_download_writes_only_used_fields(data_paths, monkeypatch):
    _write_core_cache(data_paths, version="old")
    tables = _core_tables()
    tables["character_table.json"]["char_alt"]["unused"] = "x" * 100000
    commit = "a" * 40
    def handler(request):
        path = request.url.path
        if "/git/ref/" in path:
            return httpx.Response(200, json={"object": {"sha": commit, "type": "commit"}})
        if path.endswith("/version"):
            return httpx.Response(200, text="new")
        name = path.rsplit("/", 1)[-1]
        if name == "char_meta_table.json":
            return httpx.Response(200, json={"spCharGroups": {"char_alt": ["char_alt"]}})
        if name == "GachaPoolTable.json":
            return httpx.Response(200, json={})
        return httpx.Response(200, json=tables[name])
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handler)))
    repository = GameDataRepository()
    monkeypatch.setattr(repository, "_refresh_operator_metadata", AsyncMock(return_value=None))
    version, downloaded, failed = await repository.load()
    assert (version, downloaded, failed) == ("new", 7, 0)
    path = data_paths / GameDataRepository.FILES[1]
    assert path.stat().st_size < 1000
    assert "unused" not in json.loads(path.read_text("utf-8"))["char_alt"]
    assert repository.operator_catalog.by_id["char_alt"].name == "凛御银灰"


@pytest.mark.asyncio
async def test_repeating_metadata_pages_stop_before_unbounded_growth(data_paths, monkeypatch):
    path = data_paths / "operator_metadata.json"
    path.write_text("old-cache", encoding="utf-8")
    calls = []
    rows = [{"title": {"char_id": f"char_{i}", "branch": "领主", "gender": "女", "race": "萨卡兹"}} for i in range(500)]
    def handler(request):
        calls.append(request)
        assert len(calls) <= 2, "repeated metadata pages must not loop indefinitely"
        return httpx.Response(200, json={"cargoquery": rows})
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handler)))
    with pytest.raises(RequestException, match="分页未返回新的"):
        await GameDataRepository()._refresh_operator_metadata()
    assert len(calls) == 2
    assert path.read_text("utf-8") == "old-cache"
