from __future__ import annotations

import re
from dataclasses import dataclass


_SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


@dataclass(frozen=True, slots=True)
class SemanticVersion:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] | None
    build: tuple[str, ...] | None


def parse_semver(value: str) -> SemanticVersion:
    if not isinstance(value, str):
        raise ValueError("Skill version must be a string")
    match = _SEMVER_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError("Skill version must be a valid semantic version")
    prerelease = tuple(match.group(4).split(".")) if match.group(4) else None
    if prerelease and any(
        identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0")
        for identifier in prerelease
    ):
        raise ValueError("Numeric semantic-version prerelease identifiers cannot have leading zeroes")
    build = tuple(match.group(5).split(".")) if match.group(5) else None
    return SemanticVersion(
        major=int(match.group(1)),
        minor=int(match.group(2)),
        patch=int(match.group(3)),
        prerelease=prerelease,
        build=build,
    )


def compare_semver(candidate: str, installed: str) -> int:
    """Return -1, 0, or 1 using SemVer precedence (build metadata is ignored)."""

    left = parse_semver(candidate)
    right = parse_semver(installed)
    left_core = (left.major, left.minor, left.patch)
    right_core = (right.major, right.minor, right.patch)
    if left_core != right_core:
        return 1 if left_core > right_core else -1
    if left.prerelease is None or right.prerelease is None:
        if left.prerelease is right.prerelease:
            return 0
        return 1 if left.prerelease is None else -1
    for left_id, right_id in zip(left.prerelease, right.prerelease, strict=False):
        if left_id == right_id:
            continue
        left_numeric = left_id.isdigit()
        right_numeric = right_id.isdigit()
        if left_numeric and right_numeric:
            return 1 if int(left_id) > int(right_id) else -1
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return 1 if left_id > right_id else -1
    if len(left.prerelease) == len(right.prerelease):
        return 0
    return 1 if len(left.prerelease) > len(right.prerelease) else -1
