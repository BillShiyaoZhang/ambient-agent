"""Approval-bound functional criteria, independent of schema and render checks."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from backend.app_manifest import AppManifest
from backend.app_types import get_app_type_prompt_reference, validate_app_spec
from backend.capabilities.catalog import SystemCapabilityCatalog
from backend.capabilities.models import CapabilityGrant, normalize_grants


class FeatureCoverageError(ValueError):
    code = "required_feature_missing"
    stage = "functional_verify"
    expected = "implemented"

    def __init__(self, feature_id: str, observed: str):
        super().__init__(
            f"Required feature {feature_id} is {observed}; implement the approved behavior before publishing"
        )
        self.observed = observed
        self.locations = ("manifest.json:$.app_spec.features",)


def validate_feature_requirements(
    value: Any,
    catalog: SystemCapabilityCatalog,
    grants: Iterable[CapabilityGrant | dict[str, Any]],
) -> list[dict[str, Any]]:
    """Reject malformed or unapproved dependencies; never create a grant."""
    if value is None:
        return []  # Legacy checkpoints have no criteria.
    if not isinstance(value, list) or len(value) > 50:
        raise ValueError("required_features must be an array of at most 50 criteria")
    normalized_grants = normalize_grants(list(grants))
    # Schema dependencies are checked by the proposal/contract validator.
    graph_ids = {
        entity
        for grant in normalized_grants
        if grant.id in {"graph.query", "graph.mutate"}
        for entity in grant.scope["entities"]
    }
    catalog.validate_grants(normalized_grants, graph_entity_ids=graph_ids)
    approved = {grant.id: grant.to_dict()["scope"] for grant in normalized_grants}
    reference = get_app_type_prompt_reference()
    feature_types = {
        feature_id: type_id
        for type_id, feature_ids in reference["feature_ids_by_type"].items()
        for feature_id in feature_ids
    }
    result: list[dict[str, Any]] = []
    for row in value:
        if not isinstance(row, dict) or set(row) != {"id", "description", "capability_ids", "network_sources"}:
            raise ValueError("Each required feature needs exactly id, description, capability_ids, network_sources")
        feature_id = row["id"]
        if not isinstance(feature_id, str) or not feature_id or len(feature_id) > 200:
            raise ValueError("required feature id must be a non-empty string of at most 200 characters")
        type_id = feature_types.get(feature_id, feature_id.split(".", 1)[0])
        validate_app_spec(
            {
                "spec_version": 1,
                "types": [type_id],
                "features": [{"id": feature_id, "status": "implemented", "surfaces": ["ui"]}],
            }
        )
        if any(item["id"] == feature_id for item in result):
            raise ValueError(f"Duplicate required feature: {feature_id}")
        description = row["description"]
        if not isinstance(description, str) or not description.strip() or len(description) > 1000:
            raise ValueError("Required feature description must contain 1..1000 characters")
        capability_ids = row["capability_ids"]
        if (
            not isinstance(capability_ids, list)
            or len(capability_ids) > 20
            or any(not isinstance(item, str) or item not in approved for item in capability_ids)
        ):
            raise ValueError(
                f"Required feature {feature_id} has unavailable or unapproved capability_ids: {capability_ids}"
            )
        if len(set(capability_ids)) != len(capability_ids):
            raise ValueError("Required feature capability_ids must be unique")
        network_sources = row["network_sources"]
        if not isinstance(network_sources, list) or len(network_sources) > 20:
            raise ValueError("Required feature network_sources must be an array of at most 20 sources")
        sources: list[dict[str, str]] = []
        for source in network_sources:
            if not isinstance(source, dict) or set(source) != {"source_id", "path"}:
                raise ValueError("Required network source needs exactly source_id and path")
            source_id, path = source["source_id"], source["path"]
            if not isinstance(source_id, str) or not isinstance(path, str) or "network.request" not in capability_ids:
                raise ValueError("Required network source needs an approved network.request dependency")
            spec = approved["network.request"].get("sources", {}).get(source_id)
            if not isinstance(spec, dict) or path not in spec.get("paths", []):
                raise ValueError(f"Required network source/path is not approved: {source_id} {path}")
            if source in sources:
                raise ValueError("Required network sources must be unique")
            sources.append({"source_id": source_id, "path": path})
        result.append(
            {
                "id": feature_id,
                "description": description.strip(),
                "capability_ids": list(capability_ids),
                "network_sources": sources,
            }
        )
    return result


def assert_required_feature_implementation(manifest: AppManifest, requirements: list[dict[str, Any]]) -> None:
    declarations = {feature.id: feature for feature in manifest.app_spec.features} if manifest.app_spec else {}
    for required in requirements:
        feature = declarations.get(required["id"])
        if feature is None or feature.status != "implemented":
            raise FeatureCoverageError(required["id"], feature.status if feature else "missing")
