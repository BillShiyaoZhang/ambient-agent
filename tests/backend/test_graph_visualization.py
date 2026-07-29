import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient

import backend.main as main_module
from backend.graph_visualization import (
    MAX_KNOWLEDGE_GRAPH_EDGES,
    build_graph_explorer_snapshot,
    project_data_map,
)
from backend.main import get_db
from backend.models import LLMAuditLog


class StubGraphAdapter:
    def __init__(self) -> None:
        self.list_edges_calls = 0

    def list_schemas(self):
        return [
            {
                "id": "Beta",
                "name": "Beta",
                "description": "Beta records",
                "properties": {"rank": "integer"},
                "is_core": False,
                "ontology_id": "ambient-context",
                "ontology_iri": "urn:test:Beta",
                "source": "test",
                "equivalent_to": ["https://example.test/Beta"],
                "subclass_of": "Alpha",
                "abstract": False,
                "data_scope": "user_context",
            },
            {
                "id": "Alpha",
                "name": "Alpha",
                "description": "Alpha records",
                "properties": {"title": "string"},
                "is_core": True,
                "ontology_id": "ambient-context",
                "ontology_iri": "urn:test:Alpha",
                "source": "test",
                "equivalent_to": [],
                "subclass_of": None,
                "abstract": True,
                "data_scope": "user_context",
            },
        ]

    def list_nodes(self, node_type=None):
        assert node_type is None
        return [
            {"id": "record-c", "type": "Beta", "properties": {"rank": 3}},
            {"id": "record-b", "type": "Alpha", "properties": {"title": "Bravo"}},
            {"id": "record-a", "type": "Alpha", "properties": {"title": "Alpha"}},
        ]

    def list_edges(self):
        self.list_edges_calls += 1
        return [
            {
                "from_id": "record-a",
                "to_id": "record-c",
                "type": "EXCLUDED_BY_LIMIT",
                "properties": {},
            },
            {
                "from_id": "record-a",
                "to_id": "record-b",
                "type": "RELATED_TO",
                "properties": {"weight": 1},
            },
            {
                "from_id": "record-a",
                "to_id": "record-b",
                "type": "RELATED_TO",
                "properties": {"weight": 1},
            },
            {
                "from_id": "record-c",
                "to_id": "record-a",
                "type": "ALSO_EXCLUDED",
                "properties": {},
            },
        ]

    def get_edges(self, _node_id):
        raise AssertionError("the explorer must not issue N+1 get_edges calls")


def test_graph_projection_is_stable_deduplicated_and_bounded():
    graph = StubGraphAdapter()

    first = build_graph_explorer_snapshot(graph, record_limit=2)
    second = build_graph_explorer_snapshot(graph, record_limit=2)

    assert first == second
    assert graph.list_edges_calls == 2
    assert [node["id"] for node in first["ontology"]["nodes"]] == ["ontology:Alpha", "ontology:Beta"]
    assert [edge["id"] for edge in first["ontology"]["edges"]] == ["ontology-subclass:Beta:Alpha"]
    assert first["ontology"]["nodes"][0]["details"]["record_count"] == 2
    assert first["ontology"]["nodes"][1]["details"]["record_count"] == 1

    knowledge_graph = first["knowledge_graph"]
    assert [node["id"] for node in knowledge_graph["nodes"]] == ["record-a", "record-b"]
    assert [(edge["source"], edge["target"], edge["kind"]) for edge in knowledge_graph["edges"]] == [
        ("record-a", "record-b", "RELATED_TO")
    ]
    assert knowledge_graph["metadata"]["counts"] == {
        "nodes": {"total": 3, "returned": 2},
        "edges": {"total": 3, "returned": 1},
    }
    assert knowledge_graph["metadata"]["truncated"] is True


def test_ontology_projection_has_a_hard_response_limit():
    graph = StubGraphAdapter()
    base_schema = graph.list_schemas()[0]
    graph.list_schemas = lambda: [
        {
            **base_schema,
            "id": f"Entity{index:04d}",
            "name": f"Entity {index:04d}",
            "subclass_of": None,
        }
        for index in range(501)
    ]

    snapshot = build_graph_explorer_snapshot(graph, record_limit=500)

    assert snapshot["ontology"]["metadata"]["counts"]["nodes"] == {
        "total": 501,
        "returned": 500,
    }
    assert snapshot["ontology"]["metadata"]["truncated"] is True


