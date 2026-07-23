from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from backend.capabilities.ontology import CAPABILITY_ONTOLOGY, CAPABILITY_ONTOLOGY_VERSION


_IDENTIFIER = re.compile(r"^[a-z0-9]+(?:[._:-][a-z0-9]+)*$")
_SOURCE_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_GRAPH_OPERATIONS = {"create", "delete", "update"}
_MAX_SCOPE_ITEMS = 100


def _string_list(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list) or not value or len(value) > _MAX_SCOPE_ITEMS:
        raise ValueError(f"{field} must be a non-empty array with at most {_MAX_SCOPE_ITEMS} items")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{field} entries must be non-empty strings")
    return sorted({item.strip() for item in value})


def _path_patterns(value: Any) -> list[str]:
    paths = _string_list(value, field="paths")
    for path in paths:
        pure = PurePosixPath(path)
        if (
            path.startswith("/")
            or "\\" in path
            or "\x00" in path
            or any(part in {"", ".", ".."} for part in pure.parts)
        ):
            raise ValueError("paths must contain normalized relative app://data patterns")
    return paths


def _network_sources(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict) or not value or len(value) > 32:
        raise ValueError("network.request sources must be a non-empty object with at most 32 entries")
    result: dict[str, dict[str, Any]] = {}
    for source_id, raw in sorted(value.items()):
        if not isinstance(source_id, str) or not _SOURCE_ID.fullmatch(source_id):
            raise ValueError("network source ids must be lowercase kebab-case")
        if not isinstance(raw, dict):
            raise ValueError(f"network source '{source_id}' must be an object")
        allowed_fields = {"base_url", "paths", "methods", "response_limit"}
        unknown = set(raw) - allowed_fields
        if unknown:
            raise ValueError(f"network source '{source_id}' has unknown fields: {', '.join(sorted(unknown))}")
        base_url = raw.get("base_url")
        parsed = urlsplit(base_url) if isinstance(base_url, str) else None
        hostname = parsed.hostname if parsed is not None else None
        if (
            parsed is None
            or parsed.scheme != "https"
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(f"network source '{source_id}' base_url must be a credential-free HTTPS origin")
        lowered_host = hostname.rstrip(".").lower()
        try:
            ipaddress.ip_address(lowered_host)
        except ValueError:
            pass
        else:
            raise ValueError(f"network source '{source_id}' HTTPS origin cannot use an IP literal")
        if lowered_host == "localhost" or lowered_host.endswith((".localhost", ".local")):
            raise ValueError(f"network source '{source_id}' HTTPS origin must use a public hostname")

        paths = _string_list(raw.get("paths"), field=f"network source '{source_id}' paths")
        for path in paths:
            parts = urlsplit(path)
            if (
                not path.startswith("/")
                or path.startswith("//")
                or parts.scheme
                or parts.netloc
                or parts.query
                or parts.fragment
                or any(part == ".." for part in path.split("/"))
            ):
                raise ValueError(f"network source '{source_id}' paths must be absolute URL paths")
        methods = _string_list(raw.get("methods", ["GET"]), field=f"network source '{source_id}' methods")
        if any(method not in {"GET", "POST"} for method in methods):
            raise ValueError(f"network source '{source_id}' methods supports only GET and POST")
        response_limit = raw.get("response_limit", 1_048_576)
        if type(response_limit) is not int or not 1024 <= response_limit <= 2_097_152:
            raise ValueError(f"network source '{source_id}' response_limit must be between 1024 and 2097152")
        result[source_id] = {
            "base_url": base_url.rstrip("/"),
            "paths": paths,
            "methods": methods,
            "response_limit": response_limit,
        }
    return result


def _normalize_scope(category_id: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"Capability '{category_id}' scope must be an object")
    category = CAPABILITY_ONTOLOGY[category_id]
    unknown = set(value) - set(category.scope_fields)
    if unknown:
        raise ValueError(f"Capability '{category_id}' has unknown scope fields: {', '.join(sorted(unknown))}")

    if category_id == "graph.query":
        return {"entities": _string_list(value.get("entities"), field="graph.query entities")}
    if category_id == "graph.mutate":
        entities = _string_list(value.get("entities"), field="graph.mutate entities")
        operations = _string_list(value.get("operations"), field="graph.mutate operations")
        if any(operation not in _GRAPH_OPERATIONS for operation in operations):
            raise ValueError("graph.mutate operations supports only create, update, and delete")
        result: dict[str, Any] = {"entities": entities, "operations": operations}
        if "edge_types" in value:
            raw_edge_types = value["edge_types"]
            if not isinstance(raw_edge_types, list):
                raise ValueError("graph.mutate edge_types must be an array with at most 100 items")
            if raw_edge_types:
                result["edge_types"] = _string_list(raw_edge_types, field="graph.mutate edge_types")
        return result
    if category_id == "network.request":
        return {"sources": _network_sources(value.get("sources"))}
    if category_id in {"file.read", "file.delete"}:
        return {"paths": _path_patterns(value.get("paths"))}
    if category_id == "file.write":
        max_bytes = value.get("max_bytes")
        if type(max_bytes) is not int or not 1 <= max_bytes <= 2_097_152:
            raise ValueError("file.write max_bytes must be an integer between 1 and 2097152")
        return {"paths": _path_patterns(value.get("paths")), "max_bytes": max_bytes}
    if category_id == "capability.invoke":
        catalogs = _string_list(value.get("catalog_ids"), field="capability.invoke catalog_ids")
        actions = _string_list(value.get("actions"), field="capability.invoke actions")
        if any(not _IDENTIFIER.fullmatch(item) for item in [*catalogs, *actions]):
            raise ValueError("capability.invoke catalog_ids and actions contain an invalid identifier")
        return {"catalog_ids": catalogs, "actions": actions}
    raise ValueError(f"Unknown capability category '{category_id}'")


@dataclass(frozen=True, slots=True)
class CapabilityGrant:
    id: str
    scope: dict[str, Any]

    @classmethod
    def from_dict(cls, value: Any) -> CapabilityGrant:
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict) or set(value) != {"id", "scope"}:
            raise ValueError("Capability grant must contain exactly id and scope")
        category_id = value.get("id")
        if category_id not in CAPABILITY_ONTOLOGY:
            raise ValueError(f"Unknown capability category '{category_id}'")
        return cls(id=category_id, scope=_normalize_scope(category_id, value.get("scope")))

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "scope": json.loads(json.dumps(self.scope, sort_keys=True))}


