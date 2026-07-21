"""Coding-agent registry, persistent selection, and staging runner dispatch."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from backend.codex_service import run_codex_agent
from backend.coding_agent_runtime import CodingAgentRuntime, SPECS, model_capability, spec_for
from backend.opencode_service import run_opencode_agent_acp

CodingAgentId = Literal["opencode", "codex"]


class CodingAgentConfigError(RuntimeError):
    code = "coding_agent_configuration_error"

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        if code:
            self.code = code


class AgentModelConfig(BaseModel):
    mode: Literal["native", "shared_binding", "hybrid", "none"]
    inherit: Literal["ambient.primary"] | None = None
    provider_id: str | None = None
    model_id: str | None = None
    native_model: str | None = None

    @model_validator(mode="after")
    def validate_binding(self):
        if self.mode == "shared_binding":
            explicit = bool(self.provider_id and self.model_id)
            if not self.inherit and not explicit:
                raise ValueError("A shared model binding must inherit ambient.primary or select a provider model")
            if self.inherit and (self.provider_id or self.model_id):
                raise ValueError("A shared model binding cannot inherit and select an explicit model")
        return self


def _default_agent_models() -> dict[str, AgentModelConfig]:
    return {
        "opencode": AgentModelConfig(mode="shared_binding", inherit="ambient.primary"),
        "codex": AgentModelConfig(mode="native"),
    }


class CodingAgentSettings(BaseModel):
    default_agent: CodingAgentId = "opencode"
    agent_models: dict[str, AgentModelConfig] = Field(default_factory=_default_agent_models)

    @model_validator(mode="after")
    def add_defaults(self):
        defaults = _default_agent_models()
        for agent_id, config in defaults.items():
            self.agent_models.setdefault(agent_id, config)
        return self


class CodingAgentConfigStore:
    """Stores only non-secret coding-agent preferences in the workspace."""

    def __init__(self, workspace_dir: str | Path):
        self.workspace_dir = Path(workspace_dir)
        self.directory = Path(workspace_dir) / "coding_agents"
        self.path = self.directory / "config.json"
        self.runtime = CodingAgentRuntime(workspace_dir)

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

    def update_agent_model(self, agent_id: str, data: dict[str, Any]) -> dict[str, Any]:
        spec = spec_for(agent_id)
        try:
            config = AgentModelConfig.model_validate(data)
        except ValidationError as exc:
            raise CodingAgentConfigError("Invalid coding-agent model configuration") from exc
        if config.mode not in spec.model_modes:
            raise CodingAgentConfigError(
                f"{spec.name} does not support the {config.mode} model mode",
                code="coding_agent_model_mode_unsupported",
            )
        settings = CodingAgentSettings.model_validate(self.get_settings())
        settings.agent_models[agent_id] = config
        return self.update_settings(settings.model_dump(mode="json"))["agent_models"][agent_id]

    def model_config(self, agent_id: str) -> dict[str, Any]:
        spec_for(agent_id)
        return self.get_settings()["agent_models"][agent_id]

    def catalog(self) -> list[dict[str, Any]]:
        catalog: list[dict[str, Any]] = []
        settings = self.get_settings()
        for spec in SPECS:
            installed = self.runtime.command(spec.id) is not None
            catalog.append(
                {
                    "id": spec.id,
                    "name": spec.name,
                    "description": spec.description,
                    "auth_hint": spec.auth_hint,
                    "auth_mode": "codex_native" if spec.auth_methods else "run_model",
                    "auth_methods": list(spec.auth_methods),
                    "uses_run_model": spec.default_model_mode == "shared_binding",
                    "available": installed,
                    "installed": installed,
                    "install_state": "installed" if installed else "not_installed",
                    "installable": spec.install_handler is not None,
                    "install_operation": None,
                    "command_env": spec.command_env,
                    "execution_target": "container",
                    "authenticated": None if not spec.auth_methods else False,
                    "auth_state": "not_required" if not spec.auth_methods else "signed_out",
                    "version": "",
                    "status_detail": "",
                    "model_capability": model_capability(spec),
                    "model_config": settings["agent_models"][spec.id],
                }
            )
        return catalog

    async def runtime_catalog(self) -> list[dict[str, Any]]:
        catalog = self.catalog()
        statuses = await asyncio.gather(*(self.runtime.status(item["id"]) for item in catalog))
        for item, status in zip(catalog, statuses, strict=True):
            item.update(status)
        return catalog


async def run_coding_agent(
    app_id: str,
    instruction: str,
    language: str = "zh",
    on_update: Any = None,
    *,
    promote: bool = True,
    coding_agent: str = "opencode",
    runtime: CodingAgentRuntime | None = None,
    model_config: dict[str, Any] | None = None,
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
            runtime=runtime,
            native_model=str((model_config or {}).get("native_model") or "") or None,
        )
    raise CodingAgentConfigError("Unknown coding agent", code="coding_agent_not_found")
