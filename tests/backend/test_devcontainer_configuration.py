import json
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_devcontainer_runs_workspace_with_neo4j_sidecar() -> None:
    config = json.loads((REPOSITORY_ROOT / ".devcontainer/devcontainer.json").read_text(encoding="utf-8"))

    assert config["dockerComposeFile"] == "docker-compose.yml"
    assert config["service"] == "workspace"
    assert config["workspaceFolder"] == "/workspaces/ambient-agent"
    assert config["runServices"] == ["workspace", "neo4j"]
    assert config["shutdownAction"] == "stopCompose"
    assert {8000, 5173, 5174, "neo4j:7474", "neo4j:7687"} <= set(config["forwardPorts"])

    compose = (REPOSITORY_ROOT / ".devcontainer/docker-compose.yml").read_text(encoding="utf-8")
    assert "workspace:" in compose
    assert "neo4j:" in compose
    assert "GRAPH_DATABASE_BACKEND: neo4j" in compose
    assert "NEO4J_URI: bolt://neo4j:7687" in compose
    assert "NEO4J_AUTH: neo4j/ambient-agent-dev" in compose
    assert "condition: service_healthy" in compose