def normalize_grants(values: Iterable[CapabilityGrant | dict[str, Any]] | None) -> tuple[CapabilityGrant, ...]:
    if values is None:
        raise ValueError("capabilities must be an array")
    if not isinstance(values, (list, tuple)):
        raise ValueError("capabilities must be an array")
    grants = [CapabilityGrant.from_dict(value) for value in values]
    ids = [grant.id for grant in grants]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate capability grant categories are not allowed")
    return tuple(sorted(grants, key=lambda grant: grant.id))


def grants_digest(values: Iterable[CapabilityGrant | dict[str, Any]]) -> str:
    normalized = normalize_grants(list(values))
    encoded = json.dumps(
        [grant.to_dict() for grant in normalized],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


@dataclass(frozen=True, slots=True)
class RuntimeContract:
    app_id: str
    schemas: tuple[dict[str, Any], ...]
    capabilities: tuple[CapabilityGrant, ...]
    grants_digest: str
    contract_version: int = 1
    catalog_version: int = CAPABILITY_ONTOLOGY_VERSION
    allowed_files: tuple[str, ...] = ("README.md", "controller.js", "manifest.json")

    @classmethod
    def create(
        cls,
        *,
        app_id: str,
        schemas: Iterable[dict[str, Any]],
        capabilities: Iterable[CapabilityGrant | dict[str, Any]],
    ) -> RuntimeContract:
        if not isinstance(app_id, str) or not app_id.strip():
            raise ValueError("Runtime Contract app_id must be non-empty")
        normalized = normalize_grants(list(capabilities))
        schema_values = tuple(json.loads(json.dumps(schema, sort_keys=True)) for schema in schemas)
        return cls(
            app_id=app_id,
            schemas=schema_values,
            capabilities=normalized,
            grants_digest=grants_digest(normalized),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "catalog_version": self.catalog_version,
            "app_id": self.app_id,
            "schemas": list(self.schemas),
            "capabilities": [grant.to_dict() for grant in self.capabilities],
            "grants_digest": self.grants_digest,
            "allowed_files": list(self.allowed_files),
        }