def test_knowledge_graph_edge_ids_do_not_collide_when_segments_contain_colons():
    graph = StubGraphAdapter()
    graph.list_nodes = lambda node_type=None: [
        {"id": "a", "type": "Alpha", "properties": {}},
        {"id": "a:b", "type": "Alpha", "properties": {}},
        {"id": "d", "type": "Alpha", "properties": {}},
    ]
    graph.list_edges = lambda: [
        {"from_id": "a:b", "to_id": "d", "type": "c", "properties": {}},
        {"from_id": "a", "to_id": "d", "type": "b:c", "properties": {}},
    ]

    edges = build_graph_explorer_snapshot(graph, record_limit=3)["knowledge_graph"]["edges"]

    assert len(edges) == 2
    assert len({edge["id"] for edge in edges}) == 2


def test_long_graph_ids_with_the_same_display_prefix_remain_distinct():
    graph = StubGraphAdapter()
    shared_prefix = "record-" + ("x" * 220)
    first_id = f"{shared_prefix}-a"
    second_id = f"{shared_prefix}-b"
    graph.list_nodes = lambda node_type=None: [
        {"id": first_id, "type": "Alpha", "properties": {"title": "First"}},
        {"id": second_id, "type": "Alpha", "properties": {"title": "Second"}},
    ]
    graph.list_edges = lambda: [
        {
            "from_id": first_id,
            "to_id": second_id,
            "type": "RELATED_TO",
            "properties": {},
        }
    ]

    knowledge_graph = build_graph_explorer_snapshot(graph, record_limit=2)["knowledge_graph"]

    assert [node["id"] for node in knowledge_graph["nodes"]] == [first_id, second_id]
    assert [(edge["source"], edge["target"], edge["kind"]) for edge in knowledge_graph["edges"]] == [
        (first_id, second_id, "RELATED_TO")
    ]


def test_graph_identity_fields_preserve_significant_whitespace():
    graph = StubGraphAdapter()
    graph.list_nodes = lambda node_type=None: [
        {"id": "x", "type": "Alpha", "properties": {"title": "Plain"}},
        {"id": " x", "type": "Alpha", "properties": {"title": "Prefixed"}},
    ]
    graph.list_edges = lambda: [
        {
            "from_id": "x",
            "to_id": " x",
            "type": " RELATED_TO",
            "properties": {},
        }
    ]

    knowledge_graph = build_graph_explorer_snapshot(graph, record_limit=2)["knowledge_graph"]

    assert {node["id"] for node in knowledge_graph["nodes"]} == {"x", " x"}
    assert [(edge["source"], edge["target"], edge["kind"]) for edge in knowledge_graph["edges"]] == [
        ("x", " x", " RELATED_TO")
    ]
    assert knowledge_graph["edges"][0]["source"] != knowledge_graph["edges"][0]["target"]


def test_knowledge_graph_has_a_hard_edge_response_limit():
    graph = StubGraphAdapter()
    graph.list_nodes = lambda node_type=None: [
        {"id": "source", "type": "Alpha", "properties": {}},
        {"id": "target", "type": "Alpha", "properties": {}},
    ]
    graph.list_edges = lambda: [
        {
            "from_id": "source",
            "to_id": "target",
            "type": f"RELATION_{index:04d}",
            "properties": {},
        }
        for index in range(MAX_KNOWLEDGE_GRAPH_EDGES + 1)
    ]

    knowledge_graph = build_graph_explorer_snapshot(graph, record_limit=2)["knowledge_graph"]

    assert len(knowledge_graph["edges"]) == MAX_KNOWLEDGE_GRAPH_EDGES
    assert knowledge_graph["metadata"]["counts"]["edges"] == {
        "total": MAX_KNOWLEDGE_GRAPH_EDGES + 1,
        "returned": MAX_KNOWLEDGE_GRAPH_EDGES,
    }
    assert knowledge_graph["metadata"]["edge_limit"] == MAX_KNOWLEDGE_GRAPH_EDGES
    assert knowledge_graph["metadata"]["truncated"] is True


