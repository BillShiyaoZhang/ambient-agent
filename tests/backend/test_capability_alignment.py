import json

import pytest

import backend.schema_alignment as alignment_module
from backend.graph_db import GraphDatabase
from backend.schema_alignment import SchemaAlignmentService


class Provider:
    def __init__(self, response):
        self.response = response
        self.messages = None

    async def generate(self, messages, **_kwargs):
        self.messages = messages
        return json.dumps(self.response)


@pytest.mark.asyncio
async def test_schema_alignment_proposes_capabilities_from_the_shared_ontology(tmp_path, monkeypatch):
    provider = Provider(
        {
            "reused_schemas": [
                {
                    "id": "Task",
                    "reason": "Stores tasks",
                    "extended_properties": {},
                    "data_scope": "user_context",
                }
            ],
            "new_schemas": [],
            "capabilities": [
                {"id": "graph.query", "scope": {"entities": ["Task"]}},
                {
                    "id": "graph.mutate",
                    "scope": {"entities": ["Task"], "operations": ["create", "update"]},
                },
            ],
        }
    )
    monkeypatch.setattr(alignment_module, "get_llm_provider", lambda *_args: provider)
    monkeypatch.setattr(alignment_module, "primary_selection", lambda: object())
    monkeypatch.setattr(alignment_module, "selection_ids", lambda _selection: ("provider", "model"))

    result = await SchemaAlignmentService.align_schemas(
        "Create a task editor",
        "task-app",
        GraphDatabase(str(tmp_path / "workspace")),
        approved_plan="Build a task list",
        language="en",
    )

    assert result["capabilities"] == sorted(provider.response["capabilities"], key=lambda grant: grant["id"])
    system_prompt = provider.messages[0]["content"]
    assert "Capability Ontology" in system_prompt
    assert "graph.query" in system_prompt
    assert "file.write" in system_prompt
    assert "Do not invent" in system_prompt


@pytest.mark.asyncio
async def test_schema_alignment_rejects_unknown_capability_output(tmp_path, monkeypatch):
    provider = Provider(
        {"reused_schemas": [], "new_schemas": [], "capabilities": [{"id": "shell.exec", "scope": {}}]}
    )
    monkeypatch.setattr(alignment_module, "get_llm_provider", lambda *_args: provider)
    monkeypatch.setattr(alignment_module, "primary_selection", lambda: object())
    monkeypatch.setattr(alignment_module, "selection_ids", lambda _selection: ("provider", "model"))

    with pytest.raises(Exception, match=r"capability|alignment"):
        await SchemaAlignmentService.align_schemas(
            "Run a command",
            "bad-app",
            GraphDatabase(str(tmp_path / "workspace")),
            language="en",
        )
