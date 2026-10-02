"""Structured, bounded repair policy for generated Widget artifacts."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from backend.app_types import (
    APP_SPEC_VERSION,
    MAX_APP_FEATURES,
    MAX_APP_TYPES,
    MAX_FEATURE_NOTES_LENGTH,
    MAX_SPEC_ID_LENGTH,
    project_app_type_prompt_reference,
)

Repairability = Literal["deterministic", "code_only", "design_change", "operator"]
ContractImpact = Literal["none", "subset_only", "expansion", "unknown"]
RepairAction = Literal["repair", "design", "operator", "human"]

_OPERATOR_CODES = {
    "coding_agent_code_mode_unavailable",
    "widget_runtime_budget_exceeded",
    "widget_runtime_unavailable",
    "widget_verifier_execution_failed",
    "widget_verifier_unavailable",
}
_DESIGN_CODES = {
    "contract_expansion_required",
    "design_change_required",
    "schema_extension_required",
}
_NORMALIZE_DIAGNOSTIC_WHITESPACE = re.compile(r"\s+")
_MAX_REPAIR_METADATA_CHARS = 512
_MAX_REPAIR_LOCATIONS = 16


def app_spec_declaration_rules() -> str:
    """One manifest-instance shape contract for generation and repair prompts."""

    example = json.dumps(
        {
            "spec_version": APP_SPEC_VERSION,
            "types": ["custom:weather"],
            "features": [{"id": "custom:weather.forecast", "status": "partial", "surfaces": ["ui"]}],
        },
        separators=(",", ":"),
    )
    return (
        "Optional `app_spec` is an implementation declaration with exactly `spec_version`, `types`, and `features`. "
        f"`spec_version` is the integer {APP_SPEC_VERSION}. `types` is an ordered, non-empty array of unique type ID strings "
        "(primary type first), never objects. The App Type Standard catalog's `types` entries are metadata objects: "
        "copy only each selected `id` string, never its `title`, `description`, or `features` object into `types`. "
        "The model-facing reference uses `type_ids`, `feature_ids_by_type`, and ID-keyed descriptions; "
        "these lookup keys are not Manifest fields. "
        "`features` is an array of declaration objects with `id`, `status`, `surfaces`, and optional string `notes`; "
        "never an array of ID strings or catalog feature metadata. Status is `implemented`, `partial`, or `planned`. "
        "Implemented and partial features require at least one actual surface (`data`, `tools`, or `ui`); "
        "planned features use an empty surfaces array. IDs and surfaces must be unique. "
        f"Declare at most {MAX_APP_TYPES} types and {MAX_APP_FEATURES} features; each ID is a non-empty string "
        f"of at most {MAX_SPEC_ID_LENGTH} characters and notes are at most {MAX_FEATURE_NOTES_LENGTH} characters. "
        "Standard feature IDs "
        "must belong to a declared type. Custom types use `custom:<namespace>` and their feature IDs use "
        "`custom:<namespace>.<feature>`, with lowercase alphanumeric or kebab-case names; a single word such as "
        "`custom:weather` is valid and does not require a hyphen. Choose declarations from actually delivered "
        "behavior; a capability grant alone proves no implemented feature. For an existing App, preserve and adjust "
        "its declaration to match the delivered implementation; correct a repairable declaration rather than "
        "deleting it to evade validation. Declarations do not grant permissions or change "
        "the approved Runtime Contract. This complete valid custom example illustrates shape only; select the "
        "appropriate IDs and truthful statuses for the actual App:\n\n"
        "```json\n"
        f"{example}\n"
        "```"
    )


def _diagnostic_text(value: object) -> str:
    # Structured diagnostics carry shape names, never stringify raw model data.
    return value.strip()[:_MAX_REPAIR_METADATA_CHARS] if isinstance(value, str) else ""


def _finding_locations(exc: Exception) -> tuple[str, ...]:
    raw = getattr(exc, "locations", ())
    if isinstance(raw, str):
        raw = (raw,)
    locations: list[str] = []
    if isinstance(raw, (list, tuple)):
        for item in raw[:_MAX_REPAIR_LOCATIONS]:
            location = _diagnostic_text(item)
            if location and location not in locations:
                locations.append(location)
    if not locations:
        path = _diagnostic_text(getattr(exc, "path", ""))
        if path:
            if path.startswith("app_spec"):
                path = f"manifest.json:$.{path}"
            elif path.startswith("$"):
                path = f"manifest.json:{path}"
            locations.append(path[:_MAX_REPAIR_METADATA_CHARS])
    return tuple(locations)


@dataclass(frozen=True, slots=True)
class RepairFinding:
    code: str
    stage: str
    message: str
    signature: str
    attempt: int
    repairability: Repairability
    contract_impact: ContractImpact
    artifact_hash: str
    expected: str = ""
    observed: str = ""
    locations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["locations"] = list(self.locations)
        return payload


@dataclass(frozen=True, slots=True)
class RepairDirective:
    action: RepairAction
    reason: str


def repair_finding_from_dict(value: object) -> RepairFinding:
    """Restore one persisted verifier finding without weakening its types."""

    if not isinstance(value, dict):
        raise ValueError("Persisted repair finding must be an object")
    locations = value.get("locations", ())
    if not isinstance(locations, (list, tuple)) or not all(isinstance(item, str) for item in locations):
        raise ValueError("Persisted repair finding locations must be strings")
    repairability = str(value.get("repairability") or "")
    if repairability not in {"deterministic", "code_only", "design_change", "operator"}:
        raise ValueError("Persisted repair finding has invalid repairability")
    contract_impact = str(value.get("contract_impact") or "")
    if contract_impact not in {"none", "subset_only", "expansion", "unknown"}:
        raise ValueError("Persisted repair finding has invalid contract impact")
    return RepairFinding(
        code=str(value.get("code") or ""),
        stage=str(value.get("stage") or ""),
        message=str(value.get("message") or "")[:12_000],
        signature=str(value.get("signature") or ""),
        attempt=max(1, int(value.get("attempt") or 1)),
        repairability=repairability,  # type: ignore[arg-type]
        contract_impact=contract_impact,  # type: ignore[arg-type]
        artifact_hash=str(value.get("artifact_hash") or ""),
        expected=str(value.get("expected") or ""),
        observed=str(value.get("observed") or ""),
        locations=tuple(locations),
    )


def artifact_hash(staging_dir: Path) -> str:
    """Hash the generated contract-bearing files without traversing App data."""

    digest = hashlib.sha256()
    for name in ("controller.js", "manifest.json", "README.md"):
        path = staging_dir / name
        digest.update(name.encode("utf-8"))
        if not path.is_file():
            digest.update(b"<missing>")
            continue
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<unreadable>")
    return digest.hexdigest()


def finding_from_exception(
    exc: Exception,
    *,
    attempt: int,
    artifact_revision: str,
) -> RepairFinding:
    code = str(getattr(exc, "code", "") or type(exc).__name__)
    stage = str(getattr(exc, "stage", "") or "artifact_validation")
    message = str(exc)[:12_000]
    if code in _OPERATOR_CODES:
        repairability: Repairability = "operator"
        contract_impact: ContractImpact = "unknown"
    elif code in _DESIGN_CODES:
        repairability = "design_change"
        contract_impact = "expansion"
    elif type(exc).__name__ in {"CodingAgentArtifactError", "OpenCodeArtifactError"} or code in {
        "artifact_validation_failed",
        "capability_contract_error",
        "forbidden_runtime_api",
        "runtime_contract_mismatch",
        "widget_runtime_code_error",
        "widget_syntax_error",
        "widget_verification_failed",
    }:
        repairability = "code_only"
        contract_impact = "none"
    else:
        repairability = "operator"
        contract_impact = "unknown"
    # Preserve line/column numbers and source excerpts: "exactly the same"
    # means the verifier returned the same finding, not merely the same broad
    # error class at a different source location. Whitespace-only transport
    # differences are safe to ignore.
    normalized = _NORMALIZE_DIAGNOSTIC_WHITESPACE.sub(" ", f"{stage}|{code}|{message}").strip()
    signature = hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()
    return RepairFinding(
        code=code,
        stage=stage,
        message=message,
        signature=signature,
        attempt=attempt,
        repairability=repairability,
        contract_impact=contract_impact,
        artifact_hash=artifact_revision,
        expected=_diagnostic_text(getattr(exc, "expected", "")),
        observed=_diagnostic_text(getattr(exc, "observed", "")),
        locations=_finding_locations(exc),
    )


def decide_widget_repair(
    finding: RepairFinding,
    history: Sequence[RepairFinding],
    *,
    max_repairs: int | None = None,
) -> RepairDirective:
    """Make the authority decision independently of any model session."""

    if finding.repairability == "operator" or finding.contract_impact == "unknown":
        return RepairDirective("operator", "The failure is outside the generated artifact or has unknown effects.")
    if finding.repairability == "design_change" or finding.contract_impact == "expansion":
        return RepairDirective("design", "Repair would change the approved design or expand authority.")
    if history:
        previous = history[-1]
        if previous.signature == finding.signature:
            return RepairDirective("human", "The same verifier finding repeated after an automatic repair.")
        if previous.artifact_hash == finding.artifact_hash:
            return RepairDirective("human", "The coding agent did not change the contract-bearing artifact.")
    if max_repairs is not None and len(history) >= max_repairs:
        return RepairDirective("human", "The automatic repair budget is exhausted.")
    return RepairDirective("repair", "The finding is local to code and preserves the approved Runtime Contract.")


_INSTRUCTION_SECTION = re.compile(r"(?m)^\[[A-Z][A-Z0-9 _—-]*\][ \t]*(?:\n|$)")


def _instruction_section(instruction: str, marker: str) -> str:
    start = re.search(rf"(?m)^{re.escape(marker)}[ \t]*\n", instruction)
    if start is None:
        return ""
    end = _INSTRUCTION_SECTION.search(instruction, start.end())
    return instruction[start.end() : end.start() if end else None].strip()


def _bounded_context(marker: str, text: str, *, max_chars: int) -> str:
    if not text:
        return ""
    if len(text) > max_chars:
        # Do not hand the model a truncated contract, JSON object, or request.
        # Repairs share the original ACP session, where the complete approved
        # instruction remains authoritative and can be consulted unchanged.
        text = "Consult the complete original section in this ACP session; all of its constraints still apply."
    return f"{marker}\n{text}"


def approved_runtime_contract_excerpt(instruction: str) -> str:
    marker = "[APPROVED RUNTIME CONTRACT — REFERENCE ONLY]"
    return _bounded_context(marker, _instruction_section(instruction, marker), max_chars=8_000)


def _manifest_template_context(instruction: str) -> str:
    marker = "[REQUIRED MANIFEST V2 TEMPLATE]"
    raw = _instruction_section(instruction, marker)
    if not raw:
        return ""
    try:
        template = json.loads(raw)
    except (ValueError, TypeError):
        template = None
    if isinstance(template, dict):
        fields = (
            "manifest_version",
            "id",
            "title",
            "description",
            "app_version",
            "intents",
            "schema_refs",
            "capabilities",
            "app_spec",
        )
        raw = json.dumps(
            {key: template[key] for key in fields if key in template}, ensure_ascii=False, separators=(",", ":")
        )
    return _bounded_context(marker, raw, max_chars=8_000)


def _app_type_catalog_context(instruction: str) -> str:
    marker = "[APP TYPE STANDARD]"
    raw = _instruction_section(instruction, marker)
    if not raw:
        return ""
    try:
        catalog = json.loads(raw)
        raw = json.dumps(
            project_app_type_prompt_reference(catalog, include_descriptions=False),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except (ValueError, TypeError, KeyError):
        raw = "Consult the complete App Type Standard in the original ACP instruction; do not invent standard IDs."
    return _bounded_context("[APP TYPE STANDARD — ID REFERENCE]", raw, max_chars=6_000)


def _repair_instruction_context(instruction: str) -> str:
    first_section = _INSTRUCTION_SECTION.search(instruction)
    request = instruction[: first_section.start() if first_section else None].strip()
    sections = (
        _bounded_context("[ORIGINAL APPROVED REQUEST]", request, max_chars=4_000),
        _bounded_context(
            "[APPROVED DEVELOPMENT PLAN]",
            _instruction_section(instruction, "[APPROVED DEVELOPMENT PLAN]"),
            max_chars=4_000,
        ),
        approved_runtime_contract_excerpt(instruction),
        _manifest_template_context(instruction),
        _app_type_catalog_context(instruction),
    )
    return "\n\n".join(section for section in sections if section)


def build_repair_prompt(finding: RepairFinding, *, instruction: str) -> str:
    context = _repair_instruction_context(instruction)
    contract_context = f"\n\n{context}" if context else ""
    finding_payload = json.dumps(finding.to_dict(), ensure_ascii=False, sort_keys=True, indent=2)
    return (
        "The staged Widget failed mandatory independent validation. Repair controller.js and/or manifest.json "
        "in place, then inspect the whole files for the same class of mistake. Do not create unsupported files. "
        "Preserve the approved behavior, but never add or broaden capabilities, schemas, entities, operations, "
        "sources, paths, actions, or host APIs. The Runtime Contract is an approval envelope rather than the "
        "Manifest schema; map only its approved Manifest fields. If the requested behavior cannot be implemented "
        "within the approved contract, leave the contract unchanged and explain the blocker. Do not claim success "
        "until the files themselves are repaired.\n\n"
        f"[STRUCTURED REPAIR FINDING]\n{finding_payload}\n\n"
        f"[APP TYPE DECLARATION RULES]\n{app_spec_declaration_rules()}{contract_context}"
    )
