#!/usr/bin/env python3
"""Offline routing-boundary scorer. Standard library only; never invokes a model.

Predictions: JSONL objects containing id, kind, optional app_id/sub_intents,
optional probabilities (a complete 8-class distribution), optional confidence
(a model's own reported confidence), optional abstain and measured telemetry.
Probability calibration and self-reported calibration are deliberately separate.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

KINDS = (
    "converse",
    "graph_query",
    "graph_mutation",
    "widget_create",
    "widget_modify",
    "multi_intent",
    "plan_and_act",
    "clarify",
)
EFFECT_KINDS = {"graph_mutation", "widget_create", "widget_modify", "multi_intent", "plan_and_act"}


def number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def load_cases(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data["cases"] if isinstance(data, dict) else data
    ids = [c["id"] for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate case ids")
    for c in cases:
        e = c["expected"]
        if e["kind"] not in KINDS or not set(e.get("acceptable_kinds", [e["kind"]])) <= set(KINDS):
            raise ValueError(f"Invalid gold kinds: {c['id']}")
    return cases


def load_predictions(path: Path) -> dict[str, dict[str, Any]]:
    result = {}
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise ValueError(f"Prediction line {line_no} must be an object with a string id")
        if row["id"] in result:
            raise ValueError(f"Duplicate prediction id: {row['id']}")
        result[row["id"]] = row
    return result


def validate_prediction(p: dict[str, Any]) -> list[str]:
    errors = []
    if p.get("kind") not in KINDS:
        errors.append("unknown_or_missing_kind")
    if "abstain" in p and not isinstance(p["abstain"], bool):
        errors.append("abstain_must_be_bool")
    if "confidence" in p and (not number(p["confidence"]) or not 0 <= p["confidence"] <= 1):
        errors.append("invalid_self_confidence")
    if "probabilities" in p:
        dist = p["probabilities"]
        if not isinstance(dist, dict) or set(dist) != set(KINDS):
            errors.append("probabilities_require_all_eight_kinds")
        elif any(not number(v) or not 0 <= v <= 1 for v in dist.values()):
            errors.append("invalid_probability_value")
        elif not math.isclose(sum(dist.values()), 1, abs_tol=1e-6):
            errors.append("probabilities_do_not_sum_to_one")
        elif p.get("kind") in KINDS and not math.isclose(dist[p["kind"]], max(dist.values()), abs_tol=1e-9):
            errors.append("kind_is_not_a_probability_argmax")
    for key in ("latency_ms", "cost_usd", "input_tokens", "output_tokens"):
        if key in p and (not number(p[key]) or p[key] < 0):
            errors.append(f"invalid_{key}")
    return errors


def sub_kinds(p: dict[str, Any]) -> list[str]:
    items = p.get("sub_intents", [])
    if not isinstance(items, list):
        return []
    return [v.get("kind", "") if isinstance(v, dict) else str(v) for v in items]


def row_result(c: dict[str, Any], p: dict[str, Any] | None) -> dict[str, Any]:
    e = c["expected"]
    errors = ["missing_prediction"] if p is None else validate_prediction(p)
    valid = not errors
    accepted = valid and not p.get("abstain", False)
    proposed_preferred = valid and p["kind"] == e["kind"]
    preferred = accepted and proposed_preferred
    allowed = accepted and p["kind"] in e.get("acceptable_kinds", [e["kind"]])
    app_correct = accepted and p.get("app_id") == e["app_id"] if "app_id" in e else None
    subs_correct = accepted and sub_kinds(p) == e["sub_kinds"] if "sub_kinds" in e else None
    boundary = allowed and app_correct is not False and subs_correct is not False
    proposed_false_effect = bool(e.get("no_effect_route") and p and p.get("kind") in EFFECT_KINDS)
    return {
        "id": c["id"],
        "language": c["language"],
        "tags": c["tags"],
        "origin": c.get("route_origin", "llm"),
        "gold_kind": e["kind"],
        "predicted_kind": p.get("kind") if p else None,
        "needs_policy_adjudication": c.get("needs_policy_adjudication", False),
        "valid": valid,
        "accepted": accepted,
        "preferred_kind_correct": preferred,
        "proposed_preferred_kind_correct": proposed_preferred,
        "prediction_status": "missing"
        if p is None
        else "invalid"
        if not valid
        else "abstain"
        if not accepted
        else "accepted",
        "acceptable_kind_correct": allowed,
        "boundary_correct": boundary,
        "app_correct": app_correct,
        "sub_kinds_correct": subs_correct,
        "no_effect_case": e.get("no_effect_route", False),
        "proposed_false_effect": proposed_false_effect,
        "accepted_false_effect": proposed_false_effect and accepted,
        "probabilities": p.get("probabilities") if valid else None,
        "self_confidence": p.get("confidence") if valid else None,
        "telemetry": {k: p[k] for k in ("latency_ms", "cost_usd", "input_tokens", "output_tokens") if k in p}
        if p and valid
        else {},
        "errors": errors,
    }


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * q
    low = int(index)
    high = min(low + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (index - low)


def calibration(rows: list[dict[str, Any]], source: str) -> dict[str, Any]:
    observed = []
    for r in rows:
        if not r["valid"] or r["needs_policy_adjudication"]:
            continue
        if source == "probability_pmax" and r["probabilities"] is not None:
            confidence = max(r["probabilities"].values())
        elif source == "self_assessed_confidence" and r["self_confidence"] is not None:
            confidence = r["self_confidence"]
        else:
            continue
        observed.append((confidence, float(r["proposed_preferred_kind_correct"]), r))
    bins = []
    for index in range(10):
        members = [x for x in observed if min(int(x[0] * 10), 9) == index]
        if members:
            bins.append(
                {
                    "lower": index / 10,
                    "upper": (index + 1) / 10,
                    "n": len(members),
                    "mean_confidence": mean([x[0] for x in members]),
                    "accuracy": mean([x[1] for x in members]),
                }
            )
    ece = sum(b["n"] * abs(b["mean_confidence"] - b["accuracy"]) for b in bins) / len(observed) if observed else None
    output = {
        "source": source,
        "n": len(observed),
        "ece_10_bins": ece,
        "bins": bins,
        "sample_scope": "All valid predictions with unambiguous policy labels and this confidence source, including abstentions; not conditioned on accepted predictions.",
        "included_abstentions": sum(r["prediction_status"] == "abstain" for _, _, r in observed),
    }
    if source == "probability_pmax":
        # Proper multiclass Brier; no arbitrary probability assigned to an LLM.
        output["multiclass_brier"] = mean(
            [sum((r["probabilities"][k] - float(k == r["gold_kind"])) ** 2 for k in KINDS) for _, _, r in observed]
        )
    else:
        output["correctness_brier"] = mean([(confidence - correct) ** 2 for confidence, correct, _ in observed])
        output["warning"] = (
            "Self-reported confidence is not a model probability distribution; this diagnostic is separate from probability calibration."
        )
    return output


def selective(rows: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    output = []
    for threshold in (0.5, 0.7, 0.85, 0.95):
        selected = []
        for r in rows:
            value = (
                max(r["probabilities"].values())
                if source == "probability_pmax" and r["probabilities"] is not None
                else r["self_confidence"]
                if source == "self_assessed_confidence"
                else None
            )
            if r["accepted"] and value is not None and value >= threshold:
                selected.append(r)
        output.append(
            {
                "threshold": threshold,
                "selected": len(selected),
                "coverage_of_all_cases": len(selected) / len(rows) if rows else None,
                "accepted_kind_accuracy": mean([float(r["acceptable_kind_correct"]) for r in selected]),
                "accepted_boundary_accuracy": mean([float(r["boundary_correct"]) for r in selected]),
                "accepted_false_effect_count": sum(r["accepted_false_effect"] for r in selected),
            }
        )
    return output


def metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    accepted = [r for r in rows if r["accepted"]]
    app = [r for r in rows if r["app_correct"] is not None]
    subs = [r for r in rows if r["sub_kinds_correct"] is not None]
    no_effect = [r for r in rows if r["no_effect_case"]]
    per_kind = {}
    for kind in KINDS:
        tp = sum(r["preferred_kind_correct"] and r["gold_kind"] == kind for r in rows)
        fp = sum(r["accepted"] and r["predicted_kind"] == kind and r["gold_kind"] != kind for r in rows)
        fn = sum(r["gold_kind"] == kind and not r["preferred_kind_correct"] for r in rows)
        support = sum(r["gold_kind"] == kind for r in rows)
        per_kind[kind] = {
            "support": support,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
        }
    telemetry = {}
    for key in ("latency_ms", "cost_usd", "input_tokens", "output_tokens"):
        values = [r["telemetry"][key] for r in rows if key in r["telemetry"]]
        telemetry[key] = {
            "observed_n": len(values),
            "missing_n": len(rows) - len(values),
            "sum": sum(values) if values else None,
            "mean": mean(values),
            "p50": percentile(values, 0.5),
            "p95": percentile(values, 0.95),
            "p99": percentile(values, 0.99),
        }
    confusion = Counter(
        (r["gold_kind"], r["prediction_status"], str(r["predicted_kind"]) if r["predicted_kind"] is not None else None)
        for r in rows
    )
    confusion_counts = [
        {"gold_kind": gold, "decision_status": status, "proposed_kind": proposed, "count": count}
        for (gold, status, proposed), count in sorted(
            confusion.items(), key=lambda item: tuple(str(value) for value in item[0])
        )
    ]
    return {
        "n": len(rows),
        "missing_predictions": sum("missing_prediction" in r["errors"] for r in rows),
        "invalid_predictions": sum(not r["valid"] and "missing_prediction" not in r["errors"] for r in rows),
        "accepted": len(accepted),
        "coverage": len(accepted) / len(rows) if rows else None,
        "preferred_kind_accuracy_all_cases": mean([float(r["preferred_kind_correct"]) for r in rows]),
        "acceptable_kind_accuracy_all_cases": mean([float(r["acceptable_kind_correct"]) for r in rows]),
        "accepted_kind_accuracy": mean([float(r["acceptable_kind_correct"]) for r in accepted]),
        "boundary_accuracy_all_cases": mean([float(r["boundary_correct"]) for r in rows]),
        "accepted_boundary_accuracy": mean([float(r["boundary_correct"]) for r in accepted]),
        "exact_app_accuracy": mean([float(r["app_correct"]) for r in app]),
        "ordered_sub_kinds_accuracy": mean([float(r["sub_kinds_correct"]) for r in subs]),
        "no_effect_cases": len(no_effect),
        "proposed_false_effect_count": sum(r["proposed_false_effect"] for r in rows),
        "accepted_false_effect_count": sum(r["accepted_false_effect"] for r in rows),
        "accepted_false_effect_rate_on_no_effect_cases": mean([float(r["accepted_false_effect"]) for r in no_effect]),
        "macro_f1_preferred_labels": mean([v["f1"] for v in per_kind.values() if v["support"] and v["f1"] is not None]),
        "per_kind_preferred_labels": per_kind,
        "confusion_counts": confusion_counts,
        "calibration": {s: calibration(rows, s) for s in ("probability_pmax", "self_assessed_confidence")},
        "selective_curves": {s: selective(rows, s) for s in ("probability_pmax", "self_assessed_confidence")},
        "telemetry_observed_only": telemetry,
    }


def evaluate(cases: list[dict[str, Any]], predictions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    unknown = set(predictions) - {c["id"] for c in cases}
    if unknown:
        raise ValueError(f"Unknown prediction ids: {sorted(unknown)}")
    rows = [row_result(c, predictions.get(c["id"])) for c in cases]
    llm = [r for r in rows if r["origin"] == "llm"]
    adjudicated_llm = [r for r in llm if not r["needs_policy_adjudication"]]
    slash = [r for r in rows if r["origin"] != "llm"]
    tags = sorted({t for r in rows for t in r["tags"]})
    return {
        "scope": "Synthetic routing-boundary seed only; no model/provider was invoked by this scorer.",
        "all": metrics(rows),
        "llm_origin_only": metrics(llm),
        "adjudicated_llm_origin_only": metrics(adjudicated_llm),
        "deterministic_slash_origin_only": metrics(slash),
        "by_language": {
            lang: metrics([r for r in rows if r["language"] == lang]) for lang in sorted({r["language"] for r in rows})
        },
        "by_tag": {tag: metrics([r for r in rows if tag in r["tags"]]) for tag in tags},
        "failures": [
            {k: v for k, v in r.items() if k not in {"probabilities", "telemetry"}}
            for r in rows
            if not r["boundary_correct"]
        ],
    }


def synthetic_oracle(cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Scorer fixture, not predictions from Jev or any other model."""
    result = {}
    for c in cases:
        e = c["expected"]
        p = {"id": c["id"], "kind": e["kind"], "source": "gold_echo_scorer_fixture_not_model"}
        if "app_id" in e:
            p["app_id"] = e["app_id"]
        if "sub_kinds" in e:
            p["sub_intents"] = [{"kind": k} for k in e["sub_kinds"]]
        result[c["id"]] = p
    return result


