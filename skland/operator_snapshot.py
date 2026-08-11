from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


SCHEMA_VERSION = 2


def build_operator_snapshot(
    role: Any,
    roster: Any,
    *,
    snapshot_at: datetime | None = None,
    variant_metadata_complete: bool = False,
) -> dict[str, Any]:
    """Build the credential-free, versioned Arknights operator snapshot DTO."""
    operators: list[dict[str, Any]] = []
    complete_variant_metadata = variant_metadata_complete
    for card in roster.cards:
        character = card.character
        if character is None:
            continue
        operator = {
            "char_id": card.char_id,
            "name": card.name,
            # Game data stores rarity as a zero-based value.
            "rarity": card.rarity + 1,
            "profession": card.profession,
            "evolve_phase": character.evolvePhase,
            "level": character.level,
            "modules": [
                {
                    "module_id": module.module_id,
                    "module_name": module.name,
                    "type_code": module.type_code,
                    "type_icon": module.type_icon,
                    "level": module.level,
                }
                for module in card.modules
                if not module.locked
            ],
            "skills": [
                {
                    "skill_id": skill.id,
                    "skill_index": index,
                    "mastery_level": skill.specializeLevel,
                }
                for index, skill in enumerate(card.skills, start=1)
            ],
            # The Skland API uses 0-5 for potential rank.
            "potential_rank": character.potentialRank,
        }
        variant_group_id = str(getattr(card, "variant_group_id", "") or "")
        if variant_group_id:
            operator["variant_group_id"] = variant_group_id
        else:
            complete_variant_metadata = False
        operators.append(operator)

    timestamp = snapshot_at or datetime.now(timezone.utc)
    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot_at": timestamp.isoformat(timespec="seconds"),
        "variant_metadata_complete": complete_variant_metadata,
        "role": {
            "uid": role.uid,
            "nickname": role.nickname,
            "server_id": role.channel_master_id,
            "server_name": "官服" if role.channel_master_id == "1" else "B服",
        },
        "operators": operators,
    }
