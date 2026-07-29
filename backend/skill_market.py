from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.skill_manifest import SkillManifest, SkillManifestError
from backend.skill_store import MAX_MARKET_FILE_BYTES, compute_package_digest
from backend.skill_version import parse_semver


BUNDLED_SKILL_MARKET_DIR = Path(__file__).with_name("bundled_skills")

_MARKET_ID_PATTERN = re.compile(
    r"^[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*)?$"
)
_CATALOG_ID_PATTERN = re.compile(
    r"^agent-skill:([a-z0-9]+(?:-[a-z0-9]+)*):([a-z0-9]+(?:-[a-z0-9]+)*)$"
)
_ONTOLOGY_REF_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_ACCENT_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")
_MARKET_FIELDS = {
    "market_id",
    "catalog_id",
    "title",
    "provider",
    "version",
    "tags",
    "icon",
    "accent",
    "license",
    "compatibility",
    "ontology_refs",
    "triggers",
    "surfaces",
    "provenance",
}
_PROVENANCE_FIELDS = {"source", "verified"}


class SkillMarketError(ValueError):
    """A configured local market entry is invalid or unsafe."""


@dataclass(frozen=True, slots=True)
class SkillMarketEntry:
    market_id: str
    catalog_id: str
    title: str
    provider: str
    version: str
    tags: tuple[str, ...]
    icon: str | None
    accent: str | None
    ontology_refs: tuple[str, ...]
    triggers: tuple[str, ...]
    surfaces: tuple[str, ...]
    provenance_source: str
    provenance_verified: bool
    provenance_trust: str
    manifest: SkillManifest
    digest: str
    source_dir: Path
    skill_content: bytes
    market_content: bytes

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def description(self) -> str:
        return self.manifest.description

    def as_market_item(self) -> dict[str, Any]:
        return {
            "market_id": self.market_id,
            "catalog_id": self.catalog_id,
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "version": self.version,
            "provider": self.provider,
            "tags": list(self.tags),
            "icon": self.icon,
            "accent": self.accent,
            "license": self.manifest.license,
            "compatibility": self.manifest.compatibility,
            "ontology_refs": list(self.ontology_refs),
            "triggers": list(self.triggers),
            "surfaces": list(self.surfaces),
            "provenance": {
                "source": self.provenance_source,
                "digest": self.digest,
                "verified": self.provenance_verified,
                "trust": self.provenance_trust,
            },
        }

    def as_install_record(self) -> dict[str, Any]:
        return {
            **self.as_market_item(),
            "digest": self.digest,
            "metadata": dict(self.manifest.metadata),
            # Kept for inspection only; it never grants tools or permissions.
            "allowed_tools": self.manifest.allowed_tools,
        }


