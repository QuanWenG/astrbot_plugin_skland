"""Presentation data for Stronghold Protocol history cards."""

from dataclasses import dataclass
from datetime import datetime, timezone

from .schemas.arknights.garrison import GarrisonHistory


# Match the four-column recent-history layout used by Integrated Strategies.
# The height limit also handles larger squads and smaller platform limits.
MAX_IMAGE_HEIGHT = 16000
HISTORY_LIMIT = 20
GRID_COLUMNS = 4
BOND_COLUMNS = 8
UNIT_HEIGHT = 76
SQUAD_ROW_GAP = 25
EQUIPMENT_OVERHANG = 13  # Half of a 26px equipment slot.
IMAGE_WIDTH = 2200
PAGE_OVERHEAD = 236  # Canvas padding, themed heading and footer.
OVERVIEW_HEIGHT = 268  # A 244px overview row and the gap below it.
RECORD_GAP = 20
PREFERENCE_LIMIT = 5
ROMAN_LEVELS = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI"}
MODE_LABELS = {"MULTI": "联合模拟", "SINGLE": "单人模拟"}
DIFFICULTY_LABELS = {
    "MULTI": {"FUNNY": "标准模拟", "NORMAL": "险境模拟", "HARD": "绝境模拟", "ABYSS": "终极模拟"},
    "SINGLE": {"FUNNY": "规程等级 1–4", "NORMAL": "规程等级 5–8", "HARD": "规程等级 9–14", "ABYSS": "规程等级 15–20"},
}


@dataclass(frozen=True)
class GarrisonPage:
    nickname: str
    records: list[dict]
    number: int
    total: int
    record_total: int
    overview: dict


def relative_time(timestamp: int, now: int) -> str:
    if timestamp <= 0:
        return "时间未知"
    elapsed = max(0, now - timestamp)
    if elapsed < 60:
        return "刚刚"
    if elapsed < 3600:
        return f"{elapsed // 60}分钟前"
    if elapsed < 86400:
        return f"{elapsed // 3600}小时前"
    if elapsed < 7 * 86400:
        return f"{elapsed // 86400}天前"
    return datetime.fromtimestamp(timestamp, timezone.utc).astimezone().strftime("%Y-%m-%d")


def _percentage(numerator: int, denominator: int) -> int:
    return (200 * numerator + denominator) // (2 * denominator) if denominator else 0


