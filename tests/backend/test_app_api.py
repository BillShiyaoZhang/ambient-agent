from fastapi.testclient import TestClient

from backend.main import app, app_manager


def test_invalid_app_id_returns_bad_request():
    with TestClient(app) as client:
        get_response = client.get("/api/apps/CON")
        delete_response = client.delete("/api/apps/CON")

    assert get_response.status_code == 400
    assert delete_response.status_code == 400


def test_app_api_preserves_existing_shape_and_adds_manifest_fields():
    app_manager.create_or_update_app(
        "planner",
        "Planner",
        js="console.log('plan')",
        description="Plans a day.",
        intents=["plan my day"],
        schema_refs=["Task"],
    )

    with TestClient(app) as client:
        list_response = client.get("/api/apps")
        detail_response = client.get("/api/apps/planner")

    assert list_response.status_code == 200
    list_item = list_response.json()[0]
    assert {"id", "title", "created_at", "updated_at"} <= list_item.keys()
    assert {
        "manifest_version",
        "description",
        "app_version",
        "intents",
        "schema_refs",
    } <= list_item.keys()

    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["js"] == "console.log('plan')"
    assert detail["description"] == "Plans a day."
    assert detail["intents"] == ["plan my day"]
    assert detail["schema_refs"] == ["Task"]


def test_invalid_manifest_is_not_exposed_or_hidden_by_metadata(isolate_apps_dir):
    app_dir = isolate_apps_dir / "broken"
    app_dir.mkdir()
    (app_dir / "index.html").write_text("<main>Broken</main>", encoding="utf-8")
    (app_dir / "manifest.json").write_text("{", encoding="utf-8")
    (app_dir / "metadata.json").write_text(
        '{"id":"broken","title":"Fallback","created_at":"2025-01-01T00:00:00+00:00",'
        '"updated_at":"2025-01-01T00:00:00+00:00"}',
        encoding="utf-8",
    )

    with TestClient(app) as client:
        list_response = client.get("/api/apps")
        detail_response = client.get("/api/apps/broken")

    assert list_response.json() == []
    assert detail_response.status_code == 404
    assert detail_response.json() == {"detail": "App not found"}
    assert (app_dir / "metadata.json").exists()
