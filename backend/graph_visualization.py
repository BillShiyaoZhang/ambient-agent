from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Protocol
from urllib.parse import quote


GRAPH_DATASET_VERSION = 1
MAX_ONTOLOGY_NODES = 500
MAX_KNOWLEDGE_GRAPH_EDGES = 1_000
MAX_DETAIL_STRING_CHARACTERS = 512
MAX_DETAIL_COLLECTION_ITEMS = 32
MAX_DETAIL_DEPTH = 4
MAX_DETAIL_VALUE_BYTES = 8_192

_DETAIL_DEPTH_MARKER = "[truncated: maximum detail depth]"
_DETAIL_BUDGET_MARKER = "[truncated: overall detail budget]"
_DETAIL_BUDGET_MARKER_KEY = "__ambient_detail_truncated__"

_BLIND_SPOTS = [
    "Data flows outside the retained Audit Log window are not observable.",
    "Local or third-party operations without Audit instrumentation remain unknown.",
    "Manifest declarations describe potential access, not runtime grants or observed use.",
]
_LIMITATIONS = [
    "This map is a request-time projection and does not extend Audit Log retention.",
    "Observed flows are aggregated only from retained provider, stage, and timestamp metadata.",
    "The map is not an authorization, compliance, or proof-of-absence decision.",
]


class GraphExplorerAdapter(Protocol):
    def list_schemas(self) -> list[dict[str, Any]]: ...

    def list_nodes(self, node_type: str | None = None) -> list[dict[str, Any]]: ...

    def list_edges(self) -> list[dict[str, Any]]: ...


@dataclass
class _DetailTruncation:
    values_truncated: int = 0
    string_values_truncated: int = 0
    string_characters_omitted: int = 0
    collections_truncated: int = 0
    collection_items_omitted: int = 0
    depth_cutoffs: int = 0
    values_exceeding_budget: int = 0
    non_finite_numbers_replaced: int = 0

    @property
    def truncated(self) -> bool:
        return self.values_truncated > 0 or any(
            (
                self.string_values_truncated,
                self.collections_truncated,
                self.depth_cutoffs,
                self.values_exceeding_budget,
                self.non_finite_numbers_replaced,
            )
        )

    def merge(self, other: _DetailTruncation) -> None:
        self.values_truncated += other.values_truncated
        self.string_values_truncated += other.string_values_truncated
        self.string_characters_omitted += other.string_characters_omitted
        self.collections_truncated += other.collections_truncated
        self.collection_items_omitted += other.collection_items_omitted
        self.depth_cutoffs += other.depth_cutoffs
        self.values_exceeding_budget += other.values_exceeding_budget
        self.non_finite_numbers_replaced += other.non_finite_numbers_replaced

    def sort_key(self) -> tuple[int, ...]:
        return (
            self.values_truncated,
            self.string_values_truncated,
            self.string_characters_omitted,
            self.collections_truncated,
            self.collection_items_omitted,
            self.depth_cutoffs,
            self.values_exceeding_budget,
            self.non_finite_numbers_replaced,
        )


def _json_size(value: Any) -> int:
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return len(serialized.encode("utf-8"))
    except (OverflowError, TypeError, UnicodeError, ValueError):
        return MAX_DETAIL_VALUE_BYTES + 1


def _bounded_string(value: str, stats: _DetailTruncation) -> str:
    if len(value) <= MAX_DETAIL_STRING_CHARACTERS:
        return value
    stats.string_values_truncated += 1
    retained_characters = MAX_DETAIL_STRING_CHARACTERS - 1
    stats.string_characters_omitted += len(value) - retained_characters
    return f"{value[:retained_characters]}…"


def _bounded_mapping_key(value: str, stats: _DetailTruncation) -> str:
    if len(value) <= MAX_DETAIL_STRING_CHARACTERS:
        return value
    digest = sha256(value.encode("utf-8", errors="replace")).hexdigest()[:12]
    suffix = f"…#{digest}"
    retained_characters = MAX_DETAIL_STRING_CHARACTERS - len(suffix)
    stats.string_values_truncated += 1
    stats.string_characters_omitted += len(value) - retained_characters
    return f"{value[:retained_characters]}{suffix}"


