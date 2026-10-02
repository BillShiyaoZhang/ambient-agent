from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import backend.agent.durable_workflow as workflow_module
from backend.agent.durable_workflow import DurableAgentWorkflow
from backend.agent.intent_plan import IntentKind, IntentPlan
from backend.router_context import RouterContext
from backend.run_service import AgentRunState


APP_SPEC = {
    "spec_version": 1,
    "types": ["calendar", "tasks"],
    "features": [
        {"id": "calendar.events", "status": "implemented", "surfaces": ["data", "ui"]},
        {"id": "tasks.items", "status": "partial", "surfaces": ["ui"], "notes": "List only"},
        {"id": "calendar.reminders", "status": "planned", "surfaces": []},
    ],
}


def test_router_describes_feature_status_without_claiming_verified_tools() -> None:
    context = RouterContext(app_manifests=[{"id": "planner", "title": "Planner", "app_spec": APP_SPEC}])

    prompt = context.render_for_prompt(sections=["widgets"])

    assert "App types: calendar, tasks" in prompt
    assert "author supplied" in prompt
    assert "not verified" in prompt
    assert "do not create callable tools" in prompt
    assert "Implemented: calendar.events [data, ui]" in prompt
    assert "Partial: tasks.items [ui]" in prompt
    assert "Planned: calendar.reminders [none]" in prompt
    assert "Implemented: calendar.reminders" not in prompt


@pytest.mark.parametrize("spec", [None, {}])
def test_router_preserves_unclassified_legacy_apps(spec: dict | None) -> None:
    app = {"id": "legacy-calendar", "title": "Calendar", "schema_refs": ["Event"]}
    if spec is not None:
        app["app_spec"] = spec

    prompt = RouterContext(app_manifests=[app]).render_for_prompt(sections=["widgets"])

    assert "App types: unclassified" in prompt
    assert "App types: calendar" not in prompt
    assert "Schema Refs: Event" in prompt


def test_router_type_declarations_respect_section_selection() -> None:
    context = RouterContext(app_manifests=[{"id": "planner", "app_spec": APP_SPEC}])

    prompt = context.render_for_prompt(sections=["graph_counts"])

    assert "calendar" not in prompt
    assert "Declared features" not in prompt


def test_router_custom_tool_surface_remains_an_author_declaration() -> None:
    context = RouterContext(
        app_manifests=[
            {
                "id": "review-app",
                "app_spec": {
                    "spec_version": 1,
                    "types": ["custom:review"],
                    "features": [{"id": "custom:review.check", "status": "implemented", "surfaces": ["tools"]}],
                },
            }
        ]
    )

    prompt = context.render_for_prompt(sections=["widgets"])

    assert "App types: custom:review" in prompt
    assert "Implemented: custom:review.check [tools]" in prompt
    assert "do not create callable tools" in prompt


def test_router_classification_can_have_no_declared_features() -> None:
    context = RouterContext(
        app_manifests=[{"id": "planner", "app_spec": {"spec_version": 1, "types": ["calendar"], "features": []}}]
    )

    prompt = context.render_for_prompt(sections=["widgets"])

    assert "App types: calendar" in prompt
    assert "(none declared)" in prompt
    assert "Implemented:" not in prompt


class PromptCaptured(Exception):
    pass


async def _capture_coding_instruction(monkeypatch, *, existing: dict | None = None) -> tuple[str, dict]:
    captured: list[str] = []

    captured_templates: list[dict | None] = []

    async def runner(_app_id: str, instruction: str, *, manifest_template: dict | None = None, **_kwargs) -> None:
        captured.append(instruction)
        captured_templates.append(copy.deepcopy(manifest_template))
        raise PromptCaptured

    manifest = SimpleNamespace(to_dict=lambda: copy.deepcopy(existing)) if existing else None
    manager = SimpleNamespace(get_manifest=lambda _app_id: manifest)
    workflow = DurableAgentWorkflow(
        workspace_dir="unused",
        run_store=None,
        app_manager=manager,
        graph_db=None,
        llm_config_store=None,
        coding_agent_runner=runner,
        capability_catalog_factory=lambda: SimpleNamespace(render=lambda _role: "approved tools only"),
    )
    monkeypatch.setattr(workflow, "_emit_activity", AsyncMock())
    state = AgentRunState(
        workflow_type="chat",
        workflow_version=workflow.VERSION,
        session_id="session-test",
        phase="stage_code",
        intent=IntentPlan(kind=IntentKind.WIDGET_CREATE, app_id="planner", instruction="Build a planner").to_dict(),
        model_snapshot={},
        data={"runtime_contract": {"app_id": "planner", "schemas": [], "capabilities": []}},
    )

    with pytest.raises(PromptCaptured):
        await workflow._phase_stage_code({"id": "run-test"}, state)

    instruction = captured[0]
    template_text = instruction.split("[REQUIRED MANIFEST V2 TEMPLATE]\n", 1)[1].split("\n\n[", 1)[0]
    assert captured_templates == [json.loads(template_text)]
    return instruction, json.loads(template_text)


@pytest.mark.asyncio
async def test_coding_prompt_uses_shared_catalogue_and_actual_implementation_rules(monkeypatch) -> None:
    reference = {
        "type_ids": ["custom:review"],
        "feature_ids_by_type": {"custom:review": []},
        "type_descriptions_by_id": {"custom:review": "评审实际交付的功能。"},
        "feature_descriptions_by_id": {},
    }
    monkeypatch.setattr(workflow_module, "get_app_type_prompt_reference", lambda _language: reference, raising=False)

    instruction, template = await _capture_coding_instruction(monkeypatch)

    catalogue_text = instruction.split("[APP TYPE STANDARD]\n", 1)[1].split("\n\n[", 1)[0]
    assert json.loads(catalogue_text) == reference
    assert "types" not in json.loads(catalogue_text)
    assert "actually delivered" in instruction
    assert "grant alone" in instruction
    assert "implemented" in instruction and "partial" in instruction and "planned" in instruction
    assert "app_spec" not in template
    assert template["capabilities"] == []
    assert template["schema_refs"] == []


@pytest.mark.asyncio
async def test_coding_template_preserves_declarations_while_replacing_approved_grants(monkeypatch) -> None:
    existing = {
        "manifest_version": 2,
        "id": "planner",
        "title": "Planner",
        "description": "",
        "app_version": "0.1.0",
        "intents": [],
        "schema_refs": ["Event"],
        "capabilities": [{"id": "graph.query", "scope": {"entities": ["Event"]}}],
        "app_spec": copy.deepcopy(APP_SPEC),
    }

    instruction, template = await _capture_coding_instruction(monkeypatch, existing=existing)

    assert template["app_spec"] == APP_SPEC
    assert existing["schema_refs"] == ["Event"]
    assert template["capabilities"] == []
    assert template["schema_refs"] == []
    assert "preserve and adjust" in instruction


def test_coding_system_prompt_allows_declarations_but_keeps_contract_immutable() -> None:
    prompt_path = Path(__file__).parents[2] / "backend" / "agent" / "prompts" / "coding_agent_system.md"
    prompt = prompt_path.read_text(encoding="utf-8")

    assert "app_spec" in prompt
    assert "Keep `manifest_version`, `id`, `schema_refs`, and `capabilities` exactly as provided" in prompt
    assert "not verified" in prompt
