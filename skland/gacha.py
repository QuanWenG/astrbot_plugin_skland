from collections import defaultdict

from .models import GachaRecord
from .resourcesync import game_data
from .schemas import (
    EfGachaGroup,
    EfGachaPoolInfo,
    EfGachaPull,
    EfGroupedGachaRecord,
    GachaGroup,
    GachaPool,
    GachaPull,
    GroupedGachaRecord,
)


def group_arknights_records(records: list[GachaRecord]) -> GroupedGachaRecord:
    grouped: dict[str, dict[int, list[GachaRecord]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for record in records:
        grouped[record.pool_id][record.gacha_ts].append(record)
    pools = []
    for pool_id, timestamp_groups in grouped.items():
        open_time, end_time, rule_type, up_five, up_six = game_data.pool_info(pool_id)
        groups = [
            GachaGroup(
                gacha_ts=timestamp,
                pulls=[
                    GachaPull(
                        pool_name=item.pool_name,
                        char_id=item.char_id,
                        char_name=item.char_name,
                        rarity=item.rarity,
                        is_new=item.is_new,
                        pos=item.pos,
                    )
                    for item in items
                ],
            )
            for timestamp, items in timestamp_groups.items()
        ]
        pools.append(
            GachaPool(
                gachaPoolId=pool_id,
                gachaPoolName=groups[0].pulls[0].pool_name,
                openTime=open_time,
                endTime=end_time,
                gachaRuleType=rule_type,
                up_five_chars=up_five,
                up_six_chars=up_six,
                records=groups,
            )
        )
    return GroupedGachaRecord(pools=pools)


def _ef_category(pool_id: str) -> str:
    value = pool_id.lower()
    if value.startswith("joint"):
        return "joint"
    if value.startswith("special"):
        return "special"
    if value.startswith(("weapon", "wepon")):
        return "weapon"
    if value == "beginner":
        return "beginner"
    return "standard"


def group_endfield_records(records: list[GachaRecord]) -> EfGroupedGachaRecord:
    grouped: dict[str, dict[int, list[GachaRecord]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for record in records:
        grouped[record.pool_id][record.gacha_ts].append(record)
    categories: dict[str, list[EfGachaPoolInfo]] = {
        "beginner": [],
        "standard": [],
        "special": [],
        "joint": [],
        "weapon": [],
    }
    for pool_id, timestamp_groups in grouped.items():
        groups = [
            EfGachaGroup(
                gacha_ts=timestamp,
                pulls=[
                    EfGachaPull(
                        pool_name=item.pool_name,
                        item_id=item.char_id,
                        item_name=item.char_name,
                        item_type=item.item_type,
                        rarity=item.rarity,
                        is_new=item.is_new,
                        is_free=item.is_free,
                        seq_id=item.pos,
                    )
                    for item in items
                ],
            )
            for timestamp, items in timestamp_groups.items()
        ]
        categories[_ef_category(pool_id)].append(
            EfGachaPoolInfo(
                pool_id=pool_id,
                pool_name=groups[0].pulls[0].pool_name,
                pool_type=groups[0].pulls[0].item_type,
                records=groups,
            )
        )
    return EfGroupedGachaRecord(
        beginner_pools=categories["beginner"],
        standard_pools=categories["standard"],
        special_pools=categories["special"],
        joint_pools=categories["joint"],
        weapon_pools=categories["weapon"],
    )
