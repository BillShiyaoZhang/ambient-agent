from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "evaluate_coding_agent_apps.py"
spec = importlib.util.spec_from_file_location("coding_agent_app_eval", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_eval_cases_define_three_distinct_app_surfaces_and_fixtures():
    cases = module.load_cases()
    assert [case["id"] for case in cases] == ["private-reading-list", "project-task-board", "service-status"]
    assert {case["fixture"]["kind"] for case in cases} == {"private-files", "graph", "http"}
    assert all(case["acceptance"] for case in cases)


def test_eval_case_manifest_grants_are_least_privilege_for_the_case():
    cases = {case["id"]: case for case in module.load_cases()}
    list_grants = {grant["id"] for grant in cases["private-reading-list"]["manifest"]["capabilities"]}
    graph_grants = {grant["id"] for grant in cases["project-task-board"]["manifest"]["capabilities"]}
    network_grants = {grant["id"] for grant in cases["service-status"]["manifest"]["capabilities"]}
    assert list_grants == {"file.read", "file.write"}
    assert graph_grants == {"graph.query", "graph.mutate"}
    assert network_grants == {"network.request"}


def test_external_case_has_failure_then_recovery_fixture():
    case = next(case for case in module.load_cases() if case["id"] == "service-status")
    responses = case["fixture"]["responses"]
    assert [response["status"] for response in responses] == [503, 200]
    assert "render-request-error" in case["acceptance"]
    assert "retry-after-error" in case["acceptance"]
