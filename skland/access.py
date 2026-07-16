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
