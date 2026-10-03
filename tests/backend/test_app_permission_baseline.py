from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import backend.plan_generation as plan_module
import backend.schema_alignment as alignment_module
from backend.agent.errors import BudgetExhaustedError, WorkflowError
from backend.agent.feature_review import FeatureCoverageReview
from backend.capabilities.catalog import SystemCapabilityCatalog
from backend.agent.schema_decisions import SchemaSelection
from backend.plan_generation import PlanGenerationService
from backend.llm_config import LLMConfigError
from backend.schema_alignment import (
    SchemaAlignmentService,
    capability_change_summary,
    preserve_existing_app_grants,
    validate_schema_capability_proposal,
)


def source(host="api.open-meteo.com", paths=None, methods=None, response_limit=1_048_576):
    return {
        "base_url": f"https://{host}",
        "paths": sorted(paths or ["/v1/forecast"]),
        "methods": methods or ["GET"],
        "response_limit": response_limit,
    }


def network(**sources):
    return {"id": "network.request", "scope": {"sources": sources}}


def proposal(*schemas, capabilities=None, **metadata):
    return {
        "reused_schemas": [{"id": value, "reason": "App data", "extended_properties": {}} for value in schemas],
        "new_schemas": [],
        "capabilities": capabilities or [],
        **metadata,
    }


def baseline(*capabilities):
    return {
        "id": "weather-app",
        "title": "Weather",
        "schema_refs": ["Place"],
        "capabilities": list(capabilities),
    }


def inventory(*ids):
    return [{"id": value, "name": value, "description": "Context", "properties": {}} for value in ids]


WEATHER = network(**{"open-meteo": source()})
SEARCH = network(**{"geocoding": source("geocoding-api.open-meteo.com", ["/v1/search"])})


def test_ui_only_modification_preserves_existing_weather_permission_before_approval():
    original = proposal()
    manifest = baseline(WEATHER)
    result = preserve_existing_app_grants(original, manifest, [])

    assert result["capabilities"] == [WEATHER]
    assert result["capability_changes"] == {"added": [], "changed": [], "removed": []}
    assert original == proposal()
    assert manifest == baseline(WEATHER)


def test_adding_search_preserves_weather_source_and_displays_exact_scope_change():
    result = preserve_existing_app_grants(proposal(capabilities=[SEARCH]), baseline(WEATHER), [])

    assert result["capabilities"][0]["scope"]["sources"] == {
        **WEATHER["scope"]["sources"],
        **SEARCH["scope"]["sources"],
    }
    change = result["capability_changes"]["changed"][0]
    assert change["before"] == WEATHER
    assert change["after"] == result["capabilities"][0]
    assert result["capability_changes"]["removed"] == []


def test_multiple_sources_paths_methods_and_response_limits_cannot_disappear_by_omission():
    original = network(
        forecast=source(paths=["/v1/forecast", "/v1/archive"], methods=["GET", "POST"]),
        search=source("geocoding-api.open-meteo.com", ["/v1/search"]),
    )
    generated = network(forecast=source(paths=["/v1/forecast"], response_limit=1024))
    result = preserve_existing_app_grants(proposal(capabilities=[generated]), baseline(original), [])

    assert result["capabilities"] == [original]


@pytest.mark.parametrize(
    "capability_id,scope",
    [
        ("file.read", {"paths": ["cache/**"]}),
        ("file.delete", {"paths": ["cache/**"]}),
        ("file.write", {"paths": ["cache/**"], "max_bytes": 1024}),
    ],
)
def test_deliberate_whole_category_revocation_is_reviewable(capability_id, scope):
    grant = {"id": capability_id, "scope": scope}
    result = preserve_existing_app_grants(proposal(capability_removals=[capability_id]), baseline(grant), [])

    assert result["capabilities"] == []
    assert result["capability_removals"] == [capability_id]
    assert result["capability_changes"] == {"added": [], "changed": [], "removed": [grant]}


def test_deliberate_source_removal_uses_complete_scope_replacement():
    both = network(**{**WEATHER["scope"]["sources"], **SEARCH["scope"]["sources"]})
    result = preserve_existing_app_grants(
        proposal(capabilities=[WEATHER], capability_replacements=["network.request"]), baseline(both), []
    )

    assert result["capabilities"] == [WEATHER]
    assert result["capability_changes"]["changed"] == [{"before": both, "after": WEATHER}]


