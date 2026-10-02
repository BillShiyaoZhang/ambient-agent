"""Merge generated-app fixture interaction reports into a sanitized batch summary."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CASE_FILE = ROOT / "scripts" / "coding_agent_eval_cases.json"


def artifact_hash(directory: Path) -> str:
    digest = hashlib.sha256()
    files = [directory / name for name in ("controller.js", "manifest.json", "README.md")]
    data_dir = directory / "data"
    if data_dir.is_dir():
        files.extend(data_dir.rglob("*"))
    for item in sorted(file for file in files if file.is_file() and not file.is_symlink()):
        digest.update(item.relative_to(directory).as_posix().encode())
        digest.update(item.read_bytes())
    return digest.hexdigest()


def merge(
    batch: dict[str, Any],
    cases: list[dict[str, Any]],
    artifact_root: Path,
    interaction_root: Path,
    smoke: dict[str, Any],
) -> dict[str, Any]:
    expected_by_case = {case["id"]: case["acceptance"] for case in cases}
    smoke_by_case = {item["id"]: item for item in smoke.get("results", [])}
    merged_cases: list[dict[str, Any]] = []
    for result in batch.get("cases", []):
        case_id = result["id"]
        acceptance = expected_by_case[case_id]
        assertions = dict.fromkeys(acceptance, "not_run")
        assertions.update(result.get("assertions", {}))
        app_dir = artifact_root / case_id
        observed_hash = artifact_hash(app_dir) if app_dir.is_dir() else None
        batch_hash_matches = observed_hash is not None and result.get("artifact_hash") == observed_hash
        staging_was_validated = result.get("production_staging_validated") is True
        if not batch_hash_matches:
            result["artifact_integrity"] = "failed"
        elif staging_was_validated:
            result["artifact_integrity"] = "passed"
        else:
            result["artifact_integrity"] = "unverified"
        if batch_hash_matches and staging_was_validated:
            result["artifact_dir"] = str(app_dir)
            assertions["manifest-controller-contract"] = "passed"
            assertions["feature-coverage"] = "passed"
        smoke_result = smoke_by_case.get(case_id, {})
        smoke_matches = (
            smoke_result.get("status") == "passed"
            and smoke_result.get("artifact_hash") == observed_hash
            and batch_hash_matches
            and staging_was_validated
        )
        result["runtime_smoke_integrity"] = "passed" if smoke_matches else "failed"
        if smoke_matches and "runtime-first-frame" in assertions:
            assertions["runtime-first-frame"] = "passed"
        elif "runtime-first-frame" in assertions:
            assertions["runtime-first-frame"] = "failed" if smoke_result.get("status") == "failed" else "not_run"
        interaction_path = interaction_root / case_id / "interaction-result.json"
        if interaction_path.is_file():
            interaction = json.loads(interaction_path.read_text(encoding="utf-8"))
            interaction_hash_matches = (
                interaction.get("case_id") == case_id
                and interaction.get("artifact_hash") == observed_hash
                and batch_hash_matches
            )
            result["interaction_integrity"] = "passed" if interaction_hash_matches else "failed"
            if interaction_hash_matches:
                checks = {check["id"] for check in interaction.get("checks", []) if check.get("passed") is True}
                error = str(interaction.get("error") or "")
                for assertion in acceptance:
                    if assertion in {"manifest-controller-contract", "feature-coverage", "runtime-first-frame"}:
                        continue
                    if assertion in checks:
                        assertions[assertion] = "passed"
                    elif interaction.get("passed") is not True and f"waiting for {assertion}" in error:
                        assertions[assertion] = "failed"
            result["interaction_passed"] = bool(interaction.get("passed", False)) and interaction_hash_matches
        result["assertions"] = assertions
        result["status"] = (
            "failed"
            if result.get("artifact_integrity") == "failed"
            or result.get("runtime_smoke_integrity") == "failed"
            or result.get("interaction_integrity") == "failed"
            or result.get("interaction_passed") is False
            else (
                "passed"
                if all(value == "passed" for value in assertions.values())
                and result.get("artifact_integrity") == "passed"
                and result.get("runtime_smoke_integrity") == "passed"
                and result.get("interaction_integrity") == "passed"
                and result.get("interaction_passed") is True
                else "failed"
                if any(value == "failed" for value in assertions.values())
                else "incomplete"
            )
        )
        merged_cases.append(result)
    output = {**batch, "cases": merged_cases}
    output["complete_passes"] = sum(item["status"] == "passed" for item in merged_cases)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True, help="Sanitized production generation summary JSON")
    parser.add_argument(
        "--artifact-root", type=Path, required=True, help="Retained generated controller/manifest directory"
    )
    parser.add_argument(
        "--interaction-root", type=Path, required=True, help="Directory containing per-case interaction-result.json"
    )
    parser.add_argument("--smoke", type=Path, required=True, help="Exact-artifact Runtime smoke JSON")
    parser.add_argument("--output", type=Path, required=True, help="Merged result JSON path")
    args = parser.parse_args()
    batch = json.loads(args.batch.read_text(encoding="utf-8"))
    smoke = json.loads(args.smoke.read_text(encoding="utf-8"))
    cases = json.loads(CASE_FILE.read_text(encoding="utf-8"))["cases"]
    merged = merge(batch, cases, args.artifact_root, args.interaction_root, smoke)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "complete_passes": merged["complete_passes"]}))
    return 0 if merged["complete_passes"] == len(merged["cases"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
