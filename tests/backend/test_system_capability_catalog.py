import pytest

from backend.capabilities.catalog import AgentRole, SystemCapabilityCatalog, SYSTEM_CAPABILITY_CATALOG_VERSION
from backend.capabilities.ontology import capability_category_ids


def test_catalog_is_structured_versioned_and_uses_the_capability_ontology():
    catalog = SystemCapabilityCatalog.build()
    payload = catalog.project(AgentRole.SCHEMA_ALIGNMENT)

    assert payload["catalog_version"] == SYSTEM_CAPABILITY_CATALOG_VERSION
    assert payload["capability_ontology_version"] == 1
    assert tuple(item["id"] for item in payload["widget_runtime"]["capability_categories"]) == capability_category_ids()
    assert payload["context_graph"]["ontology_id"] == "ambient-context"
    assert "widget_runtime" in payload

    categories = {item["id"]: item for item in payload["widget_runtime"]["capability_categories"]}
    graph_mutate = categories["graph.mutate"]["scope_contract"]
    assert graph_mutate["optional"] == ["edge_types"]
    assert graph_mutate["fields"]["edge_types"]["empty_means"] == "no edge authority"
    network_sources = categories["network.request"]["scope_contract"]
    assert network_sources["fields"]["sources"]["type"] == "object"
    assert network_sources["example"]["sources"]["weather-api"]["base_url"] == "https://api.example.com"
    assert network_sources["example"]["sources"]["weather-api"]["paths"] == ["/v1/forecast"]
    graph_sdk = payload["widget_runtime"]["sdk_contracts"]["graph.mutate"]
    assert graph_sdk["actions"]["create_node"]["operation"] == "create"
    assert graph_sdk["actions"]["create_node"]["example"]["action"] == "create_node"
    assert graph_sdk["forbidden_shorthand"]["action"] == ["create", "update", "delete"]


def test_role_projection_uses_least_information_and_rendering_is_deterministic():
    catalog = SystemCapabilityCatalog.build()
    router = catalog.project(AgentRole.INTENT_ROUTER)
    coding = catalog.project(AgentRole.CODING_AGENT)

    assert "model_tools" not in router
    assert "forbidden_apis" not in router["widget_runtime"]
    assert "forbidden_apis" in coding["widget_runtime"]
    assert catalog.render(AgentRole.CODING_AGENT) == catalog.render(AgentRole.CODING_AGENT)
    rendered = catalog.render(AgentRole.CODING_AGENT)
    assert f"[SYSTEM CAPABILITY CATALOG v{SYSTEM_CAPABILITY_CATALOG_VERSION}]" in rendered
    assert "graph.query" in rendered
    assert "create_node" in rendered
    assert "secret" not in rendered.lower()


def test_runtime_sources_are_sanitized_and_projected_by_role():
    catalog = SystemCapabilityCatalog.build(
        installed_capabilities=[
            {
                "catalog_id": "mcp:calendar:calendar",
                "title": "Calendar",
                "status": "ready",
                "actions": [
                    {
                        "id": "list-events",
                        "title": "List events",
                        "input_schema": {
                            "type": "object",
                            "properties": {"day": {"type": "string"}, "api_key": {"type": "string"}},
                        },
                    }
                ],
            }
        ],
        installed_skills=[
            {
                "catalog_id": "agent-skill:ambient-agent:daily-planning",
                "name": "daily-planning",
                "title": "Daily Planning",
                "description": "Use for daily planning.",
                "version": "1.0.0",
                "provider": "Ambient Agent",
                "tags": ["planning"],
                "ontology_refs": ["Task", "Event"],
                "enabled": True,
                "available": True,
                "digest": "sha256:abc",
                "trust": "bundled",
                "instructions": "must not enter the global catalog",
            }
        ],
        model_tools=[
            {"name": "query_graph", "effect": "read", "scopes": ["workspace:read"]},
            {"name": "mutate_graph", "effect": "write", "scopes": ["workspace:write"]},
        ],
        coding_agents=[{"id": "codex", "name": "Codex", "available": True, "auth_state": "signed_in"}],
    )

    router = catalog.project(AgentRole.INTENT_ROUTER)
    converse = catalog.project(AgentRole.CONVERSE)
    alignment = catalog.project(AgentRole.SCHEMA_ALIGNMENT)
    coding = catalog.project(AgentRole.CODING_AGENT)

    assert router["installed_capabilities"][0]["action_ids"] == ["list-events"]
    assert router["installed_skills"] == [
        {
            "catalog_id": "agent-skill:ambient-agent:daily-planning",
            "name": "daily-planning",
            "title": "Daily Planning",
            "description": "Use for daily planning.",
            "version": "1.0.0",
            "tags": ["planning"],
            "ontology_refs": ["Event", "Task"],
            "enabled": True,
            "available": True,
            "surfaces": ["agent_context"],
        }
    ]
    assert router["coding_agents"][0]["artifact_policy"]["manifest_version"] == 2
    assert [item["name"] for item in converse["model_tools"]] == ["query_graph"]
    assert "api_key" not in alignment["installed_capabilities"][0]["actions"][0]["input_schema"]["properties"]
    assert "installed_capabilities" not in coding
    assert "installed_skills" not in coding
    assert "must not enter the global catalog" not in catalog.render(AgentRole.CONVERSE)

    catalog.validate_grants(
        [
            {"id": "graph.query", "scope": {"entities": ["Task"]}},
            {
                "id": "capability.invoke",
                "scope": {"catalog_ids": ["mcp:calendar:calendar"], "actions": ["list-events"]},
            },
        ],
        graph_entity_ids={"Task"},
    )

    with pytest.raises(ValueError, match="delete-event"):
        catalog.validate_grants(
            [
                {
                    "id": "capability.invoke",
                    "scope": {"catalog_ids": ["mcp:calendar:calendar"], "actions": ["delete-event"]},
                }
            ]
        )
