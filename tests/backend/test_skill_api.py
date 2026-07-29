from pathlib import Path

from fastapi.testclient import TestClient

import backend.main as main_module
from backend.skill_manager import SkillManager


def test_skill_market_api_lifecycle_and_installed_catalog_projection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manager = SkillManager(tmp_path / "workspace")
    monkeypatch.setattr(main_module, "skill_manager", manager)
    monkeypatch.setattr(main_module.app_store, "providers", [manager])

    with TestClient(
        main_module.app,
        client=("127.0.0.1", 50_000),
    ) as client:
        market = client.get("/api/skill-market")
        assert market.status_code == 200
        assert market.headers["cache-control"] == "no-store"
        assert market.json()["items"][0]["install_state"] == "not_installed"
        assert market.json()["items"][0]["provenance"]["verified"] is True
        assert market.json()["items"][0]["provenance"]["trust"] == "bundled"

        installed = client.post(
            "/api/skills/install",
            json={"market_id": "ambient-agent/daily-planning", "expected_revision": 0},
        )
        assert installed.status_code == 200
        assert installed.json()["launch_mode"] == "details"
        assert installed.json()["surfaces"] == ["agent_context"]
        assert installed.json()["actions"] == []

        catalog = client.get("/api/app-store").json()
        skill_item = next(
            item
            for item in catalog["items"]
            if item["catalog_id"] == "agent-skill:ambient-agent:daily-planning"
        )
        assert skill_item["skill"]["enabled"] is True
        assert skill_item["ui_app_id"] is None

        disabled = client.patch(
            "/api/skills/agent-skill:ambient-agent:daily-planning",
            json={"enabled": False, "expected_revision": 1},
        )
        assert disabled.status_code == 200
        assert disabled.json()["status"] == "unavailable"

        removed = client.delete(
            "/api/skills/agent-skill:ambient-agent:daily-planning",
            params={"expected_revision": 2},
        )
        assert removed.status_code == 200
        assert removed.json()["revision"] == 3


def test_skill_market_rejects_untrusted_peer(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_module, "skill_manager", SkillManager(tmp_path / "workspace"))

    response = TestClient(
        main_module.app,
        client=("192.0.2.10", 50_000),
    ).get("/api/skill-market")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "skill_market_origin_denied"
    assert response.headers["cache-control"] == "no-store"
