"""Shared quality guidance for UI planning, generation and repair prompts."""

from backend.agent.prompts.manager import PromptManager


def ui_quality_guide() -> str:
    """Render the canonical UI quality guide shared with the coding prompt."""

    return PromptManager().get_prompt("ui_quality_guidance.md")
