from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

import httpx

from backend.skill_manifest import (
    MAX_SKILL_FILE_BYTES,
    SkillManifest,
    SkillManifestError,
)
from backend.skill_market import (
    BUNDLED_SKILL_MARKET_DIR,
    SkillMarket,
    SkillMarketEntry,
    SkillMarketError,
)
from backend.skill_store import compute_package_digest


MAX_SKILL_CATALOG_CONFIG_BYTES = 512 * 1024
MAX_CATALOG_ERROR_CHARS = 300
GITHUB_RAW_ORIGIN = "https://raw.githubusercontent.com"
GITHUB_WEB_ORIGIN = "https://github.com"

_SOURCE_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_NAMESPACE_PATTERN = _SOURCE_ID_PATTERN
_REPOSITORY_PATTERN = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})/"
    r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})$"
)
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_SHA256_PATTERN = re.compile(r"^sha256:([0-9a-f]{64})$")
_GITHUB_PATH_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]*$")
_ONTOLOGY_REF_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_ACCENT_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")
_CONFIG_FIELDS = {"version", "providers"}
_PROVIDER_FIELDS = {"id", "kind", "required", "entries"}
_GITHUB_ENTRY_FIELDS = {
    "repository",
    "commit",
    "path",
    "sha256",
    "files",
    "namespace",
    "title",
    "provider",
    "tags",
    "triggers",
    "ontology_refs",
    "icon",
    "accent",
}


class SkillCatalogProvider(Protocol):
    source_id: str
    kind: str
    required: bool

    def list_entries(self) -> list[SkillMarketEntry]: ...


@dataclass(frozen=True, slots=True)
class SkillCatalogSourceStatus:
    source_id: str
    kind: str
    required: bool
    enabled: bool
    status: str
    entry_count: int
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "id": self.source_id,
            "kind": self.kind,
            "required": self.required,
            "enabled": self.enabled,
            "status": self.status,
            "entry_count": self.entry_count,
        }
        if self.error is not None:
            value["error"] = self.error
        return value


@dataclass(frozen=True, slots=True)
class SkillCatalogSnapshot:
    entries: tuple[SkillMarketEntry, ...]
    sources: tuple[SkillCatalogSourceStatus, ...]


