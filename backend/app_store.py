import hashlib
import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app_manager import AppManager


_CAPABILITY_ID_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class LayoutConflictError(RuntimeError):
    def __init__(self, current: dict[str, Any]):
        super().__init__("app store layout revision conflict")
        self.current = current


class CapabilityInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["mcp_tool", "agent_message"]
    app_id: str
    tool_name: str | None = None

    @model_validator(mode="after")
    def validate_tool(self) -> "CapabilityInvocation":
        if self.type == "mcp_tool" and not self.tool_name:
            raise ValueError("mcp_tool invocation requires tool_name")
        return self


class CapabilityAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    result_schema: dict[str, Any] = Field(default_factory=dict)
    invocation: CapabilityInvocation
    recovery: Literal["manual", "restart_safe"] = "manual"

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        value = value.strip().lower()
        if not _CAPABILITY_ID_RE.fullmatch(value):
            raise ValueError("action id must contain lowercase letters, numbers, dots, underscores, or hyphens")
        return value

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 120:
            raise ValueError("action title must be between 1 and 120 characters")
        return value

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        return value.strip()[:2000]


class CapabilityManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_version: Literal[1, 2] = 1
    id: str
    kind: Literal["skill", "mcp"]
    provider: str
    title: str
    description: str = ""
    version: str = "1.0.0"
    tags: list[str] = Field(default_factory=list)
    icon: str | None = None
    accent: str | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict)
    invocation: CapabilityInvocation | None = None
    actions: list[CapabilityAction] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_contract_version(self) -> "CapabilityManifest":
        if self.manifest_version == 1 and self.invocation is None:
            raise ValueError("manifest v1 requires invocation")
        if self.manifest_version == 2 and not self.actions:
            raise ValueError("manifest v2 requires at least one action")
        action_ids = [action.id for action in self.actions]
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("action ids must be unique")
        return self

    def normalized_actions(self) -> list[CapabilityAction]:
        if self.actions:
            return list(self.actions)
        assert self.invocation is not None
        return [
            CapabilityAction(
                id="run",
                title=self.title,
                description=self.description,
                input_schema=self.input_schema,
                result_schema={},
                invocation=self.invocation,
                recovery="manual",
            )
        ]

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        value = value.strip().lower()
        if not _CAPABILITY_ID_RE.fullmatch(value):
            raise ValueError("id must contain lowercase letters, numbers, dots, underscores, or hyphens")
        return value

    @field_validator("provider", "title")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("field must not be empty")
        if len(value) > 120:
            raise ValueError("field is too long")
        return value

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        if len(value) > 2000:
            raise ValueError("description is too long")
        return value.strip()

    @field_validator("icon")
    @classmethod
    def validate_icon(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if len(value) > 12 or "://" in value or value.startswith("data:"):
            raise ValueError("icon must be a short local glyph or emoji")
        return value or None

    @field_validator("accent")
    @classmethod
    def validate_accent(cls, value: str | None) -> str | None:
        if value is not None and not _HEX_COLOR_RE.fullmatch(value):
            raise ValueError("accent must be a six-digit hex color")
        return value

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str]) -> list[str]:
        result: list[str] = []
        for tag in value:
            normalized = tag.strip()
            if normalized and normalized not in result:
                result.append(normalized)
        return result[:20]


class CapabilityProvider(Protocol):
    def list_catalog_items(self) -> list[dict[str, Any]]: ...


class GeneratedAppProvider:
    def __init__(self, app_manager: AppManager):
        self.app_manager = app_manager

    def list_catalog_items(self) -> list[dict[str, Any]]:
        return [
            {
                "catalog_id": f"app:{app['id']}",
                "kind": "generated_app",
                "title": app["title"],
                "description": app.get("description", ""),
                "version": app.get("app_version", "0.1.0"),
                "provider": "Ambient Agent",
                "tags": list(app.get("intents", [])),
                "icon": None,
                "accent": None,
                "ui_app_id": app["id"],
                "launch_mode": "ui",
                "actions": [],
                "status": "ready",
                "created_at": app.get("created_at"),
                "updated_at": app.get("updated_at"),
            }
            for app in self.app_manager.list_apps()
        ]