def test_same_source_origin_change_requires_reviewable_explicit_replacement():
    changed = network(**{"open-meteo": source("forecast.example.com")})
    with pytest.raises(ValueError, match="origin requires an explicit"):
        preserve_existing_app_grants(proposal(capabilities=[changed]), baseline(WEATHER), [])

    result = preserve_existing_app_grants(
        proposal(capabilities=[changed], capability_replacements=["network.request"]), baseline(WEATHER), []
    )
    assert result["capabilities"] == [changed]
    assert result["capability_changes"]["changed"] == [{"before": WEATHER, "after": changed}]


@pytest.mark.parametrize(
    "metadata,capabilities,message",
    [
        ({"capability_removals": "network.request"}, [], "must be an array"),
        ({"capability_removals": ["shell.exec"]}, [], "currently approved"),
        ({"capability_removals": ["network.request", "network.request"]}, [], "duplicate"),
        ({"capability_removals": ["network.request"], "capability_replacements": ["network.request"]}, [], "both"),
        ({"capability_removals": ["network.request"]}, [WEATHER], "must not also appear"),
        ({"capability_replacements": ["network.request"]}, [], "complete replacement"),
    ],
)
def test_invalid_revocation_metadata_fails_closed(metadata, capabilities, message):
    with pytest.raises(ValueError, match=message):
        preserve_existing_app_grants(proposal(capabilities=capabilities, **metadata), baseline(WEATHER), [])


def test_preserved_graph_grants_reuse_real_dependencies_without_inventing_schema():
    grant = {"id": "graph.query", "scope": {"entities": ["Task"]}}
    result = preserve_existing_app_grants(proposal("Place"), baseline(grant), inventory("Task", "Place"))

    assert [item["id"] for item in result["reused_schemas"]] == ["Place", "Task"]
    assert result["new_schemas"] == []
    validate_schema_capability_proposal(result, SystemCapabilityCatalog.build())

    with pytest.raises(ValueError, match="absent from the Schema inventory"):
        preserve_existing_app_grants(proposal(), baseline(grant), inventory("Place"))


def test_required_features_can_use_explicitly_proposed_custom_graph_entity():
    criteria = [
        {
            "id": "custom:weather.history",
            "description": "Read saved weather notes",
            "capability_ids": ["graph.query"],
            "network_sources": [],
        }
    ]
    generated = proposal(
        capabilities=[{"id": "graph.query", "scope": {"entities": ["WeatherContext"]}}],
        required_features=criteria,
    )
    generated["new_schemas"] = [{"id": "WeatherContext", "properties": {"summary": "string"}}]

    result = validate_schema_capability_proposal(generated, SystemCapabilityCatalog.build())
    assert result["required_features"] == criteria


def test_adding_graph_scope_keeps_prior_entity_operation_and_schema_dependencies():
    previous = {"id": "graph.mutate", "scope": {"entities": ["Task"], "operations": ["update"]}}
    added = {"id": "graph.mutate", "scope": {"entities": ["Event"], "operations": ["create"]}}
    result = preserve_existing_app_grants(
        proposal("Event", capabilities=[added]), baseline(previous), inventory("Task", "Event")
    )

    assert result["capabilities"] == [
        {"id": "graph.mutate", "scope": {"entities": ["Event", "Task"], "operations": ["create", "update"]}}
    ]
    assert {item["id"] for item in result["reused_schemas"]} == {"Task", "Event"}


def test_explicit_graph_scope_replacement_does_not_resurrect_revoked_dependencies():
    previous = {"id": "graph.query", "scope": {"entities": ["Event", "Task"]}}
    replacement = {"id": "graph.query", "scope": {"entities": ["Event"]}}
    result = preserve_existing_app_grants(
        proposal("Event", capabilities=[replacement], capability_replacements=["graph.query"]),
        baseline(previous),
        inventory("Task", "Event"),
    )

    assert [item["id"] for item in result["reused_schemas"]] == ["Event"]
    assert result["capabilities"] == [replacement]


def test_user_edited_empty_approval_is_authoritative_and_diff_marks_revocation():
    generated = preserve_existing_app_grants(proposal(), baseline(WEATHER), [])
    edited = copy.deepcopy(generated)
    edited["capabilities"] = []
    approved = validate_schema_capability_proposal(edited, SystemCapabilityCatalog.build())

    assert approved["capabilities"] == []
    assert capability_change_summary([WEATHER], approved["capabilities"])["removed"] == [WEATHER]


