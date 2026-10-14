"""Keep the game-table fields consumed by this plugin, without story/asset payloads."""

from .schemas.arknights.game_data import PROFILE_FIELD_PATTERN


CHARACTER_FIELDS = (
    "name", "appellation", "profession", "rarity", "isNotObtainable", "position",
    "potentialItemId", "displayNumber", "subProfessionId", "nationId", "groupId",
    "teamId", "tagList",
)


def _fields(value, names):
    if not isinstance(value, dict):
        raise ValueError("游戏数据条目必须是对象")
    return {name: value[name] for name in names if name in value}


def _character(value):
    result = _fields(value, CHARACTER_FIELDS)
    result["skills"] = [_fields(skill, ("skillId",)) for skill in value.get("skills") or ()]
    if value.get("mainPower"):
        result["mainPower"] = _fields(value["mainPower"], ("nationId", "groupId", "teamId"))
    return result


def _profile(value):
    result = _fields(value, ("charID",))
    matches = {}
    for section in value.get("storyTextAudio") or ():
        for story in section.get("stories") or ():
            for match in PROFILE_FIELD_PATTERN.finditer(story.get("storyText") or ""):
                matches.setdefault(match.group("field"), match.group(0))
        if len(matches) == 2:
            break
    result["storyTextAudio"] = [{"stories": [{"storyText": "\n".join(matches.values())}]}]
    return result


def compact_game_table(route, value):
    """Preserve catalog, skill, module, profile and pool behavior with smaller caches."""
    if not isinstance(value, dict):
        raise ValueError("游戏数据表必须是对象")
    name = route.rsplit("/", 1)[-1]
    if name == "character_table.json":
        return {key: _character(item) for key, item in value.items()}
    if name == "gacha_table.json":
        return {"gachaPoolClient": [_fields(pool, (
            "gachaPoolId", "gachaPoolName", "openTime", "endTime", "gachaRuleType",
        )) for pool in value["gachaPoolClient"]]}
    if name == "char_patch_table.json":
        return {
            "patchChars": {key: _character(item) for key, item in (value.get("patchChars") or {}).items()},
            "infos": {key: _fields(item, ("tmplIds",)) for key, item in (value.get("infos") or {}).items()},
        }
    if name == "uniequip_table.json":
        return {
            "charEquip": value.get("charEquip") or {},
            "equipDict": {key: _fields(item, ("typeIcon", "uniEquipName", "typeName2"))
                          for key, item in (value.get("equipDict") or {}).items()},
        }
    if name == "handbook_info_table.json":
        return {"handbookDict": {key: _profile(item) for key, item in (value.get("handbookDict") or {}).items()}}
    if name == "handbook_team_table.json":
        return {key: _fields(item, ("powerName",)) for key, item in value.items()}
    raise ValueError(f"未知游戏数据表：{name}")
