import pytest

from backend.capabilities.models import CapabilityGrant, RuntimeContract, grants_digest, normalize_grants
from backend.capabilities.ontology import CAPABILITY_ONTOLOGY, capability_category_ids


EXPECTED_CATEGORIES = {
    "graph.query",
    "graph.mutate",
    "network.request",
    "file.read",
    "file.write",
    "file.delete",
    "capability.invoke",
}


def test_capability_ontology_exposes_the_stable_widget_categories():
    assert capability_category_ids() == tuple(sorted(EXPECTED_CATEGORIES))
    assert set(CAPABILITY_ONTOLOGY) == EXPECTED_CATEGORIES
    assert CAPABILITY_ONTOLOGY["graph.query"].sdk_methods == ("graph.subscribe",)
    assert CAPABILITY_ONTOLOGY["capability.invoke"].approval_required is True


def test_grants_are_canonical_and_have_a_deterministic_digest():
    left = normalize_grants(
        [
            {"id": "graph.query", "scope": {"entities": ["Event", "Task", "Task"]}},
            {
                "id": "graph.mutate",
                "scope": {
                    "entities": ["Task"],
                    "operations": ["update", "create"],
                    "edge_types": ["BLOCKS", "RELATES_TO"],
                },
            },
        ]
    )
    right = normalize_grants(
        [
            {
                "scope": {
                    "edge_types": ["RELATES_TO", "BLOCKS"],
                    "operations": ["create", "update"],
                    "entities": ["Task"],
                },
                "id": "graph.mutate",
            },
            {"scope": {"entities": ["Task", "Event"]}, "id": "graph.query"},
        ]
    )

    assert [grant.to_dict() for grant in left] == [grant.to_dict() for grant in right]
    assert grants_digest(left) == grants_digest(right)
    assert grants_digest(left).startswith("sha256:")


@pytest.mark.parametrize(
    "raw, message",
    [
        ({"id": "unknown.read", "scope": {}}, "Unknown capability category"),
        ({"id": "graph.query", "scope": {"entities": []}}, "entities"),
        (
            {"id": "graph.mutate", "scope": {"entities": ["Task"], "operations": ["execute"]}},
            "operations",
        ),
        ({"id": "file.read", "scope": {"paths": ["../secret"]}}, "paths"),
        (
            {
                "id": "network.request",
                "scope": {
                    "sources": {
                        "api": {"base_url": "http://example.com", "paths": ["/v1"], "methods": ["GET"]}
                    }
                },
            },
            "HTTPS",
        ),
        (
            {
                "id": "capability.invoke",
                "scope": {"catalog_ids": ["mcp:calendar:calendar"], "actions": []},
            },
            "actions",
        ),
    ],
)
def test_invalid_grants_fail_closed(raw, message):
    with pytest.raises(ValueError, match=message):
        CapabilityGrant.from_dict(raw)


def test_duplicate_category_grants_are_rejected_instead_of_implicitly_merged():
    with pytest.raises(ValueError, match="Duplicate capability grant"):
        normalize_grants(
            [
                {"id": "graph.query", "scope": {"entities": ["Task"]}},
                {"id": "graph.query", "scope": {"entities": ["Event"]}},
            ]
        )


def test_runtime_contract_binds_app_schemas_grants_and_allowed_artifacts():
    contract = RuntimeContract.create(
        app_id="planner",
        schemas=[{"id": "Task", "properties": {"title": "string"}}],
        capabilities=[{"id": "graph.query", "scope": {"entities": ["Task"]}}],
    )

    payload = contract.to_dict()
    assert payload["contract_version"] == 1
    assert payload["catalog_version"] == 1
    assert payload["app_id"] == "planner"
    assert payload["allowed_files"] == ["README.md", "controller.js", "manifest.json"]
    assert payload["grants_digest"] == grants_digest(contract.capabilities)
