from pathlib import Path

from skland.filters import ef_charId_to_avatarUrl
from skland.schemas.arknights.rogue.data import Topics
from skland.schemas.endfield.gacha.base import EfCharGachaResponse


def test_endfield_character_response_filters_non_draw_entries():
    draw = {
        "kind": "draw",
        "nameText": "Operator",
        "poolId": "pool-1",
        "poolName": "Pool",
        "charId": "chr_1",
        "charName": "Operator",
        "rarity": 6,
        "isFree": False,
        "isNew": True,
        "gachaTs": "1000",
        "seqId": "2",
    }
    response = EfCharGachaResponse.model_validate(
        {
            "list": [
                {"kind": "information", "nameText": "Information Book"},
                draw,
            ],
            "hasMore": False,
        }
    )

    assert len(response.gacha_list) == 1
    assert response.gacha_list[0].charId == "chr_1"


def test_endfield_assets_use_current_endpoint():
    assert ef_charId_to_avatarUrl("chr_1") == (
        "https://endfieldtools.dev/assets/images/endfield/charremoteicon/icon_chr_1.png"
    )
    assert ef_charId_to_avatarUrl("wpn_1") == (
        "https://endfieldtools.dev/assets/images/endfield/itemiconbig/wpn_1.png"
    )


def test_rogue_six_topic_and_assets_are_available():
    assert Topics("黑流树海").topic_id == "rogue_6"
    resource_dir = Path("skland/resources/images")
    assert (resource_dir / "background/rogue/pic_rogue_6_kv1.png").is_file()
    assert len(list((resource_dir / "rogue/band/rogue_6").glob("*.png"))) == 22
