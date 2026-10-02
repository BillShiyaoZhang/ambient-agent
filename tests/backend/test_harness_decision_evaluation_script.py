"""Validate evaluator plumbing with transport fixtures, never a paid call."""

import argparse
import json
import stat
from pathlib import Path

import httpx
import pytest

from backend.agent.jev_router import JevJSONClient
from scripts.evaluate_harness_decisions import evaluate, private_output, scenarios


def test_synthetic_scenarios_cover_twenty_cases_and_all_families():
    cases = scenarios()
    assert len(cases) == 20
    assert len({case["id"] for case in cases}) == 20
    assert {case["family"] for case in cases} == {"query", "composite", "development", "schema"}
    assert any(case.get("polluted") for case in cases)
    assert any(case.get("ambiguous") for case in cases)
    assert any(case["expected"].get("disposition") == "NO_GRAPH_DATA" for case in cases)


def test_output_rejects_checkout_paths_and_symlinks_to_the_checkout(tmp_path):
    with pytest.raises(ValueError, match="private temporary"):
        private_output(Path(__file__).resolve().parent / "must-not-write.jsonl")
    link = tmp_path / "outside"
    link.symlink_to(Path(__file__).resolve().parent, target_is_directory=True)
    with pytest.raises(ValueError, match="private temporary"):
        private_output(link / "must-not-write.jsonl")


def _install_transport(monkeypatch, *, error=False):
    requests = []

    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert request.headers["Authorization"] == "Bearer evaluator-test-secret"
        if error:
            return httpx.Response(429, json={"error": "evaluator-test-secret"})
        answers = {}
        for name, question in payload["questions"].items():
            if question["type"] == "choice":
                keys = list(question["criteria"])
                answers[name] = {
                    "type": "choice",
                    "choice": keys[0],
                    "confidence": 0.9,
                    "probabilities": {
                        key: 0.98 if index == 0 else 0.02 / (len(keys) - 1) for index, key in enumerate(keys)
                    },
                }
            elif question["type"] == "noul":
                answers[name] = {"type": "noul", "noul": 0.99}
            else:
                levels = question["criteria"]
                answers[name] = {
                    "type": "score",
                    "score": float(len(levels) - 1),
                    "confidence": 1.0,
                    "probabilities": {str(index): float(index == len(levels) - 1) for index in range(len(levels))},
                    "legend": {str(index): value for index, value in enumerate(levels)},
                }
        return httpx.Response(
            200,
            json={
                "model": payload["model"],
                "answers": answers,
                "usage": {"input_tokens": 100, "output_tokens": 20},
                "debug_secret": "evaluator-test-secret",
            },
        )

    monkeypatch.setenv("TYPESAFE_API_KEY", "evaluator-test-secret")
    monkeypatch.setenv("JEV_DECISION_MODE", "off")
    for setting in (
        "MODEL",
        "TIMEOUT_SECONDS",
        "MIN_PROBABILITY",
        "MIN_MARGIN",
        "MAX_STATE_CHARS",
        "MAX_CANDIDATES",
        "MAX_QUESTIONS",
    ):
        monkeypatch.delenv("JEV_DECISION_" + setting, raising=False)
    monkeypatch.setattr(
        "backend.agent.decisions.JevJSONClient", lambda: JevJSONClient(transport=httpx.MockTransport(handle))
    )
    return requests


@pytest.mark.asyncio
async def test_full_evaluator_uses_real_helpers_sanitized_audits_and_exclusive_private_output(
    tmp_path, monkeypatch, capsys
):
    requests = _install_transport(monkeypatch)
    output = tmp_path / "synthetic-fixture.jsonl"
    await evaluate(argparse.Namespace(output=output, limit=20, timeout_seconds=0.5))

    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert len(rows) == len(requests) == 20
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert all(row["config"]["mode"] == "cascade" for row in rows)
    assert all(row["config"]["timeout_s"] == 0.5 for row in rows)
    assert all(row["model_response_valid"] for row in rows)
    assert all(len(row["decision_evidence"]) == 1 for row in rows)
    stages = {row["decision_evidence"][0]["stage"] for row in rows}
    assert stages == {
        "decision:graph_query_template",
        "decision:composite_review",
        "decision:development_plan_review",
        "decision:schema_selection",
    }
    assert all(row["decision_evidence"][0]["usage"]["input_tokens"] == 100 for row in rows)
    serialized = output.read_text()
    console = capsys.readouterr().out
    assert "evaluator-test-secret" not in serialized + console
    assert all("request" not in row and "prompt" not in row for row in rows)
    assert all("config" not in call["bundle"] for row in rows for call in row["decision_evidence"])
    assert json.loads(console)["decision_calls"] == 20
    with pytest.raises(FileExistsError):
        await evaluate(argparse.Namespace(output=output, limit=1, timeout_seconds=None))
    assert len(requests) == 20


@pytest.mark.asyncio
async def test_failed_api_call_is_unknown_semantic_result_and_unknown_charge(tmp_path, monkeypatch, capsys):
    _install_transport(monkeypatch, error=True)
    output = tmp_path / "error-fixture.jsonl"
    await evaluate(argparse.Namespace(output=output, limit=1, timeout_seconds=None))
    row = json.loads(output.read_text())
    assert row["model_response_valid"] is False
    assert row["matches_expected"] is None
    assert row["decision_evidence"][0]["error"] == "jev_http_429"
    assert row["decision_evidence"][0]["usage"] == {}
    summary = json.loads(capsys.readouterr().out)
    assert summary["unknown_cost_calls"] == 1
    assert summary["decision_errors"] == 1