def _fit_string_to_budget(value: str) -> str:
    if _json_size(value) <= MAX_DETAIL_VALUE_BYTES:
        return value
    low = 0
    high = len(value)
    while low < high:
        midpoint = (low + high + 1) // 2
        candidate = f"{value[:midpoint]}…"
        if _json_size(candidate) <= MAX_DETAIL_VALUE_BYTES:
            low = midpoint
        else:
            high = midpoint - 1
    return f"{value[:low]}…" if low else ""


def _fit_value_to_budget(value: Any) -> Any:
    if _json_size(value) <= MAX_DETAIL_VALUE_BYTES:
        return value
    if isinstance(value, str):
        return _fit_string_to_budget(value)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, nested in value.items():
            candidate = {**result, key: nested}
            if _json_size(candidate) > MAX_DETAIL_VALUE_BYTES:
                break
            result[key] = nested
        marker_key = _DETAIL_BUDGET_MARKER_KEY
        while marker_key in result:
            marker_key = f"_{marker_key}"
        result[marker_key] = _DETAIL_BUDGET_MARKER
        while result and _json_size(result) > MAX_DETAIL_VALUE_BYTES:
            removable = next(
                (key for key in reversed(result) if key != marker_key),
                None,
            )
            if removable is None:
                return {_DETAIL_BUDGET_MARKER_KEY: _DETAIL_BUDGET_MARKER}
            del result[removable]
        return result
    if isinstance(value, list):
        result = []
        for nested in value:
            candidate = [*result, nested]
            if _json_size(candidate) > MAX_DETAIL_VALUE_BYTES:
                break
            result.append(nested)
        result.append(_DETAIL_BUDGET_MARKER)
        while len(result) > 1 and _json_size(result) > MAX_DETAIL_VALUE_BYTES:
            result.pop(-2)
        return result if _json_size(result) <= MAX_DETAIL_VALUE_BYTES else []
    return _DETAIL_BUDGET_MARKER


def _bounded_json_value(value: Any) -> tuple[Any, _DetailTruncation]:
    stats = _DetailTruncation()

    def visit(item: Any, depth: int) -> Any:
        if isinstance(item, Mapping):
            if depth >= MAX_DETAIL_DEPTH:
                stats.depth_cutoffs += 1
                return _DETAIL_DEPTH_MARKER
            raw_items = sorted(
                ((str(key), nested) for key, nested in item.items()),
                key=lambda entry: entry[0],
            )
            if len(raw_items) > MAX_DETAIL_COLLECTION_ITEMS:
                stats.collections_truncated += 1
                stats.collection_items_omitted += len(raw_items) - MAX_DETAIL_COLLECTION_ITEMS
                raw_items = raw_items[:MAX_DETAIL_COLLECTION_ITEMS]
            bounded: dict[str, Any] = {}
            for raw_key, nested in raw_items:
                key = _bounded_mapping_key(raw_key, stats)
                if key in bounded:
                    continue
                bounded[key] = visit(nested, depth + 1)
            return bounded
        if isinstance(item, (list, tuple)):
            if depth >= MAX_DETAIL_DEPTH:
                stats.depth_cutoffs += 1
                return _DETAIL_DEPTH_MARKER
            raw_items = list(item)
            if len(raw_items) > MAX_DETAIL_COLLECTION_ITEMS:
                stats.collections_truncated += 1
                stats.collection_items_omitted += len(raw_items) - MAX_DETAIL_COLLECTION_ITEMS
                raw_items = raw_items[:MAX_DETAIL_COLLECTION_ITEMS]
            return [visit(nested, depth + 1) for nested in raw_items]
        if isinstance(item, str):
            return _bounded_string(item, stats)
        if isinstance(item, float) and not math.isfinite(item):
            stats.non_finite_numbers_replaced += 1
            if math.isnan(item):
                label = "NaN"
            elif item > 0:
                label = "Infinity"
            else:
                label = "-Infinity"
            return f"[non-finite number: {label}]"
        if item is None or isinstance(item, (bool, int, float)):
            return item
        return _bounded_string(str(item), stats)

    bounded = visit(value, 0)
    if _json_size(bounded) > MAX_DETAIL_VALUE_BYTES:
        bounded = _fit_value_to_budget(bounded)
        stats.values_exceeding_budget += 1
    stats.values_truncated = int(stats.truncated)
    return bounded, stats


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _text(value: Any, fallback: str = "", *, max_length: int = 200) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        text = fallback
    return text[:max_length]


