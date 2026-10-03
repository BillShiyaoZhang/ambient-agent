from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import struct
import zlib
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "merge_coding_agent_ui_quality_results.py"
spec = importlib.util.spec_from_file_location("coding_agent_ui_quality_pairing", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
GENERATION_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "evaluate_coding_agent_ui_quality.py"
generation_spec = importlib.util.spec_from_file_location("coding_agent_ui_quality_generation", GENERATION_SCRIPT)
assert generation_spec and generation_spec.loader
generation_module = importlib.util.module_from_spec(generation_spec)
generation_spec.loader.exec_module(generation_module)


CASE_IDS = ("forecast-operations-dashboard", "compact-follow-up-dashboard")
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
    *(
        f"{check}-{width}-{theme}"
        for width in (320, 640)
        for theme in ("light", "dark")
        for check in ("no-overflow", "accessible-controls", "runtime-clean")
    ),
}


def _png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        payload = kind + data
        return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    row = b"\x00" + b"\x00\x00\x00\x00" * width
    pixels = zlib.compress(row * height)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")


CASE_CHECKS = {
    CASE_IDS[0]: {
        "fixture-request",
        "current-conditions",
        "svg-data-chart",
        "svg-chart-uses-data",
        "compact-initial-summary",
        "expand-seven-day-details",
        "retry-error",
        "malformed-data-error",
    },
    CASE_IDS[1]: {
        "compact-empty-state",
        "create-follow-up",
        "read-follow-up",
        "rename-follow-up",
        "toggle-done",
        "delete-follow-up",
        "storage-error",
        "file-write-error",
        "retry-file-write",
    },
}


def _json_write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _digest(directory: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(directory.rglob("*")):
        if item.is_file() and not item.is_symlink():
            digest.update(item.relative_to(directory).as_posix().encode())
            digest.update(item.read_bytes())
    return digest.hexdigest()


def _renderer_hashes() -> dict[str, str]:
    root = Path(__file__).resolve().parents[2]
    return {source: hashlib.sha256((root / source).read_bytes()).hexdigest() for source in RENDERER_SOURCES}


def _evidence_tree(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, object]]:
    canonical_cases = {"format": 1, "cases": [{"id": case_id} for case_id in CASE_IDS]}
    cases_path = tmp_path / "cases.json"
    _json_write(cases_path, canonical_cases)
    case_hash = hashlib.sha256(cases_path.read_bytes()).hexdigest()
    browser_root = tmp_path / "browser"
    generation_cases = []

    for case_id in CASE_IDS:
        case_root = browser_root / case_id
        app_dir = case_root / "app"
        app_dir.mkdir(parents=True)
        (app_dir / "controller.js").write_text(
            f"export default function App() {{ return {json.dumps(case_id)}; }}\n", encoding="utf-8"
        )
        _json_write(app_dir / "manifest.json", {"id": case_id, "manifest_version": 2})
        artifact_hash = _digest(app_dir)

        screenshots = []
        for width in (320, 640):
            for theme in ("light", "dark"):
                screenshot_path = case_root / "screenshots" / f"{width}-{theme}.png"
                screenshot_path.parent.mkdir(parents=True, exist_ok=True)
                screenshot_path.write_bytes(_png(width, 680))
                screenshots.append(
                    {
                        "viewport_css_px": {"width": width, "height": 680},
                        "theme": theme,
                        "phase": "initial",
                        "path": str(screenshot_path),
                    }
                )

        checks = sorted(COMMON_CHECKS | CASE_CHECKS[case_id])
        browser_report = {
            "case_id": case_id,
            "case_set_sha256": case_hash,
            "artifact_hash": artifact_hash,
            "artifact_dir": str(app_dir),
            "browser": {"name": "Chromium", "version": "140.0.0", "playwright_core_version": "1.54.0"},
            "renderer_source_sha256": _renderer_hashes(),
            "passed": True,
            "checks": [{"id": check, "passed": True} for check in checks],
            "screenshots": screenshots,
            "page_errors": [],
            "blocked_network_attempts": [],
        }
        _json_write(case_root / "browser-report.json", browser_report)
        generation_cases.append(
            {
                "id": case_id,
                # Browser acceptance is still pending; generation evidence is complete.
                "status": "incomplete",
                "generation_ready": True,
                "production_staging_validated": True,
                "artifact_hash": artifact_hash,
                "assertions": {
                    "manifest-controller-contract": "passed",
                    "feature-coverage": "passed",
                    "runtime-first-frame": "passed",
                },
            }
        )

    generation = {
        "model": "gpt-6-luna",
        "executor": "production backend.coding_agent.run_coding_agent",
        "promote": False,
        "case_set_sha256": case_hash,
        "cases": generation_cases,
    }
    generation_path = tmp_path / "generation.json"
    _json_write(generation_path, generation)
    return generation_path, browser_root, cases_path, generation


def test_pair_accepts_complete_canonical_evidence_while_browser_stage_is_pending(tmp_path):
    generation_path, browser_root, cases_path, _ = _evidence_tree(tmp_path)

    paired = module.pair(generation_path, browser_root, cases_path)

    assert paired["passed"] is True
    assert paired["complete_passes"] == 2
    assert [case["id"] for case in paired["cases"]] == list(CASE_IDS)