def test_graph_detail_values_are_bounded_and_report_exact_truncation():
    graph = StubGraphAdapter()
    graph.list_schemas = lambda: [
        {
            "id": "Adversarial",
            "name": "Adversarial",
            "description": "Bounded detail fixture",
            "properties": {"many": list(range(40))},
            "equivalent_to": [f"urn:test:{index:02d}" for index in range(40)],
            "is_core": False,
            "subclass_of": None,
            "abstract": False,
            "data_scope": "user_context",
        }
    ]
    graph.list_nodes = lambda node_type=None: [
        {
            "id": "record-string",
            "type": "Adversarial",
            "properties": {"title": "String", "huge": "s" * 1_000},
        },
        {
            "id": "record-collection",
            "type": "Adversarial",
            "properties": {"title": "Collection", "many": list(range(40))},
        },
        {
            "id": "record-depth",
            "type": "Adversarial",
            "properties": {
                "title": "Depth",
                "level1": {
                    "level2": {
                        "level3": {
                            "level4": {"secret": "must not escape the depth bound"},
                        }
                    }
                },
            },
        },
        {
            "id": "record-budget",
            "type": "Adversarial",
            "properties": {f"field-{index:02d}": "b" * 512 for index in range(32)},
        },
    ]
    graph.list_edges = lambda: [
        {
            "from_id": "record-string",
            "to_id": "record-collection",
            "type": "RELATED_TO",
            "properties": {"huge": "e" * 1_000},
        }
    ]

    snapshot = build_graph_explorer_snapshot(graph, record_limit=4)
    repeated = build_graph_explorer_snapshot(graph, record_limit=4)

    assert snapshot == repeated
    ontology = snapshot["ontology"]
    assert ontology["nodes"][0]["details"]["properties"]["many"] == list(range(32))
    assert ontology["nodes"][0]["details"]["equivalent_to"] == [f"urn:test:{index:02d}" for index in range(32)]
    assert ontology["metadata"]["truncated"] is False
    assert ontology["metadata"]["detail_truncation"] == {
        "truncated": True,
        "values_truncated": 2,
        "string_values_truncated": 0,
        "string_characters_omitted": 0,
        "collections_truncated": 2,
        "collection_items_omitted": 16,
        "depth_cutoffs": 0,
        "values_exceeding_budget": 0,
        "non_finite_numbers_replaced": 0,
        "limits": {
            "max_string_characters": 512,
            "max_collection_items": 32,
            "max_depth": 4,
            "max_value_bytes": 8_192,
        },
    }

    knowledge_graph = snapshot["knowledge_graph"]
    nodes = {node["id"]: node for node in knowledge_graph["nodes"]}
    bounded_string = nodes["record-string"]["details"]["properties"]["huge"]
    assert len(bounded_string) == 512
    assert bounded_string.endswith("…")
    assert nodes["record-collection"]["details"]["properties"]["many"] == list(range(32))
    assert (
        nodes["record-depth"]["details"]["properties"]["level1"]["level2"]["level3"]["level4"]
        == "[truncated: maximum detail depth]"
    )
    for item in [*knowledge_graph["nodes"], *knowledge_graph["edges"]]:
        properties = item["details"]["properties"]
        encoded = json.dumps(properties, ensure_ascii=False, sort_keys=True).encode("utf-8")
        assert len(encoded) <= 8_192

    assert knowledge_graph["metadata"]["truncated"] is False
    assert knowledge_graph["metadata"]["detail_truncation"] == {
        "truncated": True,
        "values_truncated": 5,
        "string_values_truncated": 2,
        "string_characters_omitted": 978,
        "collections_truncated": 1,
        "collection_items_omitted": 8,
        "depth_cutoffs": 1,
        "values_exceeding_budget": 1,
        "non_finite_numbers_replaced": 0,
        "limits": {
            "max_string_characters": 512,
            "max_collection_items": 32,
            "max_depth": 4,
            "max_value_bytes": 8_192,
        },
    }


