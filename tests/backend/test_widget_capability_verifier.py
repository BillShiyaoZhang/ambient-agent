import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFIER = REPO_ROOT / "scripts" / "verify_widget_controller.mjs"


def verifier_error_report(completed):
    decoder = json.JSONDecoder()
    for output in (completed.stderr, completed.stdout):
        try:
            report, _ = decoder.raw_decode(output.lstrip())
        except json.JSONDecodeError:
            continue
        if isinstance(report, dict):
            return report
    raise AssertionError(f"Verifier did not emit a JSON error report: {completed.stderr or completed.stdout}")


def verify(tmp_path, source, capabilities, requirements=None):
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
        [
            "node",
            str(VERIFIER),
            str(controller),
            *(["--requirements-json", json.dumps(requirements)] if requirements is not None else []),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_location_requires_current_grant_and_bounded_options(tmp_path):
    grant = [{"id": "device.location", "scope": {"operations": ["current"]}}]
    source = "export default function App() { const locate = () => ambient.location.getCurrentPosition({timeout:1000,maximumAge:0}); return null; }"
    assert verify(tmp_path, source, grant).returncode == 0
    for index, (candidate, grants) in enumerate(
        [
            (source, []),
            (source.replace("timeout:1000", "timeout:0"), grant),
            (source.replace("maximumAge:0", "enableHighAccuracy:true"), grant),
            (source.replace("getCurrentPosition", "watchPosition"), grant),
        ]
    ):
        directory = tmp_path / f"invalid-{index}"
        directory.mkdir()
        assert verify(directory, candidate, grants).returncode != 0


def test_required_feature_cannot_be_satisfied_by_explanatory_placeholder(tmp_path):
    grants = [{"id": "device.location", "scope": {"operations": ["current"]}}]
    requirements = [{"id": "current-location", "capability_ids": ["device.location"], "network_sources": []}]
    completed = verify(
        tmp_path,
        "export default function App(){ return ambient.html`<div>设备定位不可用</div>`; }",
        grants,
        requirements,
    )
    assert completed.returncode != 0
    assert verifier_error_report(completed)["code"] == "required_feature_missing"


def test_required_network_feature_needs_called_exact_source_and_path(tmp_path):
    grants = [
        {
            "id": "network.request",
            "scope": {
                "sources": {
                    "weather": {
                        "base_url": "https://api.open-meteo.com",
                        "paths": ["/v1/forecast", "/other"],
                        "methods": ["GET"],
                        "response_limit": 4096,
                    }
                }
            },
        }
    ]
    requirements = [
        {
            "id": "weather",
            "capability_ids": ["network.request"],
            "network_sources": [{"source_id": "weather", "path": "/v1/forecast"}],
        }
    ]
    good = "ambient.net.request('weather',{path:'/v1/forecast',method:'GET'})"
    cases = [
        "const request = ambient.net.request;",
        f"if(false) {good};",
        f"const neverCalled = () => {good};",
        f"function neverCalled() {{ return {good}; }}",
        f"const ambient = {{net: {{request: () => null}}}}; {good};",
        "ambient.net.request('weather',{path:'/other',method:'GET'});",
        "const path='/v1/forecast'; ambient.net.request('weather',{path,method:'GET'});",
        "const source='weather'; ambient.net.request(source,{path:'/v1/forecast',method:'GET'});",
    ]
    for index, body in enumerate(cases):
        directory = tmp_path / f"case-{index}"
        directory.mkdir()
        completed = verify(directory, f"export default function App(){{ {body} return null; }}", grants, requirements)
        assert completed.returncode != 0, body
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    completed = verify(
        allowed,
        f"export default function App(){{ const load = () => {good}; return ambient.html`<button onClick=${{load}}>Load</button>`; }}",
        grants,
        requirements,
    )
    assert completed.returncode == 0, completed.stderr


def test_verifier_rejects_graph_use_without_a_grant(tmp_path):
    completed = verify(
        tmp_path,
        "export default function App() { ambient.graph.subscribe({ type: 'Task' }, () => {}); return null; }",
        [],
    )
    assert completed.returncode != 0
    assert verifier_error_report(completed)["code"] == "capability_contract_error"


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
    assert verifier_error_report(denied)["code"] == "capability_contract_error"


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
    assert verifier_error_report(denied)["code"] == "capability_contract_error"


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
    assert verifier_error_report(denied)["code"] == "capability_contract_error"


def test_verifier_explains_graph_action_dsl_instead_of_misreporting_grant_scope(tmp_path):
    completed = verify(
        tmp_path,
        """
        export default function App() {
          ambient.graph.mutate([{ action: 'create', type: 'Task', properties: {} }]);
          return null;
        }
        """,
        [{"id": "graph.mutate", "scope": {"entities": ["Task"], "operations": ["create"]}}],
    )

    assert completed.returncode != 0
    diagnostic = verifier_error_report(completed)
    assert diagnostic["code"] == "capability_contract_error"
    assert "action 'create' is invalid" in diagnostic["message"]
    assert "create_node" in diagnostic["message"]
    assert "authorization values" in diagnostic["hint"]


def test_verifier_allows_only_the_declared_local_storage_surface(tmp_path):
    accepted_path = tmp_path / "accepted"
    accepted_path.mkdir()
    accepted = verify(
        accepted_path,
        """
        export default function App() {
          ambient.storage.set("draft", { text: "hello" });
          ambient.storage.get("draft");
          ambient.storage.list();
          return null;
        }
        """,
        [],
    )
    assert accepted.returncode == 0, accepted.stderr

    rejected_path = tmp_path / "rejected"
    rejected_path.mkdir()
    rejected = verify(
        rejected_path,
        """
        export default function App() {
          ambient.storage.openDatabase("other-widget");
          return null;
        }
        """,
        [],
    )
    assert rejected.returncode != 0
    assert "Unknown ambient.storage method" in rejected.stderr


def test_verifier_allows_only_the_declared_lifecycle_surface(tmp_path):
    accepted_path = tmp_path / "accepted-lifecycle"
    accepted_path.mkdir()
    accepted = verify(
        accepted_path,
        """
        export default function App() {
          const unsubscribe = ambient.lifecycle.onBeforeSuspend(async () => {
            await ambient.storage.set("draft", { text: "hello" });
          });
          unsubscribe();
          return null;
        }
        """,
        [],
    )
    assert accepted.returncode == 0, accepted.stderr

    rejected_path = tmp_path / "rejected-lifecycle"
    rejected_path.mkdir()
    rejected = verify(
        rejected_path,
        """
        export default function App() {
          ambient.lifecycle.preventSuspendForever();
          return null;
        }
        """,
        [],
    )
    assert rejected.returncode != 0
    assert "Unknown ambient.lifecycle method" in rejected.stderr


def test_verifier_rejects_navigation_and_peer_network_globals(tmp_path):
    for index, source in enumerate(
        (
            "export default function App() { location.href = 'https://example.com'; return null; }",
            "export default function App() { new RTCPeerConnection(); return null; }",
            "export default function App() { new WebTransport('https://example.com'); return null; }",
            "export default function App() { getComputedStyle({}); return null; }",
        )
    ):
        case_path = tmp_path / f"case-{index}"
        case_path.mkdir()
        completed = verify(case_path, source, [])
        assert completed.returncode != 0
        assert "Forbidden" in verifier_error_report(completed)["message"]
