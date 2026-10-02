"""Generate and smoke-check the representative coding-agent evaluation cases.

Run from the repository root, preferably inside the production backend container. The
execution mode performs three real Codex turns and stores only sanitized summaries.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from backend.app_manifest import AppManifest
from backend.coding_agent import run_coding_agent
from backend.coding_agent_acp import (
    CodingAgentDraftError,
    CodingAgentStagedResult,
    validate_coding_agent_feature_coverage,
)
from backend.graph_db import GraphDatabase
from backend.widget_runtime_smoke import WidgetRuntimeSmokeTester

ROOT = Path(__file__).resolve().parent.parent
CASE_FILE = ROOT / "scripts" / "coding_agent_eval_cases.json"
MODEL = "gpt-6-luna"


def load_cases() -> list[dict[str, Any]]:
    payload = json.loads(CASE_FILE.read_text(encoding="utf-8"))
    if payload.get("format") != 1 or not isinstance(payload.get("cases"), list):
        raise ValueError("Unsupported coding-agent evaluation case format")
    cases = payload["cases"]
    ids = [case.get("id") for case in cases]
    if not cases or len(ids) != len(set(ids)):
        raise ValueError("Evaluation cases must have unique IDs")
    for case in cases:
        AppManifest.from_dict(case["manifest"], expected_app_id=case["id"])
        if not isinstance(case.get("acceptance"), list) or not case["acceptance"]:
            raise ValueError(f"Case {case['id']} has no acceptance assertions")
    return cases


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(path.rglob("*")):
        if item.is_file() and not item.is_symlink():
            digest.update(item.relative_to(path).as_posix().encode())
            digest.update(item.read_bytes())
    return digest.hexdigest()


async def _run_case(
    case: dict[str, Any],
    runtime_smoke: WidgetRuntimeSmokeTester,
    artifact_dir: Path,
    repair_feedback: str | None = None,
) -> dict[str, Any]:
    app_id = case["id"]
    manifest_template = case["manifest"]
    try:
        result = await run_coding_agent(
            app_id,
            case["instruction"]
            + (
                f"\n\nRepair feedback from the prior production interaction test: {repair_feedback}"
                if repair_feedback
                else ""
            ),
            language="en",
            promote=False,
            coding_agent="codex",
            model_config={"mode": "native", "native_model": MODEL},
            manifest_template=manifest_template,
            artifact_validator=lambda result: validate_coding_agent_feature_coverage(
                result.staging_dir,
                result.app_id,
                case["requirements"],
            ),
        )
    except CodingAgentDraftError as exc:
        retained = artifact_dir / app_id
        shutil.copytree(exc.staged_result.staging_dir, retained)
        return {
            "id": app_id,
            "status": "failed",
            "production_staging_validated": False,
            "model": MODEL,
            "error_code": exc.error_code,
            "repair_action": exc.repair_action,
            "artifact_hash": exc.staged_result.artifact_hash,
            "artifact_dir": str(retained),
            "assertions": dict.fromkeys(case["acceptance"], "not_run"),
        }
    if not isinstance(result, CodingAgentStagedResult):
        return {"id": app_id, "status": "failed", "model": MODEL, "error_code": "unexpected_result"}

    artifact = result.staging_dir
    assertions = dict.fromkeys(case["acceptance"], "not_run")
    contract_checks: dict[str, str] = {}
    for assertion, check in (
        ("manifest-v2-valid", lambda: AppManifest.read(artifact / "manifest.json", expected_app_id=app_id)),
        ("controller-present", lambda: _require_controller(artifact)),
    ):
        try:
            check()
            contract_checks[assertion] = "passed"
        except Exception:
            contract_checks[assertion] = "failed"
        if assertion in assertions:
            assertions[assertion] = contract_checks[assertion]
    if "manifest-controller-contract" in assertions:
        assertions["manifest-controller-contract"] = (
            "passed"
            if contract_checks.get("manifest-v2-valid") == "passed"
            and contract_checks.get("controller-present") == "passed"
            else "failed"
        )
    if "feature-coverage" in assertions:
        assertions["feature-coverage"] = "passed"

    runtime_status = "not_run"
    try:
        await runtime_smoke.verify(result)
        runtime_status = "passed"
        if "runtime-first-frame" in assertions:
            assertions["runtime-first-frame"] = "passed"
    except Exception as exc:
        runtime_status = "failed"
        runtime_error = str(getattr(exc, "code", type(exc).__name__))
        if "runtime-first-frame" in assertions:
            assertions["runtime-first-frame"] = "failed"
    retained = artifact_dir / app_id
    if retained.exists():
        raise FileExistsError(f"Refusing to overwrite retained artifacts for {app_id}")
    shutil.copytree(artifact, retained)
    return {
        "id": app_id,
        "status": "passed" if all(value == "passed" for value in assertions.values()) else "incomplete",
        "production_staging_validated": True,
        "model": MODEL,
        "artifact_hash": _digest(artifact),
        "artifact_dir": str(retained),
        "repair_attempts": result.repair_attempts,
        "runtime_smoke": runtime_status,
        **({"runtime_error_code": runtime_error} if runtime_status == "failed" else {}),
        "assertions": assertions,
    }


def _require_controller(path: Path) -> None:
    if not (path / "controller.js").is_file():
        raise FileNotFoundError("controller.js is missing")


async def execute(
    artifact_dir: Path,
    *,
    case_ids: set[str] | None = None,
    seed_dir: Path | None = None,
    repair_feedback: str | None = None,
) -> dict[str, Any]:
    cases = load_cases()
    if case_ids:
        cases = [case for case in cases if case["id"] in case_ids]
        if not cases or {case["id"] for case in cases} != case_ids:
            raise ValueError("Unknown evaluation case ID")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    original_workspace = os.environ.get("WORKSPACE_DIR")
    original_apps = os.environ.get("APPS_DIR")
    original_graph = os.environ.get("GRAPH_DATABASE_BACKEND")
    with tempfile.TemporaryDirectory(prefix="ambient-coding-agent-eval-") as temp:
        work_dir = Path(temp)
        os.environ["WORKSPACE_DIR"] = str(work_dir)
        os.environ["APPS_DIR"] = str(work_dir / "apps")
        os.environ["GRAPH_DATABASE_BACKEND"] = "sqlite"
        Path(os.environ["APPS_DIR"]).mkdir(parents=True, exist_ok=True)
        if seed_dir:
            for case in cases:
                source = seed_dir / case["id"]
                if source.is_dir():
                    shutil.copytree(source, Path(os.environ["APPS_DIR"]) / case["id"])
        graph = GraphDatabase(str(work_dir / "graph"))
        tester = WidgetRuntimeSmokeTester(graph_db=graph)
        results = []
        try:
            for case in cases:
                started = time.perf_counter()
                case_feedback = repair_feedback or (case.get("repair_feedback") if seed_dir else None)
                outcome = await _run_case(case, tester, artifact_dir, case_feedback)
                outcome["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
                results.append(outcome)
        finally:
            graph.close()
            for name, old in (
                ("WORKSPACE_DIR", original_workspace),
                ("APPS_DIR", original_apps),
                ("GRAPH_DATABASE_BACKEND", original_graph),
            ):
                if old is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = old
    return {
        "format": 1,
        "model": MODEL,
        "executor": "production backend.coding_agent.run_coding_agent",
        "promote": False,
        "repair_run": bool(seed_dir),
        "case_set_sha256": hashlib.sha256(CASE_FILE.read_bytes()).hexdigest(),
        "repair_feedback_sha256": hashlib.sha256(repair_feedback.encode()).hexdigest() if repair_feedback else None,
        "cases": results,
        "complete_passes": sum(case["status"] == "passed" for case in results),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="List cases without model calls")
    parser.add_argument("--execute", action="store_true", help="Run all three paid model cases")
    parser.add_argument("--output", type=Path, help="Write sanitized JSON evidence to this path")
    parser.add_argument("--case", action="append", dest="case_ids", help="Run one named case; repeatable")
    parser.add_argument("--seed-dir", type=Path, help="Existing case artifact directory to continue editing")
    parser.add_argument(
        "--feedback", help="Observed behavior from a prior interaction run; applies only with --seed-dir"
    )
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=ROOT / "docs" / "verification" / "coding-agent-app-evaluation-artifacts",
        help="Retain generated, unpromoted artifacts here for interaction tests",
    )
    args = parser.parse_args()
    if args.list == args.execute:
        parser.error("choose exactly one of --list or --execute")
    if args.list:
        print(json.dumps(load_cases(), ensure_ascii=False, indent=2))
        return 0
    if args.feedback and not args.seed_dir:
        parser.error("--feedback only applies with --seed-dir")
    total_started = time.perf_counter()
    evidence = asyncio.run(
        execute(
            args.artifact_dir,
            case_ids=set(args.case_ids) if args.case_ids else None,
            seed_dir=args.seed_dir,
            repair_feedback=args.feedback,
        )
    )
    evidence["elapsed_ms"] = round((time.perf_counter() - total_started) * 1000)
    serialized = json.dumps(evidence, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0 if evidence["complete_passes"] == len(evidence["cases"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
