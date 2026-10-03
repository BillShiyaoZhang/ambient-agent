import json

import pytest

from backend.agent.prompts.manager import PromptManager
from backend.app_types import get_app_type_catalog
from backend.capabilities.models import RuntimeContract
from backend.coding_agent_repair import RepairFinding, build_repair_prompt
from backend.plan_generation import PlanGenerationService


UI_GUIDE_MARKERS = (
    "meaningful visual hierarchy",
    "expandable details",
    "reading-oriented",
    "data-driven graphics",
    "320",
    "keyboard",
    "light and dark",
    "raw provider exceptions",
    "no universal text-count limit",
    "Preserve unsaved user edits",
    "retry the failed operation",
)


def _assert_ui_guide(prompt: str) -> None:
    for marker in UI_GUIDE_MARKERS:
        assert marker in prompt


def test_coding_agent_prompt_includes_ui_quality_guide() -> None:
    prompt = PromptManager().get_prompt(
        "coding_agent_system.md",
        app_id="weather-app",
        target_dir="staging",
        instruction="Build a weather app",
        language="en",
    )

    _assert_ui_guide(prompt)
    assert "Do not represent a chart with Unicode glyphs" in prompt
    assert "inline styles" in prompt


@pytest.mark.asyncio
async def test_plan_generation_and_refinement_include_ui_quality_guide(monkeypatch) -> None:
    captured_messages = []

    class Provider:
        async def generate(self, messages, **_kwargs):
            captured_messages.append(messages)
            return "A plan"

    monkeypatch.setattr("backend.plan_generation.primary_selection", lambda: object())
    monkeypatch.setattr("backend.plan_generation.selection_ids", lambda _selection: ("provider", "model"))
    monkeypatch.setattr("backend.plan_generation.get_llm_provider", lambda *_args: Provider())

    common = {
        "instruction": "Show the current weather and 24-hour forecast",
        "app_id": "weather-app",
        "schemas_context": "",
        "language": "en",
        "capability_catalog": None,
    }
    await PlanGenerationService.generate_plan(**common)
    await PlanGenerationService.refine_plan(
        **common,
        current_plan="Show current conditions and forecast",
        feedback="Make the summary easier to scan",
    )

    assert len(captured_messages) == 2
    for messages in captured_messages:
        _assert_ui_guide(messages[0]["content"])


def test_repair_prompt_preserves_ui_quality_guide() -> None:
    contract = RuntimeContract.create(app_id="weather-app", schemas=[], capabilities=[]).to_dict()
    instruction = (
        "Show the current weather.\n\n[APPROVED RUNTIME CONTRACT — REFERENCE ONLY]\n"
        f"{json.dumps(contract)}\n\n[REQUIRED MANIFEST V2 TEMPLATE]\n{{}}\n\n"
        "[APP TYPE STANDARD]\n"
        f"{json.dumps(get_app_type_catalog())}\n"
    )
    finding = RepairFinding(
        code="widget_verification_failed",
        stage="static_verify",
        message="Missing a visible primary summary",
        signature="finding-signature",
        attempt=1,
        repairability="code_only",
        contract_impact="none",
        artifact_hash="artifact-hash",
    )

    prompt = build_repair_prompt(finding, instruction=instruction)

    _assert_ui_guide(prompt)
    assert "never add or broaden capabilities" in prompt
