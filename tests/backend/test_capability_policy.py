import pytest

from backend.capabilities.policy import CapabilityAuthorizer, CapabilityDenied


class Manifest:
    def __init__(self, capabilities, revision="2:1.0.0", grants_digest="digest-v1"):
        self.capabilities = capabilities
        self.revision = revision
        self.grants_digest = grants_digest


@pytest.fixture
def authorizer():
    manifests = {
        "planner": Manifest(
            [
                {"id": "graph.query", "scope": {"entities": ["Task", "Event"]}},
                {
                    "id": "graph.mutate",
                    "scope": {
                        "entities": ["Task"],
                        "operations": ["create", "update"],
                        "edge_types": ["RELATES_TO"],
                    },
                },
                {
                    "id": "network.request",
                    "scope": {
                        "sources": {
                            "forecast": {
                                "base_url": "https://api.example.com",
                                "paths": ["/v1/forecast"],
                                "methods": ["GET"],
                                "response_limit": 4096,
                            }
                        }
                    },
                },
                {"id": "file.read", "scope": {"paths": ["drafts/**", "settings.json"]}},
                {"id": "file.write", "scope": {"paths": ["drafts/**"], "max_bytes": 1024}},
                {
                    "id": "capability.invoke",
                    "scope": {"catalog_ids": ["mcp:calendar:calendar"], "actions": ["list-events"]},
                },
            ]
        )
    }
    node_types = {"task-1": "Task", "event-1": "Event"}
    return CapabilityAuthorizer(
        manifest_loader=manifests.get,
        node_type_loader=node_types.get,
    )


def test_graph_query_requires_explicit_approved_root_and_include_types(authorizer):
    authorizer.authorize_graph_query(
        "planner",
        {"type": "Task", "include": [{"relation": "RELATES_TO", "target_type": "Event"}]},
        manifest_revision="2:1.0.0",
    )

    with pytest.raises(CapabilityDenied) as missing_type:
        authorizer.authorize_graph_query("planner", {})
    assert missing_type.value.code == "capability_scope_denied"

    with pytest.raises(CapabilityDenied):
        authorizer.authorize_graph_query("planner", {"type": "Document"})

    with pytest.raises(CapabilityDenied):
        authorizer.authorize_graph_query(
            "planner",
            {"type": "Task", "include": [{"relation": "RELATES_TO", "target_type": "Document"}]},
        )


def test_graph_mutation_resolves_existing_node_types_before_authorizing(authorizer):
    authorizer.authorize_graph_mutation(
        "planner",
        [
            {"action": "create_node", "id": "task-2", "type": "Task", "properties": {}},
            {"action": "update_node_property", "id": "task-1", "properties": {"status": "done"}},
        ],
    )

    with pytest.raises(CapabilityDenied):
        authorizer.authorize_graph_mutation(
            "planner",
            [{"action": "update_node_property", "id": "event-1", "properties": {"title": "x"}}],
        )

    with pytest.raises(CapabilityDenied):
        authorizer.authorize_graph_mutation(
            "planner",
            [{"action": "delete_node", "id": "task-1"}],
        )


def test_network_files_and_installed_capabilities_use_exact_scope(authorizer):
    source = authorizer.authorize_network_request(
        "planner", "forecast", path="/v1/forecast", method="GET", manifest_revision="2:1.0.0"
    )
    assert source["base_url"] == "https://api.example.com"
    authorizer.authorize_file("planner", "read", "settings.json")
    authorizer.authorize_file("planner", "write", "drafts/today.md", size=100)
    authorizer.authorize_invocation("planner", "mcp:calendar:calendar", "list-events")

    for operation in (
        lambda: authorizer.authorize_network_request("planner", "forecast", path="/admin", method="GET"),
        lambda: authorizer.authorize_file("planner", "write", "settings.json", size=10),
        lambda: authorizer.authorize_file("planner", "write", "drafts/large.md", size=2048),
        lambda: authorizer.authorize_invocation("planner", "mcp:calendar:calendar", "delete-event"),
    ):
        with pytest.raises(CapabilityDenied):
            operation()


def test_missing_manifest_grant_and_stale_revision_are_denied(authorizer):
    with pytest.raises(CapabilityDenied) as missing:
        authorizer.authorize_file("missing-app", "read", "x.txt")
    assert missing.value.code == "app_manifest_unavailable"

    with pytest.raises(CapabilityDenied) as stale:
        authorizer.authorize_graph_query("planner", {"type": "Task"}, manifest_revision="2:0.9.0")
    assert stale.value.code == "manifest_revision_stale"

    with pytest.raises(CapabilityDenied) as stale_digest:
        authorizer.authorize_graph_query("planner", {"type": "Task"}, grants_digest="digest-v0")
    assert stale_digest.value.code == "grants_digest_stale"
