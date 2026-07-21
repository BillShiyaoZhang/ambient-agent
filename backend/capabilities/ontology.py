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
    effect: str
    approval_required: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "description": self.description,
            "sdk_methods": list(self.sdk_methods),
            "scope_fields": list(self.scope_fields),
            "effect": self.effect,
            "approval_required": self.approval_required,
        }


_CATEGORIES = (
    CapabilityCategory(
        id="capability.invoke",
        description="Invoke exact installed App Center catalog actions",
        sdk_methods=("capabilities.invoke",),
        scope_fields=("catalog_ids", "actions"),
        effect="execute",
    ),
    CapabilityCategory(
        id="file.delete",
        description="Delete approved regular files below app://data",
        sdk_methods=("files.delete",),
        scope_fields=("paths",),
        effect="delete",
    ),
    CapabilityCategory(
        id="file.read",
        description="Read and list approved paths below app://data",
        sdk_methods=("files.list", "files.read"),
        scope_fields=("paths",),
        effect="read",
    ),
    CapabilityCategory(
        id="file.write",
        description="Atomically write approved paths below app://data",
        sdk_methods=("files.write",),
        scope_fields=("paths", "max_bytes"),
        effect="write",
    ),
    CapabilityCategory(
        id="graph.mutate",
        description="Create, update, or delete approved ontology entities and edges",
        sdk_methods=("graph.mutate",),
        scope_fields=("entities", "operations", "edge_types"),
        effect="write",
    ),
    CapabilityCategory(
        id="graph.query",
        description="Subscribe to queries over approved ontology entities",
        sdk_methods=("graph.subscribe",),
        scope_fields=("entities",),
        effect="read",
    ),
    CapabilityCategory(
        id="network.request",
        description="Request approved public HTTPS JSON sources",
        sdk_methods=("net.request",),
        scope_fields=("sources",),
        effect="network",
    ),
)

CAPABILITY_ONTOLOGY: dict[str, CapabilityCategory] = {item.id: item for item in _CATEGORIES}


def capability_category_ids() -> tuple[str, ...]:
    return tuple(sorted(CAPABILITY_ONTOLOGY))
