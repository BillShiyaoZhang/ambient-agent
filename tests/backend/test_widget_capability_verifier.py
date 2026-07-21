import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFIER = REPO_ROOT / "scripts" / "verify_widget_controller.mjs"


def verify(tmp_path, source, capabilities):
    app_dir = tmp_path / "test-app"
    app_dir.mkdir()
    controller = app_dir / "controller.js"
    controller.write_text(source, encoding="utf-8")
    (app_dir / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 2,
                "id": "test-app",
                "title": "Test App",
                "description": "",
                "app_version": "1.0.0",
                "intents": [],
                "schema_refs": [],
                "capabilities": capabilities,
            }
        ),
        encoding="utf-8",
    )
    return subprocess.run(
        ["node", str(VERIFIER), str(controller)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_verifier_rejects_graph_use_without_a_grant(tmp_path):
    completed = verify(
        tmp_path,
        "export default function App() { ambient.graph.subscribe({ type: 'Task' }, () => {}); return null; }",
        [],
    )
    assert completed.returncode != 0
    assert json.loads(completed.stderr)["code"] == "capability_contract_error"


def test_verifier_accepts_scoped_graph_use_and_rejects_another_entity(tmp_path):
    grant = [{"id": "graph.query", "scope": {"entities": ["Task"]}}]
    allowed = verify(
        tmp_path,
        "export default function App() { ambient.graph.subscribe({ type: 'Task' }, () => {}); return null; }",
        grant,
    )
    assert allowed.returncode == 0, allowed.stderr

    denied_dir = tmp_path / "denied"
    denied_dir.mkdir()
    denied = verify(
        denied_dir,
        "export default function App() { ambient.graph.subscribe({ type: 'Document' }, () => {}); return null; }",
        grant,
    )
    assert denied.returncode != 0
    assert json.loads(denied.stderr)["code"] == "capability_contract_error"


def test_verifier_requires_literal_approved_capability_action(tmp_path):
    grant = [
        {
            "id": "capability.invoke",
            "scope": {"catalog_ids": ["mcp:calendar:calendar"], "actions": ["list-events"]},
        }
    ]
    allowed = verify(
        tmp_path,
        "export default function App() { ambient.capabilities.invoke('mcp:calendar:calendar', {}, 'list-events'); return null; }",
        grant,
    )
    assert allowed.returncode == 0, allowed.stderr

    denied_dir = tmp_path / "denied"
    denied_dir.mkdir()
    denied = verify(
        denied_dir,
        "export default function App() { const action = 'list-events'; ambient.capabilities.invoke('mcp:calendar:calendar', {}, action); return null; }",
        grant,
    )
    assert denied.returncode != 0
    assert json.loads(denied.stderr)["code"] == "capability_contract_error"


def test_verifier_checks_graph_network_and_file_scope_literals(tmp_path):
    capabilities = [
        {
            "id": "graph.mutate",
            "scope": {"entities": ["Task"], "operations": ["create"]},
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
        {"id": "file.read", "scope": {"paths": ["drafts/**"]}},
    ]
    allowed = verify(
        tmp_path,
        """
        export default function App() {
          ambient.graph.mutate([{ action: 'create_node', type: 'Task', properties: {} }]);
          ambient.net.request('forecast', { path: '/v1/forecast', method: 'GET' });
          ambient.files.read('drafts/today.md');
          return null;
        }
        """,
        capabilities,
    )
    assert allowed.returncode == 0, allowed.stderr

    denied_dir = tmp_path / "denied-scope"
    denied_dir.mkdir()
    denied = verify(
        denied_dir,
        """
        export default function App() {
          ambient.net.request('forecast', { path: '/admin', method: 'POST' });
          return null;
        }
        """,
        capabilities,
    )
    assert denied.returncode != 0
    assert json.loads(denied.stderr)["code"] == "capability_contract_error"
