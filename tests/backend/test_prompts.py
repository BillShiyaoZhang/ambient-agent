import json
import re
from copy import deepcopy

import pytest

from backend.agent.prompts.manager import PromptManager
from backend.app_types import AppSpecificationError, validate_app_spec
from backend.coding_agent_repair import app_spec_declaration_rules


def test_prompt_manager_initialization():
    pm = PromptManager()
    assert pm.prompts_dir.exists()


def test_router_prompt_rendering():
    pm = PromptManager()
    existing_apps = [{"id": "todo-app-1234", "title": "My Todo App"}, {"id": "clock-app-5678", "title": "My Clock App"}]
    prompt = pm.get_prompt("router.md", existing_apps=existing_apps)

    assert "todo-app-1234" in prompt
    assert "My Clock App" in prompt
    assert "is_coding" in prompt


def test_router_prompt_rendering_empty():
    pm = PromptManager()
    prompt = pm.get_prompt("router.md", existing_apps=[])
    assert "(None)" in prompt


def test_agent_system_prompt_inclusion():
    pm = PromptManager()
    prompt = pm.get_prompt("agent_system.md")

    assert "You are Ambient Agent" in prompt


def test_opencode_system_prompt_inclusion():
    pm = PromptManager()
    prompt = pm.get_prompt(
        "coding_agent_system.md",
        app_id="weather-app",
        target_dir="/some/path",
        instruction="make weather blue",
        app_spec_rules=app_spec_declaration_rules(),
    )

    assert "weather-app" in prompt
    assert "/some/path" in prompt
    assert "make weather blue" in prompt
    assert "must survive Runtime suspension" in prompt
    assert "Never keep user-authored drafts only in React hook state" in prompt
    assert "ambient.lifecycle.onBeforeSuspend(handler)" in prompt
    assert "awaits its `ambient.storage.set(...)`" in prompt
    assert "Gate write-through until `storage.get` hydration finishes" in prompt
    assert "List` renders its `items` prop as text rows" in prompt
    assert "ignores child elements" in prompt
    assert "map the records to `<${Row}>` children inside `<${Column}>`" in prompt
    assert "code `file_not_found`" in prompt
    assert "never turn every read error into empty data" in prompt
    assert app_spec_declaration_rules() in prompt


def _rendered_app_spec_example():
    prompt = PromptManager().get_prompt(
        "coding_agent_system.md",
        app_id="weather-app",
        target_dir="staging",
        instruction="Create an approved draft",
        app_spec_rules=app_spec_declaration_rules(),
    )
    examples = [json.loads(text) for text in re.findall(r"```json\n(.*?)\n```", prompt, re.S)]
    assert len(examples) == 1
    example = examples[0]
    assert validate_app_spec(example).to_dict() == example
    return example


@pytest.mark.parametrize(
    "change",
    [
        {"spec_version": True},
        {"spec_version": 1.0},
        {"types": [{"id": "custom:weather"}]},
        {"types": []},
        {"types": ["custom:weather", "custom:weather"]},
        {"types": ["custom:Weather"]},
        {"types": ["custom:other"]},
        {"types": ["calendar.unknown"]},
        {"features": ["custom:weather.forecast"]},
        {"features": [{"id": "custom:weather.forecast", "status": "partial", "surfaces": []}]},
        {"features": [{"id": "custom:weather.forecast", "status": "planned", "surfaces": ["ui"]}]},
        {"features": [{"id": "custom:weather.forecast", "status": "partial", "surfaces": ["network"]}]},
        {"features": [{"id": "custom:weather.forecast", "status": "partial", "surfaces": ["ui", "ui"]}]},
        {"features": [{"id": "custom:weather.forecast", "status": "partial", "surfaces": ["ui"], "notes": {}}]},
        {"features": [{"id": "custom:weather.forecast", "status": "partial", "surfaces": ["ui"], "title": "Weather"}]},
        {"title": "Weather catalog metadata"},
    ],
)
def test_rendered_manifest_example_remains_strict_against_shape_and_semantic_mutations(change):
    example = deepcopy(_rendered_app_spec_example())
    example.update(change)
    with pytest.raises(AppSpecificationError):
        validate_app_spec(example)
