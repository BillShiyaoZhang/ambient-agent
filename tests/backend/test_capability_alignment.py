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


class SequenceProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def generate(self, messages, **_kwargs):
        self.calls.append(messages)
        return json.dumps(self.responses.pop(0))


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


@pytest.mark.asyncio
async def test_schema_alignment_repairs_invalid_capability_scope_with_validation_feedback(tmp_path, monkeypatch):
    base = {
        "reused_schemas": [
            {
                "id": "Place",
                "reason": "Weather location",
                "extended_properties": {},
                "data_scope": "user_context",
            }
        ],
        "new_schemas": [],
    }
    provider = SequenceProvider(
        [
            {
                **base,
                "capabilities": [
                    {"id": "network.request", "scope": {"sources": ["weather-api"]}},
                ],
            },
            {
                **base,
                "capabilities": [
                    {
                        "id": "network.request",
                        "scope": {
                            "sources": {
                                "weather-api": {
                                    "base_url": "https://api.example.com",
                                    "paths": ["/v1/forecast"],
                                    "methods": ["GET"],
                                    "response_limit": 1_048_576,
                                }
                            }
                        },
                    }
                ],
            },
        ]
    )
    monkeypatch.setattr(alignment_module, "get_llm_provider", lambda *_args: provider)
    monkeypatch.setattr(alignment_module, "primary_selection", lambda: object())
    monkeypatch.setattr(alignment_module, "selection_ids", lambda _selection: ("provider", "model"))

    proposal = await SchemaAlignmentService.align_schemas(
        "Build weather",
        "weather-app",
        GraphDatabase(str(tmp_path / "workspace")),
        language="en",
    )

    assert len(provider.calls) == 2
    correction = provider.calls[1][-1]["content"]
    assert "network.request sources must be a non-empty object" in correction
    assert "Do not broaden the requested capabilities" in correction
    assert proposal["capabilities"][0]["scope"]["sources"]["weather-api"]["paths"] == ["/v1/forecast"]
