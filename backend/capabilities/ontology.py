from __future__ import annotations

from dataclasses import dataclass


CAPABILITY_ONTOLOGY_ID = "ambient-widget-capabilities"
CAPABILITY_ONTOLOGY_VERSION = 1


@dataclass(frozen=True, slots=True)
class CapabilityCategory:
    id: str
    description: str
    sdk_methods: tuple[str, ...]
    scope_fields: tuple[str, ...]
    scope_contract: dict[str, object]
    effect: str
    approval_required: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "description": self.description,
            "sdk_methods": list(self.sdk_methods),
            "scope_fields": list(self.scope_fields),
            "scope_contract": self.scope_contract,
            "effect": self.effect,
            "approval_required": self.approval_required,
        }


_CATEGORIES = (
    CapabilityCategory(
        id="capability.invoke",
        description="Invoke exact installed App Center catalog actions",
        sdk_methods=("capabilities.invoke",),
        scope_fields=("catalog_ids", "actions"),
        scope_contract={
            "required": ["catalog_ids", "actions"],
            "optional": [],
            "fields": {
                "catalog_ids": {"type": "array", "items": "available installed catalog ID", "min_items": 1},
                "actions": {"type": "array", "items": "action ID from those catalogs", "min_items": 1},
            },
            "example": {"catalog_ids": ["mcp:calendar:calendar"], "actions": ["list-events"]},
        },
        effect="execute",
    ),
    CapabilityCategory(
        id="file.delete",
        description="Delete approved regular files below app://data",
        sdk_methods=("files.delete",),
        scope_fields=("paths",),
        scope_contract={
            "required": ["paths"],
            "optional": [],
            "fields": {
                "paths": {
                    "type": "array",
                    "items": "normalized relative app://data path or glob; never include app://data/ prefix",
                    "min_items": 1,
                    "max_items": 100,
                }
            },
            "example": {"paths": ["cache/**"]},
        },
        effect="delete",
    ),
    CapabilityCategory(
        id="file.read",
        description="Read and list approved paths below app://data",
        sdk_methods=("files.list", "files.read"),
        scope_fields=("paths",),
        scope_contract={
            "required": ["paths"],
            "optional": [],
            "fields": {
                "paths": {
                    "type": "array",
                    "items": "normalized relative app://data path or glob; never include app://data/ prefix",
                    "min_items": 1,
                    "max_items": 100,
                }
            },
            "example": {"paths": ["cache/**"]},
        },
        effect="read",
    ),
    CapabilityCategory(
        id="file.write",
        description="Atomically write approved paths below app://data",
        sdk_methods=("files.write",),
        scope_fields=("paths", "max_bytes"),
        scope_contract={
            "required": ["paths", "max_bytes"],
            "optional": [],
            "fields": {
                "paths": {
                    "type": "array",
                    "items": "normalized relative app://data path or glob; never include app://data/ prefix",
                    "min_items": 1,
                    "max_items": 100,
                },
                "max_bytes": {"type": "integer", "minimum": 1, "maximum": 2_097_152},
            },
            "example": {"paths": ["cache/**"], "max_bytes": 1_048_576},
        },
        effect="write",
    ),
    CapabilityCategory(
        id="graph.mutate",
        description="Create, update, or delete approved ontology entities and edges",
        sdk_methods=("graph.mutate",),
        scope_fields=("entities", "operations", "edge_types"),
        scope_contract={
            "required": ["entities", "operations"],
            "optional": ["edge_types"],
            "fields": {
                "entities": {"type": "array", "items": "approved ontology entity ID", "min_items": 1},
                "operations": {
                    "type": "array",
                    "items_enum": ["create", "update", "delete"],
                    "min_items": 1,
                },
                "edge_types": {
                    "type": "array",
                    "items": "edge type string",
                    "min_items": 0,
                    "max_items": 100,
                    "empty_means": "no edge authority",
                },
            },
            "example": {"entities": ["Task"], "operations": ["create", "update"]},
        },
        effect="write",
    ),
    CapabilityCategory(
        id="graph.query",
        description="Subscribe to queries over approved ontology entities",
        sdk_methods=("graph.subscribe",),
        scope_fields=("entities",),
        scope_contract={
            "required": ["entities"],
            "optional": [],
            "fields": {
                "entities": {"type": "array", "items": "approved ontology entity ID", "min_items": 1}
            },
            "example": {"entities": ["Task"]},
        },
        effect="read",
    ),
    CapabilityCategory(
        id="network.request",
        description="Request approved public HTTPS JSON sources",
        sdk_methods=("net.request",),
        scope_fields=("sources",),
        scope_contract={
            "required": ["sources"],
            "optional": [],
            "fields": {
                "sources": {
                    "type": "object",
                    "min_properties": 1,
                    "max_properties": 32,
                    "key": "lowercase kebab-case source ID",
                    "value": {
                        "required": ["base_url", "paths"],
                        "optional": ["methods", "response_limit"],
                        "base_url": "credential-free public HTTPS origin with no path",
                        "paths": "non-empty array of exact absolute URL paths",
                        "methods": {"type": "array", "items_enum": ["GET", "POST"], "default": ["GET"]},
                        "response_limit": {"type": "integer", "minimum": 1024, "maximum": 2_097_152},
                    },
                }
            },
            "example": {
                "sources": {
                    "weather-api": {
                        "base_url": "https://api.example.com",
                        "paths": ["/v1/forecast"],
                        "methods": ["GET"],
                        "response_limit": 1_048_576,
                    }
                }
            },
        },
        effect="network",
    ),
)

CAPABILITY_ONTOLOGY: dict[str, CapabilityCategory] = {item.id: item for item in _CATEGORIES}


def capability_category_ids() -> tuple[str, ...]:
    return tuple(sorted(CAPABILITY_ONTOLOGY))
