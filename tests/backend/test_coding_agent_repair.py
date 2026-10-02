import json
import re
from copy import deepcopy

import pytest

from backend.agent.prompts.manager import PromptManager
from backend.app_manifest import AppManifest, ManifestValidationError
from backend.app_types import get_app_type_catalog, get_app_type_prompt_reference, validate_app_spec
from backend.capabilities.models import RuntimeContract
from backend.coding_agent_repair import (
    RepairFinding,
    app_spec_declaration_rules,
    approved_runtime_contract_excerpt,
    build_repair_prompt,
    decide_widget_repair,
    finding_from_exception,
    repair_finding_from_dict,
)


class VerifierError(RuntimeError):
    code = "widget_verification_failed"
    stage = "static_verify"


def finding(message: str, *, attempt: int, artifact: str) -> RepairFinding:
    return finding_from_exception(
        VerifierError(message),
        attempt=attempt,
        artifact_revision=artifact,
    )


def test_finding_signature_only_ignores_whitespace_not_source_locations() -> None:
    first = finding("Unexpected token (980:3)\n  980 | `;", attempt=1, artifact="one")
    whitespace_variant = finding("Unexpected   token (980:3) 980 | `;", attempt=2, artifact="two")
    moved_finding = finding("Unexpected token (1100:3)\n  1100 | `;", attempt=3, artifact="three")

    assert first.signature == whitespace_variant.signature
    assert first.signature != moved_finding.signature


def test_distinct_findings_keep_repairing_beyond_the_old_three_turn_limit() -> None:
    history = tuple(
        finding(f"Verifier issue at line {index}", attempt=index, artifact=f"artifact-{index}") for index in range(1, 8)
    )
    current = finding("Verifier issue at line 8", attempt=8, artifact="artifact-8")

    assert decide_widget_repair(current, history).action == "repair"


def test_consecutive_identical_finding_stops_automatic_repair() -> None:
    previous = finding("Unexpected token (980:3)", attempt=1, artifact="artifact-one")
    current = finding("Unexpected token (980:3)", attempt=2, artifact="artifact-two")

    directive = decide_widget_repair(current, (previous,))

    assert directive.action == "human"
    assert "same verifier finding" in directive.reason


def test_persisted_finding_round_trips_for_cross_run_stall_detection() -> None:
    original = finding("Unexpected token (980:3)", attempt=4, artifact="artifact-four")

    restored = repair_finding_from_dict(original.to_dict())

    assert restored == original


def test_unavailable_code_mode_host_requires_environment_repair() -> None:
    from backend.coding_agent_acp import CodingAgentArtifactError

    error = CodingAgentArtifactError("The managed Codex code-mode host is unavailable")
    error.code = "coding_agent_code_mode_unavailable"
    current = finding_from_exception(error, attempt=1, artifact_revision="empty-staging")

    assert current.repairability == "operator"
    assert current.contract_impact == "unknown"
    assert decide_widget_repair(current, ()).action == "operator"


def _manifest(app_spec: dict | None = None) -> dict:
    return {
        "manifest_version": 2,
        "id": "weather-app",
        "title": "Weather",
        "description": "City weather",
        "app_version": "0.1.0",
        "intents": ["weather"],
        "schema_refs": [],
        "capabilities": [],
        **({"app_spec": app_spec} if app_spec is not None else {}),
    }


def _instruction(*, template: dict | None = None, catalog: dict | None = None) -> str:
    contract = RuntimeContract.create(app_id="weather-app", schemas=[], capabilities=[]).to_dict()
    return (
        "显示杭州天气；使用中文界面并保留城市选择。\n\n"
        "[APPROVED DEVELOPMENT PLAN]\nKeep the city selector and truthful partial status.\n\n"
        "[APPROVED RUNTIME CONTRACT — REFERENCE ONLY]\n"
        f"{json.dumps(contract)}\n\n"
        "[REQUIRED MANIFEST V2 TEMPLATE]\n"
        f"{json.dumps(template or _manifest())}\n\n"
        "[MANIFEST V2 FIELD RULES]\nKeep the approved security fields immutable.\n\n"
        "[APP TYPE STANDARD]\n"
        f"{json.dumps(catalog or get_app_type_catalog(), ensure_ascii=False, indent=2)}\n\n"
        "[APP TYPE DECLARATION RULES]\n"
        f"{app_spec_declaration_rules()}\n\n"
        "[SYSTEM CAPABILITIES]\nAvailable catalog entries do not authorize extra grants."
    )