def _identity(value: Any, fallback: str = "") -> str:
    """Return an exact non-empty identifier without display-text truncation."""

    text = str(value) if value is not None else ""
    return text or fallback


def _id_segment(value: str) -> str:
    return quote(value, safe="-._~")


def _detail_truncation_metadata(
    truncations: Iterable[_DetailTruncation],
) -> dict[str, Any]:
    aggregate = _DetailTruncation()
    for truncation in truncations:
        aggregate.merge(truncation)
    return {
        "truncated": aggregate.values_truncated > 0,
        "values_truncated": aggregate.values_truncated,
        "string_values_truncated": aggregate.string_values_truncated,
        "string_characters_omitted": aggregate.string_characters_omitted,
        "collections_truncated": aggregate.collections_truncated,
        "collection_items_omitted": aggregate.collection_items_omitted,
        "depth_cutoffs": aggregate.depth_cutoffs,
        "values_exceeding_budget": aggregate.values_exceeding_budget,
        "non_finite_numbers_replaced": aggregate.non_finite_numbers_replaced,
        "limits": {
            "max_string_characters": MAX_DETAIL_STRING_CHARACTERS,
            "max_collection_items": MAX_DETAIL_COLLECTION_ITEMS,
            "max_depth": MAX_DETAIL_DEPTH,
            "max_value_bytes": MAX_DETAIL_VALUE_BYTES,
        },
    }


