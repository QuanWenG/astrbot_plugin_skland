import json
from io import BytesIO
import httpx
import pytest
from PIL import Image

from skland import config as paths
from skland.filters import (garrison_band_icon, garrison_bond_icon, garrison_decoration, garrison_trophy_icon,
                            garrison_equipment_icon, garrison_portrait_url, garrison_operator_badge)
from skland.garrison_assets import (BAND_ICON_BASE, BOND_ICON_BASE, TROPHY_ICON_BASE, DECORATION_FILES,
                                   resolve_band_icon_urls, resolve_bond_resources, trophy_icon_id, resolve_equipment_resources)
from skland.image_cache import collect_missing_images, write_cached_image


def test_reported_strategy_has_its_actual_official_icon():
    root = paths.RES_DIR / "images/garrison"
    manifest = json.loads((root / "strategies.json").read_text(encoding="utf-8"))
    entries = {item["strategyId"]: item for item in manifest["strategies"]}
    assert entries["band_prepfin"]["initiatorName"] == "苏苏洛"
    for band_id in ("band_prepfin", "band_mech", "band_mess", "band_protect", "band_evolve", "band_support", "band_ursus"):
        assert garrison_band_icon(band_id) == (root / "band" / f"{band_id}.png").as_uri()
        with Image.open(root / "band" / f"{band_id}.png") as decoded:
            decoded.verify()
    assert not (root / "band_unknown.svg").exists()


def test_missing_strategy_registers_official_url_and_reuses_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "RES_DIR", tmp_path / "resources")
    monkeypatch.setattr(paths, "CACHE_DIR", tmp_path / "cache")
    url = f"{BAND_ICON_BASE}band_prepfin.png?revision=2"
    cached = paths.CACHE_DIR / "garrison/band/band_prepfin.png"
    with collect_missing_images() as pending:
        assert garrison_band_icon("band_prepfin", url) == url
    assert pending == {(url, cached)}
    stream = BytesIO()
    Image.new("RGB", (20, 20), "teal").save(stream, format="PNG")
    write_cached_image(cached, stream.getvalue())
    with collect_missing_images() as pending:
        assert garrison_band_icon("band_prepfin", url) == cached.as_uri()
    assert pending == set()


def test_strategy_urls_and_cache_paths_remain_within_expected_locations(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "RES_DIR", tmp_path / "resources")
    monkeypatch.setattr(paths, "CACHE_DIR", tmp_path / "cache")
    with collect_missing_images() as pending:
        assert garrison_band_icon("../../escape") == ""
        assert garrison_band_icon("band_new", "https://localhost/image.png") == f"{BAND_ICON_BASE}band_new.png"
    assert pending == {(f"{BAND_ICON_BASE}band_new.png", paths.CACHE_DIR / "garrison/band/band_new.png")}


def test_invalid_image_body_is_not_persisted(tmp_path):
    cached = tmp_path / "band.png"
    write_cached_image(cached, b"<html>error</html>")
    assert not cached.exists()