class SkillMarket:
    """Read a trusted, local directory of immutable context-only skill packages."""

    def __init__(self, market_dir: str | Path = BUNDLED_SKILL_MARKET_DIR):
        self.market_dir = Path(market_dir).expanduser().absolute()
        # Trust is a loader property, never a publisher-controlled manifest
        # claim. Conservative path equality means an alias of the bundled
        # directory is treated as an unverified local Market.
        self._is_bundled_market = (
            self.market_dir == BUNDLED_SKILL_MARKET_DIR.expanduser().absolute()
        )

    def list_entries(self) -> list[SkillMarketEntry]:
        self._validate_market_dir()
        entries: list[SkillMarketEntry] = []
        seen_market_ids: set[str] = set()
        seen_catalog_ids: set[str] = set()
        for entry_dir in sorted(self.market_dir.iterdir(), key=lambda path: path.name):
            if entry_dir.name.startswith("."):
                continue
            if entry_dir.is_symlink() or not entry_dir.is_dir():
                raise SkillMarketError(f"Skill market entries must be real directories: {entry_dir}")
            entry = self._load_entry(entry_dir)
            if entry.market_id in seen_market_ids:
                raise SkillMarketError(f"Duplicate market_id in skill market: {entry.market_id}")
            if entry.catalog_id in seen_catalog_ids:
                raise SkillMarketError(f"Duplicate catalog_id in skill market: {entry.catalog_id}")
            seen_market_ids.add(entry.market_id)
            seen_catalog_ids.add(entry.catalog_id)
            entries.append(entry)
        return entries

    def get(self, market_id: str) -> SkillMarketEntry:
        for entry in self.list_entries():
            if entry.market_id == market_id:
                return entry
        raise KeyError(market_id)

    def _validate_market_dir(self) -> None:
        if self.market_dir.is_symlink() or not self.market_dir.is_dir():
            raise SkillMarketError(f"Skill market must be a real directory: {self.market_dir}")

    def _load_entry(self, entry_dir: Path) -> SkillMarketEntry:
        try:
            children = list(entry_dir.iterdir())
        except OSError as exc:
            raise SkillMarketError(f"Unable to inspect skill market entry: {entry_dir}") from exc
        names = {child.name for child in children}
        if names != {"SKILL.md", "market.json"}:
            raise SkillMarketError(
                f"Skill market entry '{entry_dir.name}' must contain only SKILL.md and market.json"
            )
        if any(child.is_symlink() or not child.is_file() for child in children):
            raise SkillMarketError(f"Skill market entry contains an unsafe file: {entry_dir}")

        skill_path = entry_dir / "SKILL.md"
        market_path = entry_dir / "market.json"
        try:
            skill_content = skill_path.read_bytes()
            market_content = market_path.read_bytes()
        except OSError as exc:
            raise SkillMarketError(f"Unable to read skill market entry: {entry_dir}") from exc
        if len(market_content) > MAX_MARKET_FILE_BYTES:
            raise SkillMarketError(f"market.json exceeds the {MAX_MARKET_FILE_BYTES}-byte limit")
        try:
            manifest = SkillManifest.from_bytes(skill_content, expected_name=entry_dir.name)
        except SkillManifestError as exc:
            raise SkillMarketError(f"Invalid SKILL.md in market entry '{entry_dir.name}': {exc}") from exc
        try:
            raw = json.loads(market_content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SkillMarketError(f"Invalid market.json in market entry '{entry_dir.name}'") from exc
        if not isinstance(raw, dict):
            raise SkillMarketError("market.json must contain a JSON object")
        unknown = sorted(set(raw) - _MARKET_FIELDS)
        if unknown:
            raise SkillMarketError(f"Unsupported market.json field: {unknown[0]}")

        market_id = _required_string(raw, "market_id", 160)
        if not _MARKET_ID_PATTERN.fullmatch(market_id):
            raise SkillMarketError("market_id must be a lowercase hyphenated name, optionally namespaced with /")
        if "/" in market_id and market_id.rsplit("/", 1)[1] != manifest.name:
            raise SkillMarketError("market_id name must match SKILL.md name")
        if "/" not in market_id and market_id != manifest.name:
            raise SkillMarketError("market_id must match SKILL.md name")

        catalog_id = _required_string(raw, "catalog_id", 192)
        catalog_match = _CATALOG_ID_PATTERN.fullmatch(catalog_id)
        if catalog_match is None or catalog_match.group(2) != manifest.name:
            raise SkillMarketError(
                "catalog_id must have form agent-skill:<namespace>:<SKILL.md name>"
            )
        if not self._is_bundled_market and catalog_match.group(1) == "ambient-agent":
            raise SkillMarketError(
                "External Skill markets cannot publish into the reserved ambient-agent namespace"
            )
        if "/" in market_id and catalog_match.group(1) != market_id.split("/", 1)[0]:
            raise SkillMarketError("market_id and catalog_id namespaces must match")

        title = _required_string(raw, "title", 120)
        provider = _required_string(raw, "provider", 120)
        version = _required_string(raw, "version", 128)
        try:
            parse_semver(version)
        except ValueError as exc:
            raise SkillMarketError(str(exc)) from exc

        tags = _string_list(raw, "tags", min_items=0, max_items=16, max_chars=64)
        triggers = _string_list(raw, "triggers", min_items=1, max_items=64, max_chars=160)
        ontology_refs = _string_list(raw, "ontology_refs", min_items=0, max_items=64, max_chars=128)
        for ontology_ref in ontology_refs:
            if _ONTOLOGY_REF_PATTERN.fullmatch(ontology_ref) is None:
                raise SkillMarketError(f"Invalid ontology reference: {ontology_ref}")

        surfaces = _string_list(raw, "surfaces", min_items=1, max_items=1, max_chars=32)
        if surfaces != ("agent_context",):
            raise SkillMarketError("Context-only skills must declare surfaces as ['agent_context']")

        icon = _optional_string(raw, "icon", 32)
        accent = _optional_string(raw, "accent", 7)
        if accent is not None and _ACCENT_PATTERN.fullmatch(accent) is None:
            raise SkillMarketError("Skill accent must be a six-digit hexadecimal color")

        market_license = _optional_string(raw, "license", 256)
        if market_license is not None and market_license != manifest.license:
            raise SkillMarketError("market.json license must match SKILL.md license")
        market_compatibility = _optional_string(raw, "compatibility", 500)
        if market_compatibility is not None and market_compatibility != manifest.compatibility:
            raise SkillMarketError("market.json compatibility must match SKILL.md compatibility")

        provenance = raw.get("provenance")
        if not isinstance(provenance, dict):
            raise SkillMarketError("market.json provenance must be an object")
        unknown_provenance = sorted(set(provenance) - _PROVENANCE_FIELDS)
        if unknown_provenance:
            raise SkillMarketError(f"Unsupported provenance field: {unknown_provenance[0]}")
        _required_string(provenance, "source", 512)
        claimed_verified = provenance.get("verified")
        if not isinstance(claimed_verified, bool):
            raise SkillMarketError("market.json provenance.verified must be a boolean")
        if self._is_bundled_market:
            source = f"bundled://ambient-agent/{manifest.name}"
            verified = True
            trust = "bundled"
        else:
            source = f"local-market://{market_id}"
            verified = False
            trust = "local"

        return SkillMarketEntry(
            market_id=market_id,
            catalog_id=catalog_id,
            title=title,
            provider=provider,
            version=version,
            tags=tags,
            icon=icon,
            accent=accent,
            ontology_refs=ontology_refs,
            triggers=triggers,
            surfaces=surfaces,
            provenance_source=source,
            provenance_verified=verified,
            provenance_trust=trust,
            manifest=manifest,
            digest=compute_package_digest(skill_content, market_content),
            source_dir=entry_dir,
            skill_content=skill_content,
            market_content=market_content,
        )


def _required_string(raw: dict[str, Any], key: str, max_chars: int) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SkillMarketError(f"market.json field '{key}' must be a non-empty string")
    normalized = value.strip()
    if normalized != value:
        raise SkillMarketError(f"market.json field '{key}' must not contain surrounding whitespace")
    if len(normalized) > max_chars:
        raise SkillMarketError(f"market.json field '{key}' exceeds {max_chars} characters")
    return normalized


def _optional_string(raw: dict[str, Any], key: str, max_chars: int) -> str | None:
    if key not in raw or raw[key] is None:
        return None
    return _required_string(raw, key, max_chars)


def _string_list(
    raw: dict[str, Any],
    key: str,
    *,
    min_items: int,
    max_items: int,
    max_chars: int,
) -> tuple[str, ...]:
    value = raw.get(key)
    if not isinstance(value, list):
        raise SkillMarketError(f"market.json field '{key}' must be an array of strings")
    if not min_items <= len(value) <= max_items:
        raise SkillMarketError(
            f"market.json field '{key}' must contain between {min_items} and {max_items} items"
        )
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise SkillMarketError(f"market.json field '{key}' must contain non-empty strings")
        if item != item.strip():
            raise SkillMarketError(f"market.json field '{key}' values must not contain surrounding whitespace")
        if len(item) > max_chars:
            raise SkillMarketError(f"market.json field '{key}' contains a value longer than {max_chars} characters")
        comparison_key = item.casefold()
        if comparison_key in seen:
            raise SkillMarketError(f"market.json field '{key}' contains a duplicate value")
        seen.add(comparison_key)
        normalized.append(item)
    return tuple(normalized)
