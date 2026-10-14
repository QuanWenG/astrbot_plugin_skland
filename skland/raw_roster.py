"""Credential-free projections for the public raw roster contract."""
from .exception import RosterExportError


def project_card(data: dict) -> dict:
    if (not isinstance(data, dict) or not isinstance(data.get("chars"), list)
            or not isinstance(data.get("charInfoMap"), dict)
            or not isinstance(data.get("equipmentInfoMap"), dict)):
        raise RosterExportError("invalid_response", "森空岛返回的干员数据格式无效。")
    chars = []
    for char in data["chars"]:
        if not isinstance(char, dict) or not isinstance(char.get("skills"), list) or not isinstance(char.get("equip"), list):
            raise RosterExportError("invalid_response", "森空岛返回的练度数据格式无效。")
        row = {key: char[key] for key in ("charId", "evolvePhase", "level", "potentialRank") if key in char}
        row["skills"] = []
        row["equip"] = []
        for source, fields in (("skills", ("id", "specializeLevel")), ("equip", ("id", "level", "locked"))):
            for item in char[source]:
                if not isinstance(item, dict):
                    raise RosterExportError("invalid_response", "森空岛返回的练度记录无效。")
                row[source].append({key: item[key] for key in fields if key in item})
        chars.append(row)
    def project_map(name, fields):
        output = {}
        for identifier, value in data[name].items():
            if not isinstance(value, dict):
                raise RosterExportError("invalid_response", "森空岛返回的干员资料格式无效。")
            output[identifier] = {key: value[key] for key in fields if key in value}
        return output
    return {"chars": chars,
            "charInfoMap": project_map("charInfoMap", ("id", "name", "profession", "rarity", "variant_group_id", "skill_ids")),
            "equipmentInfoMap": project_map("equipmentInfoMap", ("id", "name", "typeIcon", "type_code"))}