@pytest.mark.asyncio
async def test_resource_metadata_reuses_disk_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "RES_DIR", tmp_path / "resources")
    monkeypatch.setattr(paths, "CACHE_DIR", tmp_path / "cache")
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"code": 0, "data": {"strategies": [
            {"strategyId": "band_new", "iconUrl": f"{BAND_ICON_BASE}band_new.png"},
            {"strategyId": "band_unsafe", "iconUrl": "https://localhost/unsafe.png"},
        ]}})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(**kw, transport=httpx.MockTransport(handle)))
    expected = {"band_new": f"{BAND_ICON_BASE}band_new.png"}
    assert await resolve_band_icon_urls({"band_new"}) == expected
    assert (paths.CACHE_DIR / "garrison/strategies.json").is_file()
    assert await resolve_band_icon_urls({"band_new"}) == expected
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_official_bonds_are_bundled_with_names_and_do_not_request_metadata(monkeypatch):
    root = paths.RES_DIR / "images/garrison"
    metadata = json.loads((root / "bonds.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: pytest.fail("bundled bonds requested metadata"))
    entries = await resolve_bond_resources({item["bondId"] for item in metadata["bonds"]})
    assert entries["preciShip"]["name"] == "精准"
    assert entries["suntShip"]["name"] == "绝技"
    assert entries["sargonShip"]["activeCount"] == 3
    assert entries["preciShip"]["activeCount"] == 2
    assert entries["soloShip"]["activeCount"] == 1
    assert len(entries) == 26
    for key in entries:
        icon = root / "bond" / f"{key}.png"
        assert garrison_bond_icon(key) == icon.as_uri()
        with Image.open(icon) as decoded:
            decoded.verify()


def test_missing_bond_registers_official_url_and_reuses_valid_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "RES_DIR", tmp_path / "resources")
    monkeypatch.setattr(paths, "CACHE_DIR", tmp_path / "cache")
    url = f"{BOND_ICON_BASE}preciShip.png?revision=2"
    cached = paths.CACHE_DIR / "garrison/bond/preciShip.png"
    with collect_missing_images() as pending:
        assert garrison_bond_icon("preciShip", url) == url
        assert garrison_bond_icon("../../escape") == ""
        assert garrison_bond_icon("safeBond", "https://localhost/image.png") == f"{BOND_ICON_BASE}safeBond.png"
    assert (url, cached) in pending and len(pending) == 2
    write_cached_image(cached, b"<html>Not Found</html>")
    assert not cached.exists()
    stream = BytesIO()
    Image.new("RGBA", (34, 34), "gold").save(stream, format="PNG")
    write_cached_image(cached, stream.getvalue())
    with collect_missing_images() as pending:
        assert garrison_bond_icon("preciShip", url) == cached.as_uri()
    assert not pending


@pytest.mark.asyncio
async def test_new_bonds_refresh_and_reuse_metadata_with_names(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "RES_DIR", tmp_path / "resources")
    monkeypatch.setattr(paths, "CACHE_DIR", tmp_path / "cache")
    requests = []
    url = f"{BOND_ICON_BASE}newShip.png"

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"code": 0, "data": {"bonds": [
            {"bondId": "newShip", "name": "新盟约", "iconUrl": url, "activeCount": 3},
            {"bondId": "../escape", "iconUrl": url},
            {"bondId": "unsafe", "iconUrl": "https://localhost/unsafe.png"},
        ]}})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(**kw, transport=httpx.MockTransport(handle)))
    expected = {"newShip": {"bondId": "newShip", "name": "新盟约", "iconUrl": url, "activeCount": 3}}
    assert await resolve_bond_resources({"newShip"}) == expected
    assert await resolve_bond_resources({"newShip"}) == expected
    assert (paths.CACHE_DIR / "garrison/bonds.json").is_file()
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_older_bond_cache_keeps_bundled_activation_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "CACHE_DIR", tmp_path / "cache")
    metadata = paths.CACHE_DIR / "garrison/bonds.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(json.dumps({"bonds": [
        {"bondId": "sargonShip", "name": "萨尔贡", "iconUrl": f"{BOND_ICON_BASE}sargonShip.png"}
    ]}), encoding="utf-8")
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: pytest.fail("bundled bonds requested metadata"))
    assert (await resolve_bond_resources({"sargonShip"}))["sargonShip"]["activeCount"] == 3


@pytest.mark.asyncio
async def test_bond_ring_metadata_handles_invalid_and_future_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "CACHE_DIR", tmp_path / "cache")
    metadata = paths.CACHE_DIR / "garrison/bonds.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(json.dumps({"bonds": [
        {"bondId": key, "iconUrl": f"{BOND_ICON_BASE}{key}.png", "activeCount": count}
        for key, count in (("invalidShip", None), ("futureShip", 99), ("negativeShip", -1))
    ]}), encoding="utf-8")
    entries = await resolve_bond_resources(set())
    assert [entries[key]["activeCount"] for key in ("invalidShip", "futureShip", "negativeShip")] == [1, 4, 1]


