import json
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_devcontainer_runs_all_widget_dependencies_and_forwards_browser_frame() -> None:
    config = json.loads((REPOSITORY_ROOT / ".devcontainer/devcontainer.json").read_text(encoding="utf-8"))

    assert config["dockerComposeFile"] == "docker-compose.yml"
    assert config["service"] == "workspace"
    assert config["workspaceFolder"] == "/workspaces/ambient-agent"
    assert config["runServices"] == ["workspace", "neo4j", "widget-runtime", "widget-frame"]
    assert config["shutdownAction"] == "stopCompose"
    assert {
        8000,
        5173,
        5174,
        "widget-frame:8001",
        "neo4j:7474",
        "neo4j:7687",
    } <= set(config["forwardPorts"])
    assert config["portsAttributes"]["widget-frame:8001"]["label"] == "Widget Frame"

    compose = (REPOSITORY_ROOT / ".devcontainer/docker-compose.yml").read_text(encoding="utf-8")
    assert "workspace:" in compose
    assert "neo4j:" in compose
    assert "widget-runtime:" in compose
    assert "widget-frame:" in compose
    assert "dockerfile: widget-runtime/frame.Dockerfile" in compose
    assert "WIDGET_FRAME_PORT: 8001" in compose
    assert 'fetch("http://127.0.0.1:8001/health")' in compose
    assert "GRAPH_DATABASE_BACKEND: neo4j" in compose
    assert "NEO4J_URI: bolt://neo4j:7687" in compose
    assert "NEO4J_AUTH: neo4j/ambient-agent-dev" in compose
    assert "condition: service_healthy" in compose
    assert compose.count("condition: service_healthy") >= 3
    assert 'createConnection("/run/ambient-widget-runtime/runtime.sock")' in compose
    assert "network_mode: none" in compose
    assert "widget_runtime_socket:/run/ambient-widget-runtime" in compose


def test_devcontainer_pins_the_same_codex_acp_bridge_as_production() -> None:
    dev_dockerfile = (REPOSITORY_ROOT / ".devcontainer/Dockerfile").read_text(encoding="utf-8")
    production_dockerfile = (REPOSITORY_ROOT / "backend/Dockerfile").read_text(encoding="utf-8")

    for dockerfile in (dev_dockerfile, production_dockerfile):
        assert "@agentclientprotocol/codex-acp@1.1.7" in dockerfile
        assert "/opt/coding-agent-acp" in dockerfile