def test_unavailable_previously_installed_capability_is_reported_instead_of_silently_removed():
    grant = {
        "id": "capability.invoke",
        "scope": {"catalog_ids": ["mcp:calendar:calendar"], "actions": ["list-events"]},
    }
    preserved = preserve_existing_app_grants(proposal(), baseline(grant), [])

    with pytest.raises(ValueError, match="unavailable"):
        validate_schema_capability_proposal(preserved, SystemCapabilityCatalog.build())
    assert preserved["capabilities"] == [grant]


def test_new_capability_addition_is_displayed_without_changing_weather_baseline():
    added = {"id": "file.read", "scope": {"paths": ["cache/**"]}}
    result = preserve_existing_app_grants(proposal(capabilities=[added]), baseline(WEATHER), [])

    assert result["capability_changes"] == {"added": [added], "changed": [], "removed": []}
    assert result["capabilities"] == [added, WEATHER]


def test_generator_cannot_forge_host_baseline_bookkeeping_for_new_app():
    forged = proposal(preserved_schema_refs=["Task"], preserved_capability_ids=["graph.query"])
    result = preserve_existing_app_grants(forged, None, inventory("Task"))
    assert "preserved_schema_refs" not in result
    assert "preserved_capability_ids" not in result


class Provider:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def generate(self, messages, **_kwargs):
        self.calls.append(copy.deepcopy(messages))
        response = self.responses.pop(0)
        return response if isinstance(response, str) else json.dumps(response)


def install_provider(monkeypatch, module, provider):
    monkeypatch.setattr(module, "get_llm_provider", lambda *_: provider)
    monkeypatch.setattr(module, "primary_selection", lambda: object())
    monkeypatch.setattr(module, "selection_ids", lambda _: ("provider", "model"))
    if module is alignment_module:
        # Semantic review has its own provider budget and test module; ordinary
        # structural cases must not consume the schema generation responses.
        monkeypatch.setattr(
            alignment_module,
            "review_feature_coverage",
            AsyncMock(return_value=FeatureCoverageReview("complete", source="llm")),
        )


def strict_schema_kwargs(method, generated):
    kwargs = {
        "instruction": "Preserve real weather and add real place search",
        "approved_plan": "Display forecast and search real locations",
        "app_id": "weather-app",
        "db": SimpleNamespace(list_schemas=lambda: []),
        "existing_app_manifest": baseline(WEATHER),
        "decision_config": {"mode": "off"},
        "require_feature_requirements": True,
    }
    if method == "refine_proposal":
        kwargs.update(current_proposal=generated, feedback="Implement both live forecast and real search")
    return kwargs


def live_weather_criteria():
    return [
        {
            "id": "custom:weather.forecast",
            "description": "Display real weather",
            "capability_ids": ["network.request"],
            "network_sources": [{"source_id": "open-meteo", "path": "/v1/forecast"}],
        },
        {
            "id": "custom:weather.search",
            "description": "Search and select real places",
            "capability_ids": ["network.request"],
            "network_sources": [{"source_id": "geocoding", "path": "/v1/search"}],
        },
    ]


def placeholder_criteria():
    return [
        {
            "id": "custom:weather.unavailable",
            "description": "Show an unavailable notice instead of real weather or search",
            "capability_ids": [],
            "network_sources": [],
        }
    ]


@pytest.mark.asyncio
async def test_alignment_repairs_bad_metadata_then_preserves_previous_weather_source(monkeypatch):
    provider = Provider(
        proposal(capabilities=[SEARCH], capability_removals=["shell.exec"]),
        proposal(capabilities=[SEARCH]),
    )
    install_provider(monkeypatch, alignment_module, provider)

    result = await SchemaAlignmentService.align_schemas(
        "Add place search",
        "weather-app",
        SimpleNamespace(list_schemas=lambda: []),
        approved_plan="Preserve weather and add search",
        existing_app_manifest=baseline(WEATHER),
        decision_config={"mode": "off"},
    )

    assert len(provider.calls) == 2
    assert "currently approved" in provider.calls[1][-1]["content"]
    assert set(result["capabilities"][0]["scope"]["sources"]) == {"open-meteo", "geocoding"}
    assert "api.open-meteo.com" in provider.calls[0][1]["content"]
    assert "required_features" in provider.calls[0][0]["content"]
    assert "preserved_schema_refs" not in result