def _dataset(
    *,
    title: str,
    description: str,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    total_nodes: int,
    total_edges: int,
    truncated: bool,
    detail_truncations: Iterable[_DetailTruncation] = (),
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    counts = {
        "nodes": {"total": total_nodes, "returned": len(nodes)},
        "edges": {"total": total_edges, "returned": len(edges)},
    }
    dataset_metadata = {
        "title": title,
        "description": description,
        "counts": counts,
        # Keep flat counts available to simple Host consumers while the
        # nested shape remains the shared GraphDataset contract.
        "total_nodes": total_nodes,
        "returned_nodes": len(nodes),
        "total_edges": total_edges,
        "returned_edges": len(edges),
        "truncated": truncated,
        "detail_truncation": _detail_truncation_metadata(detail_truncations),
        **dict(metadata or {}),
    }
    return {
        "version": GRAPH_DATASET_VERSION,
        "title": title,
        "description": description,
        "nodes": nodes,
        "edges": edges,
        "metadata": dataset_metadata,
    }


def _unique_records(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[tuple[str, str, tuple[int, ...], dict[str, Any]]] = []
    for raw in records:
        record_id = _identity(raw.get("id"))
        if not record_id:
            continue
        properties, detail_truncation = _bounded_json_value(raw.get("properties") or {})
        record = {
            "id": record_id,
            "type": _identity(raw.get("type") or raw.get("ontology_entity_id"), "Unknown"),
            "properties": properties,
            "_detail_truncation": detail_truncation,
        }
        canonical_record = {key: value for key, value in record.items() if not key.startswith("_")}
        candidates.append(
            (
                record_id,
                _canonical_json(canonical_record),
                detail_truncation.sort_key(),
                record,
            )
        )
    unique: dict[str, dict[str, Any]] = {}
    for record_id, _serialized, _truncation_key, record in sorted(
        candidates,
        key=lambda candidate: candidate[:3],
    ):
        unique.setdefault(record_id, record)
    return list(unique.values())


def _unique_graph_edges(edges: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[tuple[str, str, str, str, tuple[int, ...], dict[str, Any]]] = []
    for raw in edges:
        source = _identity(raw.get("from_id"))
        target = _identity(raw.get("to_id"))
        edge_type = _identity(raw.get("type"), "related_to")
        if not source or not target:
            continue
        properties, detail_truncation = _bounded_json_value(raw.get("properties") or {})
        edge = {
            "from_id": source,
            "to_id": target,
            "type": edge_type,
            "properties": properties,
            "_detail_truncation": detail_truncation,
        }
        candidates.append(
            (
                source,
                target,
                edge_type,
                _canonical_json(edge["properties"]),
                detail_truncation.sort_key(),
                edge,
            )
        )
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for source, target, edge_type, _serialized, _truncation_key, edge in sorted(
        candidates,
        key=lambda candidate: candidate[:5],
    ):
        unique.setdefault((source, target, edge_type), edge)
    return list(unique.values())


def _record_label(record: Mapping[str, Any]) -> str:
    properties = record.get("properties")
    if isinstance(properties, Mapping):
        for field in ("title", "name", "subject", "label"):
            value = properties.get(field)
            if value is not None and str(value).strip():
                return _text(value, max_length=200)
    return _text(record.get("id"), "Unnamed record")


def _project_ontology(
    schemas: Iterable[Mapping[str, Any]],
    records: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    record_counts = Counter(_identity(record.get("type"), "Unknown") for record in records)
    candidates: list[tuple[str, str, tuple[int, ...], dict[str, Any]]] = []
    for raw in schemas:
        entity_id = _identity(raw.get("id"))
        if not entity_id:
            continue
        properties, detail_truncation = _bounded_json_value(raw.get("properties") or {})
        raw_equivalent_to = sorted({_identity(item) for item in (raw.get("equivalent_to") or []) if _identity(item)})
        equivalent_to, equivalent_truncation = _bounded_json_value(raw_equivalent_to)
        detail_truncation.merge(equivalent_truncation)
        schema = {
            "id": entity_id,
            "name": _text(raw.get("name"), entity_id),
            "description": _text(raw.get("description"), max_length=2_000),
            "properties": properties,
            "is_core": bool(raw.get("is_core")),
            "ontology_iri": _text(raw.get("ontology_iri"), max_length=1_000) or None,
            "source": _text(raw.get("source"), max_length=200) or None,
            "equivalent_to": equivalent_to,
            "subclass_of": _identity(raw.get("subclass_of")) or None,
            "abstract": bool(raw.get("abstract")),
            "data_scope": _text(raw.get("data_scope"), "user_context"),
            "_detail_truncation": detail_truncation,
        }
        canonical_schema = {key: value for key, value in schema.items() if not key.startswith("_")}
        candidates.append(
            (
                entity_id,
                _canonical_json(canonical_schema),
                detail_truncation.sort_key(),
                schema,
            )
        )

    all_schemas_by_id: dict[str, dict[str, Any]] = {}
    for entity_id, _serialized, _truncation_key, schema in sorted(
        candidates,
        key=lambda candidate: candidate[:3],
    ):
        all_schemas_by_id.setdefault(entity_id, schema)
    all_schemas = list(all_schemas_by_id.values())
    returned_schemas = all_schemas[:MAX_ONTOLOGY_NODES]
    returned_ids = {schema["id"] for schema in returned_schemas}

    nodes = []
    for schema in returned_schemas:
        badges = []
        if schema["is_core"]:
            badges.append("core")
        if schema["abstract"]:
            badges.append("abstract")
        nodes.append(
            {
                "id": f"ontology:{schema['id']}",
                "label": schema["name"],
                "kind": "ontology",
                "status": "abstract" if schema["abstract"] else "existing",
                "summary": schema["description"],
                "details": {
                    "description": schema["description"],
                    "properties": schema["properties"],
                    "ontology_iri": schema["ontology_iri"],
                    "equivalent_to": schema["equivalent_to"],
                    "is_core": schema["is_core"],
                    "abstract": schema["abstract"],
                    "source": schema["source"],
                    "data_scope": schema["data_scope"],
                    "record_count": record_counts.get(schema["id"], 0),
                },
                "badges": badges,
                "action": None,
            }
        )

    all_relations = sorted(
        {
            (schema["id"], schema["subclass_of"])
            for schema in all_schemas
            if schema["subclass_of"] and schema["subclass_of"] in all_schemas_by_id
        }
    )
    returned_relations = [
        relation for relation in all_relations if relation[0] in returned_ids and relation[1] in returned_ids
    ]
    edges = [
        {
            "id": f"ontology-subclass:{_id_segment(child)}:{_id_segment(parent)}",
            "source": f"ontology:{child}",
            "target": f"ontology:{parent}",
            "label": "subclass of",
            "kind": "inheritance",
            "status": "existing",
            "details": {"relation": "subclass_of"},
        }
        for child, parent in returned_relations
    ]
    truncated = len(returned_schemas) < len(all_schemas) or len(returned_relations) < len(all_relations)
    return _dataset(
        title="Ontology",
        description="Canonical ontology entities and subclass relationships.",
        nodes=nodes,
        edges=edges,
        total_nodes=len(all_schemas),
        total_edges=len(all_relations),
        truncated=truncated,
        detail_truncations=(schema["_detail_truncation"] for schema in returned_schemas),
        metadata={"limit": MAX_ONTOLOGY_NODES},
    )


def _project_knowledge_graph(
    records: list[dict[str, Any]],
    graph_edges: Iterable[Mapping[str, Any]],
    *,
    record_limit: int,
) -> dict[str, Any]:
    returned_records = records[:record_limit]
    returned_ids = {record["id"] for record in returned_records}
    all_edges = _unique_graph_edges(graph_edges)
    eligible_edges = [edge for edge in all_edges if edge["from_id"] in returned_ids and edge["to_id"] in returned_ids]
    returned_edges = eligible_edges[:MAX_KNOWLEDGE_GRAPH_EDGES]

    nodes = [
        {
            "id": record["id"],
            "label": _record_label(record),
            "kind": "record",
            "status": "existing",
            "summary": f"{record['type']} context record",
            "details": {
                "entity_type": record["type"],
                "properties": record["properties"],
            },
            "badges": [record["type"]],
            "action": None,
        }
        for record in returned_records
    ]
    edges = [
        {
            "id": (f"kg-edge:{_id_segment(edge['from_id'])}:{_id_segment(edge['type'])}:{_id_segment(edge['to_id'])}"),
            "source": edge["from_id"],
            "target": edge["to_id"],
            "label": edge["type"],
            "kind": edge["type"],
            "status": "existing",
            "details": {"properties": edge["properties"]},
        }
        for edge in returned_edges
    ]
    truncated = len(returned_records) < len(records) or len(returned_edges) < len(all_edges)
    return _dataset(
        title="Knowledge graph",
        description="A bounded snapshot of user ContextRecords and their directed relationships.",
        nodes=nodes,
        edges=edges,
        total_nodes=len(records),
        total_edges=len(all_edges),
        truncated=truncated,
        detail_truncations=(item["_detail_truncation"] for item in [*returned_records, *returned_edges]),
        metadata={
            "record_limit": record_limit,
            "edge_limit": MAX_KNOWLEDGE_GRAPH_EDGES,
        },
    )


def build_graph_explorer_snapshot(
    graph: GraphExplorerAdapter,
    *,
    record_limit: int,
) -> dict[str, Any]:
    """Read the public Graph adapter once per collection and project it for the trusted Host."""

    if not 1 <= record_limit <= 500:
        raise ValueError("record_limit must be between 1 and 500")
    schemas = graph.list_schemas()
    records = _unique_records(graph.list_nodes())
    edges = graph.list_edges()
    return {
        "version": GRAPH_DATASET_VERSION,
        "ontology": _project_ontology(schemas, records),
        "knowledge_graph": _project_knowledge_graph(records, edges, record_limit=record_limit),
    }


def _field(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _add_node(nodes: dict[str, dict[str, Any]], node: dict[str, Any]) -> None:
    existing = nodes.get(node["id"])
    if existing is None or _canonical_json(node) < _canonical_json(existing):
        nodes[node["id"]] = node


def project_data_map(
    audit_logs: Iterable[Any],
    app_manifests: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build a privacy-safe map exclusively from whitelisted Audit and Manifest metadata."""

    logs = list(audit_logs)
    apps = list(app_manifests)
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    local_source_id = "data-source:local-runtime-context"
    _add_node(
        nodes,
        {
            "id": local_source_id,
            "label": "Local runtime context",
            "kind": "data_source",
            "status": "existing",
            "summary": "Local context category; raw values are intentionally omitted.",
            "details": {"source_category": "local_runtime_context"},
            "badges": ["local"],
            "action": None,
        },
    )

    observed: dict[tuple[str, str], list[datetime]] = defaultdict(list)
    observed_counts: Counter[tuple[str, str]] = Counter()
    valid_timestamps: list[datetime] = []
    for log in logs:
        provider = _text(_field(log, "provider"), "unknown-provider", max_length=120)
        stage = _text(_field(log, "stage"), "unknown-stage", max_length=120)
        observed_counts[(stage, provider)] += 1
        timestamp = _timestamp(_field(log, "timestamp"))
        if timestamp is not None:
            observed[(stage, provider)].append(timestamp)
            valid_timestamps.append(timestamp)
        else:
            observed[(stage, provider)]

    for (stage, provider), timestamps in sorted(observed.items()):
        target_id = f"provider:{_id_segment(provider)}"
        _add_node(
            nodes,
            {
                "id": target_id,
                "label": provider,
                "kind": "provider",
                "status": "observed",
                "summary": "Provider observed in retained Audit metadata.",
                "details": {
                    "semantics": "observed",
                    "target_category": f"provider:{provider}",
                },
                "badges": ["observed"],
                "action": None,
            },
        )
        first_seen = min(timestamps).isoformat() if timestamps else None
        last_seen = max(timestamps).isoformat() if timestamps else None
        edges.append(
            {
                "id": f"observed:{_id_segment(stage)}:{_id_segment(provider)}",
                "source": local_source_id,
                "target": target_id,
                "label": f"{stage} → {provider}",
                "kind": "data_flow",
                "status": "observed",
                "details": {
                    "semantics": "observed",
                    "source_category": "local_runtime_context",
                    "stage": stage,
                    "target_category": f"provider:{provider}",
                    "count": observed_counts[(stage, provider)],
                    "first_seen": first_seen,
                    "last_seen": last_seen,
                },
            }
        )

    valid_apps: dict[str, Mapping[str, Any]] = {}
    for app in sorted(apps, key=lambda item: _text(item.get("id"))):
        app_id = _text(app.get("id"), max_length=120)
        if app_id:
            valid_apps.setdefault(app_id, app)

    for app_id, app in valid_apps.items():
        title = _text(app.get("title"), app_id, max_length=200)
        schema_refs = sorted(
            {_text(item, max_length=200) for item in (app.get("schema_refs") or []) if _text(item, max_length=200)}
        )
        capability_ids = sorted(
            {
                _text(capability.get("id"), max_length=200)
                for capability in (app.get("capabilities") or [])
                if isinstance(capability, Mapping) and _text(capability.get("id"), max_length=200)
            }
        )
        app_node_id = f"app:{_id_segment(app_id)}"
        _add_node(
            nodes,
            {
                "id": app_node_id,
                "label": title,
                "kind": "app",
                "status": "declared",
                "summary": "Current App Manifest declaration.",
                "details": {
                    "semantics": "declared",
                    "app_id": app_id,
                    "data_categories": schema_refs,
                    "capability_categories": capability_ids,
                },
                "badges": ["declared"],
                "action": None,
            },
        )
        for schema_ref in schema_refs:
            source_id = f"schema:{_id_segment(schema_ref)}"
            _add_node(
                nodes,
                {
                    "id": source_id,
                    "label": schema_ref,
                    "kind": "data_category",
                    "status": "declared",
                    "summary": "Schema category referenced by a current App Manifest.",
                    "details": {
                        "semantics": "declared",
                        "source_category": f"schema:{schema_ref}",
                    },
                    "badges": ["declared"],
                    "action": None,
                },
            )
            edges.append(
                {
                    "id": f"declared-schema:{_id_segment(schema_ref)}:{_id_segment(app_id)}",
                    "source": source_id,
                    "target": app_node_id,
                    "label": "declared input",
                    "kind": "data_flow",
                    "status": "declared",
                    "details": {
                        "semantics": "declared",
                        "source_category": f"schema:{schema_ref}",
                        "target_category": f"app:{app_id}",
                        "app_id": app_id,
                    },
                }
            )
        for capability_id in capability_ids:
            target_id = f"capability:{_id_segment(capability_id)}"
            _add_node(
                nodes,
                {
                    "id": target_id,
                    "label": capability_id,
                    "kind": "capability",
                    "status": "declared",
                    "summary": "Capability category declared by a current App Manifest.",
                    "details": {
                        "semantics": "declared",
                        "target_category": f"capability:{capability_id}",
                    },
                    "badges": ["declared"],
                    "action": None,
                },
            )
            edges.append(
                {
                    "id": f"declared-capability:{_id_segment(app_id)}:{_id_segment(capability_id)}",
                    "source": app_node_id,
                    "target": target_id,
                    "label": "declared capability",
                    "kind": "data_flow",
                    "status": "declared",
                    "details": {
                        "semantics": "declared",
                        "source_category": f"app:{app_id}",
                        "target_category": f"capability:{capability_id}",
                        "app_id": app_id,
                    },
                }
            )

    unknown_targets = (
        (
            "outside-retention",
            "Outside retained window",
            "Audit evidence outside the retained window is unavailable.",
        ),
        (
            "uninstrumented",
            "Uninstrumented flow",
            "A possible flow without retained Audit instrumentation.",
        ),
    )
    for target_key, label, reason in unknown_targets:
        target_id = f"unknown:{target_key}"
        _add_node(
            nodes,
            {
                "id": target_id,
                "label": label,
                "kind": "unknown",
                "status": "unknown",
                "summary": reason,
                "details": {
                    "semantics": "unknown",
                    "target_category": f"blind_spot:{target_key}",
                    "reason": reason,
                },
                "badges": ["unknown"],
                "action": None,
            },
        )
        edges.append(
            {
                "id": f"unknown:{target_key}",
                "source": local_source_id,
                "target": target_id,
                "label": "coverage unknown",
                "kind": "data_flow",
                "status": "unknown",
                "details": {
                    "semantics": "unknown",
                    "source_category": "local_runtime_context",
                    "target_category": f"blind_spot:{target_key}",
                    "reason": reason,
                },
            }
        )

    returned_nodes = sorted(nodes.values(), key=lambda node: node["id"])
    returned_edges = sorted(edges, key=lambda edge: edge["id"])
    observed_stages = sorted({stage for stage, _provider in observed})
    return _dataset(
        title="Privacy data map",
        description="Observed, declared, and unknown data-flow metadata without raw payloads.",
        nodes=returned_nodes,
        edges=returned_edges,
        total_nodes=len(returned_nodes),
        total_edges=len(returned_edges),
        truncated=False,
        metadata={
            "time_window": {
                "start": min(valid_timestamps).isoformat() if valid_timestamps else None,
                "end": max(valid_timestamps).isoformat() if valid_timestamps else None,
                "basis": "retained_audit_metadata",
            },
            "coverage": {
                "evidence_count": len(logs),
                "declared_app_count": len(valid_apps),
                "observed_flow_count": len(observed),
                "declared_flow_count": sum(edge["status"] == "declared" for edge in returned_edges),
                "observed_stages": observed_stages,
                "instrumentation": [
                    "retained_llm_provider_call_metadata",
                    "current_app_manifest_declarations",
                ],
            },
            "blind_spots": list(_BLIND_SPOTS),
            "limitations": list(_LIMITATIONS),
            "semantics": {
                "observed": "Aggregated evidence in the retained Audit metadata window.",
                "declared": "Potential flow inferred from a current App Manifest only.",
                "unknown": "Possible flow outside retention or instrumentation coverage.",
            },
        },
    )