def test_graph_detail_values_replace_non_finite_numbers_with_json_safe_markers():
    graph = StubGraphAdapter()
    graph.list_nodes = lambda node_type=None: [
        {
            "id": "non-finite",
            "type": "Alpha",
            "properties": {
                "nan": float("nan"),
                "positive": float("inf"),
                "negative": float("-inf"),
            },
        }
    ]
    graph.list_edges = lambda: []

    knowledge_graph = build_graph_explorer_snapshot(graph, record_limit=1)["knowledge_graph"]
    properties = knowledge_graph["nodes"][0]["details"]["properties"]

    assert properties == {
        "nan": "[non-finite number: NaN]",
        "negative": "[non-finite number: -Infinity]",
        "positive": "[non-finite number: Infinity]",
    }
    json.dumps(knowledge_graph, allow_nan=False)
    assert knowledge_graph["metadata"]["detail_truncation"]["values_truncated"] == 1
    assert knowledge_graph["metadata"]["detail_truncation"]["non_finite_numbers_replaced"] == 3


def test_graph_detail_depth_bound_handles_cyclic_property_values():
    graph = StubGraphAdapter()
    cyclic_properties = {}
    cyclic_properties["self"] = cyclic_properties
    graph.list_nodes = lambda node_type=None: [
        {"id": "cyclic-record", "type": "Alpha", "properties": cyclic_properties}
    ]
    graph.list_edges = lambda: []

    first = build_graph_explorer_snapshot(graph, record_limit=1)
    second = build_graph_explorer_snapshot(graph, record_limit=1)

    assert first == second
    knowledge_graph = first["knowledge_graph"]
    assert knowledge_graph["nodes"][0]["id"] == "cyclic-record"
    assert knowledge_graph["metadata"]["detail_truncation"]["depth_cutoffs"] == 1
    assert knowledge_graph["metadata"]["detail_truncation"]["values_truncated"] == 1


def _walk_keys(value):
    if isinstance(value, dict):
        for key, nested in value.items():
            yield key
            yield from _walk_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_keys(nested)


def test_data_map_has_three_semantics_without_raw_audit_or_manifest_payloads():
    logs = [
        LLMAuditLog(
            timestamp=datetime(2026, 7, 29, 12, 0, tzinfo=UTC),
            provider="openai",
            model="DO-NOT-LEAK-MODEL",
            prompt="DO-NOT-LEAK-PROMPT",
            response="DO-NOT-LEAK-RESPONSE",
            stage="route",
            error="DO-NOT-LEAK-ERROR",
            usage={"secret": "DO-NOT-LEAK-USAGE"},
            prompt_hash="DO-NOT-LEAK-PROMPT-HASH",
            tool_schema_hash="DO-NOT-LEAK-TOOL-HASH",
            artifact_hashes={"secret": "DO-NOT-LEAK-ARTIFACT-HASH"},
        ),
        LLMAuditLog(
            timestamp=datetime(2026, 7, 29, 11, 0, tzinfo=UTC),
            provider="openai",
            model="DO-NOT-LEAK-MODEL",
            prompt="DO-NOT-LEAK-PROMPT-2",
            response="DO-NOT-LEAK-RESPONSE-2",
            stage="route",
        ),
    ]
    apps = [
        {
            "id": "planner",
            "title": "Planner",
            "description": "DO-NOT-LEAK-MANIFEST-DESCRIPTION",
            "schema_refs": ["Task"],
            "capabilities": [
                {
                    "id": "network.request",
                    "scope": {
                        "sources": {
                            "private": {
                                "base_url": "https://DO-NOT-LEAK-SCOPE.example",
                            }
                        }
                    },
                }
            ],
        }
    ]

    result = project_data_map(logs, apps)
    reversed_result = project_data_map(list(reversed(logs)), list(reversed(apps)))

    assert result == reversed_result
    assert {edge["status"] for edge in result["edges"]} == {"observed", "declared", "unknown"}
    observed = [edge for edge in result["edges"] if edge["status"] == "observed"]
    assert len(observed) == 1
    assert observed[0]["details"] == {
        "semantics": "observed",
        "source_category": "local_runtime_context",
        "stage": "route",
        "target_category": "provider:openai",
        "count": 2,
        "first_seen": "2026-07-29T11:00:00+00:00",
        "last_seen": "2026-07-29T12:00:00+00:00",
    }
    assert result["metadata"]["time_window"] == {
        "start": "2026-07-29T11:00:00+00:00",
        "end": "2026-07-29T12:00:00+00:00",
        "basis": "retained_audit_metadata",
    }
    assert result["metadata"]["coverage"]["evidence_count"] == 2
    assert result["metadata"]["coverage"]["declared_app_count"] == 1
    assert result["metadata"]["coverage"]["observed_stages"] == ["route"]
    assert result["metadata"]["blind_spots"]
    assert result["metadata"]["limitations"]

    serialized = json.dumps(result, ensure_ascii=False, sort_keys=True)
    for secret in (
        "DO-NOT-LEAK-MODEL",
        "DO-NOT-LEAK-PROMPT",
        "DO-NOT-LEAK-RESPONSE",
        "DO-NOT-LEAK-ERROR",
        "DO-NOT-LEAK-USAGE",
        "DO-NOT-LEAK-PROMPT-HASH",
        "DO-NOT-LEAK-TOOL-HASH",
        "DO-NOT-LEAK-ARTIFACT-HASH",
        "DO-NOT-LEAK-MANIFEST-DESCRIPTION",
        "DO-NOT-LEAK-SCOPE",
    ):
        assert secret not in serialized
    forbidden_keys = {
        "prompt",
        "response",
        "model",
        "error",
        "usage",
        "prompt_hash",
        "tool_schema_hash",
        "artifact_hashes",
        "properties",
        "scope",
    }
    assert forbidden_keys.isdisjoint(_walk_keys(result))