class AppStoreService:
    """Workspace-scoped capability registry and synchronized launcher layout."""

    def __init__(self, workspace_dir: str, app_manager: AppManager):
        self.workspace_dir = Path(workspace_dir)
        self.store_dir = self.workspace_dir / ".ambient"
        self.capabilities_path = self.store_dir / "capabilities.json"
        self.layout_path = self.store_dir / "app-store-layout.json"
        self.app_manager = app_manager
        self.generated_provider = GeneratedAppProvider(app_manager)
        self._lock = threading.RLock()
        self.generating_ids: set[str] = set()

    @staticmethod
    def catalog_id(manifest: CapabilityManifest) -> str:
        provider_key = re.sub(r"[^a-z0-9]+", "-", manifest.provider.lower()).strip("-")
        if not provider_key:
            provider_key = hashlib.sha256(manifest.provider.encode("utf-8")).hexdigest()[:10]
        return f"{manifest.kind}:{provider_key}:{manifest.id}"

    @staticmethod
    def generated_ui_app_id(catalog_id: str) -> str:
        readable = re.sub(r"[^a-z0-9]+", "-", catalog_id.lower()).strip("-")
        digest = hashlib.sha256(catalog_id.encode("utf-8")).hexdigest()[:8]
        readable = readable[:42].rstrip("-") or "capability"
        return f"{readable}-ui-{digest}"

    @staticmethod
    def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
        try:
            with path.open(encoding="utf-8") as file:
                data = json.load(file)
            return data if isinstance(data, dict) else default
        except (OSError, UnicodeError, json.JSONDecodeError):
            return default

    def _write_json_atomic(self, path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
            ) as file:
                temporary_path = Path(file.name)
                json.dump(data, file, indent=2, ensure_ascii=False)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _registry(self) -> dict[str, Any]:
        return self._read_json(self.capabilities_path, {"version": 1, "capabilities": {}, "ui_bindings": {}})

    def _layout(self) -> dict[str, Any]:
        return self._read_json(self.layout_path, {"version": 1, "revision": 0, "root": [], "folders": []})

    def register_capability(self, manifest: CapabilityManifest) -> dict[str, Any]:
        with self._lock:
            registry = self._registry()
            catalog_id = self.catalog_id(manifest)
            registry.setdefault("capabilities", {})[catalog_id] = manifest.model_dump(exclude_none=True)
            registry.setdefault("ui_bindings", {})
            self._write_json_atomic(self.capabilities_path, registry)
            return self.get_catalog_item(catalog_id) or {}

    def delete_capability(self, catalog_id: str) -> bool:
        with self._lock:
            registry = self._registry()
            capabilities = registry.setdefault("capabilities", {})
            if catalog_id not in capabilities:
                return False
            capabilities.pop(catalog_id, None)
            registry.setdefault("ui_bindings", {}).pop(catalog_id, None)
            self._write_json_atomic(self.capabilities_path, registry)
            self._remove_catalog_ids_from_layout({catalog_id})
            return True

    def bind_ui(self, catalog_id: str, app_id: str) -> None:
        with self._lock:
            registry = self._registry()
            if catalog_id not in registry.setdefault("capabilities", {}):
                raise KeyError(catalog_id)
            registry.setdefault("ui_bindings", {})[catalog_id] = app_id
            self._write_json_atomic(self.capabilities_path, registry)

    def unbind_ui(self, catalog_id: str) -> str | None:
        with self._lock:
            registry = self._registry()
            app_id = registry.setdefault("ui_bindings", {}).pop(catalog_id, None)
            self._write_json_atomic(self.capabilities_path, registry)
            return app_id

    def on_app_deleted(self, app_id: str) -> None:
        with self._lock:
            registry = self._registry()
            bindings = registry.setdefault("ui_bindings", {})
            changed = False
            for catalog_id, bound_app_id in list(bindings.items()):
                if bound_app_id == app_id:
                    bindings.pop(catalog_id, None)
                    changed = True
            if changed:
                self._write_json_atomic(self.capabilities_path, registry)
            self._remove_catalog_ids_from_layout({f"app:{app_id}"})

    def get_capability(self, catalog_id: str) -> CapabilityManifest | None:
        raw = self._registry().get("capabilities", {}).get(catalog_id)
        if not isinstance(raw, dict):
            return None
        try:
            return CapabilityManifest.model_validate(raw)
        except ValueError:
            return None

    def _capability_items(self) -> list[dict[str, Any]]:
        registry = self._registry()
        bindings = registry.get("ui_bindings", {})
        result: list[dict[str, Any]] = []
        for catalog_id, raw in sorted(registry.get("capabilities", {}).items()):
            try:
                manifest = CapabilityManifest.model_validate(raw)
            except ValueError:
                continue
            bound_app_id = bindings.get(catalog_id)
            ready = bool(bound_app_id and self.app_manager.get_manifest(bound_app_id))
            if bound_app_id and not ready:
                bound_app_id = None
            # V1 keeps its historical status for API compatibility, but is still
            # directly launchable through the normalized default action. V2
            # headless capabilities are first-class ready apps.
            status = "generating" if catalog_id in self.generating_ids else (
                "ready" if ready or manifest.manifest_version == 2 else "needs_ui"
            )
            result.append(
                {
                    "catalog_id": catalog_id,
                    "kind": manifest.kind,
                    "title": manifest.title,
                    "description": manifest.description,
                    "version": manifest.version,
                    "provider": manifest.provider,
                    "tags": manifest.tags,
                    "icon": manifest.icon,
                    "accent": manifest.accent,
                    "input_schema": manifest.input_schema,
                    "actions": [action.model_dump(exclude_none=True) for action in manifest.normalized_actions()],
                    "ui_app_id": bound_app_id,
                    "launch_mode": "ui" if ready else "actions",
                    "status": status,
                }
            )
        return result

    def list_catalog_items(self) -> list[dict[str, Any]]:
        capabilities = self._capability_items()
        bound_app_ids = {item["ui_app_id"] for item in capabilities if item.get("ui_app_id")}
        apps = [item for item in self.generated_provider.list_catalog_items() if item["ui_app_id"] not in bound_app_ids]
        return apps + capabilities

    def get_catalog_item(self, catalog_id: str) -> dict[str, Any] | None:
        return next((item for item in self.list_catalog_items() if item["catalog_id"] == catalog_id), None)

    def get_action(self, catalog_id: str, action_id: str) -> CapabilityAction | None:
        manifest = self.get_capability(catalog_id)
        if manifest is None:
            return None
        return next((action for action in manifest.normalized_actions() if action.id == action_id), None)

    @staticmethod
    def _normalize_layout(layout: dict[str, Any], valid_item_ids: set[str]) -> dict[str, Any]:
        seen_items: set[str] = set()
        seen_folders: set[str] = set()
        normalized_folders: list[dict[str, Any]] = []
        folder_entry_map: dict[str, str] = {}

        raw_folders = layout.get("folders", [])
        if not isinstance(raw_folders, list):
            raise ValueError("folders must be an array")
        for raw_folder in raw_folders:
            if not isinstance(raw_folder, dict):
                raise ValueError("folder must be an object")
            folder_id = str(raw_folder.get("id", "")).strip()
            if not folder_id or folder_id in seen_folders:
                raise ValueError("folder ids must be non-empty and unique")
            seen_folders.add(folder_id)
            items: list[str] = []
            for item_id in raw_folder.get("items", []):
                if not isinstance(item_id, str) or item_id.startswith("folder:"):
                    raise ValueError("folders may only contain catalog items")
                if item_id not in valid_item_ids:
                    continue
                if item_id in seen_items:
                    raise ValueError("catalog items may appear only once")
                seen_items.add(item_id)
                items.append(item_id)
            if len(items) >= 2:
                normalized_folders.append({"id": folder_id, "name": str(raw_folder.get("name", "Folder"))[:80], "items": items})
                folder_entry_map[f"folder:{folder_id}"] = f"folder:{folder_id}"
            elif len(items) == 1:
                folder_entry_map[f"folder:{folder_id}"] = items[0]

        root: list[str] = []
        raw_root = layout.get("root", [])
        if not isinstance(raw_root, list):
            raise ValueError("root must be an array")
        for entry in raw_root:
            if not isinstance(entry, str):
                raise ValueError("root entries must be strings")
            resolved = folder_entry_map.get(entry, entry)
            dissolved_folder_item = entry in folder_entry_map and resolved in valid_item_ids
            if resolved.startswith("folder:"):
                if resolved not in folder_entry_map:
                    continue
            elif resolved not in valid_item_ids:
                continue
            if resolved in valid_item_ids:
                if resolved in seen_items and not dissolved_folder_item:
                    continue
                seen_items.add(resolved)
            if resolved not in root:
                root.append(resolved)

        for folder in normalized_folders:
            entry = f"folder:{folder['id']}"
            if entry not in root:
                root.append(entry)
        for item_id in sorted(valid_item_ids - seen_items):
            root.append(item_id)
        return {"root": root, "folders": normalized_folders}

    def get_state(self) -> dict[str, Any]:
        with self._lock:
            items = self.list_catalog_items()
            layout = self._layout()
            normalized = self._normalize_layout(layout, {item["catalog_id"] for item in items})
            state = {
                "version": 1,
                "revision": int(layout.get("revision", 0)),
                "items": items,
                **normalized,
            }
            if normalized["root"] != layout.get("root") or normalized["folders"] != layout.get("folders"):
                self._write_json_atomic(
                    self.layout_path,
                    {"version": 1, "revision": state["revision"], **normalized},
                )
            return state

    def save_layout(self, revision: int, root: list[str], folders: list[dict[str, Any]]) -> dict[str, Any]:
        with self._lock:
            current = self.get_state()
            if revision != current["revision"]:
                raise LayoutConflictError(current)
            valid_ids = {item["catalog_id"] for item in current["items"]}
            normalized = self._normalize_layout({"root": root, "folders": folders}, valid_ids)
            next_revision = current["revision"] + 1
            self._write_json_atomic(
                self.layout_path,
                {"version": 1, "revision": next_revision, **normalized},
            )
            return {**current, "revision": next_revision, **normalized}

    def _remove_catalog_ids_from_layout(self, ids: set[str]) -> None:
        layout = self._layout()
        root = [entry for entry in layout.get("root", []) if entry not in ids]
        folders = []
        for folder in layout.get("folders", []):
            updated = {**folder, "items": [item for item in folder.get("items", []) if item not in ids]}
            folders.append(updated)
        valid_ids = {item["catalog_id"] for item in self.list_catalog_items()}
        normalized = self._normalize_layout({"root": root, "folders": folders}, valid_ids)
        self._write_json_atomic(
            self.layout_path,
            {"version": 1, "revision": int(layout.get("revision", 0)) + 1, **normalized},
        )
