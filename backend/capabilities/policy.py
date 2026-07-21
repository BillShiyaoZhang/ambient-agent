from __future__ import annotations

from collections.abc import Callable
from pathlib import PurePosixPath
from typing import Any

from backend.capabilities.models import CapabilityGrant, normalize_grants


class CapabilityDenied(PermissionError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        capability: str,
        operation: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.capability = capability
        self.operation = operation
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "capability": self.capability,
            "operation": self.operation,
            "details": dict(self.details),
        }


def _path_matches(pattern: str, path: str) -> bool:
    if pattern.endswith("/**"):
        prefix = pattern[:-3].rstrip("/")
        return path == prefix or path.startswith(f"{prefix}/")
    return PurePosixPath(path).match(pattern)


class CapabilityAuthorizer:
    """Default-deny policy over the current persistent App manifest."""

    def __init__(
        self,
        *,
        manifest_loader: Callable[[str], Any],
        node_type_loader: Callable[[str], str | None] | None = None,
    ) -> None:
        self.manifest_loader = manifest_loader
        self.node_type_loader = node_type_loader or (lambda _node_id: None)

    def _manifest(
        self,
        app_id: str,
        manifest_revision: str | None = None,
        grants_digest: str | None = None,
    ) -> Any:
        manifest = self.manifest_loader(app_id)
        if manifest is None:
            raise CapabilityDenied(
                "app_manifest_unavailable",
                f"App '{app_id}' has no valid Manifest V2",
                capability="manifest",
                operation="load",
            )
        current_revision = getattr(manifest, "revision", None)
        if manifest_revision is not None and current_revision is not None and manifest_revision != current_revision:
            raise CapabilityDenied(
                "manifest_revision_stale",
                "The App capability snapshot is stale",
                capability="manifest",
                operation="load",
                details={"current_revision": current_revision},
            )
        current_digest = getattr(manifest, "grants_digest", None)
        if grants_digest is not None and current_digest is not None and grants_digest != current_digest:
            raise CapabilityDenied(
                "grants_digest_stale",
                "The App capability grant snapshot is stale",
                capability="manifest",
                operation="load",
                details={"current_grants_digest": current_digest},
            )
        return manifest

    def _grant(
        self,
        app_id: str,
        category_id: str,
        operation: str,
        revision: str | None = None,
        grants_digest: str | None = None,
    ) -> CapabilityGrant:
        manifest = self._manifest(app_id, revision, grants_digest)
        try:
            grants = normalize_grants(list(getattr(manifest, "capabilities", ())))
        except ValueError as exc:
            raise CapabilityDenied(
                "capability_contract_invalid",
                "The App capability contract is invalid",
                capability=category_id,
                operation=operation,
            ) from exc
        grant = next((item for item in grants if item.id == category_id), None)
        if grant is None:
            raise CapabilityDenied(
                "capability_not_granted",
                f"App '{app_id}' has no '{category_id}' grant",
                capability=category_id,
                operation=operation,
            )
        return grant

    @staticmethod
    def _scope_denied(category_id: str, operation: str, resource: Any) -> CapabilityDenied:
        return CapabilityDenied(
            "capability_scope_denied",
            f"The requested resource is outside the '{category_id}' grant",
            capability=category_id,
            operation=operation,
            details={"resource": resource},
        )

    def authorize_graph_query(
        self,
        app_id: str,
        query: dict[str, Any],
        manifest_revision: str | None = None,
        grants_digest: str | None = None,
    ) -> None:
        grant = self._grant(app_id, "graph.query", "query", manifest_revision, grants_digest)
        allowed = set(grant.scope["entities"])
        root_type = query.get("type") if isinstance(query, dict) else None
        if not isinstance(root_type, str) or root_type not in allowed:
            raise self._scope_denied("graph.query", "query", {"entity": root_type})
        includes = query.get("include", [])
        if not isinstance(includes, list):
            raise self._scope_denied("graph.query", "query", {"include": "invalid"})
        for include in includes:
            target_type = include.get("target_type") if isinstance(include, dict) else None
            if not isinstance(target_type, str) or target_type not in allowed:
                raise self._scope_denied("graph.query", "query", {"entity": target_type})

    def authorize_graph_mutation(
        self,
        app_id: str,
        actions: list[dict[str, Any]],
        manifest_revision: str | None = None,
        grants_digest: str | None = None,
    ) -> None:
        grant = self._grant(app_id, "graph.mutate", "mutate", manifest_revision, grants_digest)
        entities = set(grant.scope["entities"])
        operations = set(grant.scope["operations"])
        edge_types = set(grant.scope.get("edge_types", ()))
        known_types: dict[str, str] = {}
        for action in actions:
            kind = action.get("action")
            if kind == "create_node":
                operation, entity = "create", action.get("type")
                node_id = action.get("id")
                if isinstance(node_id, str) and isinstance(entity, str):
                    known_types[node_id] = entity
            elif kind in {"update_node_property", "delete_node"}:
                operation = "update" if kind == "update_node_property" else "delete"
                node_id = action.get("id")
                entity = known_types.get(node_id) if isinstance(node_id, str) else None
                if entity is None and isinstance(node_id, str):
                    entity = self.node_type_loader(node_id)
            elif kind in {"create_edge", "delete_edge"}:
                operation = "create" if kind == "create_edge" else "delete"
                edge_type = action.get("type")
                if not isinstance(edge_type, str) or edge_type not in edge_types:
                    raise self._scope_denied("graph.mutate", operation, {"edge_type": edge_type})
                endpoint_types = []
                for field in ("from_id", "to_id"):
                    node_id = action.get(field)
                    entity = known_types.get(node_id) if isinstance(node_id, str) else None
                    if entity is None and isinstance(node_id, str):
                        entity = self.node_type_loader(node_id)
                    endpoint_types.append(entity)
                if any(entity not in entities for entity in endpoint_types):
                    raise self._scope_denied("graph.mutate", operation, {"entities": endpoint_types})
                entity = None
            else:
                raise self._scope_denied("graph.mutate", "mutate", {"action": kind})
            if operation not in operations:
                raise self._scope_denied("graph.mutate", operation, {"action": kind})
            if entity is not None and entity not in entities:
                raise self._scope_denied("graph.mutate", operation, {"entity": entity})

    def authorize_network_request(
        self,
        app_id: str,
        source_id: str,
        *,
        path: str,
        method: str,
        manifest_revision: str | None = None,
        grants_digest: str | None = None,
    ) -> dict[str, Any]:
        grant = self._grant(app_id, "network.request", "request", manifest_revision, grants_digest)
        source = grant.scope["sources"].get(source_id)
        normalized_method = method.upper() if isinstance(method, str) else method
        if source is None or path not in source["paths"] or normalized_method not in source["methods"]:
            raise self._scope_denied(
                "network.request",
                "request",
                {"source_id": source_id, "path": path, "method": normalized_method},
            )
        return dict(source)

    def authorize_file(
        self,
        app_id: str,
        operation: str,
        path: str,
        *,
        size: int | None = None,
        manifest_revision: str | None = None,
        grants_digest: str | None = None,
    ) -> None:
        category = {"read": "file.read", "list": "file.read", "write": "file.write", "delete": "file.delete"}.get(
            operation
        )
        if category is None:
            raise self._scope_denied("file.read", operation, {"path": path})
        grant = self._grant(app_id, category, operation, manifest_revision, grants_digest)
        if not any(_path_matches(pattern, path) for pattern in grant.scope["paths"]):
            raise self._scope_denied(category, operation, {"path": path})
        if operation == "write" and (size is None or size > grant.scope["max_bytes"]):
            raise CapabilityDenied(
                "capability_scope_denied",
                f"The requested resource is outside the '{category}' grant",
                capability=category,
                operation=operation,
                details={"path": path, "size": size, "max_bytes": grant.scope["max_bytes"]},
            )

    def authorize_invocation(
        self,
        app_id: str,
        catalog_id: str,
        action_id: str,
        manifest_revision: str | None = None,
        grants_digest: str | None = None,
    ) -> None:
        grant = self._grant(app_id, "capability.invoke", "invoke", manifest_revision, grants_digest)
        if catalog_id not in grant.scope["catalog_ids"] or action_id not in grant.scope["actions"]:
            raise self._scope_denied(
                "capability.invoke", "invoke", {"catalog_id": catalog_id, "action_id": action_id}
            )