def build_garrison_overview(history: GarrisonHistory) -> dict:
    """Aggregate the merged match archive before selecting recent history cards."""
    records = sorted(history.records, key=lambda item: item.gameEndTs, reverse=True)
    strategies: dict[str, dict] = {}
    bonds: dict[str, dict] = {}
    completed = 0
    boss_kills = hidden_boss_kills = 0
    duration_seconds = 0
    duration_known = True
    for record in records:
        completed += record.completeState == 1
        boss_kills += sum(boss.isKilled for boss in record.acBossInfo)
        hidden_boss_kills += sum(boss.isKilled and boss.isHidden for boss in record.acBossInfo)
        if record.gameStartTs > 0 and record.gameEndTs >= record.gameStartTs:
            duration_seconds += record.gameEndTs - record.gameStartTs
        else:
            duration_known = False
        if record.bandId:
            strategy = strategies.setdefault(record.bandId, {"id": record.bandId, "count": 0, "wins": 0})
            strategy["count"] += 1
            strategy["wins"] += record.completeState == 1
        match_bonds: dict[str, int] = {}
        for bond in record.bondInfo:
            if bond.activate == 1 and bond.bondId:
                match_bonds[bond.bondId] = max(match_bonds.get(bond.bondId, bond.layer), bond.layer)
        for key, layer in match_bonds.items():
            item = bonds.setdefault(key, {"id": key, "count": 0, "max_layer": layer})
            item["count"] += 1
            item["max_layer"] = max(item["max_layer"], layer)
    for strategy in strategies.values():
        strategy["win_rate"] = _percentage(strategy["wins"], strategy["count"])
    hours, minutes = divmod(duration_seconds // 60, 60)
    duration = f"{hours}小时{minutes:02d}分" if hours else f"{minutes}分钟"
    return {
        "total": len(records),
        "is_archived": history.is_archived,
        "trophies": records[0].trophyAfter if records else 0,
        "completed": completed,
        "completion_rate": f"{_percentage(completed, len(records))}%" if records else "—",
        "boss_kills": boss_kills,
        "hidden_boss_kills": hidden_boss_kills,
        "duration_seconds": duration_seconds if duration_known else None,
        "duration": duration if duration_known else "—",
        # Stable ties show the most recently used entry first.
        "strategies": sorted(strategies.values(), key=lambda item: -item["count"])[:PREFERENCE_LIMIT],
        "bonds": sorted((item for item in bonds.values() if item["max_layer"] > 0),
                        key=lambda item: -item["count"])[:PREFERENCE_LIMIT],
    }


def build_garrison_pages(
    history: GarrisonHistory, nickname: str, *, now: int | None = None,
    max_height: int = MAX_IMAGE_HEIGHT,
) -> list[GarrisonPage]:
    """Show only the latest 20 matches in four-column pages."""
    now = int(datetime.now(timezone.utc).timestamp()) if now is None else now
    overview = build_garrison_overview(history)
    records = []
    recent = sorted(history.records, key=lambda item: item.gameEndTs, reverse=True)[:HISTORY_LIMIT]
    for index, record in enumerate(recent, 1):
        state = record.completeState
        squad = [
            {
                "char_id": chess.character_id,
                "name": chess.chessInfo.charName or chess.character_id or "未知棋子",
                "level": chess.chessInfo.chessLevel,
                "badge": ROMAN_LEVELS.get(chess.chessInfo.chessLevel, str(chess.chessInfo.chessLevel)),
                "char_level": chess.chessInfo.charLevel,
                "evolve_phase": chess.chessInfo.evolvePhase,
                "merge_level": chess.mergeLevel,
                "equips": [{"id": equip.equipId} for equip in chess.equips if equip.equipId][:2]
                          if chess.equips is not None else None,
            }
            for chess in record.chessSquad
        ]
        records.append({
            "id": record.id,
            "index": index,
            "round": record.passRound,
            "status": "模拟成功" if state == 1 else "模拟中断" if state == 0 else f"模拟状态 {state}",
            "success": state == 1,
            "time": relative_time(record.gameEndTs, now),
            "mode": MODE_LABELS.get(record.modeType, record.modeType or "模式未知"),
            "team_count": 4 if record.modeType == "MULTI" else 1 if record.modeType == "SINGLE" else 0,
            "difficulty": DIFFICULTY_LABELS.get(record.modeType, {}).get(record.modeDifficulty, record.modeDifficulty or "难度未知"),
            "difficulty_key": record.modeDifficulty.lower() if record.modeDifficulty.lower() in {"abyss", "hard", "normal", "fast", "funny", "training"} else "",
            "band_id": record.bandId,
            "squad": squad,
            "unit_height": UNIT_HEIGHT,
            "squad_row_gap": SQUAD_ROW_GAP,
            "equipment_overhang": EQUIPMENT_OVERHANG,
            "empty_slots": max(0, 10 - len(squad)),
            "bonds": sorted(
                [{"id": bond.bondId, "layer": bond.layer}
                 for bond in record.bondInfo if bond.activate == 1 and bond.bondId],
                key=lambda bond: bond["layer"], reverse=True,
            ),
        })
    chunks: list[list[dict]] = []
    chunk: list[dict] = []
    height = PAGE_OVERHEAD + OVERVIEW_HEIGHT
    row_height = 0
    for record in records:
        rows = (max(10, len(record["squad"])) + 4) // 5
        bond_rows = max(1, (len(record["bonds"]) + BOND_COLUMNS - 1) // BOND_COLUMNS)
        record_height = (rows * record["unit_height"] + (rows - 1) * SQUAD_ROW_GAP + EQUIPMENT_OVERHANG + 164
                         + 14 + bond_rows * 66 + (bond_rows - 1) * 10)
        new_row = len(chunk) % GRID_COLUMNS == 0
        addition = (record_height + (RECORD_GAP if chunk else 0)
                    if new_row else max(0, record_height - row_height))
        if chunk and height + addition > max_height:
            chunks.append(chunk)
            chunk = []
            height = PAGE_OVERHEAD + OVERVIEW_HEIGHT
            new_row = True
            addition = record_height
        chunk.append(record)
        row_height = record_height if new_row else max(row_height, record_height)
        height += addition
    if chunk or not chunks:
        chunks.append(chunk)
    return [GarrisonPage(nickname, chunk, i, len(chunks), len(records), overview)
            for i, chunk in enumerate(chunks, 1)]
