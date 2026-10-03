"""Bind production generation, runtime, and native-frame browser UI evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES = ROOT / "scripts" / "coding_agent_ui_quality_cases.json"
VIEWPORTS = {320, 640}
THEMES = {"light", "dark"}
SCREENSHOT_SIGNATURE = b"\x89PNG\r\n\x1a\n"
RENDERER_SOURCES = (
    "widget-runtime/controller_facade.mjs",
    "widget-runtime/frame_server.mjs",
    "widget-runtime/frame_shell.mjs",
    "widget-runtime/frame_shell.css",
    "widget-runtime/presentation_context.mjs",
    "widget-runtime/runtime.mjs",
)
COMMON_CHECKS = {
    "network-isolated",
    "keyboard-primary-action",
    *(f"{kind}-{width}-{theme}" for width in VIEWPORTS for theme in THEMES for kind in ("no-overflow", "accessible-controls", "runtime-clean")),
}
CASE_CHECKS = {
    "forecast-operations-dashboard": {
        "fixture-request", "current-conditions", "svg-data-chart", "svg-chart-uses-data",
        "compact-initial-summary", "expand-seven-day-details", "retry-error", "malformed-data-error",
    },
        "compact-follow-up-dashboard": {
        "compact-empty-state", "create-follow-up", "read-follow-up", "rename-follow-up",
        "toggle-done", "delete-follow-up", "storage-error", "file-write-error", "retry-file-write",
    },
}


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return payload


def artifact_digest(directory: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(directory.rglob("*")):
        if item.is_file() and not item.is_symlink():
            digest.update(item.relative_to(directory).as_posix().encode())
            digest.update(item.read_bytes())
    return digest.hexdigest()


def renderer_source_digests() -> dict[str, str]:
    """Hash the production renderer inputs whose versions are recorded by the browser runner."""
    return {
        source: hashlib.sha256((ROOT / source).read_bytes()).hexdigest()
        for source in RENDERER_SOURCES
    }


def _check_screenshots(case_root: Path, screenshots: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(screenshots, list):
        return ["screenshots is not a list"]
    initial_pairs: set[tuple[int, str]] = set()
    for screenshot in screenshots:
        if not isinstance(screenshot, dict):
            errors.append("invalid screenshot record")
            continue
        viewport = screenshot.get("viewport_css_px")
        if not isinstance(viewport, dict):
            errors.append("screenshot has no viewport metadata")
            continue
        width, theme = viewport.get("width"), screenshot.get("theme")
        if screenshot.get("phase", "initial") == "initial":
            if isinstance(width, int) and theme in THEMES:
                initial_pairs.add((width, theme))
        path_value = screenshot.get("path")
        if not isinstance(path_value, str) or not path_value:
            errors.append("screenshot path is missing")
            continue
        path = Path(path_value)
        if not path.is_absolute():
            path = case_root / path
        resolved_path = path.resolve()
        if not resolved_path.is_relative_to(case_root.resolve()):
            errors.append(f"screenshot is outside the case bundle: {path_value}")
            continue
        try:
            content = path.read_bytes()
            if len(content) < 24 or not content.startswith(SCREENSHOT_SIGNATURE):
                errors.append(f"invalid PNG screenshot: {path_value}")
                continue
            image_width = int.from_bytes(content[16:20], "big")
            image_height = int.from_bytes(content[20:24], "big")
            expected_width = width
            expected_height = viewport.get("height")
            if image_width != expected_width or image_height != expected_height:
                errors.append(f"screenshot dimensions {image_width}x{image_height} do not match viewport {expected_width}x{expected_height}: {path_value}")
        except OSError:
            errors.append(f"missing screenshot: {path_value}")
    expected_pairs = {(width, theme) for width in VIEWPORTS for theme in THEMES}
    if initial_pairs != expected_pairs:
        errors.append("initial screenshots must cover exactly 320/640 CSS px in light/dark themes")
    if not screenshots:
        errors.append("no screenshots recorded")
    return errors


def pair(generation_path: Path, browser_root: Path, cases_path: Path = DEFAULT_CASES) -> dict[str, Any]:
    generation = read_json(generation_path)
    case_doc = read_json(cases_path)
    expected_cases = case_doc.get("cases")
    generated_cases = generation.get("cases")
    if not isinstance(expected_cases, list) or not isinstance(generated_cases, list):
        raise ValueError("Case definitions and generation report must contain case arrays")
    expected_ids = [case.get("id") for case in expected_cases]
    generated_ids = [case.get("id") for case in generated_cases]
    global_errors: list[str] = []
    if not expected_ids or len(set(expected_ids)) != len(expected_ids):
        raise ValueError("Canonical case IDs must be nonempty and unique")
    if set(generated_ids) != set(expected_ids) or len(generated_ids) != len(expected_ids):
        global_errors.append("generation case IDs differ from the canonical case set")
    actual_case_hash = hashlib.sha256(cases_path.read_bytes()).hexdigest()
    actual_renderer_hashes = renderer_source_digests()
    case_set_hash_matches = generation.get("case_set_sha256") == actual_case_hash
    if not case_set_hash_matches:
        global_errors.append("generation case-set hash does not match the canonical JSON")
    for key, expected in (("model", "gpt-6-luna"), ("executor", "production backend.coding_agent.run_coding_agent"), ("promote", False)):
        if generation.get(key) != expected:
            global_errors.append(f"generation metadata {key!r} must be {expected!r}")

    browser_reports = sorted(browser_root.glob("*/browser-report.json"))
    browser_ids = {path.parent.name for path in browser_reports}
    if browser_ids != set(expected_ids):
        global_errors.append("browser report case directories differ from the canonical case set")
    browser_by_id = {path.parent.name: path for path in browser_reports}
    generated_by_id = {case.get("id"): case for case in generated_cases if isinstance(case, dict)}
    results = []
    for case_id in expected_ids:
        generated = generated_by_id.get(case_id)
        case_errors: list[str] = []
        if generated is None:
            case_errors.append("generation result is missing")
            generated = {}
        browser_path = browser_by_id.get(case_id)
        browser: dict[str, Any] = {}
        if browser_path is None:
            case_errors.append("browser report is missing")
            case_root = browser_root / case_id
        else:
            case_root = browser_path.parent
            try:
                browser = read_json(browser_path)
            except (OSError, ValueError) as exc:
                case_errors.append(f"browser report cannot be read: {exc}")
        if browser.get("case_id") != case_id:
            case_errors.append("browser report case_id does not match its directory")
        if not isinstance(browser.get("browser"), dict) or not browser["browser"].get("version") or not browser["browser"].get("playwright_core_version"):
            case_errors.append("browser report is missing Chromium/Playwright version provenance")
        reported_renderer_hashes = browser.get("renderer_source_sha256")
        if not isinstance(reported_renderer_hashes, dict):
            case_errors.append("browser report is missing renderer source SHA-256 provenance")
        elif set(reported_renderer_hashes) != set(actual_renderer_hashes):
            case_errors.append("renderer source SHA-256 paths differ from the required production renderer files")
        elif reported_renderer_hashes != actual_renderer_hashes:
            case_errors.append("renderer source SHA-256 values do not match the current production renderer files")
        if browser.get("case_set_sha256") != actual_case_hash:
            case_errors.append("browser case-set hash does not match the canonical JSON")
        assertions = generated.get("assertions", {})
        stage_checks_passed = (
            generated.get("production_staging_validated") is True
            and assertions.get("manifest-controller-contract") == "passed"
            and assertions.get("feature-coverage") == "passed"
            and assertions.get("runtime-first-frame") == "passed"
        )
        status = generated.get("status")
        generation_ready_field = generated.get("generation_ready")
        status_ready = (
            (generation_ready_field is True and status in {"generation_ready", "passed", "incomplete"})
            or (generation_ready_field is None and status == "passed")
        )
        generation_ok = stage_checks_passed and status_ready and generation_ready_field is not False
        if not generation_ok:
            case_errors.append("production staging, feature coverage, or first-frame validation failed")
        if browser.get("passed") is not True or browser.get("page_errors") or browser.get("blocked_network_attempts"):
            case_errors.append("browser verification failed or reported page/network errors")
        check_ids = {item.get("id") for item in browser.get("checks", []) if isinstance(item, dict) and item.get("passed") is True}
        required_checks = COMMON_CHECKS | CASE_CHECKS.get(case_id, set())
        missing_checks = sorted(required_checks - check_ids)
        if missing_checks:
            case_errors.append(f"required browser checks are missing or failed: {', '.join(missing_checks)}")
        screenshot_errors = _check_screenshots(case_root, browser.get("screenshots"))
        case_errors.extend(screenshot_errors)

        artifact_dir = case_root / "app"
        generation_hash = generated.get("artifact_hash")
        browser_hash = browser.get("artifact_hash")
        browser_artifact_dir = browser.get("artifact_dir")
        if browser_artifact_dir and Path(browser_artifact_dir).resolve() != artifact_dir.resolve():
            case_errors.append("browser artifact_dir does not identify the retained case/app directory")
        try:
            actual_hash = artifact_digest(artifact_dir)
            if actual_hash != generation_hash or actual_hash != browser_hash:
                case_errors.append("retained artifact SHA-256 differs from generation or browser report")
        except OSError as exc:
            actual_hash = None
            case_errors.append(f"retained artifact tree is unavailable: {exc}")

        results.append({
            "id": case_id,
            "passed": not case_errors and case_set_hash_matches and not global_errors,
            "production_staging_validated": generated.get("production_staging_validated") is True,
            "production_first_frame": assertions.get("runtime-first-frame") == "passed",
            "feature_coverage": assertions.get("feature-coverage") == "passed",
            "artifact_hash": generation_hash,
            "browser_artifact_hash": browser_hash,
            "retained_artifact_hash": actual_hash,
            "case_set_hash_matches": case_set_hash_matches,
            "browser_checks": browser.get("checks", []),
            "screenshots": browser.get("screenshots", []),
            "browser": browser.get("browser"),
            "renderer_source_sha256": browser.get("renderer_source_sha256"),
            "errors": case_errors,
            "generation_report": str(generation_path.resolve()),
            "browser_report": str((browser_path or case_root / "browser-report.json").resolve()),
        })
    return {
        "format": 1,
        "model": generation.get("model"),
        "executor": generation.get("executor"),
        "promote": generation.get("promote"),
        "case_set_sha256": actual_case_hash,
        "generation_report_sha256": hashlib.sha256(generation_path.read_bytes()).hexdigest(),
        "cases": results,
        "complete_passes": sum(case["passed"] for case in results),
        "passed": bool(results) and all(case["passed"] for case in results) and not global_errors,
        "errors": global_errors,
        "interpretation": "Case-specific objective evidence for human visual review; not a universal visual-quality score.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generation", type=Path, required=True)
    parser.add_argument("--browser-root", type=Path, required=True)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = pair(args.generation, args.browser_root, args.cases)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "complete_passes": result["complete_passes"], "cases": len(result["cases"]), "errors": result["errors"]}))
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
