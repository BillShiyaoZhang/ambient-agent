"""Structured, bounded repair policy for generated Widget artifacts."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

Repairability = Literal["deterministic", "code_only", "design_change", "operator"]
ContractImpact = Literal["none", "subset_only", "expansion", "unknown"]
RepairAction = Literal["repair", "design", "operator", "human"]

_OPERATOR_CODES = {
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
_NORMALIZE_DIAGNOSTIC_PATTERN = re.compile(r"\b(?:0x)?[0-9a-f]{8,}\b|\b\d+\b", re.IGNORECASE)


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
    normalized = _NORMALIZE_DIAGNOSTIC_PATTERN.sub("#", f"{stage}|{code}|{message}")
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
    )


def decide_widget_repair(
    finding: RepairFinding,
    history: Sequence[RepairFinding],
    *,
    max_repairs: int = 3,
) -> RepairDirective:
    """Make the authority decision independently of any model session."""

    if finding.repairability == "operator" or finding.contract_impact == "unknown":
        return RepairDirective("operator", "The failure is outside the generated artifact or has unknown effects.")
    if finding.repairability == "design_change" or finding.contract_impact == "expansion":
        return RepairDirective("design", "Repair would change the approved design or expand authority.")
    if len(history) >= max_repairs:
        return RepairDirective("human", "The automatic repair budget is exhausted.")
    if history:
        previous = history[-1]
        if previous.signature == finding.signature:
            return RepairDirective("human", "The same verifier finding repeated after an automatic repair.")
        if previous.artifact_hash == finding.artifact_hash:
            return RepairDirective("human", "The coding agent did not change the contract-bearing artifact.")
    return RepairDirective("repair", "The finding is local to code and preserves the approved Runtime Contract.")


def approved_runtime_contract_excerpt(instruction: str) -> str:
    marker = "[APPROVED RUNTIME CONTRACT — REFERENCE ONLY]"
    start = instruction.find(marker)
    if start < 0:
        return ""
    end = instruction.find("\n\n[SYSTEM CAPABILITIES]", start)
    return instruction[start : end if end >= 0 else None][:24_000]


def build_repair_prompt(finding: RepairFinding, *, instruction: str) -> str:
    contract = approved_runtime_contract_excerpt(instruction)
    contract_context = f"\n\n{contract}" if contract else ""
    finding_payload = json.dumps(finding.to_dict(), ensure_ascii=False, sort_keys=True, indent=2)
    return (
        "The staged Widget failed mandatory independent validation. Repair controller.js and/or manifest.json "
        "in place, then inspect the whole files for the same class of mistake. Do not create unsupported files. "
        "Preserve the approved behavior, but never add or broaden capabilities, schemas, entities, operations, "
        "sources, paths, actions, or host APIs. The Runtime Contract is an approval envelope rather than the "
        "Manifest schema; map only its approved Manifest fields. If the requested behavior cannot be implemented "
        "within the approved contract, leave the contract unchanged and explain the blocker. Do not claim success "
        "until the files themselves are repaired.\n\n"
        f"[STRUCTURED REPAIR FINDING]\n{finding_payload}{contract_context}"
    )
