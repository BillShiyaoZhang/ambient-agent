from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "merge_coding_agent_eval_results.py"
spec = importlib.util.spec_from_file_location("coding_agent_eval_merge", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_merge_marks_only_observed_browser_checks_and_keeps_failure(tmp_path):
    case = {
        "id": "demo",
        "acceptance": [
            "manifest-controller-contract",
            "feature-coverage",
            "runtime-first-frame",
            "create-row",
            "reload-row",
        ],
    }
    artifact = tmp_path / "artifacts" / "demo"
    artifact.mkdir(parents=True)
    (artifact / "controller.js").write_text("export default function App() {}", encoding="utf-8")
    (artifact / "manifest.json").write_text("{}", encoding="utf-8")
    interaction_root = tmp_path / "interactions"
    interaction = interaction_root / "demo"
    interaction.mkdir(parents=True)
    digest = module.artifact_hash(artifact)
    (interaction / "interaction-result.json").write_text(
        json.dumps(
            {
                "case_id": "demo",
                "artifact_hash": digest,
                "passed": False,
                "checks": [{"id": "create-row", "passed": True}],
                "error": "Timed out waiting for reload-row",
            }
        ),
        encoding="utf-8",
    )
    merged = module.merge(
        {
            "cases": [
                {
                    "id": "demo",
                    "artifact_hash": digest,
                    "production_staging_validated": True,
                    "assertions": {"runtime-first-frame": "passed"},
                }
            ]
        },
        [case],
        tmp_path / "artifacts",
        interaction_root,
        {"results": [{"id": "demo", "status": "passed", "artifact_hash": digest}]},
    )
    result = merged["cases"][0]
    assert result["assertions"] == {
        "manifest-controller-contract": "passed",
        "feature-coverage": "passed",
        "runtime-first-frame": "passed",
        "create-row": "passed",
        "reload-row": "failed",
    }
    assert result["status"] == "failed"
    assert merged["complete_passes"] == 0


def test_merge_rejects_stale_hash_and_unvalidated_artifact(tmp_path):
    case = {"id": "demo", "acceptance": ["manifest-controller-contract", "feature-coverage", "create-row"]}
    artifact = tmp_path / "artifacts" / "demo"
    artifact.mkdir(parents=True)
    (artifact / "controller.js").write_text("export default function App() {}", encoding="utf-8")
    (artifact / "manifest.json").write_text("{}", encoding="utf-8")
    interaction_root = tmp_path / "interactions"
    interaction = interaction_root / "demo"
    interaction.mkdir(parents=True)
    (interaction / "interaction-result.json").write_text(
        json.dumps(
            {
                "case_id": "demo",
                "artifact_hash": "stale",
                "passed": True,
                "checks": [{"id": "create-row", "passed": True}],
            }
        ),
        encoding="utf-8",
    )
    merged = module.merge(
        {"cases": [{"id": "demo", "artifact_hash": "stale", "production_staging_validated": False}]},
        [case],
        tmp_path / "artifacts",
        interaction_root,
        {"results": [{"id": "demo", "status": "passed", "artifact_hash": "stale"}]},
    )
    result = merged["cases"][0]
    assert result["artifact_integrity"] == "failed"
    assert result["interaction_integrity"] == "failed"
    assert result["assertions"]["manifest-controller-contract"] == "not_run"
    assert result["assertions"]["create-row"] == "not_run"
    assert result["status"] == "failed"


def test_failed_driver_cannot_pass_case_with_every_check_recorded(tmp_path):
    case = {"id": "demo", "acceptance": ["manifest-controller-contract", "feature-coverage", "create-row"]}
    artifact = tmp_path / "artifacts" / "demo"
    artifact.mkdir(parents=True)
    (artifact / "controller.js").write_text("export default function App() {}", encoding="utf-8")
    (artifact / "manifest.json").write_text("{}", encoding="utf-8")
    digest = module.artifact_hash(artifact)
    interaction = tmp_path / "interactions" / "demo"
    interaction.mkdir(parents=True)
    (interaction / "interaction-result.json").write_text(
        json.dumps(
            {
                "case_id": "demo",
                "artifact_hash": digest,
                "passed": False,
                "checks": [{"id": "create-row", "passed": True}],
                "error": "Unexpected page error",
            }
        ),
        encoding="utf-8",
    )
    merged = module.merge(
        {"cases": [{"id": "demo", "artifact_hash": digest, "production_staging_validated": True}]},
        [case],
        tmp_path / "artifacts",
        tmp_path / "interactions",
        {"results": [{"id": "demo", "status": "passed", "artifact_hash": digest}]},
    )
    assert merged["cases"][0]["assertions"]["create-row"] == "passed"
    assert merged["cases"][0]["status"] == "failed"


def test_merge_does_not_accept_an_artifact_hash_changed_by_evidence_files(tmp_path):
    artifact = tmp_path / "artifacts" / "demo"
    artifact.mkdir(parents=True)
    (artifact / "controller.js").write_text("export default function App() {}", encoding="utf-8")
    before = module.artifact_hash(artifact)
    (artifact / "interaction-result.json").write_text("{}", encoding="utf-8")
    (artifact / "demo.png").write_bytes(b"fixture screenshot")
    assert module.artifact_hash(artifact) == before


def test_complete_pass_requires_exact_hashes_staging_smoke_and_interaction(tmp_path):
    case = {
        "id": "demo",
        "acceptance": ["manifest-controller-contract", "feature-coverage", "runtime-first-frame", "create-row"],
    }
    artifact = tmp_path / "artifacts" / "demo"
    artifact.mkdir(parents=True)
    (artifact / "controller.js").write_text("export default function App() {}", encoding="utf-8")
    (artifact / "manifest.json").write_text("{}", encoding="utf-8")
    digest = module.artifact_hash(artifact)
    interaction = tmp_path / "interactions" / "demo"
    interaction.mkdir(parents=True)
    (interaction / "interaction-result.json").write_text(
        json.dumps(
            {
                "case_id": "demo",
                "artifact_hash": digest,
                "passed": True,
                "checks": [{"id": "create-row", "passed": True}],
            }
        ),
        encoding="utf-8",
    )
    batch = {"cases": [{"id": "demo", "artifact_hash": digest, "production_staging_validated": True}]}
    smoke = {"results": [{"id": "demo", "status": "passed", "artifact_hash": digest}]}
    merged = module.merge(batch, [case], tmp_path / "artifacts", tmp_path / "interactions", smoke)
    assert merged["cases"][0]["status"] == "passed"
    assert merged["complete_passes"] == 1

    smoke["results"][0]["artifact_hash"] = "stale"
    incomplete = module.merge(batch, [case], tmp_path / "artifacts", tmp_path / "interactions", smoke)
    assert incomplete["cases"][0]["status"] == "failed"
