"""Fetch the official Stronghold Protocol UI images used by cards.

Source: ArknightsAssets/ArknightsAssets2, cn branch.
Run explicitly when updating bundled art; runtime rendering stays local.
"""

import json
import base64
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote, urlsplit
from urllib.request import urlopen


SOURCE = "https://raw.githubusercontent.com/ArknightsAssets/ArknightsAssets2/cn/assets/dyn/ui/autochess/[uc]autochesscommon/"
ROOT = Path(__file__).resolve().parents[1] / "skland/resources/images/garrison"
RESOURCE_URL = "https://zonai.skland.com/api/v1/game/garrison/guide/resource?seasonId=act3autochess"
FILES = [
    "arts/bandicon/icon_acfend.png",
    "arts/bandicon/icon_acspec.png",
    "arts/bandicon/icon_amedic.png",
    "arts/bandicon/icon_amiya.png",
    "arts/bandicon/icon_ayaji.png",
    "arts/bandicon/icon_bison.png",
    "arts/bandicon/icon_bldsk.png",
    "arts/bandicon/icon_cannot.png",
    "arts/bandicon/icon_cathy.png",
    "arts/bandicon/icon_chen.png",
    "arts/bandicon/icon_chiave.png",
    "arts/bandicon/icon_clementia.png",
    "arts/bandicon/icon_damaztic.png",
    "arts/bandicon/icon_doberm.png",
    "arts/bandicon/icon_ducklord.png",
    "arts/bandicon/icon_durnar.png",
    "arts/bandicon/icon_dusk.png",
    "arts/bandicon/icon_duyaoy.png",
    "arts/bandicon/icon_emperor.png",
    "arts/bandicon/icon_ermengard.png",
    "arts/bandicon/icon_fang.png",
    "arts/bandicon/icon_flamtl.png",
    "arts/bandicon/icon_glady.png",
    "arts/bandicon/icon_gvial2.png",
    "arts/bandicon/icon_harold.png",
    "arts/bandicon/icon_headb2.png",
    "arts/bandicon/icon_humus.png",
    "arts/bandicon/icon_ioleta.png",
    "arts/bandicon/icon_jesica.png",
    "arts/bandicon/icon_justin.png",
    "arts/bandicon/icon_kazema.png",
    "arts/bandicon/icon_kirara.png",
    "arts/bandicon/icon_kjera.png",
    "arts/bandicon/icon_lasher.png",
    "arts/bandicon/icon_liesel.png",
    "arts/bandicon/icon_lisa.png",
    "arts/bandicon/icon_lmlee.png",
    "arts/bandicon/icon_makiri.png",
    "arts/bandicon/icon_malkie.png",
    "arts/bandicon/icon_mberry.png",
    "arts/bandicon/icon_mitm.png",
    "arts/bandicon/icon_mlynar.png",
    "arts/bandicon/icon_mlyss.png",
    "arts/bandicon/icon_mob.png",
    "arts/bandicon/icon_narant.png",
    "arts/bandicon/icon_nymph.png",
    "arts/bandicon/icon_odda.png",
    "arts/bandicon/icon_orchid.png",
    "arts/bandicon/icon_paganini.png",
    "arts/bandicon/icon_pepe.png",
    "arts/bandicon/icon_pianst.png",
    "arts/bandicon/icon_pith.png",
    "arts/bandicon/icon_provs.png",
    "arts/bandicon/icon_pudd.png",
    "arts/bandicon/icon_qalaisa.png",
    "arts/bandicon/icon_quintus.png",
    "arts/bandicon/icon_rmixer.png",
    "arts/bandicon/icon_rockr.png",
    "arts/bandicon/icon_sarkazb.png",
    "arts/bandicon/icon_sciurus.png",
    "arts/bandicon/icon_skybx.png",
    "arts/bandicon/icon_susuro.png",
    "arts/bandicon/icon_swire2.png",
    "arts/bandicon/icon_talr.png",
    "arts/bandicon/icon_thorn2.png",
    "arts/bandicon/icon_trsrhuntr.png",
    "arts/bandicon/icon_vodfox.png",
    "arts/bandicon/icon_wang.png",
    "arts/bandicon/icon_yara.png",
    "arts/bandicon/icon_ymgscm.png",
    "arts/bandicon/icon_yu.png",
    "arts/bandicon/icon_zumama.png",
    "arts/modeicon/mode_abyss_icon.png",
    "arts/modeicon/mode_fast_icon.png",
    "arts/modeicon/mode_funny_icon.png",
    "arts/modeicon/mode_hard_icon.png",
    "arts/modeicon/mode_normal_icon.png",
    "arts/modeicon/mode_training_icon.png",
    "chess/common_chess_level/img_chess_level_1.png",
    "chess/common_chess_level/img_chess_level_2.png",
    "chess/common_chess_level/img_chess_level_3.png",
    "chess/common_chess_level/img_chess_level_4.png",
    "chess/common_chess_level/img_chess_level_5.png",
    "chess/common_chess_level/img_chess_level_6.png",
]


