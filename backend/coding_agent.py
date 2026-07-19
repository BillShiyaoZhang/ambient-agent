"""Coding-agent registry, persistent selection, and staging runner dispatch."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from backend.codex_service import codex_host_bridge_status, run_codex_agent
from backend.opencode_service import run_opencode_agent_acp

CodingAgentId = Literal["opencode", "codex"]


class CodingAgentConfigError(RuntimeError):
    code = "coding_agent_configuration_error"

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        if code:
            self.code = code


class CodingAgentSettings(BaseModel):
    default_agent: CodingAgentId = "opencode"


_AGENTS: tuple[dict[str, str], ...] = (
    {
        "id": "opencode",
        "name": "OpenCode",
        "description": "Uses OpenCode's ACP server and the model selected for this Ambient Agent run.",
        "command_env": "OPENCODE_COMMAND",
        "default_command": "opencode",
        "auth_hint": "Uses the configured LLM provider credentials.",
        "auth_mode": "run_model",
    },
    {
        "id": "codex",
        "name": "Codex",
        "description": "Uses the host Codex CLI through an authenticated local bridge.",
        "command_env": "CODEX_HOST_COMMAND",
        "default_command": "",
        "auth_hint": "Uses the host Codex login/ChatGPT subscription, not the Ambient Agent LLM provider.",
        "auth_mode": "codex_native",
    },
)


def _command_available(command: str) -> bool:
    try:
        argv = shlex.split(command, posix=os.name != "nt")
    except ValueError:
        return False
    if not argv:
        return False
    executable = argv[0]
    if Path(executable).is_absolute():
        return Path(executable).is_file()
    return shutil.which(executable) is not None


class CodingAgentConfigStore:
    """Stores only non-secret coding-agent preferences in the workspace."""

    def __init__(self, workspace_dir: str | Path):
        self.directory = Path(workspace_dir) / "coding_agents"
        self.path = self.directory / "config.json"

    def get_settings(self) -> dict[str, Any]:
        if not self.path.exists():
            return CodingAgentSettings().model_dump(mode="json")
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return CodingAgentSettings.model_validate(raw).model_dump(mode="json")
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise CodingAgentConfigError("Invalid coding-agent configuration") from exc

    def update_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        try:
            settings = CodingAgentSettings.model_validate({**self.get_settings(), **(changes or {})})
        except ValidationError as exc:
            raise CodingAgentConfigError(
                "Unknown coding agent",
                code="coding_agent_not_found",
            ) from exc
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(settings.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.directory,
                prefix=".config-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            temp_path.replace(self.path)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink(missing_ok=True)
        return settings.model_dump(mode="json")

    def catalog(self) -> list[dict[str, Any]]:
        catalog: list[dict[str, Any]] = []
        for definition in _AGENTS:
            command = os.getenv(definition["command_env"], definition["default_command"])
            catalog.append(
                {
                    "id": definition["id"],
                    "name": definition["name"],
                    "description": definition["description"],
                    "auth_hint": definition["auth_hint"],
                    "auth_mode": definition["auth_mode"],
                    "uses_run_model": definition["auth_mode"] == "run_model",
                    "available": _command_available(command) if definition["id"] == "opencode" else False,
                    "command_env": definition["command_env"],
                    "execution_target": "host" if definition["id"] == "codex" else "container",
                    "authenticated": None,
                    "version": "",
                    "status_detail": "",
                }
            )
        return catalog

    async def runtime_catalog(self) -> list[dict[str, Any]]:
        catalog = self.catalog()
        status = await codex_host_bridge_status()
        codex = next(item for item in catalog if item["id"] == "codex")
        codex.update(status)
        return catalog


async def run_coding_agent(
    app_id: str,
    instruction: str,
    language: str = "zh",
    on_update: Any = None,
    *,
    promote: bool = True,
    coding_agent: str = "opencode",
):
    if coding_agent == "opencode":
        return await run_opencode_agent_acp(
            app_id,
            instruction,
            language=language,
            on_update=on_update,
            promote=promote,
        )
    if coding_agent == "codex":
        return await run_codex_agent(
            app_id,
            instruction,
            language=language,
            on_update=on_update,
            promote=promote,
        )
    raise CodingAgentConfigError("Unknown coding agent", code="coding_agent_not_found")
