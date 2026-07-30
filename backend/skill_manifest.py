from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from yaml.events import AliasEvent


MAX_SKILL_FILE_BYTES = 256 * 1024
MAX_FRONTMATTER_BYTES = 32 * 1024
MAX_BODY_CHARS = 96 * 1024
MAX_METADATA_ITEMS = 64
MAX_METADATA_KEY_CHARS = 128
MAX_METADATA_VALUE_CHARS = 2_048
MAX_ALLOWED_TOOLS_CHARS = 2_048

_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_FRONTMATTER_FIELDS = {
    "name",
    "description",
    "license",
    "compatibility",
    "metadata",
    "allowed-tools",
}


class SkillManifestError(ValueError):
    """Raised when a SKILL.md file violates the supported manifest contract."""


class _StrictSafeLoader(yaml.SafeLoader):
    """Safe YAML loader that also rejects aliases and duplicate keys."""

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(AliasEvent):
            raise SkillManifestError("YAML aliases are not supported in SKILL.md frontmatter")
        return super().compose_node(parent, index)

    def construct_mapping(self, node: yaml.nodes.MappingNode, deep: bool = False) -> dict[Any, Any]:
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise SkillManifestError("SKILL.md frontmatter keys must be scalar values") from exc
            if duplicate:
                raise SkillManifestError(f"Duplicate SKILL.md frontmatter field: {key}")
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


