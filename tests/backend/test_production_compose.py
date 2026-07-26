from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _compose(name: str) -> dict:
    return yaml.safe_load((REPOSITORY_ROOT / name).read_text(encoding="utf-8"))


def test_production_compose_has_bounded_resources_and_rotated_logs() -> None:
    compose = _compose("docker-compose.yml")
    services = compose["services"]
    expected_limits = {
        "neo4j": {"mem_limit": "1250m", "cpus": 1.0, "pids_limit": 256},
        "backend": {"mem_limit": "1g", "cpus": 1.5, "pids_limit": 256},
        "widget-runtime": {"mem_limit": "768m", "cpus": 1.0, "pids_limit": 192},
        "widget-frame": {"mem_limit": "192m", "cpus": 0.25, "pids_limit": 32},
        "frontend": {"mem_limit": "128m", "cpus": 0.25, "pids_limit": 32},
    }

    assert set(services) == {"neo4j", "backend", "widget-runtime", "widget-frame", "frontend"}
    for service_name, service in services.items():
        assert {key: service[key] for key in ("mem_limit", "cpus", "pids_limit")} == expected_limits[service_name]
        assert service["mem_limit"] == service["memswap_limit"]
        assert service["logging"] == {
            "driver": "json-file",
            "options": {"max-size": "10m", "max-file": "3"},
        }

    assert services["neo4j"]["environment"]["NEO4J_server_memory_heap_max__size"] == "512m"
    assert services["neo4j"]["environment"]["NEO4J_server_memory_pagecache_size"] == "256m"
    assert services["neo4j"]["environment"]["NEO4J_dbms_memory_transaction_total_max"] == "256m"
    assert services["backend"]["environment"]["RUNNER_MAX_CONCURRENCY"].endswith(":-1}")
    assert services["widget-runtime"]["environment"]["WIDGET_RUNTIME_MAX_CONTEXTS"].endswith(":-4}")
    assert services["widget-runtime"]["environment"]["WIDGET_RUNTIME_IDLE_TIMEOUT_MS"].endswith(":-60000}")


def test_production_compose_publishes_host_ports_on_loopback_only() -> None:
    services = _compose("docker-compose.yml")["services"]
    published_ports = {
        service_name: service["ports"] for service_name, service in services.items() if service.get("ports")
    }

    assert published_ports == {
        "neo4j": ["127.0.0.1:7474:7474", "127.0.0.1:7687:7687"],
        "backend": ["127.0.0.1:8000:8000"],
        "widget-frame": ["127.0.0.1:8001:8001"],
        "frontend": ["127.0.0.1:5173:5173"],
    }


def test_backend_uses_init_to_reap_coding_agent_and_mcp_children() -> None:
    backend = _compose("docker-compose.yml")["services"]["backend"]

    assert backend["init"] is True


def test_production_compose_does_not_mount_project_source_or_dependencies() -> None:
    services = _compose("docker-compose.yml")["services"]
    mounts = [mount for service in services.values() for mount in service.get("volumes", [])]

    forbidden = (
        "./backend:",
        "./forward:",
        "./scripts:",
        "./tests:",
        "./frontend/",
        "node_modules",
    )
    assert not any(token in mount for mount in mounts for token in forbidden)
    assert "./workspace:/app/workspace" in services["backend"]["volumes"]


def test_development_override_restores_reload_and_hmr_mounts() -> None:
    compose = _compose("docker-compose.dev.yml")
    backend = compose["services"]["backend"]
    frontend = compose["services"]["frontend"]

    assert "--reload" in backend["command"]
    assert "./backend:/app/backend" in backend["volumes"]
    assert "./tests:/app/tests" in backend["volumes"]
    assert frontend["build"]["target"] == "development"
    assert frontend["read_only"] is False
    assert frontend["mem_limit"] == frontend["memswap_limit"] == "768m"
    assert frontend["pids_limit"] == 128
    assert "./frontend/src:/app/src" in frontend["volumes"]
    assert "/app/node_modules" in frontend["volumes"]


def test_frontend_image_uses_build_and_small_static_runtime_stages() -> None:
    dockerfile = (REPOSITORY_ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")
    nginx = (REPOSITORY_ROOT / "frontend" / "nginx.conf").read_text(encoding="utf-8")
    frontend = _compose("docker-compose.yml")["services"]["frontend"]

    assert "FROM dependencies AS build" in dockerfile
    assert "RUN npm run build" in dockerfile
    assert "FROM nginx:1.30-alpine AS production" in dockerfile
    assert "COPY --from=build /app/dist /usr/share/nginx/html" in dockerfile
    assert "USER nginx" in dockerfile
    assert 'CMD ["nginx", "-g", "daemon off;"]' in dockerfile
    assert frontend["user"] == "101:101"
    assert frontend["read_only"] is True
    assert frontend["cap_drop"] == ["ALL"]
    assert frontend["healthcheck"]["test"][-1] == "http://127.0.0.1:5173/healthz"
    assert "worker_processes 1;" in nginx
    assert "pid /tmp/nginx.pid;" in nginx
    assert "client_body_temp_path /tmp/client_temp;" in nginx
    assert "gzip on;" in nginx
    assert 'add_header Cache-Control "public, immutable";' in nginx
    assert 'add_header Cache-Control "no-cache";' in nginx
