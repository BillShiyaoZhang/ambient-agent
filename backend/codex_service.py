"""Backward-compatible Codex facade over the unified Coding Agent ACP runner."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from backend.coding_agent_repair import RepairDirective, RepairFinding
from backend.coding_agent_runtime import CodingAgentRuntime
from backend.coding_agent_acp import (
    CodingAgentACPError,
    CodingAgentACPInputError,
    CodingAgentACPProtocolError,
    CodingAgentACPStartupError,
    CodingAgentACPTimeoutError,
    CodingAgentStagedResult,
)

# Preserve import compatibility without preserving a second execution protocol.
CodexAgentError = CodingAgentACPError
CodexAgentInputError = CodingAgentACPInputError
CodexAgentStartupError = CodingAgentACPStartupError
CodexAgentTimeoutError = CodingAgentACPTimeoutError
CodexAgentProtocolError = CodingAgentACPProtocolError


def _codex_environment() -> dict[str, str]:
    """Return the same secret-minimized environment used by the ACP descriptor."""

    return CodingAgentRuntime(os.getenv("WORKSPACE_DIR", "workspace")).process_environment("codex")


def _codex_prompt(app_id: str, instruction: str, language: str) -> str:
    """Render the shared Widget coding contract retained for API compatibility."""

    from backend.agent.prompts.manager import PromptManager

    return PromptManager().get_prompt(
        "coding_agent_system.md",
        app_id=app_id,
        target_dir=".",
        instruction=instruction,
        language=language,
    )


async def run_codex_agent(
    app_id: str,
    instruction: str,
    language: str = "zh",
    on_update: Callable[[Any], Any] | None = None,
    *,
    promote: bool = True,
    runtime: CodingAgentRuntime | None = None,
    native_model: str | None = None,
    staged_result: CodingAgentStagedResult | None = None,
    artifact_validator: Callable[[CodingAgentStagedResult], Any] | None = None,
    repair_decider: Callable[[RepairFinding, tuple[RepairFinding, ...]], RepairDirective] | None = None,
) -> str | CodingAgentStagedResult:
    """Delegate legacy callers to the sole ACP orchestration boundary."""

    from backend.coding_agent import run_coding_agent

    return await run_coding_agent(
        app_id,
        instruction,
        language=language,
        on_update=on_update,
        promote=promote,
        coding_agent="codex",
        runtime=runtime,
        model_config={"mode": "native", "native_model": native_model},
        staged_result=staged_result,
        artifact_validator=artifact_validator,
        repair_decider=repair_decider,
    )
