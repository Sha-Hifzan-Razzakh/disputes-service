"""Role ranks shared by the API guards and the approval policy."""

from __future__ import annotations

ROLE_RANK: dict[str, int] = {"analyst": 1, "ops_lead": 2, "admin": 3}


def satisfies(role: str, required: str) -> bool:
    return ROLE_RANK.get(role, 0) >= ROLE_RANK[required]