def test_official_backgrounds_and_textures_are_bundled():
    root = paths.RES_DIR / "images/garrison"
    metadata = json.loads((root / "decorations.json").read_text(encoding="utf-8"))
    assert metadata["seasonId"] == "act3autochess"
    for name, filename in DECORATION_FILES.items():
        image_path = root / "decorations" / filename
        assert metadata["assets"][name]["file"] == filename
        assert garrison_decoration(name) == image_path.as_uri()
        with Image.open(image_path) as decoded:
            decoded.verify()


@pytest.mark.parametrize("name,url", [
    ("page_background", "https://bbs.hycdn.cn/image/2026/11/06/background.jpg"),
    ("card_background", "https://assets.skland.com/_static_assets/game-tools/texture.png"),
])
def test_missing_background_downloads_are_cached_by_source_url(tmp_path, monkeypatch, name, url):
    monkeypatch.setattr(paths, "RES_DIR", tmp_path / "resources")
    monkeypatch.setattr(paths, "CACHE_DIR", tmp_path / "cache")
    metadata = paths.RES_DIR / "images/garrison/decorations.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(json.dumps({"assets": {name: {"url": url}}}), encoding="utf-8")
    with collect_missing_images() as pending:
        assert garrison_decoration(name) == url
    assert len(pending) == 1
    _, cached = pending.pop()
    assert cached.parent == paths.CACHE_DIR / "garrison/decorations"
    stream = BytesIO()
    Image.new("RGB", (20, 20), "teal").save(stream, format="PNG")
    write_cached_image(cached, stream.getvalue())
    with collect_missing_images() as pending:
        assert garrison_decoration(name) == cached.as_uri()
    assert not pending
    changed = url + "?revision=2"
    metadata.write_text(json.dumps({"assets": {name: {"url": changed}}}), encoding="utf-8")
    with collect_missing_images() as pending:
        assert garrison_decoration(name) == changed
    assert len(pending) == 1 and next(iter(pending))[1] != cached


def test_decoration_rejects_unregistered_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "RES_DIR", tmp_path / "resources")
    monkeypatch.setattr(paths, "CACHE_DIR", tmp_path / "cache")
    metadata = paths.RES_DIR / "images/garrison/decorations.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(json.dumps({"assets": {"card_background": {
        "url": "https://localhost/private.png"
    }}}), encoding="utf-8")
    with collect_missing_images() as pending:
        assert garrison_decoration("card_background") == ""
        assert garrison_decoration("../../outside") == ""
    assert not pending


@pytest.mark.parametrize("cups,level", [
    (0, 1), (9, 1), (10, 2), (29, 2), (30, 3), (59, 3), (60, 4), (99, 4),
    (100, 5), (149, 5), (150, 6), (199, 6), (200, 7), (299, 7), (300, 8), (10000, 8),
])
def test_trophy_icon_uses_official_thresholds(cups, level):
    icon = f"trophy_level{level}_icon"
    assert trophy_icon_id(cups) == icon
    source = paths.RES_DIR / "images/garrison/trophy" / f"{icon}.png"
    assert garrison_trophy_icon(cups) == source.as_uri()
    with Image.open(source) as decoded:
        decoded.verify()


def test_trophy_icon_missing_asset_downloads_once_into_cache(tmp_path, monkeypatch):
    original = paths.RES_DIR / "images/garrison/trophies.json"
    metadata = original.read_text(encoding="utf-8")
    monkeypatch.setattr(paths, "RES_DIR", tmp_path / "resources")
    monkeypatch.setattr(paths, "CACHE_DIR", tmp_path / "cache")
    target = paths.RES_DIR / "images/garrison/trophies.json"
    target.parent.mkdir(parents=True)
    target.write_text(metadata, encoding="utf-8")
    url = f"{TROPHY_ICON_BASE}trophy_level6_icon.png"
    cached = paths.CACHE_DIR / "garrison/trophy/trophy_level6_icon.png"
    with collect_missing_images() as pending:
        assert garrison_trophy_icon(157) == url
        assert garrison_trophy_icon(None) == garrison_trophy_icon(-1) == ""
    assert pending == {(url, cached)}
    buffer = BytesIO()
    Image.new("RGBA", (44, 44), "gold").save(buffer, format="PNG")
    write_cached_image(cached, buffer.getvalue())
    with collect_missing_images() as pending:
        assert garrison_trophy_icon(157) == cached.as_uri()
    assert not pending