def _json_section(prompt: str, marker: str) -> dict:
    raw = prompt.split(marker + "\n", 1)[1].split("\n\n[", 1)[0].strip()
    return json.loads(raw)


def test_real_manifest_type_object_finding_has_precise_shape_without_raw_metadata() -> None:
    spec = {
        "spec_version": 1,
        "types": [{"id": "custom:weather", "title": {"en": "private-title-marker"}}],
        "features": [{"id": "custom:weather.forecast", "status": "partial", "surfaces": ["ui"]}],
    }
    with pytest.raises(ManifestValidationError) as error:
        AppManifest.from_dict(_manifest(spec), expected_app_id="weather-app")

    current = finding_from_exception(error.value, attempt=1, artifact_revision="type-object-draft")

    assert current.locations == ("manifest.json:$.app_spec.types[0]",)
    assert current.expected == "non-empty type ID string (max 200 characters)"
    assert current.observed == "object"
    assert "private-title-marker" not in json.dumps(current.to_dict())
    assert repair_finding_from_dict(current.to_dict()) == current

    # Correct only the instance shape; the custom namespace needs no hyphen,
    # and declarations remain present without expanding approved authority.
    corrected = deepcopy(spec)
    corrected["types"] = [entry["id"] for entry in spec["types"]]
    manifest = AppManifest.from_dict(_manifest(corrected), expected_app_id="weather-app")
    baseline = AppManifest.from_dict(_manifest(), expected_app_id="weather-app")
    assert manifest.to_dict()["app_spec"] == corrected
    assert manifest.grants_digest == baseline.grants_digest
    assert manifest.schema_refs == baseline.schema_refs


def test_wrapped_finding_prioritizes_explicit_bounded_locations_and_shape_names() -> None:
    error = VerifierError("A validation error")
    error.expected = "e" * 1_000
    error.observed = {"raw_private_model_value": "must-not-serialize"}
    error.path = "app_spec.types[1]"
    error.locations = ["manifest.json:$.app_spec.types[0]"] * 2 + ["l" * 1_000] * 20

    current = finding_from_exception(error, attempt=1, artifact_revision="one")

    assert current.expected == "e" * 512
    assert current.observed == ""
    assert current.locations == ("manifest.json:$.app_spec.types[0]", "l" * 512)
    assert "types[1]" not in str(current.locations)
    assert "raw_private_model_value" not in json.dumps(current.to_dict())


def test_structured_location_count_is_bounded_and_legacy_findings_stay_empty() -> None:
    error = VerifierError("A validation error")
    error.locations = [f"controller.js:{line}" for line in range(30)]
    current = finding_from_exception(error, attempt=1, artifact_revision="one")
    assert len(current.locations) == 16

    legacy = finding("Legacy verifier", attempt=1, artifact="one")
    assert (legacy.expected, legacy.observed, legacy.locations) == ("", "", ())


@pytest.mark.parametrize("namespace", ["custom:weather", "custom:weather-app"])
def test_generation_and_repair_shape_examples_are_valid_and_custom_names_need_no_hyphen(namespace: str) -> None:
    rules = app_spec_declaration_rules()
    system_prompt = PromptManager().get_prompt(
        "coding_agent_system.md", app_id="weather-app", target_dir="staging", instruction="Draft", app_spec_rules=rules
    )
    rule_example = json.loads(re.search(r"```json\n(.*?)\n```", rules, re.S)[1])
    system_example = json.loads(re.search(r"```json\n(.*?)\n```", system_prompt, re.S)[1])
    assert rule_example == system_example
    assert validate_app_spec(rule_example).to_dict() == rule_example
    spec = deepcopy(rule_example)
    spec["types"] = [namespace]
    spec["features"][0]["id"] = f"{namespace}.forecast"
    assert validate_app_spec(spec).to_dict() == spec
    for prompt in (rules, system_prompt):
        assert "type ID strings" in prompt
        assert "never objects" in prompt
        assert "metadata objects" in prompt
        assert "declaration objects" in prompt
        assert "deleting it to evade validation" in prompt


