from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from ozy.constants import PROFILE_LANGUAGE_CODES

_LEVEL_RE = re.compile(r"^([GMS])([1-9])$", re.IGNORECASE)


@dataclass(frozen=True)
class OnboardingProfileSelection:
    preferred_language: str | None
    guardsmen_level: int | None
    monsters_level: int | None
    specialists_level: int | None
    issues: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return (
            self.preferred_language is not None
            and self.guardsmen_level is not None
            and self.monsters_level is not None
            and self.specialists_level is not None
            and not self.issues
        )


def extract_onboarding_profile(
    roles: Iterable[tuple[int, str]],
    language_role_map: dict[str, int],
) -> OnboardingProfileSelection:
    """Read native Discord Onboarding answers from a member's roles.

    Language and G/M/S roles are metadata only. They carry no clan access by
    themselves. The caller decides when to mirror a complete selection into the
    persistent member profile.
    """

    normalized_language_map = {
        code.strip().upper(): int(role_id)
        for code, role_id in language_role_map.items()
        if code.strip().upper() in PROFILE_LANGUAGE_CODES
    }
    role_id_to_language = {role_id: code for code, role_id in normalized_language_map.items()}

    languages: set[str] = set()
    levels: dict[str, set[int]] = {"G": set(), "M": set(), "S": set()}

    for role_id, role_name in roles:
        name = str(role_name or "").strip().upper()
        code = role_id_to_language.get(int(role_id))
        if code:
            languages.add(code)
        elif name in PROFILE_LANGUAGE_CODES:
            # Safe fallback for OZY where language roles are named by code.
            languages.add(name)

        match = _LEVEL_RE.fullmatch(name)
        if match:
            prefix, value = match.groups()
            levels[prefix].add(int(value))

    issues: list[str] = []
    if len(languages) > 1:
        issues.append("multiple language roles")
    for prefix, values in levels.items():
        if len(values) > 1:
            issues.append(f"multiple {prefix} roles")

    return OnboardingProfileSelection(
        preferred_language=(next(iter(languages)) if len(languages) == 1 else None),
        guardsmen_level=(next(iter(levels["G"])) if len(levels["G"]) == 1 else None),
        monsters_level=(next(iter(levels["M"])) if len(levels["M"]) == 1 else None),
        specialists_level=(next(iter(levels["S"])) if len(levels["S"]) == 1 else None),
        issues=tuple(issues),
    )