class SkillCatalog:
    """Merge bounded provider snapshots without granting trust or execution."""

    def __init__(self, providers: Sequence[SkillCatalogProvider]):
        if not providers:
            raise ValueError("Skill catalog requires at least one provider")
        self.providers = tuple(providers)
        seen_source_ids: set[str] = set()
        for provider in self.providers:
            source_id = getattr(provider, "source_id", None)
            kind = getattr(provider, "kind", None)
            required = getattr(provider, "required", None)
            if not isinstance(source_id, str) or _SOURCE_ID_PATTERN.fullmatch(source_id) is None:
                raise ValueError("Skill catalog provider source_id is invalid")
            if source_id in seen_source_ids:
                raise ValueError(f"Duplicate skill catalog source_id: {source_id}")
            if not isinstance(kind, str) or not kind:
                raise ValueError("Skill catalog provider kind is invalid")
            if not isinstance(required, bool):
                raise ValueError("Skill catalog provider required must be a boolean")
            seen_source_ids.add(source_id)

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(provider.source_id for provider in self.providers)

    def list_snapshot(
        self,
        *,
        source_enabled: Mapping[str, bool] | None = None,
    ) -> SkillCatalogSnapshot:
        entries: list[SkillMarketEntry] = []
        statuses: list[SkillCatalogSourceStatus] = []
        seen_market_ids: dict[str, str] = {}
        seen_catalog_ids: dict[str, str] = {}

        for provider in self.providers:
            enabled = source_enabled.get(provider.source_id, True) if source_enabled is not None else True
            if not isinstance(enabled, bool):
                raise ValueError(f"Skill catalog source preference for '{provider.source_id}' must be a boolean")
            if not enabled:
                statuses.append(
                    SkillCatalogSourceStatus(
                        source_id=provider.source_id,
                        kind=provider.kind,
                        required=provider.required,
                        enabled=False,
                        status="disabled",
                        entry_count=0,
                    )
                )
                continue
            try:
                provider_entries = sorted(
                    provider.list_entries(),
                    key=lambda entry: (entry.market_id, entry.catalog_id),
                )
                for entry in provider_entries:
                    if not isinstance(entry, SkillMarketEntry):
                        raise SkillMarketError(f"Catalog source '{provider.source_id}' returned an invalid entry")
                    if entry.catalog_source_id != provider.source_id or entry.catalog_source_kind != provider.kind:
                        raise SkillMarketError(
                            f"Catalog source '{provider.source_id}' returned an entry with mismatched source identity"
                        )
            except Exception as exc:
                message = _bounded_error(exc)
                if provider.required:
                    raise SkillMarketError(
                        f"Required catalog source '{provider.source_id}' is unavailable: {message}"
                    ) from exc
                statuses.append(
                    SkillCatalogSourceStatus(
                        source_id=provider.source_id,
                        kind=provider.kind,
                        required=False,
                        enabled=True,
                        status="unavailable",
                        entry_count=0,
                        error=message,
                    )
                )
                continue

            statuses.append(
                SkillCatalogSourceStatus(
                    source_id=provider.source_id,
                    kind=provider.kind,
                    required=provider.required,
                    enabled=True,
                    status="available",
                    entry_count=len(provider_entries),
                )
            )
            for entry in provider_entries:
                market_owner = seen_market_ids.get(entry.market_id)
                if market_owner is not None:
                    raise SkillMarketError(
                        "Duplicate market_id across catalog sources: "
                        f"{entry.market_id} ({market_owner}, {provider.source_id})"
                    )
                catalog_owner = seen_catalog_ids.get(entry.catalog_id)
                if catalog_owner is not None:
                    raise SkillMarketError(
                        "Duplicate catalog_id across catalog sources: "
                        f"{entry.catalog_id} ({catalog_owner}, {provider.source_id})"
                    )
                seen_market_ids[entry.market_id] = provider.source_id
                seen_catalog_ids[entry.catalog_id] = provider.source_id
                entries.append(entry)

        return SkillCatalogSnapshot(tuple(entries), tuple(statuses))

    def list_entries(
        self,
        *,
        source_enabled: Mapping[str, bool] | None = None,
    ) -> list[SkillMarketEntry]:
        return list(self.list_snapshot(source_enabled=source_enabled).entries)

    def get(
        self,
        market_id: str,
        *,
        source_enabled: Mapping[str, bool] | None = None,
    ) -> SkillMarketEntry:
        for entry in self.list_snapshot(source_enabled=source_enabled).entries:
            if entry.market_id == market_id:
                return entry
        raise KeyError(market_id)


