import json
from pathlib import Path

from fastapi.testclient import TestClient

import backend.main as main_module
from backend.skill_manager import SkillManager


def _write_external_skill(market_dir: Path) -> None:
    entry = market_dir / "external-review"
    entry.mkdir(parents=True)
    (entry / "SKILL.md").write_text(
        "---\n"
        "name: external-review\n"
        "description: Review material using an external procedure.\n"
        "allowed-tools: Shell CalendarWrite\n"
        "---\n"
        "Review the supplied material carefully.\n",
        encoding="utf-8",
    )
    (entry / "market.json").write_text(
        json.dumps(
            {
                "market_id": "external/external-review",
                "catalog_id": "agent-skill:external:external-review",
                "title": "External Review",
                "provider": "External Test",
                "version": "1.0.0",
                "tags": ["review"],
                "ontology_refs": [],
                "triggers": ["external review"],
                "surfaces": ["agent_context"],
                "provenance": {"source": "https://example.invalid", "verified": True},
            }
        ),
        encoding="utf-8",
    )


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
        assert installed.json()["authorization"]["state"] == "trusted"
        assert installed.json()["authorization"]["activation_policy"] == "implicit"
        assert installed.json()["skill"]["registry_revision"] == 1

        catalog = client.get("/api/app-store").json()
        skill_item = next(
            item
            for item in catalog["items"]
            if item["catalog_id"] == "agent-skill:ambient-agent:daily-planning"
        )
        assert skill_item["skill"]["enabled"] is True
        assert skill_item["skill"]["authorization"]["state"] == "trusted"
        assert skill_item["skill"]["registry_revision"] == 1
        assert skill_item["ui_app_id"] is None

        trusted_revoke = client.patch(
            "/api/skills/agent-skill:ambient-agent:daily-planning/authorization",
            json={
                "activation_policy": "none",
                "expected_digest": installed.json()["digest"],
                "expected_revision": 1,
            },
        )
        assert trusted_revoke.status_code == 409
        assert trusted_revoke.headers["cache-control"] == "no-store"
        assert (
            trusted_revoke.json()["detail"]["code"]
            == "skill_trusted_authorization_immutable"
        )

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


def test_external_skill_authorization_api_is_trusted_host_digest_and_revision_bound(
    tmp_path: Path,
    monkeypatch,
) -> None:
    market_dir = tmp_path / "market"
    market_dir.mkdir()
    _write_external_skill(market_dir)
    manager = SkillManager(tmp_path / "workspace", market_dir)
    monkeypatch.setattr(main_module, "skill_manager", manager)
    monkeypatch.setattr(main_module.app_store, "providers", [manager])
    catalog_id = "agent-skill:external:external-review"

    with TestClient(
        main_module.app,
        client=("127.0.0.1", 50_000),
    ) as client:
        installed = client.post(
            "/api/skills/install",
            json={"market_id": "external/external-review", "expected_revision": 0},
        )
        assert installed.status_code == 200
        assert installed.headers["cache-control"] == "no-store"
        installed_body = installed.json()
        digest = installed_body["digest"]
        assert installed_body["enabled"] is False
        assert installed_body["authorization"]["state"] == "quarantined"
        assert installed_body["authorization"]["activation_policy"] == "none"
        assert installed_body["authorization"]["requires_reauthorization"] is True
        assert installed_body["skill"]["registry_revision"] == 1
        assert installed_body["skill"]["allowed_tools_declared"] == "Shell CalendarWrite"

        bypass = client.patch(
            f"/api/skills/{catalog_id}",
            json={"enabled": True, "expected_revision": 1},
        )
        assert bypass.status_code == 409
        assert bypass.headers["cache-control"] == "no-store"
        assert bypass.json()["detail"]["code"] == "skill_authorization_required"

        missing_revision = client.patch(
            f"/api/skills/{catalog_id}/authorization",
            json={
                "activation_policy": "explicit_only",
                "expected_digest": digest,
            },
        )
        assert missing_revision.status_code == 422
        assert missing_revision.headers["cache-control"] == "no-store"

        stale_digest = client.patch(
            f"/api/skills/{catalog_id}/authorization",
            json={
                "activation_policy": "explicit_only",
                "expected_digest": f"sha256:{'0' * 64}",
                "expected_revision": 1,
            },
        )
        assert stale_digest.status_code == 409
        assert stale_digest.headers["cache-control"] == "no-store"
        assert stale_digest.json()["detail"]["code"] == "skill_authorization_digest_mismatch"
        assert stale_digest.json()["detail"]["actual_digest"] == digest

        authorized = client.patch(
            f"/api/skills/{catalog_id}/authorization",
            json={
                "activation_policy": "explicit_only",
                "expected_digest": digest,
                "expected_revision": 1,
            },
        )
        assert authorized.status_code == 200
        assert authorized.headers["cache-control"] == "no-store"
        assert authorized.json()["enabled"] is True
        assert authorized.json()["authorization"]["state"] == "authorized"
        assert authorized.json()["authorization"]["activation_policy"] == "explicit_only"
        assert authorized.json()["skill"]["registry_revision"] == 2

        stale_revision = client.patch(
            f"/api/skills/{catalog_id}/authorization",
            json={
                "activation_policy": "implicit",
                "expected_digest": digest,
                "expected_revision": 1,
            },
        )
        assert stale_revision.status_code == 409
        assert stale_revision.headers["cache-control"] == "no-store"
        assert stale_revision.json()["detail"]["code"] == "skill_revision_conflict"

        revoked = client.patch(
            f"/api/skills/{catalog_id}/authorization",
            json={
                "activation_policy": "none",
                "expected_digest": digest,
                "expected_revision": 2,
            },
        )
        assert revoked.status_code == 200
        assert revoked.headers["cache-control"] == "no-store"
        assert revoked.json()["enabled"] is False
        assert revoked.json()["authorization"]["state"] == "quarantined"
        assert revoked.json()["skill"]["registry_revision"] == 3

        catalog = client.get("/api/app-store").json()
        item = next(item for item in catalog["items"] if item["catalog_id"] == catalog_id)
        assert item["skill"]["authorization"]["state"] == "quarantined"
        assert item["skill"]["registry_revision"] == 3


def test_skill_authorization_api_rejects_untrusted_peer(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_module, "skill_manager", SkillManager(tmp_path / "workspace"))

    response = TestClient(
        main_module.app,
        client=("192.0.2.10", 50_000),
    ).patch(
        "/api/skills/agent-skill:external:missing/authorization",
        json={
            "activation_policy": "implicit",
            "expected_digest": f"sha256:{'0' * 64}",
            "expected_revision": 0,
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "skill_market_origin_denied"
    assert response.headers["cache-control"] == "no-store"


def test_app_store_control_plane_rejects_untrusted_peer() -> None:
    with TestClient(
        main_module.app,
        client=("192.0.2.10", 50_000),
    ) as client:
        read = client.get("/api/app-store")
        layout = client.put(
            "/api/app-store/layout",
            json={"revision": 0, "root": [], "folders": []},
        )

    for response in (read, layout):
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "app_store_origin_denied"
        assert response.headers["cache-control"] == "no-store"


def test_skill_market_rejects_untrusted_peer(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_module, "skill_manager", SkillManager(tmp_path / "workspace"))

    response = TestClient(
        main_module.app,
        client=("192.0.2.10", 50_000),
    ).get("/api/skill-market")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "skill_market_origin_denied"
    assert response.headers["cache-control"] == "no-store"
