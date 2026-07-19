from backend.workspace_storage import normalize_canvas_config


def test_migrates_legacy_canvas_spans_to_canvas_v3():
    result = normalize_canvas_config(
        {
            "pinned_ids": ["weather", "tasks"],
            "widget_spans": {"weather": {"cols": 6, "rows": 4}},
        }
    )

    assert result["version"] == 3
    assert result["open_app_ids"] == ["weather", "tasks"]
    assert result["active_app_id"] == "tasks"
    assert result["windows"]["weather"]["mode"] == "floating"
    assert set(result["windows"]["weather"]["bounds"]) == {"x", "y", "width", "height"}


def test_normalizes_invalid_canvas_v3_values():
    result = normalize_canvas_config(
        {
            "version": 3,
            "open_app_ids": ["weather", "weather", "missing"],
            "active_app_id": "unknown",
            "windows": {
                "weather": {
                    "mode": "floating",
                    "bounds": {"x": -4, "y": 7, "width": 8, "height": 0},
                }
            },
        }
    )

    assert result["open_app_ids"] == ["weather", "missing"]
    assert result["active_app_id"] == "missing"
    assert result["windows"]["weather"]["bounds"] == {"x": 0.0, "y": 0.7, "width": 1.0, "height": 0.3}
    assert result["windows"]["missing"]["mode"] == "maximized"