@dataclass(frozen=True, slots=True)
class SkillManifest:
    name: str
    description: str
    instructions: str
    license: str | None = None
    compatibility: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    allowed_tools: str | None = None

    @property
    def body(self) -> str:
        """Alias used by callers that refer to the Markdown portion as the body."""
        return self.instructions

    def as_dict(self, *, include_instructions: bool = True) -> dict[str, Any]:
        value: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "license": self.license,
            "compatibility": self.compatibility,
            "metadata": dict(self.metadata),
            # This is declarative metadata only. Nothing in this module authorizes tools.
            "allowed_tools": self.allowed_tools,
        }
        if include_instructions:
            value["instructions"] = self.instructions
        return value

    @classmethod
    def from_text(cls, text: str, *, expected_name: str | None = None) -> SkillManifest:
        if not isinstance(text, str):
            raise SkillManifestError("SKILL.md content must be text")
        try:
            encoded_size = len(text.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise SkillManifestError("SKILL.md must be valid UTF-8") from exc
        if encoded_size > MAX_SKILL_FILE_BYTES:
            raise SkillManifestError(f"SKILL.md exceeds the {MAX_SKILL_FILE_BYTES}-byte limit")

        lines = text.splitlines(keepends=True)
        if not lines or lines[0].rstrip("\r\n") != "---":
            raise SkillManifestError("SKILL.md must begin with YAML frontmatter delimited by ---")

        closing_index: int | None = None
        frontmatter_size = 0
        for index, line in enumerate(lines[1:], start=1):
            if line.rstrip("\r\n") == "---":
                closing_index = index
                break
            frontmatter_size += len(line.encode("utf-8"))
            if frontmatter_size > MAX_FRONTMATTER_BYTES:
                raise SkillManifestError(f"SKILL.md frontmatter exceeds the {MAX_FRONTMATTER_BYTES}-byte limit")
        if closing_index is None:
            raise SkillManifestError("SKILL.md frontmatter is missing its closing --- delimiter")

        frontmatter_text = "".join(lines[1:closing_index])
        try:
            raw = yaml.load(frontmatter_text, Loader=_StrictSafeLoader)
        except SkillManifestError:
            raise
        except yaml.YAMLError as exc:
            raise SkillManifestError(f"Invalid SKILL.md YAML frontmatter: {exc}") from exc
        if not isinstance(raw, dict):
            raise SkillManifestError("SKILL.md frontmatter must be a YAML mapping")
        if any(not isinstance(key, str) for key in raw):
            raise SkillManifestError("SKILL.md frontmatter field names must be strings")
        unknown = sorted(set(raw) - _FRONTMATTER_FIELDS)
        if unknown:
            raise SkillManifestError(f"Unsupported SKILL.md frontmatter field: {unknown[0]}")

        name = _required_string(raw, "name", max_chars=64)
        if not _NAME_PATTERN.fullmatch(name):
            raise SkillManifestError(
                "SKILL.md name must contain only lowercase ASCII letters, digits, and single hyphens"
            )
        if expected_name is not None and name != expected_name:
            raise SkillManifestError(f"SKILL.md name '{name}' does not match package name '{expected_name}'")

        description = _required_string(raw, "description", max_chars=1_024)
        license_value = _optional_string(raw, "license", max_chars=256)
        compatibility = _optional_string(raw, "compatibility", max_chars=500)
        allowed_tools = _optional_string(raw, "allowed-tools", max_chars=MAX_ALLOWED_TOOLS_CHARS)
        if allowed_tools is not None:
            allowed_tools = " ".join(allowed_tools.split())

        metadata_raw = raw.get("metadata", {})
        if not isinstance(metadata_raw, dict):
            raise SkillManifestError("SKILL.md metadata must be a mapping of strings to strings")
        if len(metadata_raw) > MAX_METADATA_ITEMS:
            raise SkillManifestError(f"SKILL.md metadata may contain at most {MAX_METADATA_ITEMS} entries")
        metadata: dict[str, str] = {}
        for key, value in metadata_raw.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise SkillManifestError("SKILL.md metadata keys and values must be strings")
            normalized_key = key.strip()
            normalized_value = value.strip()
            if not normalized_key or not normalized_value:
                raise SkillManifestError("SKILL.md metadata keys and values must be non-empty")
            if len(normalized_key) > MAX_METADATA_KEY_CHARS:
                raise SkillManifestError("SKILL.md metadata key is too long")
            if len(normalized_value) > MAX_METADATA_VALUE_CHARS:
                raise SkillManifestError(f"SKILL.md metadata value for '{normalized_key}' is too long")
            metadata[normalized_key] = normalized_value

        instructions = "".join(lines[closing_index + 1 :]).strip()
        if len(instructions) > MAX_BODY_CHARS:
            raise SkillManifestError(f"SKILL.md body exceeds the {MAX_BODY_CHARS}-character limit")

        return cls(
            name=name,
            description=description,
            instructions=instructions,
            license=license_value,
            compatibility=compatibility,
            metadata=metadata,
            allowed_tools=allowed_tools,
        )

    @classmethod
    def from_bytes(cls, content: bytes, *, expected_name: str | None = None) -> SkillManifest:
        if len(content) > MAX_SKILL_FILE_BYTES:
            raise SkillManifestError(f"SKILL.md exceeds the {MAX_SKILL_FILE_BYTES}-byte limit")
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SkillManifestError("SKILL.md must be valid UTF-8") from exc
        return cls.from_text(text, expected_name=expected_name)

    @classmethod
    def read(cls, path: str | Path, *, expected_name: str | None = None) -> SkillManifest:
        manifest_path = Path(path)
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise SkillManifestError(f"SKILL.md must be a regular file: {manifest_path}")
        try:
            size = manifest_path.stat().st_size
        except OSError as exc:
            raise SkillManifestError(f"Unable to inspect SKILL.md: {manifest_path}") from exc
        if size > MAX_SKILL_FILE_BYTES:
            raise SkillManifestError(f"SKILL.md exceeds the {MAX_SKILL_FILE_BYTES}-byte limit")
        try:
            content = manifest_path.read_bytes()
        except OSError as exc:
            raise SkillManifestError(f"Unable to read SKILL.md: {manifest_path}") from exc
        return cls.from_bytes(content, expected_name=expected_name)


def _required_string(raw: dict[str, Any], key: str, *, max_chars: int) -> str:
    if key not in raw:
        raise SkillManifestError(f"SKILL.md frontmatter is missing required field '{key}'")
    value = raw[key]
    if not isinstance(value, str):
        raise SkillManifestError(f"SKILL.md field '{key}' must be a string")
    normalized = value.strip()
    if not normalized:
        raise SkillManifestError(f"SKILL.md field '{key}' must not be empty")
    if len(normalized) > max_chars:
        raise SkillManifestError(f"SKILL.md field '{key}' exceeds {max_chars} characters")
    return normalized


def _optional_string(raw: dict[str, Any], key: str, *, max_chars: int) -> str | None:
    if key not in raw:
        return None
    value = raw[key]
    if not isinstance(value, str):
        raise SkillManifestError(f"SKILL.md field '{key}' must be a string")
    normalized = value.strip()
    if not normalized:
        raise SkillManifestError(f"SKILL.md field '{key}' must not be empty")
    if len(normalized) > max_chars:
        raise SkillManifestError(f"SKILL.md field '{key}' exceeds {max_chars} characters")
    return normalized


def parse_skill_manifest(
    content: str | bytes,
    *,
    expected_name: str | None = None,
) -> SkillManifest:
    """Public convenience parser for callers that do not need the classmethod form."""
    if isinstance(content, bytes):
        return SkillManifest.from_bytes(content, expected_name=expected_name)
    return SkillManifest.from_text(content, expected_name=expected_name)


def load_skill_manifest(path: str | Path, *, expected_name: str | None = None) -> SkillManifest:
    return SkillManifest.read(path, expected_name=expected_name)