@pytest.mark.asyncio
async def test_refinement_preserves_baseline_and_returns_reviewable_revocation(monkeypatch):
    provider = Provider(proposal(capability_removals=["network.request"]))
    install_provider(monkeypatch, alignment_module, provider)
    result = await SchemaAlignmentService.refine_proposal(
        "Remove weather networking",
        "weather-app",
        proposal(capabilities=[WEATHER]),
        "Revoke weather API",
        SimpleNamespace(list_schemas=lambda: []),
        existing_app_manifest=baseline(WEATHER),
        decision_config={"mode": "off"},
    )

    assert result["capabilities"] == []
    assert result["capability_changes"]["removed"] == [WEATHER]
    assert "api.open-meteo.com" in provider.calls[0][1]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["align_schemas", "refine_proposal"])
@pytest.mark.parametrize("criteria", [None, []])
async def test_new_generated_proposal_cannot_omit_or_empty_required_acceptance_criteria(monkeypatch, method, criteria):
    generated = proposal(capabilities=[WEATHER])
    if criteria is not None:
        generated["required_features"] = criteria
    provider = Provider(generated, generated)
    install_provider(monkeypatch, alignment_module, provider)
    kwargs = {
        "instruction": "Implement live weather",
        "app_id": "weather-app",
        "db": SimpleNamespace(list_schemas=lambda: []),
        "existing_app_manifest": baseline(WEATHER),
        "decision_config": {"mode": "off"},
        "require_feature_requirements": True,
    }
    if method == "refine_proposal":
        kwargs.update(current_proposal=generated, feedback="Keep live forecast")

    with pytest.raises(WorkflowError, match="alignment"):
        await getattr(SchemaAlignmentService, method)(**kwargs)

    assert len(provider.calls) == 2
    assert "required_features acceptance criterion" in provider.calls[1][-1]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["align_schemas", "refine_proposal"])
async def test_valid_weather_and_search_acceptance_requirements_use_preserved_and_added_sources(monkeypatch, method):
    criteria = [
        {
            "id": "custom:weather.forecast",
            "description": "Display real weather",
            "capability_ids": ["network.request"],
            "network_sources": [{"source_id": "open-meteo", "path": "/v1/forecast"}],
        },
        {
            "id": "custom:weather.search",
            "description": "Search and select real places",
            "capability_ids": ["network.request"],
            "network_sources": [{"source_id": "geocoding", "path": "/v1/search"}],
        },
    ]
    generated = proposal(capabilities=[SEARCH], required_features=criteria)
    provider = Provider(generated)
    install_provider(monkeypatch, alignment_module, provider)
    kwargs = {
        "instruction": "Add real place search to weather",
        "app_id": "weather-app",
        "db": SimpleNamespace(list_schemas=lambda: []),
        "existing_app_manifest": baseline(WEATHER),
        "decision_config": {"mode": "off"},
        "require_feature_requirements": True,
    }
    if method == "refine_proposal":
        kwargs.update(current_proposal=generated, feedback="Preserve live weather")
    result = await getattr(SchemaAlignmentService, method)(**kwargs)

    assert len(provider.calls) == 1
    assert result["required_features"] == criteria
    assert set(result["capabilities"][0]["scope"]["sources"]) == {"open-meteo", "geocoding"}