@pytest.mark.parametrize("reference_shape", ["legacy_catalog", "id_reference"])
def test_repair_context_keeps_request_template_and_complete_ids_without_catalog_metadata(reference_shape) -> None:
    catalog = get_app_type_catalog()
    if reference_shape == "legacy_catalog":
        context = deepcopy(catalog)
        for entry in context["types"]:
            entry["description"] = {"en": "expensive-catalog-description-marker" * 500}
    else:
        context = get_app_type_prompt_reference("zh")
        context["type_descriptions_by_id"] = dict.fromkeys(
            context["type_ids"], "expensive-catalog-description-marker" * 500
        )
    template = {**_manifest(), "contract_version": 1, "catalog_version": 12, "unapproved_metadata": "do-not-copy"}
    prompt = build_repair_prompt(
        finding("Bad type ID", attempt=1, artifact="one"), instruction=_instruction(template=template, catalog=context)
    )

    assert "显示杭州天气；使用中文界面并保留城市选择。" in prompt
    assert "Keep the city selector and truthful partial status." in prompt
    assert "expensive-catalog-description-marker" not in prompt
    assert "do-not-copy" not in prompt
    assert "Available catalog entries" not in prompt
    assert _json_section(prompt, "[REQUIRED MANIFEST V2 TEMPLATE]") == _manifest()
    reference = _json_section(prompt, "[APP TYPE STANDARD — ID REFERENCE]")
    assert reference["type_ids"] == [entry["id"] for entry in catalog["types"]]
    assert reference["feature_ids_by_type"] == {
        entry["id"]: [feature["id"] for feature in entry["features"]] for entry in catalog["types"]
    }
    assert app_spec_declaration_rules() in prompt
    assert "never add or broaden capabilities" in prompt
    assert len(prompt) < 8_000


@pytest.mark.parametrize("context", ["malformed-json", {"type_ids": ["calendar"], "feature_ids_by_type": {}}])
def test_malformed_retained_reference_uses_original_session_without_partial_mapping(context) -> None:
    instruction = _instruction(catalog=context) if isinstance(context, dict) else "[APP TYPE STANDARD]\n" + context
    prompt = build_repair_prompt(finding("Bad type ID", attempt=1, artifact="one"), instruction=instruction)
    reference = prompt.split("[APP TYPE STANDARD — ID REFERENCE]\n", 1)[1].split("\n\n[", 1)[0]
    assert "Consult the complete App Type Standard in the original ACP instruction" in reference
    assert '"type_ids"' not in reference
    assert app_spec_declaration_rules() in prompt


def test_contract_excerpt_does_not_accidentally_duplicate_template_or_catalog() -> None:
    excerpt = approved_runtime_contract_excerpt(_instruction())
    contract = _json_section(excerpt, "[APPROVED RUNTIME CONTRACT — REFERENCE ONLY]")
    assert contract["app_id"] == "weather-app"
    assert contract["capabilities"] == []
    assert "[REQUIRED MANIFEST V2 TEMPLATE]" not in excerpt
    assert "[APP TYPE STANDARD]" not in excerpt


def test_oversized_sections_reference_full_approved_context_without_partial_json_or_lost_shape_rules() -> None:
    template = {**_manifest(), "description": "large-template-marker" * 2_000}
    instruction = _instruction(template=template)
    prompt = build_repair_prompt(finding("Bad type ID", attempt=1, artifact="one"), instruction=instruction)
    template_context = prompt.split("[REQUIRED MANIFEST V2 TEMPLATE]\n", 1)[1].split("\n\n[", 1)[0]
    assert (
        template_context
        == "Consult the complete original section in this ACP session; all of its constraints still apply."
    )
    assert "large-template-marker" not in prompt
    assert "[APP TYPE STANDARD — ID REFERENCE]" in prompt
    assert app_spec_declaration_rules() in prompt
    assert "显示杭州天气" in prompt


def test_new_shape_context_does_not_change_same_finding_stop_policy() -> None:
    previous = finding("app_spec: type ID must be a non-empty string", attempt=1, artifact="one")
    current = finding("app_spec: type ID must be a non-empty string", attempt=2, artifact="two")
    assert decide_widget_repair(previous, ()).action == "repair"
    assert decide_widget_repair(current, (previous,)).action == "human"
    prompt = build_repair_prompt(previous, instruction=_instruction())
    assert "`types` is an ordered, non-empty array of unique type ID strings" in prompt
