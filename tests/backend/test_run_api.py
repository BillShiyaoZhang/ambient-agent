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
            event = websocket.receive_json()
            assert event["type"] == "run_event"
            assert event["event"]["run_id"] == run_id
            first_sequence = event["event"]["sequence"]

        cancelled = client.post(f"/api/runs/{run_id}/cancel")
        assert cancelled.json()["status"] == "cancelled"
        retried = client.post(f"/api/runs/{run_id}/retry")
        assert retried.status_code == 202
        assert retried.json()["retry_of"] == run_id

        with client.websocket_connect(f"/ws/runs?after_sequence={first_sequence}") as websocket:
            next_event = websocket.receive_json()
            assert next_event["event"]["sequence"] > first_sequence
