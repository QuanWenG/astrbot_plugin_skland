from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


SCHEMA_VERSION = 2


def build_operator_snapshot(
    role: Any,
    roster: Any,
    *,
    snapshot_at: datetime | None = None,
) -> dict[str, Any]:
    """Build the credential-free, versioned Arknights operator snapshot DTO."""
    operators: list[dict[str, Any]] = []
    for card in roster.cards:
        character = card.character
        if character is None:
            continue
        operators.append(
            {
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
        )

    timestamp = snapshot_at or datetime.now(timezone.utc)
    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot_at": timestamp.isoformat(timespec="seconds"),
        "role": {
            "uid": role.uid,
            "nickname": role.nickname,
            "server_id": role.channel_master_id,
            "server_name": "官服" if role.channel_master_id == "1" else "B服",
        },
        "operators": operators,
    }