@pytest.mark.asyncio
async def test_missing_source_for_required_feature_gets_corrected_in_proposal_before_coding(monkeypatch):
    criteria = [
        {
            "id": "custom:weather.search",
            "description": "Search real places",
            "capability_ids": ["network.request"],
            "network_sources": [{"source_id": "geocoding", "path": "/v1/search"}],
        }
    ]
    provider = Provider(
        proposal(required_features=criteria), proposal(capabilities=[SEARCH], required_features=criteria)
    )
    install_provider(monkeypatch, alignment_module, provider)
    result = await SchemaAlignmentService.align_schemas(
        "Add real search",
        "weather-app",
        SimpleNamespace(list_schemas=lambda: []),
        existing_app_manifest=baseline(WEATHER),
        decision_config={"mode": "off"},
        require_feature_requirements=True,
    )

    assert len(provider.calls) == 2
    assert "geocoding" in provider.calls[1][-1]["content"]
    assert result["required_features"] == criteria


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["align_schemas", "refine_proposal"])
async def test_semantic_review_corrects_placeholder_once_and_reviews_the_complete_corrected_proposal(
    monkeypatch, method
):
    initial = proposal(required_features=placeholder_criteria())
    corrected = proposal(capabilities=[SEARCH], required_features=live_weather_criteria())
    provider = Provider(initial, corrected)
    install_provider(monkeypatch, alignment_module, provider)
    reviewer = AsyncMock(
        side_effect=[
            FeatureCoverageReview(
                "revise",
                ("Implement live weather and real place search; an unavailable notice is insufficient",),
                "jev",
            ),
            FeatureCoverageReview("complete", source="llm"),
        ]
    )
    monkeypatch.setattr(alignment_module, "review_feature_coverage", reviewer)
    result = await getattr(SchemaAlignmentService, method)(**strict_schema_kwargs(method, initial))

    assert len(provider.calls) == 2
    assert reviewer.await_count == 2
    assert "unavailable notice is insufficient" in provider.calls[1][-1]["content"]
    assert result["required_features"] == live_weather_criteria()
    before, after = reviewer.await_args_list
    assert before.args[2] == placeholder_criteria()
    assert after.args[2] == live_weather_criteria()
    assert set(after.kwargs["capabilities"][0]["scope"]["sources"]) == {"open-meteo", "geocoding"}
    assert isinstance(after.kwargs["capability_catalog"], SystemCapabilityCatalog)
    if method == "refine_proposal":
        assert after.kwargs["feedback"] == "Implement both live forecast and real search"


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["align_schemas", "refine_proposal"])
async def test_semantically_incomplete_repair_cannot_return_an_approvable_proposal(monkeypatch, method):
    initial = proposal(required_features=placeholder_criteria())
    provider = Provider(initial, initial)
    install_provider(monkeypatch, alignment_module, provider)
    reviewer = AsyncMock(
        return_value=FeatureCoverageReview("revise", ("Required live behavior remains absent",), "jev")
    )
    monkeypatch.setattr(alignment_module, "review_feature_coverage", reviewer)

    with pytest.raises(WorkflowError, match="alignment") as failure:
        await getattr(SchemaAlignmentService, method)(**strict_schema_kwargs(method, initial))

    assert failure.value.code == (
        "schema_alignment_refinement_failed" if method == "refine_proposal" else "schema_alignment_failed"
    )
    assert failure.value.retryable is False
    assert "Required live behavior remains absent" in str(failure.value)
    assert len(provider.calls) == 2
    assert reviewer.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["align_schemas", "refine_proposal"])
async def test_alignment_prompts_include_app_type_ids_and_custom_feature_id_contract(monkeypatch, method):
    generated = proposal(capabilities=[WEATHER])
    provider = Provider(generated)
    install_provider(monkeypatch, alignment_module, provider)
    kwargs = strict_schema_kwargs(method, generated)
    kwargs["require_feature_requirements"] = False
    if method == "refine_proposal":
        kwargs["feedback"] = "Keep the forecast"

    await getattr(SchemaAlignmentService, method)(**kwargs)

    system_prompt = provider.calls[0][0]["content"]
    assert '"calendar.events"' in system_prompt
    assert "custom:<lowercase-kebab-case-namespace>.<lowercase-kebab-case-feature>" in system_prompt
    assert "later App implementation must declare exactly" in system_prompt
    assert '"app_spec"' not in system_prompt.split("### Output Format:", 1)[1]
    assert "multiple independently verifiable rows" in system_prompt
    assert "no-location" in system_prompt
    assert "supported fields before presenting live data" in system_prompt
    assert "only when the user or approved plan gives those details" in system_prompt
    assert "real graphics or trends" in system_prompt
    assert '"custom:task-app.accessible-view"' in system_prompt
    assert '"custom:task-app.recovery"' in system_prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["align_schemas", "refine_proposal"])