def self_test(cases: list[dict[str, Any]]) -> dict[str, Any]:
    oracle = synthetic_oracle(cases)
    result = evaluate(cases, oracle)
    assert result["all"]["boundary_accuracy_all_cases"] == 1
    assert result["all"]["calibration"]["probability_pmax"]["n"] == 0
    assert result["all"]["telemetry_observed_only"]["cost_usd"]["sum"] is None
    empty = evaluate(cases, {})
    assert empty["all"]["coverage"] == 0
    assert empty["all"]["boundary_accuracy_all_cases"] == 0
    assert empty["all"]["accepted_kind_accuracy"] is None
    assert empty["all"]["missing_predictions"] == len(cases)
    # The following distributions are mathematical scorer fixtures, not LLM estimates.
    p = {"kind": "converse", "probabilities": {k: float(k == "converse") for k in KINDS}}
    assert validate_prediction(p) == []
    p["probabilities"]["converse"] = 0.5
    assert "probabilities_do_not_sum_to_one" in validate_prediction(p)
    p["probabilities"]["converse"] = float("nan")
    assert "invalid_probability_value" in validate_prediction(p)
    p["probabilities"].pop("clarify")
    assert "probabilities_require_all_eight_kinds" in validate_prediction(p)
    assert "invalid_self_confidence" in validate_prediction({"kind": "converse", "confidence": 1.1})
    target = next(c for c in cases if c["expected"]["no_effect_route"])
    bad = {**oracle, target["id"]: {"id": target["id"], "kind": "graph_mutation"}}
    assert evaluate(cases, bad)["all"]["accepted_false_effect_count"] == 1
    target = next(c for c in cases if "app_id" in c["expected"])
    bad = {**oracle, target["id"]: {**oracle[target["id"]], "app_id": "invented-app"}}
    assert evaluate(cases, bad)["all"]["boundary_accuracy_all_cases"] < 1
    target = next(c for c in cases if "sub_kinds" in c["expected"])
    bad = {
        **oracle,
        target["id"]: {**oracle[target["id"]], "sub_intents": list(reversed(oracle[target["id"]]["sub_intents"]))},
    }
    assert evaluate(cases, bad)["all"]["boundary_accuracy_all_cases"] < 1
    # Pmax and self-report remain different even in intentionally fabricated input.
    c = next(c for c in cases if not c["needs_policy_adjudication"])
    p = {
        "id": c["id"],
        "kind": c["expected"]["kind"],
        "confidence": 0.2,
        "probabilities": {k: 0.86 if k == c["expected"]["kind"] else 0.02 for k in KINDS},
    }
    r = row_result(c, p)
    assert calibration([r], "probability_pmax")["n"] == 1
    assert calibration([r], "self_assessed_confidence")["n"] == 1
    assert math.isclose(calibration([r], "probability_pmax")["ece_10_bins"], 0.14)
    assert math.isclose(calibration([r], "self_assessed_confidence")["ece_10_bins"], 0.8)
    assert math.isclose(calibration([r], "probability_pmax")["multiclass_brier"], 0.0224)
    # Calibration includes abstentions, avoiding acceptance-conditioned bias.
    abstained = row_result(c, {**p, "abstain": True})
    assert calibration([abstained], "probability_pmax")["n"] == 1
    assert calibration([abstained], "probability_pmax")["included_abstentions"] == 1
    assert math.isclose(calibration([abstained], "probability_pmax")["multiclass_brier"], 0.0224)
    wrong_kind = next(k for k in KINDS if k != c["expected"]["kind"])
    wrong = row_result(
        c,
        {
            "id": c["id"],
            "kind": wrong_kind,
            "confidence": 0.2,
            "abstain": True,
            "probabilities": {k: 0.86 if k == wrong_kind else 0.02 for k in KINDS},
        },
    )
    assert math.isclose(calibration([wrong], "probability_pmax")["multiclass_brier"], 1.7024)
    assert math.isclose(calibration([wrong], "self_assessed_confidence")["correctness_brier"], 0.04)
    assert result["adjudicated_llm_origin_only"]["n"] == sum(
        c["route_origin"] == "llm" and not c["needs_policy_adjudication"] for c in cases
    )
    return {
        "self_test": "passed",
        "fixture_is_model_result": False,
        "cases": len(cases),
        "kinds": dict(Counter(c["expected"]["kind"] for c in cases)),
        "llm_origin": len([c for c in cases if c["route_origin"] == "llm"]),
        "slash_origin": len([c for c in cases if c["route_origin"] != "llm"]),
        "needs_policy_adjudication": sum(c["needs_policy_adjudication"] for c in cases),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path(__file__).with_name("cases.json"))
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument(
        "--write-oracle-fixture",
        type=Path,
        help="Write a clearly marked gold-echo scorer fixture; never a model result",
    )
    args = parser.parse_args()
    cases = load_cases(args.cases)
    if args.write_oracle_fixture:
        args.write_oracle_fixture.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in synthetic_oracle(cases).values()),
            encoding="utf-8",
        )
    if args.self_test:
        output = self_test(cases)
    elif args.predictions:
        output = evaluate(cases, load_predictions(args.predictions))
    else:
        parser.error("Supply --predictions or --self-test")
    encoded = json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
