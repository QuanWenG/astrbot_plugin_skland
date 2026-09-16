from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class CredentialCommandAccessPolicy:
    """Access policy for commands that handle account credentials."""

    allowed_groups: frozenset[str]

    @classmethod
    def from_values(cls, values: Iterable[object]) -> "CredentialCommandAccessPolicy":
        return cls(
            frozenset(str(value).strip() for value in values if str(value).strip())
        )

    def allows(self, group_id: str, unified_origin: str = "") -> bool:
        group_id = str(group_id).strip()
        if not group_id:
            return True
        unified_origin = str(unified_origin).strip()
        return group_id in self.allowed_groups or unified_origin in self.allowed_groups


def validate_group_sid(value: str) -> str:
    """Accept only a complete group SID; never infer the target from the entry."""
    sid = str(value).strip()
    parts = sid.split(":", 2)
    if (
        len(sid) > 512 or len(parts) != 3 or parts[1] != "GroupMessage"
        or not parts[0] or not parts[2]
        or any(char.isspace() or ord(char) < 32 for char in sid)
    ):
        raise ValueError("目标群 SID 格式应为：平台实例:GroupMessage:群会话ID。")
    return sid
