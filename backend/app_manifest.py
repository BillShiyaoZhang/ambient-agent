import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

APP_MANIFEST_VERSION = 1
MAX_APP_ID_LENGTH = 64
MAX_TITLE_LENGTH = 200
MAX_DESCRIPTION_LENGTH = 2000
MAX_APP_VERSION_LENGTH = 64
MAX_LIST_ITEMS = 100
MAX_LIST_ITEM_LENGTH = 200
MAX_MANIFEST_BYTES = 64 * 1024

_APP_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_WINDOWS_RESERVED_NAMES = {
    "aux",
    "clock$",
    "com1",
    "com2",
    "com3",
    "com4",
    "com5",
    "com6",
    "com7",
    "com8",
    "com9",
    "con",
    "lpt1",
    "lpt2",
    "lpt3",
    "lpt4",
    "lpt5",
    "lpt6",
    "lpt7",
    "lpt8",
    "lpt9",
    "nul",
    "prn",
}
_REQUIRED_FIELDS = {
    "manifest_version",
    "id",
    "title",
    "description",
    "app_version",
    "intents",
    "schema_refs",
}
_OPTIONAL_FIELDS = {
    "backend_type",
    "mcp_server",
    "agent_url",
}
_FIELDS = _REQUIRED_FIELDS | _OPTIONAL_FIELDS



class ManifestValidationError(ValueError):
    """Raised when an App Manifest does not satisfy the V1 contract."""


def validate_app_id(app_id: Any) -> str:
    if not isinstance(app_id, str):
        raise ManifestValidationError("id must be a string")
    if len(app_id) > MAX_APP_ID_LENGTH or not _APP_ID_PATTERN.fullmatch(app_id):
        raise ManifestValidationError("id must be a lowercase kebab-case identifier of at most 64 characters")
    if app_id.casefold() in _WINDOWS_RESERVED_NAMES:
        raise ManifestValidationError("id is reserved by the filesystem")
    return app_id


def _validate_text(field: str, value: Any, *, allow_empty: bool, max_length: int) -> str:
    if not isinstance(value, str):
        raise ManifestValidationError(f"{field} must be a string")
    if (not allow_empty and not value.strip()) or value != value.strip():
        raise ManifestValidationError(f"{field} must be non-empty and must not have surrounding whitespace")
    if len(value) > max_length:
        raise ManifestValidationError(f"{field} exceeds its maximum length")
    return value


def _validate_string_list(field: str, value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ManifestValidationError(f"{field} must be an array")
    if len(value) > MAX_LIST_ITEMS:
        raise ManifestValidationError(f"{field} contains too many items")
    result: list[str] = []
    for item in value:
        result.append(_validate_text(field, item, allow_empty=False, max_length=MAX_LIST_ITEM_LENGTH))
    if len(result) != len(set(result)):
        raise ManifestValidationError(f"{field} must not contain duplicate items")
    return tuple(result)


def _validate_backend_type(value: Any) -> str:
    if not isinstance(value, str):
        raise ManifestValidationError("backend_type must be a string")
    if value not in ("code", "agent", "mcp"):
        raise ManifestValidationError("backend_type must be one of 'code', 'agent', 'mcp'")
    return value


def _validate_mcp_server(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ManifestValidationError("mcp_server must be a JSON object")
    if "command" not in value:
        raise ManifestValidationError("mcp_server must contain a 'command' field")
    command = value["command"]
    if not isinstance(command, list) or not command:
        raise ManifestValidationError("mcp_server command must be a non-empty array of strings")
    for item in command:
        if not isinstance(item, str) or not item.strip():
            raise ManifestValidationError("mcp_server command items must be non-empty strings")
    
    args = value.get("args", [])
    if not isinstance(args, list):
        raise ManifestValidationError("mcp_server args must be an array of strings")
    for item in args:
        if not isinstance(item, str):
            raise ManifestValidationError("mcp_server args items must be strings")
            
    env = value.get("env", {})
    if not isinstance(env, dict):
        raise ManifestValidationError("mcp_server env must be a JSON object")
    for k, v in env.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise ManifestValidationError("mcp_server env keys and values must be strings")
            
    return {
        "command": [str(c).strip() for c in command],
        "args": [str(a) for a in args],
        "env": {str(k): str(v) for k, v in env.items()}
    }


@dataclass(frozen=True, slots=True)
class AppManifest:
    manifest_version: int
    id: str
    title: str
    description: str
    app_version: str
    intents: tuple[str, ...]
    schema_refs: tuple[str, ...]
    backend_type: str = "code"
    mcp_server: dict[str, Any] | None = None
    agent_url: str | None = None


    @classmethod
    def from_dict(cls, data: Any, *, expected_app_id: str) -> "AppManifest":
        if not isinstance(data, dict):
            raise ManifestValidationError("manifest must be a JSON object")
        unknown = set(data) - _FIELDS
        missing = _REQUIRED_FIELDS - set(data)
        if unknown:
            raise ManifestValidationError(f"manifest contains unknown fields: {', '.join(sorted(unknown))}")
        if missing:
            raise ManifestValidationError(f"manifest is missing required fields: {', '.join(sorted(missing))}")
        if type(data["manifest_version"]) is not int or data["manifest_version"] != APP_MANIFEST_VERSION:
            raise ManifestValidationError(f"manifest_version must be the supported integer {APP_MANIFEST_VERSION}")

        app_id = validate_app_id(data["id"])
        if app_id != expected_app_id:
            raise ManifestValidationError("manifest id must match its App directory name")

        backend_type = _validate_backend_type(data.get("backend_type", "code"))
        mcp_server = _validate_mcp_server(data.get("mcp_server"))
        agent_url = data.get("agent_url")
        if agent_url is not None:
            agent_url = _validate_text("agent_url", agent_url, allow_empty=False, max_length=1000)

        return cls(
            manifest_version=APP_MANIFEST_VERSION,
            id=app_id,
            title=_validate_text("title", data["title"], allow_empty=False, max_length=MAX_TITLE_LENGTH),
            description=_validate_text(
                "description", data["description"], allow_empty=True, max_length=MAX_DESCRIPTION_LENGTH
            ),
            app_version=_validate_text(
                "app_version", data["app_version"], allow_empty=False, max_length=MAX_APP_VERSION_LENGTH
            ),
            intents=_validate_string_list("intents", data["intents"]),
            schema_refs=_validate_string_list("schema_refs", data["schema_refs"]),
            backend_type=backend_type,
            mcp_server=mcp_server,
            agent_url=agent_url,
        )

    @classmethod
    def read(cls, path: Path, *, expected_app_id: str) -> "AppManifest":
        try:
            if path.stat().st_size > MAX_MANIFEST_BYTES:
                raise ManifestValidationError("manifest exceeds its maximum size")
            with path.open(encoding="utf-8") as manifest_file:
                data = json.load(manifest_file)
        except ManifestValidationError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ManifestValidationError("manifest must be readable UTF-8 containing valid JSON") from exc
        return cls.from_dict(data, expected_app_id=expected_app_id)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "manifest_version": self.manifest_version,
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "app_version": self.app_version,
            "intents": list(self.intents),
            "schema_refs": list(self.schema_refs),
        }
        if self.backend_type != "code":
            result["backend_type"] = self.backend_type
        if self.mcp_server is not None:
            result["mcp_server"] = self.mcp_server
        if self.agent_url is not None:
            result["agent_url"] = self.agent_url
        return result

    def write_atomic(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                json.dump(self.to_dict(), temporary_file, indent=2, ensure_ascii=False)
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
