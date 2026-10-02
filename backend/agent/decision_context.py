"""Purpose-specific decision projections; preserve intent and candidate coverage."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from backend.router_context import RouterContext


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


@dataclass(frozen=True)
class RoutingProjection:
    context_text: str
    app_candidates: list[dict[str, Any]]
    metadata: dict[str, Any]


def project_routing_context(content: str, context: RouterContext, sections: list[str]) -> RoutingProjection:
    """Send each routing fact once, without code, nodes, grants or a full catalog.

    This projection does not shorten user messages, descriptions or candidate
    sets. The transport rejects oversized input; it never accepts a decision
    over a silently reduced target set. App metadata remains untrusted data.
    """
    state: dict[str, Any] = {"context_version": "routing-context-v2"}
    if "history" in sections:
        history = [{"role": item.get("role"), "content": item.get("content") or ""} for item in context.session_recent]
        if history and history[-1] == {"role": "user", "content": content}:
            history = history[:-1]
        if history:
            state["history"] = history
        if context.session_summary:
            state["summary"] = context.session_summary
    if "graph_counts" in sections:
        state["graph"] = {"types": context.graph_snapshot.type_counts, "nodes": context.graph_snapshot.node_count}
    candidates = []
    if "widgets" in sections:
        for app in context.app_manifests:
            candidate = {
                key: app[key] for key in ("id", "title", "description", "intents", "schema_refs") if key in app
            }
            spec = app.get("app_spec")
            if isinstance(spec, dict):
                candidate["app_spec"] = {
                    "types": spec.get("types") or [],
                    "features": [
                        {key: feature[key] for key in ("id", "status", "surfaces") if key in feature}
                        for feature in spec.get("features") or []
                        if isinstance(feature, dict)
                    ],
                }
            candidates.append(candidate)
    text = canonical_json(state)
    metadata = {
        "version": "routing-context-v2",
        "candidate_count": len(candidates),
        "history_count": len(state.get("history") or []),
        "context_chars": len(text),
        "context_hash": content_hash(state),
        "candidate_hash": content_hash(candidates),
        "candidate_coverage": "complete",
    }
    return RoutingProjection(text, candidates, metadata)