async def test_network_grant_prompts_require_evidence_backed_origins_and_paths(monkeypatch, method):
    generated = proposal(capabilities=[WEATHER])
    provider = Provider(generated)
    install_provider(monkeypatch, alignment_module, provider)
    kwargs = strict_schema_kwargs(method, generated)
    kwargs["require_feature_requirements"] = False
    if method == "refine_proposal":
        kwargs["feedback"] = "Keep current weather access"

    await getattr(SchemaAlignmentService, method)(**kwargs)

    system_prompt = provider.calls[0][0]["content"]
    assert "authoritative provider documentation or user-supplied evidence" in system_prompt
    assert "Do not infer an endpoint from the service or feature name" in system_prompt
    assert "different API hosts" in system_prompt
    assert "exact capability boundaries" in system_prompt
    assert "explicitly mark it for endpoint verification" in system_prompt


@pytest.mark.asyncio
async def test_semantic_repair_applies_each_feedback_item_and_preserves_existing_criteria(monkeypatch):
    initial_criteria = [
        {
            "id": "custom:weather.forecast",
            "description": "Fetch a real location-based forecast and show provider-confirmed fields with units.",
            "capability_ids": ["network.request"],
            "network_sources": [{"source_id": "open-meteo", "path": "/v1/forecast"}],
        }
    ]
    corrected_criteria = [
        *initial_criteria,
        {
            "id": "custom:weather.recovery",
            "description": "Show loading, no-location, empty, invalid-location, and request-error states; preserve input and unsaved edits and retry only the failed request.",
            "capability_ids": [],
            "network_sources": [],
        },
        {
            "id": "custom:weather.visualization",
            "description": "Show readable live trends and forecast details with keyboard-accessible named controls at 320px and 640px in light and dark themes.",
            "capability_ids": [],
            "network_sources": [],
        },
    ]
    initial = proposal(capabilities=[WEATHER], required_features=initial_criteria)
    corrected = proposal(capabilities=[WEATHER], required_features=corrected_criteria)
    provider = Provider(initial, corrected)
    install_provider(monkeypatch, alignment_module, provider)
    feedback = (
        "Confirm requested variables, units and response mapping against provider docs before presenting weather as live.",
        "Preserve location input and unsaved edits during failures; retry only the failed request.",
        "Add real trends, keyboard support and light/dark responsive behavior.",
    )
    reviewer = AsyncMock(
        side_effect=[
            FeatureCoverageReview("revise", feedback, "llm"),
            FeatureCoverageReview("complete", source="llm"),
        ]
    )
    monkeypatch.setattr(alignment_module, "review_feature_coverage", reviewer)

    result = await SchemaAlignmentService.align_schemas(
        "Build a full weather app",
        "weather-app",
        SimpleNamespace(list_schemas=lambda: []),
        approved_plan="Show real weather, preserve failed edits, and use clear trend graphics in accessible themes.",
        existing_app_manifest=baseline(WEATHER),
        decision_config={"mode": "off"},
        require_feature_requirements=True,
    )

    assert [row["id"] for row in result["required_features"]] == [
        "custom:weather.forecast",
        "custom:weather.recovery",
        "custom:weather.visualization",
    ]
    assert result["required_features"][0] == initial_criteria[0]
    assert result["capabilities"] == [WEATHER]
    repair_prompt = provider.calls[1][-1]["content"]
    for item in feedback:
        assert item in repair_prompt
    assert "address each actionable feedback item separately" in repair_prompt
    assert "Preserve every still-correct required feature" in repair_prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["align_schemas", "refine_proposal"])
async def test_invalid_feature_ids_get_one_targeted_repair_then_actionable_nonretryable_failure(monkeypatch, method):
    invalid = proposal(
        capabilities=[WEATHER],
        required_features=[
            {
                "id": "weather.current-and-forecast",
                "description": "Display live weather",
                "capability_ids": ["network.request"],
                "network_sources": [{"source_id": "open-meteo", "path": "/v1/forecast"}],
            }
        ],
    )
    provider = Provider(invalid, invalid)
    install_provider(monkeypatch, alignment_module, provider)
    kwargs = strict_schema_kwargs(method, invalid)
    if method == "refine_proposal":
        kwargs["feedback"] = "Keep the forecast"

    with pytest.raises(WorkflowError) as failure:
        await getattr(SchemaAlignmentService, method)(**kwargs)

    assert failure.value.code == (
        "schema_alignment_refinement_failed" if method == "refine_proposal" else "schema_alignment_failed"
    )
    assert failure.value.retryable is False
    assert "unknown type ID" in str(failure.value)
    assert len(provider.calls) == 2
    repair_prompt = provider.calls[1][-1]["content"]
    assert "failed Schema, capability, or required-feature validation" in repair_prompt
    assert "Capability Ontology scope contract" not in repair_prompt
    assert "Do not infer an endpoint from the service or feature name" in repair_prompt
    assert "different API hosts" in repair_prompt
    assert "exact capability boundaries" in repair_prompt