def test_data_map_counts_all_timestamp_less_audit_entries():
    logs = [
        {"provider": "local-provider", "stage": "route", "timestamp": None},
        {"provider": "local-provider", "stage": "route", "timestamp": None},
        {"provider": "local-provider", "stage": "route", "timestamp": "not-a-timestamp"},
    ]

    result = project_data_map(logs, [])

    observed = [edge for edge in result["edges"] if edge["status"] == "observed"]
    assert len(observed) == 1
    assert observed[0]["details"] == {
        "semantics": "observed",
        "source_category": "local_runtime_context",
        "stage": "route",
        "target_category": "provider:local-provider",
        "count": 3,
        "first_seen": None,
        "last_seen": None,
    }
    assert result["metadata"]["time_window"] == {
        "start": None,
        "end": None,
        "basis": "retained_audit_metadata",
    }
    assert result["metadata"]["coverage"]["evidence_count"] == 3


class StubStorage:
    def get_audit_logs(self):
        return [
            LLMAuditLog(
                provider="test-provider",
                model="private-model",
                prompt="private prompt",
                response="private response",
                stage="chat",
            )
        ]


class StubAppManager:
    def list_apps(self):
        return []


def test_graph_and_data_map_api_are_no_store_and_validate_record_limit(monkeypatch):
    graph = StubGraphAdapter()
    storage = StubStorage()

    def override_get_db():
        yield storage

    monkeypatch.setattr(main_module, "graph_db", graph)
    monkeypatch.setattr(main_module, "app_manager", StubAppManager())
    main_module.app.dependency_overrides[get_db] = override_get_db
    client = TestClient(main_module.app, client=("127.0.0.1", 50_000))
    try:
        graph_response = client.get("/api/graph/explorer?record_limit=2")
        data_map_response = client.get("/api/data-map")
        too_small = client.get("/api/graph/explorer?record_limit=0")
        too_large = client.get("/api/graph/explorer?record_limit=501")
    finally:
        main_module.app.dependency_overrides.clear()

    assert graph_response.status_code == 200
    assert graph_response.headers["cache-control"] == "no-store"
    assert {"ontology", "knowledge_graph"} <= set(graph_response.json())
    assert data_map_response.status_code == 200
    assert data_map_response.headers["cache-control"] == "no-store"
    assert too_small.status_code == 422
    assert too_large.status_code == 422


