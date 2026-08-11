import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

import skland.resourcesync as resourcesync
from skland.resourcesync import GameDataRepository


def _core_tables() -> dict[str, object]:
    return {
        "gacha_table.json": {"gachaPoolClient": []},
        "character_table.json": {
            "char_alt": {
                "name": "凛御银灰",
                "appellation": "",
                "profession": "WARRIOR",
                "rarity": 5,
                "skills": [],
            }
        },
        "char_patch_table.json": {},
        "uniequip_table.json": {"charEquip": {}, "equipDict": {}},
        "handbook_info_table.json": {"handbookDict": {}},
        "handbook_team_table.json": {},
    }


def _write_core_cache(root: Path, *, version: str = "same") -> None:
    (root / "version").parent.mkdir(parents=True, exist_ok=True)
    (root / "version").write_text(version, "utf-8")
    tables = _core_tables()
    for route in GameDataRepository.FILES:
        target = root / route
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(tables[target.name], ensure_ascii=False),
            "utf-8",
        )


@pytest.fixture
def data_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(resourcesync.paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(
        resourcesync.paths,
        "OPERATOR_METADATA_PATH",
        tmp_path / "operator_metadata.json",
    )
    return tmp_path


def test_legacy_core_cache_loads_without_optional_variant_table(
    data_paths: Path,
) -> None:
    _write_core_cache(data_paths)
    repository = GameDataRepository()

    assert repository.load_cached()
    assert repository.operator_catalog.by_id["char_alt"].variant_group_id == ""
    assert not repository.variant_groups_checked


@pytest.mark.asyncio
async def test_same_version_fetches_missing_optional_variant_table(
    data_paths: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_core_cache(data_paths)
    payloads = {
        GameDataRepository.VERSION: b"same",
        GameDataRepository.DETAILS: b'{"gachaPoolClient": []}',
        GameDataRepository.EF_POOLS: b"{}",
    }
    tables = _core_tables()
    for route in GameDataRepository.FILES:
        payloads[GameDataRepository.RAW + route] = json.dumps(
            tables[Path(route).name],
            ensure_ascii=False,
        ).encode("utf-8")
    payloads[
        GameDataRepository.RAW + GameDataRepository.CHAR_META_FILE
    ] = b'{"spCharGroups": {"char_base": ["char_alt"]}}'

    class Response:
        def __init__(self, content: bytes) -> None:
            self.content = content

        @property
        def text(self) -> str:
            return self.content.decode("utf-8")

        def raise_for_status(self) -> None:
            return None

    class Client:
        def __init__(self) -> None:
            self.urls: list[str] = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def get(self, url: str, **_kwargs) -> Response:
            self.urls.append(url)
            return Response(payloads[url])

    client = Client()
    monkeypatch.setattr(
        resourcesync.httpx,
        "AsyncClient",
        lambda *_args, **_kwargs: client,
    )
    repository = GameDataRepository()
    monkeypatch.setattr(
        repository,
        "_refresh_operator_metadata",
        AsyncMock(return_value=None),
    )

    version, downloaded, failed = await repository.load()

    variant_url = GameDataRepository.RAW + GameDataRepository.CHAR_META_FILE
    assert (version, downloaded, failed) == (
        "same",
        len(GameDataRepository.SYNC_FILES),
        0,
    )
    assert variant_url in client.urls
    assert (
        repository.operator_catalog.by_id["char_alt"].variant_group_id
        == "char_base"
    )
    assert repository.variant_groups_checked


@pytest.mark.asyncio
async def test_offline_load_falls_back_to_legacy_core_cache(
    data_paths: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_core_cache(data_paths)

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def get(self, url: str, **_kwargs):
            request = httpx.Request("GET", url)
            raise httpx.ConnectError("offline", request=request)

    monkeypatch.setattr(
        resourcesync.httpx,
        "AsyncClient",
        lambda *_args, **_kwargs: Client(),
    )
    repository = GameDataRepository()

    version, downloaded, failed = await repository.load()

    assert (version, downloaded, failed) == ("same", 0, 1)
    assert repository.operator_catalog.by_id["char_alt"].variant_group_id == ""
    # Keep the optional table retryable after an offline core-cache fallback.
    assert not repository.variant_groups_checked


def test_corrupt_optional_variant_cache_is_ignored(
    data_paths: Path,
) -> None:
    _write_core_cache(data_paths)
    variant_path = data_paths / GameDataRepository.CHAR_META_FILE
    variant_path.parent.mkdir(parents=True, exist_ok=True)
    variant_path.write_text("{", "utf-8")
    repository = GameDataRepository()

    assert repository.load_cached()
    assert repository.operator_catalog.by_id["char_alt"].variant_group_id == ""
    assert not repository.variant_groups_checked


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [],
        {"spCharGroups": []},
        {"spCharGroups": {"char_base": []}},
        {"spCharGroups": {"char_base": [1]}},
    ],
)
def test_malformed_variant_table_shape_is_treated_as_missing(
    data_paths: Path,
    payload: object,
) -> None:
    _write_core_cache(data_paths)
    variant_path = data_paths / GameDataRepository.CHAR_META_FILE
    variant_path.parent.mkdir(parents=True, exist_ok=True)
    variant_path.write_text(json.dumps(payload), "utf-8")
    repository = GameDataRepository()

    assert repository.load_cached()
    assert repository.operator_catalog.by_id["char_alt"].variant_group_id == ""
    assert not repository.variant_groups_loaded
    assert not repository.variant_groups_checked
