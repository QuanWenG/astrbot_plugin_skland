from skland.gacha import group_arknights_records
from skland.models import GachaRecord
from skland.resourcesync import game_data
from skland.schemas import GachaTable


def test_arknights_group_uses_real_pool_metadata():
    game_data.gacha_table = [
        GachaTable(
            gachaPoolId="pool_1",
            gachaPoolName="测试寻访",
            openTime=100,
            endTime=200,
            gachaRuleType=9,
        )
    ]
    game_data.gacha_details = []
    record = GachaRecord(
        "owner",
        "uid",
        "arknights",
        "char",
        "pool_1",
        "测试寻访",
        "char_1",
        "测试干员",
        5,
        True,
        False,
        150,
        1,
    )
    pool = group_arknights_records([record]).pools[0]
    assert (pool.openTime, pool.endTime, pool.gachaRuleType) == (100, 200, 9)


def test_pool_id_is_time_bounded_and_deterministic():
    game_data.gacha_table = [
        GachaTable(
            gachaPoolId="pool_2",
            gachaPoolName="限定寻访",
            openTime=100,
            endTime=200,
            gachaRuleType=1,
        )
    ]
    assert game_data.pool_id("限定寻访", 150) == "pool_2"
    assert game_data.pool_id("限定寻访", 300) == "NORM_1_0_1"