def test_graph_visualization_endpoints_reject_untrusted_browser_origins(monkeypatch):
    graph = StubGraphAdapter()
    storage = StubStorage()

    def override_get_db():
        yield storage

    monkeypatch.setenv("AMBIENT_FRONTEND_ORIGINS", "http://localhost:5173")
    monkeypatch.setattr(main_module, "graph_db", graph)
    monkeypatch.setattr(main_module, "app_manager", StubAppManager())
    main_module.app.dependency_overrides[get_db] = override_get_db
    client = TestClient(main_module.app, client=("127.0.0.1", 50_000))
    remote_client = TestClient(main_module.app, client=("192.0.2.10", 50_000))
    try:
        denied_graph = client.get(
            "/api/graph/explorer",
            headers={"origin": "https://attacker.example"},
        )
        denied_map = client.get(
            "/api/data-map",
            headers={"origin": "https://attacker.example"},
        )
        trusted_graph = client.get(
            "/api/graph/explorer",
            headers={"origin": "http://localhost:5173"},
        )
        trusted_map = client.get(
            "/api/data-map",
            headers={"origin": "http://localhost:5173"},
        )
        denied_originless_remote = remote_client.get("/api/graph/explorer")
        denied_spoofed_origin_remote = remote_client.get(
            "/api/graph/explorer",
            headers={"origin": "http://localhost:5173"},
        )
        monkeypatch.setenv("AMBIENT_TRUSTED_HOST_PEERS", "192.0.2.0/24")
        explicitly_trusted_remote = remote_client.get(
            "/api/graph/explorer",
            headers={"origin": "http://localhost:5173"},
        )
    finally:
        main_module.app.dependency_overrides.clear()

    for response in (
        denied_graph,
        denied_map,
        denied_originless_remote,
        denied_spoofed_origin_remote,
    ):
        assert response.status_code == 403
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {
            "detail": {
                "code": "graph_visualization_origin_denied",
                "message": "Graph visualization is only available to the trusted Host",
            }
        }
    assert trusted_graph.status_code == 200
    assert trusted_map.status_code == 200
    assert explicitly_trusted_remote.status_code == 200


def test_raw_audit_log_endpoint_uses_the_same_trusted_host_boundary():
    storage = StubStorage()

    def override_get_db():
        yield storage

    main_module.app.dependency_overrides[get_db] = override_get_db
    local_client = TestClient(main_module.app, client=("127.0.0.1", 50_000))
    remote_client = TestClient(main_module.app, client=("192.0.2.10", 50_000))
    try:
        denied_browser = local_client.get(
            "/api/audit-logs",
            headers={"origin": "https://attacker.example"},
        )
        denied_remote = remote_client.get("/api/audit-logs")
        trusted_browser = local_client.get(
            "/api/audit-logs",
            headers={"origin": "http://localhost:5173"},
        )
        trusted_native = local_client.get("/api/audit-logs")
        denied_spoofed_origin_remote = remote_client.get(
            "/api/audit-logs",
            headers={"origin": "http://localhost:5173"},
        )
    finally:
        main_module.app.dependency_overrides.clear()

    for response in (denied_browser, denied_remote, denied_spoofed_origin_remote):
        assert response.status_code == 403
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {
            "detail": {
                "code": "audit_log_origin_denied",
                "message": "Raw Audit Logs are only available to the trusted Host",
            }
        }
        assert "private prompt" not in response.text
    for response in (trusted_browser, trusted_native):
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert "private prompt" in response.text


def test_graph_explorer_returns_a_stable_unavailable_error(monkeypatch):
    class BrokenGraph:
        def list_schemas(self):
            raise RuntimeError("private database details")

    monkeypatch.setattr(main_module, "graph_db", BrokenGraph())

    response = TestClient(
        main_module.app,
        client=("127.0.0.1", 50_000),
    ).get("/api/graph/explorer")

    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "detail": {
            "code": "graph_explorer_unavailable",
            "message": "Graph explorer snapshot is temporarily unavailable",
        }
    }
    assert "private database details" not in response.text
