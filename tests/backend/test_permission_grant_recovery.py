from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from backend.run_service import RunCoordinator, RunStore, RunVersionConflict


@pytest.fixture
def permission_http_client():
    import backend.main as main

    # These API tests drive the real approval transaction without starting
    # unrelated services or executing the newly queued external MCP request.
    client = TestClient(main.app)
    try:
        yield client
    finally:
        client.close()


def permission_run(tmp_path, permission_type, interaction_type="permission"):
    store = RunStore(str(tmp_path))
    run = store.create_run(
        owner_id="permission-app",
        action_id="test",
        action_title="Test",
        source_type="user",
        source_id=None,
        adapter_type="mcp_tool",
        runtime_id="permission-app",
        tool_name="test",
        input_data={},
    )
    claimed = store.claim_next("worker", 4, 1)
    value = {"app_id": "permission-app", "command": ["mcp"], "args": [], "agent_url": "https://agent.example"}
    interaction = store.create_interaction(
        run["id"],
        interaction_type,
        "Allow?",
        {"permission_type": permission_type, "value": value},
    )
    waiting = store.transition(
        run["id"],
        "waiting_user",
        expected_lease_owner="worker",
        expected_lease_epoch=claimed["lease_epoch"],
    )
    backend = SimpleNamespace(approve_agent=Mock(), approve_mcp=Mock())
    coordinator = RunCoordinator(store, None, None, backend)
    return store, coordinator, backend, interaction, waiting


@pytest.mark.parametrize("permission_type", ["mcp_spawn", "agent_connect"])
@pytest.mark.parametrize("invalid", ["stale", "cancelled", "resolved"])
def test_invalid_permission_approval_has_no_grant_side_effect(tmp_path, permission_type, invalid):
    store, coordinator, backend, interaction, waiting = permission_run(tmp_path, permission_type)
    expected_version = waiting["version"]
    if invalid == "stale":
        expected_version -= 1
    elif invalid == "cancelled":
        store.request_cancel(waiting["id"])
    else:
        store.resolve_interaction(interaction["id"], {"approved": False}, target_status="failed")

    with pytest.raises(RunVersionConflict if invalid == "stale" else ValueError):
        coordinator.resolve_interaction(interaction["id"], {"approved": True}, expected_run_version=expected_version)

    backend.approve_agent.assert_not_called()
    backend.approve_mcp.assert_not_called()
    assert store.pending_permission_grants() == []


def test_permission_is_committed_before_grant_and_blocks_claim_until_applied(tmp_path):
    store, coordinator, backend, interaction, waiting = permission_run(tmp_path, "mcp_spawn")

    def grant(app_id, command, args):
        assert store.get_interaction(interaction["id"])["status"] == "resolved"
        assert store.claim_next("other-worker", 4, 1) is None
        assert (app_id, command, args) == ("permission-app", ["mcp"], [])

    backend.approve_mcp.side_effect = grant
    result = coordinator.resolve_interaction(
        interaction["id"],
        {"approved": True, "value": {"app_id": "untrusted"}},
        expected_run_version=waiting["version"],
    )
    assert result["status"] == "queued"
    assert store.pending_permission_grants() == []
    assert store.claim_next("other-worker", 4, 1)["id"] == waiting["id"]
    backend.approve_mcp.assert_called_once()


def test_failed_grant_remains_durable_and_is_recovered_after_restart(tmp_path):
    store, coordinator, backend, interaction, waiting = permission_run(tmp_path, "agent_connect")
    backend.approve_agent.side_effect = OSError("secret-token must not enter durable errors")
    coordinator.resolve_interaction(interaction["id"], {"approved": True})
    assert store.get_interaction(interaction["id"])["status"] == "resolved"
    assert store.claim_next("worker", 4, 1) is None
    assert len(store.pending_permission_grants()) == 1
    assert "secret-token" not in str(store.get_run(waiting["id"]))

    recovered_store = RunStore(str(tmp_path))
    recovered_backend = SimpleNamespace(approve_agent=Mock(), approve_mcp=Mock())
    recovered = RunCoordinator(recovered_store, None, None, recovered_backend)
    recovered.recover_permission_grants()
    recovered.recover_permission_grants()
    recovered_backend.approve_agent.assert_called_once_with("permission-app", "https://agent.example")
    assert recovered_store.pending_permission_grants() == []
    assert recovered_store.claim_next("worker", 4, 1)["id"] == waiting["id"]


def test_non_boolean_permission_response_cannot_grant_access(tmp_path):
    store, coordinator, backend, interaction, waiting = permission_run(tmp_path, "agent_connect")
    result = coordinator.resolve_interaction(interaction["id"], {"approved": "false"})
    assert result["status"] == "failed"
    backend.approve_agent.assert_not_called()
    assert store.pending_permission_grants() == []


def test_graph_approval_metadata_is_not_a_permanent_permission_grant(tmp_path):
    store, coordinator, backend, interaction, waiting = permission_run(
        tmp_path,
        "graph_mutation",
        interaction_type="graph_mutation_approval",
    )
    result = coordinator.resolve_interaction(interaction["id"], {"approved": True})
    assert result["status"] == "queued"
    assert store.get_interaction(interaction["id"])["status"] == "resolved"
    backend.approve_agent.assert_not_called()
    backend.approve_mcp.assert_not_called()
    assert store.pending_permission_grants() == []


@pytest.mark.parametrize("response", ["false", "true", 1, 0, [], [True], None, {"approved": "false"}])
def test_http_non_boolean_permission_response_cannot_grant_access(
    tmp_path, monkeypatch, response, permission_http_client
):
    import backend.main as main

    store, coordinator, backend, interaction, waiting = permission_run(tmp_path, "agent_connect")
    monkeypatch.setattr(main, "run_store", store)
    monkeypatch.setattr(main, "run_coordinator", coordinator)
    result = permission_http_client.post(
        f"/api/run-interactions/{interaction['id']}/resolve", json={"response": response}
    )
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "failed"
    backend.approve_agent.assert_not_called()
    assert store.pending_permission_grants() == []


@pytest.mark.parametrize("response", [True, {"approved": True}])
def test_http_literal_permission_approval_keeps_boolean_compatibility(
    tmp_path, monkeypatch, response, permission_http_client
):
    import backend.main as main

    store, coordinator, backend, interaction, waiting = permission_run(tmp_path, "agent_connect")
    monkeypatch.setattr(main, "run_store", store)
    monkeypatch.setattr(main, "run_coordinator", coordinator)
    result = permission_http_client.post(
        f"/api/run-interactions/{interaction['id']}/resolve", json={"response": response}
    )
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "queued"
    backend.approve_agent.assert_called_once_with("permission-app", "https://agent.example")
