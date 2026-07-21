from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any

from backend.capabilities.ontology import (
    CAPABILITY_ONTOLOGY,
    CAPABILITY_ONTOLOGY_VERSION,
    capability_category_ids,
)
from backend.capabilities.models import CapabilityGrant, normalize_grants
from backend.ontology import ONTOLOGY_ID, PREBUILT_ONTOLOGY


class AgentRole(StrEnum):
    INTENT_ROUTER = "intent_router"
    CONVERSE = "converse"
    SCHEMA_ALIGNMENT = "schema_alignment"
    CODING_AGENT = "coding_agent"
    VERIFICATION = "verification"


class SystemCapabilityCatalog:
    def __init__(self, payload: dict[str, Any]):
        self._payload = payload

    @classmethod
    def build(
        cls,
        *,
        installed_capabilities: Iterable[Mapping[str, Any]] = (),
        model_tools: Iterable[Mapping[str, Any]] = (),
        coding_agents: Iterable[Mapping[str, Any]] = (),
    ) -> SystemCapabilityCatalog:
        return cls(
            {
                "catalog_version": CAPABILITY_ONTOLOGY_VERSION,
                "runtime_contract": {
                    "flow": ["plan", "alignment_approval", "staging", "verification", "promotion"],
                    "durable_runs": True,
                    "recovery": ["retry", "cancel", "needs_attention", "reconcile"],
                },
                "context_graph": {
                    "ontology_id": ONTOLOGY_ID,
                    "entities": [entity.as_schema() for entity in PREBUILT_ONTOLOGY],
                },
                "widget_runtime": {
                    "manifest_version": 2,
                    "capability_categories": [
                        CAPABILITY_ONTOLOGY[category_id].to_dict() for category_id in capability_category_ids()
                    ],
                    "forbidden_apis": [
                        "ambient.mcp",
                        "eval",
                        "fetch",
                        "host filesystem",
                        "raw WebSocket",
                        "shell",
                    ],
                },
                "installed_capabilities": cls._installed_capabilities(installed_capabilities),
                "model_tools": cls._model_tools(model_tools),
                "coding_agents": cls._coding_agents(coding_agents),
            }
        )

    @staticmethod
    def _schema(value: Any, *, depth: int = 0) -> Any:
        """Copy public contracts into a deterministic, bounded, secret-free shape."""

        if depth >= 6:
            return {"truncated": True}
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return value[:2_000]
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            entries = sorted(value.items(), key=lambda item: str(item[0]))
            for raw_key, raw_value in entries[:100]:
                key = str(raw_key)
                normalized = key.lower().replace("-", "_")
                if any(marker in normalized for marker in ("api_key", "credential", "password", "secret", "token")):
                    continue
                result[key] = SystemCapabilityCatalog._schema(raw_value, depth=depth + 1)
            if len(value) > 100:
                result["truncated"] = True
            return result
        if isinstance(value, (list, tuple, set, frozenset)):
            items = list(value)
            copied = [SystemCapabilityCatalog._schema(item, depth=depth + 1) for item in items[:100]]
            if len(items) > 100:
                copied.append({"truncated": True})
            return copied
        return str(value)[:2_000]

    @classmethod
    def _installed_capabilities(cls, items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in sorted(items, key=lambda value: str(value.get("catalog_id") or ""))[:100]:
            catalog_id = str(item.get("catalog_id") or "").strip()
            if not catalog_id:
                continue
            actions: list[dict[str, Any]] = []
            for action in sorted(item.get("actions") or (), key=lambda value: str(value.get("id") or ""))[:100]:
                action_id = str(action.get("id") or "").strip()
                if not action_id:
                    continue
                actions.append(
                    {
                        "id": action_id,
                        "title": str(action.get("title") or action_id)[:120],
                        "description": str(action.get("description") or "")[:500],
                        "input_schema": cls._schema(action.get("input_schema") or {}),
                        "result_schema": cls._schema(action.get("result_schema") or {}),
                        "approval_required": True,
                    }
                )
            status = str(item.get("status") or "unavailable")
            result.append(
                {
                    "catalog_id": catalog_id,
                    "title": str(item.get("title") or catalog_id)[:120],
                    "description": str(item.get("description") or "")[:500],
                    "status": status,
                    "available": status in {"ready", "needs_ui"},
                    "actions": actions,
                }
            )
        return result

    @classmethod
    def _model_tools(cls, items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in sorted(items, key=lambda value: str(value.get("name") or ""))[:100]:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            result.append(
                {
                    "name": name,
                    "description": str(item.get("description") or "")[:500],
                    "input_schema": cls._schema(item.get("input_schema") or {}),
                    "effect": str(item.get("effect") or "read"),
                    "scopes": sorted(str(scope) for scope in item.get("scopes") or ()),
                    "approval_required": bool(item.get("approval_required")),
                    "available": bool(item.get("available", True)),
                }
            )
        return result

    @classmethod
    def _coding_agents(cls, items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in sorted(items, key=lambda value: str(value.get("id") or ""))[:20]:
            agent_id = str(item.get("id") or "").strip()
            if not agent_id:
                continue
            result.append(
                {
                    "id": agent_id,
                    "name": str(item.get("name") or agent_id)[:120],
                    "available": bool(item.get("available")),
                    "auth_state": str(item.get("auth_state") or "unknown"),
                    "model_capability": cls._schema(item.get("model_capability") or {}),
                    "artifact_policy": {
                        "manifest_version": 2,
                        "allowed_files": ["README.md", "controller.js", "manifest.json"],
                        "staged_only": True,
                    },
                }
            )
        return result

    @staticmethod
    def _capability_summaries(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "catalog_id": item["catalog_id"],
                "title": item["title"],
                "status": item["status"],
                "available": item["available"],
                "action_ids": [action["id"] for action in item["actions"]],
            }
            for item in items
        ]

    def project(self, role: AgentRole) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "catalog_version": self._payload["catalog_version"],
            "runtime_contract": self._payload["runtime_contract"],
            "context_graph": self._payload["context_graph"],
            "widget_runtime": self._payload["widget_runtime"],
        }
        if role == AgentRole.INTENT_ROUTER:
            payload["widget_runtime"] = {
                "manifest_version": self._payload["widget_runtime"]["manifest_version"],
                "capability_categories": self._payload["widget_runtime"]["capability_categories"],
            }
            payload["installed_capabilities"] = self._capability_summaries(
                self._payload["installed_capabilities"]
            )
            payload["coding_agents"] = self._payload["coding_agents"]
        elif role == AgentRole.CONVERSE:
            payload["model_tools"] = [
                item for item in self._payload["model_tools"] if item.get("effect") == "read"
            ]
            payload["installed_capabilities"] = self._capability_summaries(
                self._payload["installed_capabilities"]
            )
            payload["widget_runtime"] = {
                "manifest_version": self._payload["widget_runtime"]["manifest_version"],
                "capability_categories": self._payload["widget_runtime"]["capability_categories"],
            }
        elif role == AgentRole.SCHEMA_ALIGNMENT:
            payload["installed_capabilities"] = self._payload["installed_capabilities"]
        return json.loads(json.dumps(payload, ensure_ascii=False, sort_keys=True))

    def validate_grants(
        self,
        grants: Iterable[CapabilityGrant | dict[str, Any]],
        *,
        graph_entity_ids: Iterable[str] | None = None,
    ) -> tuple[CapabilityGrant, ...]:
        """Validate ontology-dependent scopes against the current runtime catalog."""

        normalized = normalize_grants(list(grants))
        allowed_entities = (
            {str(entity_id) for entity_id in graph_entity_ids}
            if graph_entity_ids is not None
            else {str(item["id"]) for item in self._payload["context_graph"]["entities"]}
        )
        available_actions = {
            item["catalog_id"]: {action["id"] for action in item["actions"]}
            for item in self._payload["installed_capabilities"]
            if item["available"]
        }
        for grant in normalized:
            if grant.id in {"graph.query", "graph.mutate"}:
                unknown_entities = sorted(set(grant.scope["entities"]) - allowed_entities)
                if unknown_entities:
                    raise ValueError(f"Unknown Graph entities in '{grant.id}' grant: {', '.join(unknown_entities)}")
            elif grant.id == "capability.invoke":
                for catalog_id in grant.scope["catalog_ids"]:
                    if catalog_id not in available_actions:
                        raise ValueError(f"Installed capability '{catalog_id}' is unavailable")
                    unknown_actions = sorted(set(grant.scope["actions"]) - available_actions[catalog_id])
                    if unknown_actions:
                        raise ValueError(
                            f"Unknown actions for installed capability '{catalog_id}': {', '.join(unknown_actions)}"
                        )
        return normalized

    def render(self, role: AgentRole, *, max_chars: int = 24_000) -> str:
        payload = self.project(role)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(encoded) > max_chars:
            for section, id_key in (
                ("installed_capabilities", "catalog_id"),
                ("model_tools", "name"),
                ("coding_agents", "id"),
            ):
                entries = payload.get(section)
                if isinstance(entries, list):
                    payload[section] = {
                        "truncated": True,
                        "available_ids": [str(item.get(id_key)) for item in entries if isinstance(item, dict)][:100],
                    }
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(encoded) > max_chars:
            payload = {
                "catalog_version": self._payload["catalog_version"],
                "truncated": True,
                "capability_category_ids": list(capability_category_ids()),
            }
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return f"[SYSTEM CAPABILITY CATALOG v{self._payload['catalog_version']}]\n{encoded}\n[END SYSTEM CAPABILITY CATALOG]"