@pytest.mark.asyncio
async def test_targeted_repair_transport_failure_remains_retryable(monkeypatch):
    invalid = proposal(capabilities=[WEATHER], required_features=placeholder_criteria())
    provider = Provider(invalid, RuntimeError("provider unavailable"))
    install_provider(monkeypatch, alignment_module, provider)
    reviewer = AsyncMock(return_value=FeatureCoverageReview("revise", ("Add the real forecast dependency",), "jev"))
    monkeypatch.setattr(alignment_module, "review_feature_coverage", reviewer)

    with pytest.raises(WorkflowError) as failure:
        await SchemaAlignmentService.align_schemas(**strict_schema_kwargs("align_schemas", invalid))

    assert failure.value.code == "schema_alignment_failed"
    assert failure.value.retryable is True
    assert len(provider.calls) == 2


@pytest.mark.asyncio
async def test_unexpected_feature_reviewer_failure_is_not_misclassified_as_invalid_proposal(monkeypatch):
    generated = proposal(capabilities=[WEATHER], required_features=placeholder_criteria())
    provider = Provider(generated)
    install_provider(monkeypatch, alignment_module, provider)
    reviewer = AsyncMock(side_effect=RuntimeError("reviewer process failed"))
    monkeypatch.setattr(alignment_module, "review_feature_coverage", reviewer)

    with pytest.raises(WorkflowError) as failure:
        await SchemaAlignmentService.align_schemas(**strict_schema_kwargs("align_schemas", generated))

    assert failure.value.code == "schema_alignment_failed"
    assert failure.value.retryable is True
    assert len(provider.calls) == 1
    assert reviewer.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["align_schemas", "refine_proposal"])
async def test_failed_semantic_repair_cannot_start_another_generation_round_as_schema_fallback(monkeypatch, method):
    initial = proposal(required_features=placeholder_criteria())
    provider = Provider(initial, initial, initial, initial)
    install_provider(monkeypatch, alignment_module, provider)
    reviewer = AsyncMock(
        return_value=FeatureCoverageReview("revise", ("Mandatory live functionality is still absent",), "jev")
    )
    monkeypatch.setattr(alignment_module, "review_feature_coverage", reviewer)

    async def select(*_args, **_kwargs):
        return SchemaSelection(mode="cascade", disposition="NO_GRAPH_DATA", inventory_ids=(), accepted=True)

    monkeypatch.setattr(alignment_module, "select_schema_candidates", select)
    kwargs = strict_schema_kwargs(method, initial)
    kwargs["decision_config"] = {"mode": "cascade"}
    with pytest.raises(WorkflowError):
        await getattr(SchemaAlignmentService, method)(**kwargs)

    assert len(provider.calls) == reviewer.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["align_schemas", "refine_proposal"])
@pytest.mark.parametrize("failure_mode", ["unavailable", "timeout", "configuration"])
async def test_unavailable_or_timed_out_semantic_reviewer_never_defaults_to_success(monkeypatch, method, failure_mode):
    generated = proposal(capabilities=[SEARCH], required_features=live_weather_criteria())
    provider = Provider(generated, generated)
    install_provider(monkeypatch, alignment_module, provider)
    if failure_mode == "unavailable":
        reviewer = AsyncMock(
            return_value=FeatureCoverageReview("revise", ("Jev and fallback LLM review unavailable",), "unavailable")
        )
        expected_error, expected_calls = WorkflowError, 2
    elif failure_mode == "timeout":
        reviewer = AsyncMock(side_effect=BudgetExhaustedError("Feature review exceeded its wall-clock budget"))
        expected_error, expected_calls = BudgetExhaustedError, 1
    else:
        reviewer = AsyncMock(side_effect=LLMConfigError("Fallback reviewer model unavailable"))
        expected_error, expected_calls = LLMConfigError, 1
    monkeypatch.setattr(alignment_module, "review_feature_coverage", reviewer)

    with pytest.raises(expected_error):
        await getattr(SchemaAlignmentService, method)(**strict_schema_kwargs(method, generated))

    assert len(provider.calls) == expected_calls
    assert reviewer.await_count == expected_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["align_schemas", "refine_proposal"])
