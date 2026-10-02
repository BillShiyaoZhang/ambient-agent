import pytest

from backend.app_manifest import AppManifest
from backend.capabilities.catalog import SystemCapabilityCatalog
from backend.coding_agent_acp import CodingAgentArtifactError, validate_coding_agent_feature_coverage
from backend.widget_requirements import (
    FeatureCoverageError,
    assert_required_feature_implementation,
    validate_feature_requirements,
)


GRANTS = [
    {
        "id": "network.request",
        "scope": {
            "sources": {
                "weather": {
                    "base_url": "https://api.open-meteo.com",
                    "paths": ["/v1/forecast"],
                    "methods": ["GET"],
                    "response_limit": 1048576,
                }
            }
        },
    }
]
FEATURE = {
    "id": "custom:weather.forecast",
    "description": "显示真实天气",
    "capability_ids": ["network.request"],
    "network_sources": [{"source_id": "weather", "path": "/v1/forecast"}],
}


def manifest(status="implemented", surfaces=None):
    return AppManifest.from_dict(
        {
            "manifest_version": 2,
            "id": "weather-app",
            "title": "天气",
            "description": "",
            "app_version": "0.1.0",
            "intents": [],
            "schema_refs": [],
            "capabilities": GRANTS,
            "app_spec": {
                "spec_version": 1,
                "types": ["custom:weather"],
                "features": [
                    {
                        "id": "custom:weather.forecast",
                        "status": status,
                        "surfaces": surfaces if surfaces is not None else ["ui"],
                    }
                ],
            },
        },
        expected_app_id="weather-app",
    )


def test_required_weather_dependency_must_be_approved():
    assert validate_feature_requirements([FEATURE], SystemCapabilityCatalog.build(), GRANTS) == [FEATURE]
    with pytest.raises(ValueError, match=r"network\.request"):
        validate_feature_requirements([FEATURE], SystemCapabilityCatalog.build(), [])


@pytest.mark.parametrize(
    "change",
    [
        {"network_sources": [{"source_id": "missing", "path": "/v1/forecast"}]},
        {"network_sources": [{"source_id": "weather", "path": "/unapproved"}]},
        {"capability_ids": ["device.invented"]},
        {"id": "custom:weather"},
        {"unknown": True},
    ],
)
def test_requirement_shape_and_dependencies_are_strict(change):
    with pytest.raises(ValueError):
        validate_feature_requirements([{**FEATURE, **change}], SystemCapabilityCatalog.build(), GRANTS)


def test_planned_weather_notice_cannot_count_as_completed():
    with pytest.raises(FeatureCoverageError) as captured:
        assert_required_feature_implementation(manifest("planned", []), [FEATURE])
    assert captured.value.code == "required_feature_missing"
    assert "custom:weather.forecast" in str(captured.value)
    assert captured.value.observed == "planned"


def test_implemented_required_feature_and_legacy_empty_criteria():
    assert_required_feature_implementation(manifest(), [FEATURE])
    assert_required_feature_implementation(manifest("planned", []), [])
    assert validate_feature_requirements(None, SystemCapabilityCatalog.build(), GRANTS) == []


@pytest.mark.parametrize("status", ["planned", "partial", None])
def test_required_feature_declaration_cannot_be_downgraded(status):
    candidate = manifest(status or "implemented", [] if status == "planned" else ["ui"])
    if status is None:
        data = candidate.to_dict()
        data["app_spec"]["features"] = []
        candidate = AppManifest.from_dict(data, expected_app_id="weather-app")
    with pytest.raises(FeatureCoverageError):
        assert_required_feature_implementation(candidate, [FEATURE])


def test_declaring_weather_implemented_without_weather_call_is_rejected(tmp_path):
    tmp_path = tmp_path / "staged"
    tmp_path.mkdir()
    manifest().write_atomic(tmp_path / "manifest.json")
    (tmp_path / "controller.js").write_text(
        "export default function App(){return ambient.html`<div>无天气能力</div>`;}"
    )
    with pytest.raises(CodingAgentArtifactError) as captured:
        validate_coding_agent_feature_coverage(tmp_path, "weather-app", [FEATURE])
    assert captured.value.code == "required_feature_missing"


def test_required_feature_uses_exact_approved_source(tmp_path):
    tmp_path = tmp_path / "staged"
    tmp_path.mkdir()
    manifest().write_atomic(tmp_path / "manifest.json")
    (tmp_path / "controller.js").write_text(
        "export default function App(){ambient.net.request('weather',{path:'/v1/forecast',method:'GET'});return null;}"
    )
    validate_coding_agent_feature_coverage(tmp_path, "weather-app", [FEATURE])


@pytest.mark.parametrize(
    "value",
    [
        True,
        {},
        [FEATURE, FEATURE],
        [{**FEATURE, "description": ""}],
        [{**FEATURE, "capability_ids": ["network.request", "network.request"]}],
        [{**FEATURE, "network_sources": FEATURE["network_sources"] * 2}],
    ],
)
def test_requirement_arrays_and_duplicates_are_rejected(value):
    with pytest.raises(ValueError):
        validate_feature_requirements(value, SystemCapabilityCatalog.build(), GRANTS)
