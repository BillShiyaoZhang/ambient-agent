from fastapi.testclient import TestClient

import backend.main as main_module
from backend.main import app
from backend.run_service import RunStore


class ApiCoordinator:
    def __init__(self, store: RunStore):
        self.store = store

    async def start(self):
        return None

    async def shutdown(self):
        return None

    def submit(self, catalog_id, action_id, input_data, **options):
        return self.store.create_run(
            owner_id=catalog_id,
            action_id=action_id,
            action_title=action_id.title(),
            source_type=options.get("source_type", "user"),
            source_id=options.get("source_id"),
            adapter_type="mcp_tool",
            runtime_id="backend",
            tool_name=action_id,
            input_data=input_data,
            idempotency_key=options.get("idempotency_key"),
            parent_run_id=options.get("parent_run_id"),
        )

    def cancel(self, run_id):
        return self.store.request_cancel(run_id)

    def retry(self, run_id):
        original = self.store.get_run(run_id)
        return self.store.create_run(
            owner_id=original["owner_id"],
            action_id=original["action_id"],
            action_title=original["action_title"],
            source_type=original["source_type"],
            source_id=original["source_id"],
            adapter_type=original["adapter_type"],
            runtime_id=original["runtime_id"],
            tool_name=original["tool_name"],
            input_data=original["input"],
            retry_of=run_id,
            attempt=original["attempt"] + 1,
        )

    def resolve_interaction(self, interaction_id, response):
        interaction = self.store.get_interaction(interaction_id)
        self.store.resolve_interaction(
            interaction_id,
            response,
            expected_run_version=response.get("run_version") if isinstance(response, dict) else None,
        )
        return self.store.get_run(interaction["run_id"])


def test_run_rest_api_and_replayable_websocket(tmp_path, monkeypatch):
    store = RunStore(str(tmp_path))
    coordinator = ApiCoordinator(store)
    monkeypatch.setattr(main_module, "run_store", store)
    monkeypatch.setattr(main_module, "run_coordinator", coordinator)

    with TestClient(app) as client:
        created = client.post(
            "/api/runs",
            json={
                "catalog_id": "mcp:acme:mail",
                "action_id": "send",
                "input": {"subject": "Hello"},
                "source": {"type": "user", "id": "app-center"},
                "idempotency_key": "one",
            },
        )
        assert created.status_code == 202
        run_id = created.json()["id"]
        assert client.get(f"/api/runs/{run_id}").json()["input"] == {"subject": "Hello"}

        with client.websocket_connect("/ws/runs?after_sequence=0") as websocket:
            ready = websocket.receive_json()
            assert ready["type"] == "run_stream_ready"
            assert ready["stream_epoch"]
            event = websocket.receive_json()
            assert event["type"] == "run_event"
            assert event["event"]["run_id"] == run_id
            assert event["event"]["event_id"]
            assert event["event"]["schema_version"] == 1
            assert event["event"]["stream_epoch"]
            assert event["event"]["trace_id"] == run_id
            first_sequence = event["event"]["sequence"]

        cancelled = client.post(f"/api/runs/{run_id}/cancel")
        assert cancelled.json()["status"] == "cancelled"
        retried = client.post(f"/api/runs/{run_id}/retry")
        assert retried.status_code == 202
        assert retried.json()["retry_of"] == run_id

        with client.websocket_connect(f"/ws/runs?after_sequence={first_sequence}") as websocket:
            assert websocket.receive_json()["type"] == "run_stream_ready"
            next_event = websocket.receive_json()
            assert next_event["event"]["sequence"] > first_sequence


def test_widget_run_requires_explicit_action_id():
    client = TestClient(app)

    response = client.post(
        "/api/runs",
        json={
            "catalog_id": "mcp:acme:mail",
            "input": {"subject": "Hello"},
            "source": {
                "type": "widget",
                "id": "mail-widget",
                "manifest_revision": "2:1.0.0",
                "grants_digest": "digest",
            },
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Widget capability invocation requires an explicit action ID"


def test_interaction_api_uses_run_version_and_atomically_requeues(tmp_path, monkeypatch):
    store = RunStore(str(tmp_path))
    coordinator = ApiCoordinator(store)
    monkeypatch.setattr(main_module, "run_store", store)
    monkeypatch.setattr(main_module, "run_coordinator", coordinator)

    run = coordinator.submit("mcp:acme:mail", "send", {"subject": "Hello"})
    claimed = store.claim_next("worker", global_limit=1, owner_limit=1)
    interaction = store.create_interaction(run["id"], "approval", "Continue?", {})
    waiting = store.transition(
        run["id"],
        "waiting_user",
        expected_lease_owner="worker",
        expected_lease_epoch=claimed["lease_epoch"],
    )

    with TestClient(app) as client:
        conflict = client.post(
            f"/api/run-interactions/{interaction['id']}/resolve",
            json={"response": {"approved": True, "run_version": waiting["version"] - 1}},
        )
        assert conflict.status_code == 409
        assert store.get_interaction(interaction["id"])["status"] == "pending"
        assert store.get_run(run["id"])["status"] == "waiting_user"

        resolved = client.post(
            f"/api/run-interactions/{interaction['id']}/resolve",
            json={"response": {"approved": True, "run_version": waiting["version"]}},
        )
        assert resolved.status_code == 200
        assert resolved.json()["status"] == "queued"
        assert store.get_interaction(interaction["id"])["status"] == "resolved"


def test_needs_attention_api_requires_explicit_reconciliation(tmp_path, monkeypatch):
    store = RunStore(str(tmp_path))
    coordinator = ApiCoordinator(store)
    monkeypatch.setattr(main_module, "run_store", store)
    monkeypatch.setattr(main_module, "run_coordinator", coordinator)
    run = store.create_run(
        owner_id="mcp:acme:mail",
        action_id="send",
        action_title="Send",
        source_type="user",
        source_id=None,
        adapter_type="mcp_tool",
        runtime_id="mail",
        tool_name="send",
        input_data={"subject": "Hello"},
        recovery="manual",
        status="needs_attention",
    )

    with TestClient(app) as client:
        refused = client.post(f"/api/runs/{run['id']}/cancel")
        assert refused.status_code == 409
        assert store.get_run(run["id"])["status"] == "needs_attention"

        reconciled = client.post(
            f"/api/runs/{run['id']}/reconcile",
            json={
                "resolution": "confirmed_not_committed",
                "note": "provider operation ledger has no matching key",
            },
        )
        assert reconciled.status_code == 200
        assert reconciled.json()["status"] == "failed"
        assert reconciled.json()["error"]["effect_state"] == "none"