class GitHubSkillCatalogProvider:
    """Load standalone SKILL.md packages pinned by commit and expected hash."""

    kind = "github"

    def __init__(
        self,
        *,
        source_id: str,
        entries: Sequence[Mapping[str, Any]],
        cache_dir: str | Path,
        required: bool = False,
        fetcher: Callable[[str], bytes] | None = None,
    ):
        if _SOURCE_ID_PATTERN.fullmatch(source_id) is None:
            raise SkillMarketError("GitHub catalog source id must be a lowercase hyphenated name")
        if not isinstance(required, bool):
            raise SkillMarketError("GitHub catalog required must be a boolean")
        if isinstance(entries, (str, bytes)) or not isinstance(entries, Sequence):
            raise SkillMarketError("GitHub catalog entries must be an array")
        if not entries:
            raise SkillMarketError("GitHub catalog must declare at least one entry")

        self.source_id = source_id
        self.required = required
        self.cache_dir = Path(cache_dir).expanduser().absolute()
        self._ensure_cache_dir()
        self._entries = tuple(_validate_github_entry(entry, index=index) for index, entry in enumerate(entries))
        self._fetcher = fetcher or _fetch_github_bytes

    def list_entries(self) -> list[SkillMarketEntry]:
        return [self._load_entry(raw) for raw in self._entries]

    def _load_entry(self, raw: dict[str, Any]) -> SkillMarketEntry:
        repository = raw["repository"]
        commit = raw["commit"]
        path = raw["path"]
        expected_hash = raw["sha256"]
        name = PurePosixPath(path).name
        raw_url = f"{GITHUB_RAW_ORIGIN}/{repository}/{commit}/{path}/SKILL.md"
        source_uri = f"{GITHUB_WEB_ORIGIN}/{repository}/tree/{commit}/{path}"
        skill_content = self._read_or_fetch(raw_url, expected_hash)
        try:
            manifest = SkillManifest.from_bytes(skill_content, expected_name=name)
        except SkillManifestError as exc:
            raise SkillMarketError(f"Invalid pinned SKILL.md for '{repository}/{path}': {exc}") from exc

        namespace = raw["namespace"]
        version = commit
        market = {
            "market_id": f"{namespace}/{name}",
            "catalog_id": f"agent-skill:{namespace}:{name}",
            "title": raw["title"],
            "provider": raw["provider"],
            "version": version,
            "tags": list(raw["tags"]),
            "ontology_refs": list(raw["ontology_refs"]),
            "triggers": list(raw["triggers"]),
            "surfaces": ["agent_context"],
            "provenance": {"source": source_uri, "verified": False},
        }
        if raw["icon"] is not None:
            market["icon"] = raw["icon"]
        if raw["accent"] is not None:
            market["accent"] = raw["accent"]
        if manifest.license is not None:
            market["license"] = manifest.license
        if manifest.compatibility is not None:
            market["compatibility"] = manifest.compatibility
        market_content = (
            json.dumps(
                market,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode()
        digest = compute_package_digest(skill_content, market_content)
        return SkillMarketEntry(
            market_id=market["market_id"],
            catalog_id=market["catalog_id"],
            title=raw["title"],
            provider=raw["provider"],
            version=version,
            tags=raw["tags"],
            icon=raw["icon"],
            accent=raw["accent"],
            ontology_refs=raw["ontology_refs"],
            triggers=raw["triggers"],
            surfaces=("agent_context",),
            provenance_source=source_uri,
            provenance_verified=False,
            provenance_trust="local",
            manifest=manifest,
            digest=digest,
            source_dir=self._cache_path(expected_hash).parent,
            skill_content=skill_content,
            market_content=market_content,
            catalog_source_id=self.source_id,
            catalog_source_kind=self.kind,
            source_uri=source_uri,
            source_revision=commit,
            upstream_hash=expected_hash,
            update_strategy="content_hash",
        )

    def _read_or_fetch(self, url: str, expected_hash: str) -> bytes:
        self._validate_cache_components()
        cache_path = self._cache_path(expected_hash)
        if cache_path.exists():
            return self._read_verified_cache(cache_path, expected_hash)
        if cache_path.is_symlink():
            raise SkillMarketError(f"GitHub Skill cache path is unsafe: {cache_path}")
        try:
            content = self._fetcher(url)
        except Exception as exc:
            raise SkillMarketError(
                f"Unable to fetch pinned GitHub Skill from source '{self.source_id}': {_bounded_error(exc)}"
            ) from exc
        _verify_upstream_content(content, expected_hash)
        self._write_cache(cache_path, content)
        return content

    def _cache_path(self, expected_hash: str) -> Path:
        digest_match = _SHA256_PATTERN.fullmatch(expected_hash)
        if digest_match is None:
            raise SkillMarketError("GitHub Skill sha256 is invalid")
        return self.cache_dir / digest_match.group(1) / "SKILL.md"

    def _ensure_cache_dir(self) -> None:
        self._validate_cache_components()
        if self.cache_dir.exists() and (self.cache_dir.is_symlink() or not self.cache_dir.is_dir()):
            raise SkillMarketError(f"GitHub Skill cache must be a real directory: {self.cache_dir}")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._validate_cache_components()
        if not self.cache_dir.is_dir():
            raise SkillMarketError(f"GitHub Skill cache must be a real directory: {self.cache_dir}")

    def _validate_cache_components(self) -> None:
        for component in (self.cache_dir, *self.cache_dir.parents):
            if component.is_symlink():
                raise SkillMarketError(f"GitHub Skill cache path contains a symbolic link: {component}")

    def _read_verified_cache(self, path: Path, expected_hash: str) -> bytes:
        parent = path.parent
        if parent.is_symlink() or not parent.is_dir() or path.is_symlink() or not path.is_file():
            raise SkillMarketError(f"GitHub Skill cache entry is unsafe: {path}")
        try:
            children = list(parent.iterdir())
            content = path.read_bytes()
        except OSError as exc:
            raise SkillMarketError(f"Unable to read GitHub Skill cache: {path}") from exc
        if {child.name for child in children} != {"SKILL.md"}:
            raise SkillMarketError(f"GitHub Skill cache entry has unexpected files: {parent}")
        _verify_upstream_content(content, expected_hash)
        return content

    def _write_cache(self, destination: Path, content: bytes) -> None:
        self._validate_cache_components()
        staging = Path(tempfile.mkdtemp(prefix=".github-skill-", dir=self.cache_dir))
        try:
            skill_path = staging / "SKILL.md"
            with skill_path.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.replace(staging, destination.parent)
            except OSError:
                if not destination.exists():
                    raise
                self._read_verified_cache(destination, _sha256(content))
        finally:
            if staging.exists():
                shutil.rmtree(staging)


def load_skill_catalog_config(
    config_path: str | Path,
    *,
    cache_dir: str | Path,
    fetcher: Callable[[str], bytes] | None = None,
) -> list[SkillCatalogProvider]:
    path = Path(config_path).expanduser().absolute()
    if path.is_symlink() or not path.is_file():
        raise SkillMarketError(f"Skill catalog config must be a regular file: {path}")
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise SkillMarketError(f"Unable to read Skill catalog config: {path}") from exc
    if len(content) > MAX_SKILL_CATALOG_CONFIG_BYTES:
        raise SkillMarketError(f"Skill catalog config exceeds {MAX_SKILL_CATALOG_CONFIG_BYTES} bytes")
    try:
        raw = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SkillMarketError("Skill catalog config must be valid UTF-8 JSON") from exc
    if not isinstance(raw, dict):
        raise SkillMarketError("Skill catalog config must contain an object")
    unknown = sorted(set(raw) - _CONFIG_FIELDS)
    if unknown:
        raise SkillMarketError(f"Unsupported Skill catalog config field: {unknown[0]}")
    if raw.get("version") != 1:
        raise SkillMarketError("Skill catalog config version must be 1")
    providers = raw.get("providers")
    if not isinstance(providers, list):
        raise SkillMarketError("Skill catalog config providers must be an array")

    result: list[SkillCatalogProvider] = []
    seen_ids: set[str] = set()
    cache_root = Path(cache_dir).expanduser().absolute()
    for index, provider in enumerate(providers):
        if not isinstance(provider, dict):
            raise SkillMarketError(f"Skill catalog provider {index} must be an object")
        unknown_provider = sorted(set(provider) - _PROVIDER_FIELDS)
        if unknown_provider:
            raise SkillMarketError(f"Unsupported Skill catalog provider field: {unknown_provider[0]}")
        source_id = _required_string(provider, "id", 80, "catalog provider")
        if source_id in seen_ids:
            raise SkillMarketError(f"Duplicate Skill catalog provider id: {source_id}")
        kind = _required_string(provider, "kind", 40, "catalog provider")
        if kind != "github":
            raise SkillMarketError(f"Unsupported Skill catalog provider kind: {kind}")
        required = provider.get("required", False)
        if not isinstance(required, bool):
            raise SkillMarketError("Skill catalog provider required must be a boolean")
        entries = provider.get("entries")
        if not isinstance(entries, list):
            raise SkillMarketError("Skill catalog provider entries must be an array")
        result.append(
            GitHubSkillCatalogProvider(
                source_id=source_id,
                entries=entries,
                cache_dir=cache_root / source_id,
                required=required,
                fetcher=fetcher,
            )
        )
        seen_ids.add(source_id)
    return result


def build_skill_catalog(
    workspace_dir: str | Path,
    *,
    local_market_dir: str | Path | None = None,
    config_path: str | Path | None = None,
) -> SkillCatalog:
    workspace = Path(workspace_dir).expanduser().absolute()
    providers: list[SkillCatalogProvider] = [
        SkillMarket(
            BUNDLED_SKILL_MARKET_DIR,
            source_id="bundled",
            kind="bundled",
            required=True,
        )
    ]
    if local_market_dir is not None and str(local_market_dir).strip():
        providers.append(
            SkillMarket(
                local_market_dir,
                source_id="local-admin",
                kind="local",
                required=False,
            )
        )
    if config_path is not None and str(config_path).strip():
        providers.extend(
            load_skill_catalog_config(
                config_path,
                cache_dir=workspace / ".ambient" / "skill-catalog-cache",
            )
        )
    return SkillCatalog(providers)


def _validate_github_entry(
    value: Mapping[str, Any],
    *,
    index: int,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SkillMarketError(f"GitHub catalog entry {index} must be an object")
    raw = dict(value)
    unknown = sorted(set(raw) - _GITHUB_ENTRY_FIELDS)
    if unknown:
        raise SkillMarketError(f"Unsupported GitHub catalog entry field: {unknown[0]}")
    repository = _required_string(raw, "repository", 201, "GitHub catalog entry")
    if _REPOSITORY_PATTERN.fullmatch(repository) is None or ".." in repository:
        raise SkillMarketError("GitHub repository must have form owner/repository")
    commit = _required_string(raw, "commit", 40, "GitHub catalog entry")
    if _COMMIT_PATTERN.fullmatch(commit) is None:
        raise SkillMarketError("GitHub Skill source must use a 40-character lowercase commit")
    path = _required_string(raw, "path", 512, "GitHub catalog entry")
    parsed_path = PurePosixPath(path)
    if (
        path.startswith("/")
        or path.endswith("/")
        or parsed_path.as_posix() != path
        or any(part in {"", ".", ".."} for part in parsed_path.parts)
        or any(_GITHUB_PATH_SEGMENT_PATTERN.fullmatch(part) is None for part in parsed_path.parts)
        or "\\" in path
    ):
        raise SkillMarketError("GitHub Skill path must be a normalized relative path")
    expected_hash = _required_string(raw, "sha256", 71, "GitHub catalog entry")
    if _SHA256_PATTERN.fullmatch(expected_hash) is None:
        raise SkillMarketError("GitHub Skill sha256 must be sha256:<64 lowercase hex>")
    files = raw.get("files")
    if files != ["SKILL.md"]:
        raise SkillMarketError("GitHub context-only Skill entries may contain only SKILL.md")
    namespace = _required_string(raw, "namespace", 64, "GitHub catalog entry")
    if _NAMESPACE_PATTERN.fullmatch(namespace) is None or namespace == "ambient-agent":
        raise SkillMarketError("GitHub Skill namespace is invalid or reserved")
    title = _required_string(raw, "title", 120, "GitHub catalog entry")
    provider = _required_string(raw, "provider", 120, "GitHub catalog entry")
    tags = _string_list(raw, "tags", max_items=16, max_chars=64)
    triggers = _string_list(
        raw,
        "triggers",
        min_items=1,
        max_items=64,
        max_chars=160,
    )
    ontology_refs = _string_list(
        raw,
        "ontology_refs",
        max_items=64,
        max_chars=128,
    )
    for ontology_ref in ontology_refs:
        if _ONTOLOGY_REF_PATTERN.fullmatch(ontology_ref) is None:
            raise SkillMarketError(f"Invalid ontology reference: {ontology_ref}")
    icon = _optional_string(raw, "icon", 32, "GitHub catalog entry")
    accent = _optional_string(raw, "accent", 7, "GitHub catalog entry")
    if accent is not None and _ACCENT_PATTERN.fullmatch(accent) is None:
        raise SkillMarketError("GitHub Skill accent must be a hexadecimal color")
    return {
        "repository": repository,
        "commit": commit,
        "path": path,
        "sha256": expected_hash,
        "namespace": namespace,
        "title": title,
        "provider": provider,
        "tags": tags,
        "triggers": triggers,
        "ontology_refs": ontology_refs,
        "icon": icon,
        "accent": accent,
    }


def _required_string(
    raw: Mapping[str, Any],
    key: str,
    max_chars: int,
    label: str,
) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise SkillMarketError(f"{label} field '{key}' must be a non-empty string")
    if value != value.strip():
        raise SkillMarketError(f"{label} field '{key}' must not have surrounding whitespace")
    if len(value) > max_chars:
        raise SkillMarketError(f"{label} field '{key}' is too long")
    return value


def _optional_string(
    raw: Mapping[str, Any],
    key: str,
    max_chars: int,
    label: str,
) -> str | None:
    if key not in raw or raw[key] is None:
        return None
    return _required_string(raw, key, max_chars, label)


def _string_list(
    raw: Mapping[str, Any],
    key: str,
    *,
    min_items: int = 0,
    max_items: int,
    max_chars: int,
) -> tuple[str, ...]:
    value = raw.get(key)
    if not isinstance(value, list) or not min_items <= len(value) <= max_items:
        raise SkillMarketError(
            f"GitHub catalog entry field '{key}' must contain between {min_items} and {max_items} strings"
        )
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item or item != item.strip() or len(item) > max_chars:
            raise SkillMarketError(f"GitHub catalog entry field '{key}' contains an invalid string")
        folded = item.casefold()
        if folded in seen:
            raise SkillMarketError(f"GitHub catalog entry field '{key}' contains a duplicate")
        seen.add(folded)
        result.append(item)
    return tuple(result)


def _fetch_github_bytes(url: str) -> bytes:
    try:
        with httpx.Client(
            timeout=httpx.Timeout(5.0),
            follow_redirects=False,
            trust_env=False,
            headers={"Accept": "text/plain"},
        ) as client:
            with client.stream("GET", url) as response:
                if response.is_redirect:
                    raise SkillMarketError("GitHub Skill fetch refused a redirect")
                if response.status_code != 200:
                    raise SkillMarketError(f"GitHub Skill fetch returned HTTP {response.status_code}")
                declared_length = response.headers.get("content-length")
                if declared_length is not None:
                    try:
                        if int(declared_length) > MAX_SKILL_FILE_BYTES:
                            raise SkillMarketError(f"Pinned GitHub SKILL.md exceeds {MAX_SKILL_FILE_BYTES} bytes")
                    except ValueError as exc:
                        raise SkillMarketError("GitHub Skill response has an invalid content length") from exc
                chunks: list[bytes] = []
                received = 0
                for chunk in response.iter_bytes():
                    received += len(chunk)
                    if received > MAX_SKILL_FILE_BYTES:
                        raise SkillMarketError(f"Pinned GitHub SKILL.md exceeds {MAX_SKILL_FILE_BYTES} bytes")
                    chunks.append(chunk)
    except httpx.HTTPError as exc:
        raise SkillMarketError(f"GitHub request failed: {exc.__class__.__name__}") from exc
    return b"".join(chunks)


def _verify_upstream_content(content: bytes, expected_hash: str) -> None:
    if not isinstance(content, bytes):
        raise SkillMarketError("GitHub Skill fetcher must return bytes")
    if len(content) > MAX_SKILL_FILE_BYTES:
        raise SkillMarketError(f"Pinned GitHub SKILL.md exceeds {MAX_SKILL_FILE_BYTES} bytes")
    actual = _sha256(content)
    if actual != expected_hash:
        raise SkillMarketError(f"Pinned GitHub SKILL.md hash mismatch: expected {expected_hash}, found {actual}")


def _sha256(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _bounded_error(exc: Exception) -> str:
    message = " ".join(str(exc).split()) or exc.__class__.__name__
    return message[:MAX_CATALOG_ERROR_CHARS]