def fetch(path: str) -> None:
    if path.startswith("arts/bandicon/icon_"):
        name = "band/band_" + Path(path).stem.removeprefix("icon_") + ".png"
    elif path.startswith("arts/modeicon/"):
        name = "mode/" + Path(path).name
    else:
        name = "level/" + Path(path).name
    target = ROOT / name
    target.parent.mkdir(parents=True, exist_ok=True)
    url = SOURCE + quote(path, safe="/")
    with urlopen(url, timeout=30) as response:
        content = response.read()
    if not content.startswith(bytes([137, 80, 78, 71, 13, 10, 26, 10])):
        raise ValueError(f"Not a PNG: {path}")
    target.write_bytes(content)
    print(name)


def fetch_strategy_resources() -> None:
    """Save the API's strategy IDs and their actual image URLs, without aliases."""
    with urlopen(RESOURCE_URL, timeout=30) as response:
        payload = json.load(response)
    if payload.get("code") != 0:
        raise ValueError("Could not fetch the official garrison resource")
    strategies = []
    for item in payload["data"]["strategies"]:
        key, url = item.get("strategyId", ""), item.get("iconUrl", "")
        parsed = urlsplit(url)
        if (not key.startswith("band_") or len(key) > 160
                or not all(c.isalnum() or c in "_-" for c in key)
                or parsed.scheme != "https" or parsed.hostname != "web.hycdn.cn"
                or not parsed.path.startswith("/arknights/game/assets/auto_chess/band_icon/")):
            raise ValueError(f"Invalid strategy image: {key}")
        strategies.append({field: item.get(field, "") for field in (
            "strategyId", "iconUrl", "initiatorName", "name")})

    def download(item: dict) -> None:
        with urlopen(item["iconUrl"], timeout=30) as response:
            body = response.read()
        if not body.startswith(bytes([137, 80, 78, 71, 13, 10, 26, 10])):
            raise ValueError(f"Not a PNG: {item['strategyId']}")
        target = ROOT / "band" / f"{item['strategyId']}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(download, strategies))
    (ROOT / "strategies.json").write_text(json.dumps({
        "source": RESOURCE_URL, "seasonId": "act3autochess", "strategies": strategies,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Official strategy icons: {len(strategies)}")


def fetch_decoration_resources() -> None:
    """Save the season banner/logo and textures used by the official guide UI."""
    with urlopen(RESOURCE_URL, timeout=30) as response:
        payload = json.load(response)
    if payload.get("code") != 0:
        raise ValueError("Could not fetch the official garrison resource")
    season = next(item for item in payload["data"]["image"] if item["seasonId"] == "act3autochess")
    static = "https://assets.skland.com/_static_assets/game-tools/"
    entries = {
        "page_background": {"file": "page_background.jpg", "url": season["backgroundPc"]},
        "season_logo": {"file": "season_logo.png", "url": season["logoPc"]},
        "page_texture": {"file": "page_texture.jpg", "url": static + "bg-texture-CEr4_3YI.jpg"},
        "card_background": {"file": "card_background.png", "url": static + "bg_texture-CwniLq08.png"},
        "header_decoration": {"file": "header_decoration.png", "url": static + "deco-BXFxoRto.png"},
    }
    directory = ROOT / "decorations"
    directory.mkdir(parents=True, exist_ok=True)
    for item in entries.values():
        with urlopen(item["url"], timeout=30) as response:
            body = response.read()
        if not body.startswith(bytes([137, 80, 78, 71, 13, 10, 26, 10])) and not body.startswith(b"\xff\xd8\xff"):
            raise ValueError(f"Invalid decoration: {item['file']}")
        (directory / item["file"]).write_bytes(body)
    (ROOT / "decorations.json").write_text(json.dumps({
        "seasonId": "act3autochess",
        "sourcePage": "https://game.skland.com/arknights/stronghold-guide/edit",
        "sourceResource": RESOURCE_URL,
        "sourceScripts": [static + "StrongholdGuide-DEtbCvBe.js", static + "stronghold-Bj4V0ohk.js"],
        "assets": entries,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Official guide decorations: {len(entries)}")


def fetch_bond_resources() -> None:
    """Save official bond IDs, names and icons for the history's bondInfo."""
    with urlopen(RESOURCE_URL, timeout=30) as response:
        payload = json.load(response)
    if payload.get("code") != 0:
        raise ValueError("Could not fetch the official garrison resource")
    bonds = []
    for item in payload["data"]["bonds"]:
        key, url = item.get("bondId", ""), item.get("iconUrl", "")
        parsed = urlsplit(url)
        if (not key or len(key) > 128 or not key.isascii()
                or not all(c.isalnum() or c in "_-" for c in key)
                or parsed.scheme != "https" or parsed.hostname != "web.hycdn.cn"
                or parsed.port not in (None, 443) or parsed.username or parsed.password
                or not parsed.path.startswith("/arknights/game/assets/auto_chess/bond_icon/")
                or not parsed.path.endswith(".png")):
            raise ValueError(f"Invalid bond image: {key}")
        bonds.append({field: item.get(field, "") for field in ("bondId", "iconUrl", "name", "activeCount")})

    def download(item: dict) -> None:
        with urlopen(item["iconUrl"], timeout=30) as response:
            body = response.read()
        if not body.startswith(bytes([137, 80, 78, 71, 13, 10, 26, 10])):
            raise ValueError(f"Not a PNG: {item['bondId']}")
        target = ROOT / "bond" / f"{item['bondId']}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(download, bonds))
    (ROOT / "bonds.json").write_text(json.dumps({
        "source": RESOURCE_URL, "seasonId": "act3autochess", "bonds": bonds,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Official bond icons: {len(bonds)}")


def fetch_trophy_resources() -> None:
    """Save official medal thresholds and all corresponding trophy artwork."""
    table_url = "https://raw.githubusercontent.com/Kengxxiao/ArknightsGameData/master/zh_CN/gamedata/excel/activity_table.json"
    with urlopen(table_url, timeout=45) as response:
        table = json.load(response)
    entries = []
    base = "https://raw.githubusercontent.com/ArknightsAssets/ArknightsAssets2/cn/assets/dyn/ui/autochess/%5Buc%5Dautochessouter/arts/medalicon/"
    for tier in table["autoChessData"]["medalDataList"]:
        icon = tier["medalIconId"]
        if not icon.startswith("trophy_level") or not icon.endswith("_icon") or not icon.removeprefix("trophy_level").removesuffix("_icon").isdigit():
            raise ValueError("Invalid trophy artwork ID")
        filename = icon + ".png"
        url = base + filename
        with urlopen(url, timeout=30) as response:
            body = response.read()
        if not body.startswith(bytes([137, 80, 78, 71, 13, 10, 26, 10])):
            raise ValueError(f"Not a PNG: {filename}")
        destination = ROOT / "trophy" / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(body)
        entries.append({"minimum": tier["medalCount"], "iconId": icon, "url": url})
    (ROOT / "trophies.json").write_text(json.dumps({
        "source": table_url, "field": "autoChessData.medalDataList", "seasonId": "act3autochess", "tiers": entries,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Official trophy tiers: {len(entries)}")


def fetch_equipment_resources() -> None:
    """Bundle normal and promoted icons; the gold artwork includes its own backdrop."""
    with urlopen(RESOURCE_URL, timeout=30) as response:
        payload = json.load(response)
    if payload.get("code") != 0:
        raise ValueError("Could not fetch official equipment resources")
    items = []
    for item in payload["data"]["items"]:
        key, url = item.get("itemId", ""), item.get("iconUrl", "")
        parsed = urlsplit(url)
        if (not re.fullmatch(r"chess_item_[A-Za-z0-9_-]{1,128}", key)
                or parsed.scheme != "https" or parsed.hostname != "web.hycdn.cn"
                or parsed.port not in (None, 443) or parsed.username or parsed.password
                or not parsed.path.startswith("/arknights/game/assets/char_skill/")
                or not parsed.path.endswith(".png")):
            raise ValueError(f"Invalid equipment resource: {key}")
        items.append({field: item.get(field, "") for field in ("itemId", "iconUrl", "name", "shopLevel")})

    table_url = "https://raw.githubusercontent.com/Kengxxiao/ArknightsGameData/master/zh_CN/gamedata/excel/activity_table.json"
    with urlopen(table_url, timeout=45) as response:
        table = json.load(response)["activity"]["AUTOCHESS"]["act3autochess"]["V3_1"]
    by_id = {item["itemId"]: item for item in items}
    for key, chess in table["trapChessDataDict"].items():
        if chess["itemType"] != "EQUIP":
            continue
        normal_id = table["chessNormalIdLookupDict"][key]
        normal = by_id[normal_id]
        if key in by_id:
            by_id[key]["isGolden"] = chess["isGolden"]
            continue
        if not chess["isGolden"] or not re.fullmatch(r"chess_item_[A-Za-z0-9_-]{1,128}", key):
            raise ValueError(f"Unexpected promoted equipment: {key}")
        # Validate the base icon against the table before choosing its skill variant.
        original = table["trapChessDataDict"][normal_id]
        token = original["charId"].split("_", 2)[2]
        normal_suffix = f"sktok_{token}_{original['status']['skillIndex'] + 1}.png"
        if chess["charId"] != original["charId"] or not normal["iconUrl"].endswith(normal_suffix):
            raise ValueError(f"Equipment icon does not match game data: {key}")
        icon = f"sktok_{token}_{chess['status']['skillIndex'] + 1}.png"
        items.append(normal | {"itemId": key, "normalItemId": normal_id, "isGolden": True,
                               "iconUrl": normal["iconUrl"].removesuffix(normal_suffix) + icon})

    def download(item):
        target = ROOT / "equipment" / f"{item['itemId']}.png"
        if target.is_file():
            return
        with urlopen(item["iconUrl"], timeout=30) as response:
            body = response.read()
        if not body.startswith(bytes([137, 80, 78, 71, 13, 10, 26, 10])):
            raise ValueError(f"Not a PNG: {item['itemId']}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(download, items))
    (ROOT / "items.json").write_text(json.dumps({
        "source": RESOURCE_URL, "seasonId": "act3autochess", "advancedSource": table_url,
        "advancedField": "activity.AUTOCHESS.act3autochess.V3_1.trapChessDataDict", "items": items,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Official equipment icons: {len(items)}")


def fetch_operator_badges() -> None:
    source = "https://assets.skland.com/_static_assets/game-tools/stronghold-Bj4V0ohk.js"
    with urlopen(source, timeout=30) as response:
        script = response.read().decode("utf-8")
    entries = []
    for symbol, name in (("at", "phase_0"), ("ot", "phase_1"), ("st", "phase_2"), ("ct", "promoted")):
        match = re.search(r"(?<![\w$])" + symbol + r"=`data:image/png;base64,([^`]+)`", script)
        if not match:
            raise ValueError(f"Missing official operator badge: {symbol}")
        body = base64.b64decode(match.group(1), validate=True)
        if not body.startswith(bytes([137, 80, 78, 71, 13, 10, 26, 10])):
            raise ValueError(f"Invalid operator badge: {symbol}")
        target = ROOT / "operator" / f"{name}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        entries.append({"file": target.name, "sourceSymbol": symbol})
    (ROOT / "operators.json").write_text(json.dumps({
        "source": source, "badges": entries,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Official operator badges: {len(entries)}")


if __name__ == "__main__":
    if "--decorations-only" in sys.argv:
        fetch_decoration_resources()
    elif "--strategies-only" in sys.argv:
        fetch_strategy_resources()
    elif "--bonds-only" in sys.argv:
        fetch_bond_resources()
    elif "--trophies-only" in sys.argv:
        fetch_trophy_resources()
    elif "--equipment-only" in sys.argv:
        fetch_equipment_resources()
        fetch_operator_badges()
    else:
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(fetch, FILES))
        fetch_strategy_resources()
        fetch_bond_resources()
        fetch_trophy_resources()
        fetch_equipment_resources()
        fetch_operator_badges()
        fetch_decoration_resources()