def test_pair_rejects_retained_artifact_modification(tmp_path):
    generation_path, browser_root, cases_path, _ = _evidence_tree(tmp_path)
    changed_controller = browser_root / CASE_IDS[0] / "app" / "controller.js"
    changed_controller.write_text(changed_controller.read_text(encoding="utf-8") + "// changed\n", encoding="utf-8")

    paired = module.pair(generation_path, browser_root, cases_path)

    assert paired["passed"] is False
    assert "retained artifact SHA-256 differs" in " ".join(paired["cases"][0]["errors"])


def test_pair_rejects_a_missing_required_browser_check(tmp_path):
    generation_path, browser_root, cases_path, _ = _evidence_tree(tmp_path)
    report_path = browser_root / CASE_IDS[0] / "browser-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["checks"] = [check for check in report["checks"] if check["id"] != "svg-chart-uses-data"]
    _json_write(report_path, report)

    paired = module.pair(generation_path, browser_root, cases_path)

    assert paired["passed"] is False
    assert "svg-chart-uses-data" in " ".join(paired["cases"][0]["errors"])


def test_pair_rejects_a_browser_report_for_the_wrong_case(tmp_path):
    generation_path, browser_root, cases_path, _ = _evidence_tree(tmp_path)
    report_path = browser_root / CASE_IDS[0] / "browser-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["case_id"] = CASE_IDS[1]
    _json_write(report_path, report)

    paired = module.pair(generation_path, browser_root, cases_path)

    assert paired["passed"] is False
    assert "case_id does not match" in " ".join(paired["cases"][0]["errors"])


def test_pair_rejects_a_missing_canonical_browser_case(tmp_path):
    generation_path, browser_root, cases_path, _ = _evidence_tree(tmp_path)
    (browser_root / CASE_IDS[0] / "browser-report.json").unlink()

    paired = module.pair(generation_path, browser_root, cases_path)

    assert paired["passed"] is False
    assert "browser report is missing" in " ".join(paired["cases"][0]["errors"])


@pytest.mark.parametrize("missing", ["pair", "file"])
def test_pair_rejects_missing_viewport_theme_screenshot_evidence(tmp_path, missing):
    generation_path, browser_root, cases_path, _ = _evidence_tree(tmp_path)
    case_root = browser_root / CASE_IDS[0]
    report_path = case_root / "browser-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    screenshot = next(
        item for item in report["screenshots"] if item["viewport_css_px"]["width"] == 320 and item["theme"] == "dark"
    )
    if missing == "pair":
        report["screenshots"].remove(screenshot)
    else:
        Path(screenshot["path"]).unlink()
    _json_write(report_path, report)

    paired = module.pair(generation_path, browser_root, cases_path)

    assert paired["passed"] is False
    assert paired["cases"][0]["errors"]


def test_pair_rejects_a_generation_report_with_wrong_canonical_case_hash(tmp_path):
    generation_path, browser_root, cases_path, generation = _evidence_tree(tmp_path)
    generation["case_set_sha256"] = "0" * 64
    _json_write(generation_path, generation)

    paired = module.pair(generation_path, browser_root, cases_path)

    assert paired["passed"] is False
    assert any("case-set hash" in error for error in paired["errors"])


def test_pair_rejects_a_browser_report_with_a_corrupted_renderer_digest(tmp_path):
    generation_path, browser_root, cases_path, _ = _evidence_tree(tmp_path)
    report_path = browser_root / CASE_IDS[0] / "browser-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["renderer_source_sha256"]["widget-runtime/runtime.mjs"] = "0" * 64
    _json_write(report_path, report)

    paired = module.pair(generation_path, browser_root, cases_path)

    assert paired["passed"] is False
    assert any("renderer source sha-256 values" in error.lower() for error in paired["cases"][0]["errors"])


def test_pair_rejects_a_browser_report_with_wrong_renderer_source_names(tmp_path):
    generation_path, browser_root, cases_path, _ = _evidence_tree(tmp_path)
    report_path = browser_root / CASE_IDS[0] / "browser-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    hashes = report["renderer_source_sha256"]
    hashes.pop("widget-runtime/frame_shell.css")
    hashes["widget-runtime/other.mjs"] = "a" * 64
    _json_write(report_path, report)

    paired = module.pair(generation_path, browser_root, cases_path)

    assert paired["passed"] is False
    assert any("renderer" in error.lower() for error in paired["cases"][0]["errors"])


def test_generation_refuses_existing_retained_case_before_dispatch(tmp_path, monkeypatch):
    artifact_dir = tmp_path / "artifacts"
    retained = artifact_dir / "forecast-operations-dashboard" / "app"
    retained.mkdir(parents=True)
    dispatched = False

    async def unexpected_model_dispatch(*_args, **_kwargs):
        nonlocal dispatched
        dispatched = True
        raise AssertionError("must not dispatch when a retained case already exists")

    monkeypatch.setattr(generation_module, "run_coding_agent", unexpected_model_dispatch)
    monkeypatch.setattr(
        generation_module,
        "load_cases",
        lambda: [{"id": "forecast-operations-dashboard"}],
    )

    with pytest.raises(FileExistsError, match="Refusing to regenerate retained artifact"):
        asyncio.run(
            generation_module.execute(
                artifact_dir,
                case_ids={"forecast-operations-dashboard"},
            )
        )

    assert dispatched is False