@pytest.mark.asyncio
async def test_equipment_and_operator_badges_use_bundled_official_art(monkeypatch):
    root = paths.RES_DIR / "images/garrison"
    metadata = json.loads((root / "items.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: pytest.fail("bundled equipment requested metadata"))
    items = await resolve_equipment_resources({item["itemId"] for item in metadata["items"]})
    assert len(items) == 116
    assert items["chess_item_2_04_e_a"]["name"] == "萨尔贡浓茶"
    promoted = [item for item in metadata["items"] if "normalItemId" in item]
    assert len(promoted) == 46
    assert "trapChessDataDict" in metadata["advancedField"]
    for item in promoted:
        assert items[item["itemId"]]["isGolden"] is True
        normal = items[item["normalItemId"]]
        assert item["name"] == normal["name"]
        assert item["iconUrl"] == normal["iconUrl"].removesuffix("_1.png") + "_2.png"
        assert (root / "equipment" / f"{item['itemId']}.png").read_bytes() != (root / "equipment" / f"{item['normalItemId']}.png").read_bytes()
    for key in items:
        image = root / "equipment" / f"{key}.png"
        assert garrison_equipment_icon(key) == image.as_uri()
        with Image.open(image) as decoded:
            decoded.verify()
    for phase, merge, filename in ((0, 0, "phase_0.png"), (1, 0, "phase_1.png"), (2, 0, "phase_2.png"), (2, 1, "promoted.png")):
        assert garrison_operator_badge(phase, merge) == (root / "operator" / filename).as_uri()
        with Image.open(root / "operator" / filename) as decoded:
            decoded.verify()
    assert garrison_operator_badge(None, None) == ""


@pytest.mark.asyncio
async def test_new_equipment_metadata_and_downloads_are_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "RES_DIR", tmp_path / "resources")
    monkeypatch.setattr(paths, "CACHE_DIR", tmp_path / "cache")
    requests = []
    key = "chess_item_new"
    url = "https://web.hycdn.cn/arknights/game/assets/char_skill/new_equipment.png"
    real = httpx.AsyncClient

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"code": 0, "data": {"items": [
            {"itemId": key, "iconUrl": url, "name": "新装备", "shopLevel": 3},
            {"itemId": "chess_item_unsafe", "iconUrl": "https://localhost/private.png"},
        ]}})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(**kw, transport=httpx.MockTransport(handle)))
    expected = {key: {"itemId": key, "iconUrl": url, "name": "新装备", "shopLevel": 3}}
    assert await resolve_equipment_resources({key}) == expected
    assert await resolve_equipment_resources({key}) == expected
    assert len(requests) == 1
    cached = paths.CACHE_DIR / "garrison/equipment" / f"{key}.png"
    with collect_missing_images() as pending:
        assert garrison_equipment_icon(key, url) == url
        assert garrison_equipment_icon("../../escape", url) == ""
        assert garrison_equipment_icon("chess_item_unsafe", "https://localhost/private.png") == ""
    assert pending == {(url, cached)}
    data = BytesIO()
    Image.new("RGBA", (28, 28), "gold").save(data, format="PNG")
    write_cached_image(cached, data.getvalue())
    with collect_missing_images() as pending:
        assert garrison_equipment_icon(key, url) == cached.as_uri()
    assert not pending


def test_garrison_portrait_reuses_shared_cache_and_rejects_unsafe_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "CACHE_DIR", tmp_path / "cache")
    cached = paths.CACHE_DIR / "portrait/char_472_pasngr.png"
    with collect_missing_images() as pending:
        url = garrison_portrait_url("char_472_pasngr")
        assert garrison_portrait_url("../../escape").endswith("operator_empty.svg")
    assert pending == {(url, cached)}
    data = BytesIO()
    Image.new("RGB", (180, 360), "teal").save(data, format="PNG")
    write_cached_image(cached, data.getvalue())
    with collect_missing_images() as pending:
        assert garrison_portrait_url("char_472_pasngr") == cached.as_uri()
    assert not pending