async def test_strict_semantic_review_accepts_a_real_proposed_custom_graph_dependency(monkeypatch, method):
    criteria = [
        {
            "id": "custom:weather.history",
            "description": "Read saved weather context",
            "capability_ids": ["graph.query"],
            "network_sources": [],
        }
    ]
    generated = proposal(
        capabilities=[{"id": "graph.query", "scope": {"entities": ["WeatherContext"]}}], required_features=criteria
    )
    generated["new_schemas"] = [{"id": "WeatherContext", "properties": {"summary": "string"}}]
    provider = Provider(generated)
    install_provider(monkeypatch, alignment_module, provider)
    reviewer = alignment_module.review_feature_coverage
    kwargs = strict_schema_kwargs(method, generated)
    kwargs.update(
        instruction="Read saved weather context", approved_plan="Read weather notes", existing_app_manifest=None
    )
    if method == "refine_proposal":
        kwargs["feedback"] = "Keep saved weather notes"
    result = await getattr(SchemaAlignmentService, method)(**kwargs)

    assert result["required_features"] == criteria
    assert result["capabilities"][0]["scope"]["entities"] == ["WeatherContext"]
    assert len(provider.calls) == reviewer.await_count == 1
    assert reviewer.await_args.args[2] == criteria


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["align_schemas", "refine_proposal"])
async def test_legacy_schema_generation_does_not_invoke_new_semantic_review(monkeypatch, method):
    generated = proposal()
    provider = Provider(generated)
    install_provider(monkeypatch, alignment_module, provider)
    reviewer = alignment_module.review_feature_coverage
    kwargs = strict_schema_kwargs(method, generated)
    kwargs["require_feature_requirements"] = False

    result = await getattr(SchemaAlignmentService, method)(**kwargs)

    assert result["capabilities"] == [WEATHER]
    assert reviewer.await_count == 0
    assert len(provider.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selection_disposition,selected,generated",
    [
        ("NO_GRAPH_DATA", (), proposal()),
        (
            "USE_EXISTING",
            ("Event",),
            proposal("Event", capabilities=[{"id": "graph.query", "scope": {"entities": ["Event"]}}]),
        ),
    ],
)
async def test_compiled_new_schema_selection_cannot_remove_existing_graph_dependency(
    monkeypatch, selection_disposition, selected, generated
):
    provider = Provider(generated)
    install_provider(monkeypatch, alignment_module, provider)

    async def select(*_args, **_kwargs):
        return SchemaSelection(
            mode="cascade",
            disposition=selection_disposition,
            selected_ids=selected,
            inventory_ids=("Event", "Task"),
            accepted=True,
        )

    monkeypatch.setattr(alignment_module, "select_schema_candidates", select)
    previous = {"id": "graph.query", "scope": {"entities": ["Task"]}}
    result = await SchemaAlignmentService.align_schemas(
        "Modify the UI",
        "weather-app",
        SimpleNamespace(list_schemas=lambda: inventory("Event", "Task")),
        existing_app_manifest=baseline(previous),
        decision_config={"mode": "cascade"},
    )

    assert len(provider.calls) == 1
    assert {item["id"] for item in result["reused_schemas"]} == {"Task", *selected}
    assert result["capabilities"][0]["scope"]["entities"] == sorted({"Task", *selected})


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["generate_plan", "refine_plan"])
async def test_planning_sees_actual_capability_catalog_and_existing_approval(monkeypatch, method):
    provider = Provider("Preserve forecast and add place search")
    install_provider(monkeypatch, plan_module, provider)
    kwargs = {
        "instruction": "Add place search",
        "app_id": "weather-app",
        "schemas_context": "",
        "language": "en",
        "existing_app_manifest": baseline(WEATHER),
        "capability_catalog": SystemCapabilityCatalog.build(),
    }
    if method == "refine_plan":
        kwargs.update(current_plan="Forecast", feedback="Add search")
    await getattr(PlanGenerationService, method)(**kwargs)

    assert "api.open-meteo.com" in provider.calls[0][1]["content"]
    assert "network.request" in provider.calls[0][1]["content"]
    assert (
        "Catalog support is not approval" in provider.calls[0][0]["content"]
        if method == "generate_plan"
        else "actual runtime catalog" in provider.calls[0][0]["content"]
    )
