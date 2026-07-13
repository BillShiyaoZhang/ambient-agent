"""OFAT phase 4: routing experiment with multi-intent support.

Reuses the legacy V_winner variant baseline from phase 3 but runs against
the new ``multi_intent``-enabled router (which includes the S21-S24
multi-intent scenarios added in phase 4).

OFAT phase 4 differs from phase 3 in:
- 4 extra scenarios (S21-S24) that test multi-intent classification.
- Tool schema now includes ``sub_intents[]`` and ``multi_intent`` kind.
- Scoring accepts multi_intent plans and validates sub_intents.

Run:
    python -m scripts.run_routing_experiments_v2 baseline --repeats 5
    python -m scripts.run_routing_experiments_v2 ofat --repeats 5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Load .env BEFORE we read LLM_PROVIDER so the defaults reflect the user's setup.
try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=False)
except Exception:
    pass

from backend.agent.intent_plan import IntentKind, IntentPlan
from backend.agent.router import IntentRouter
from backend.experiments.scenarios import SCENARIOS, Scenario
from backend.llm_service import get_last_retry_stats


DEFAULT_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")
DEFAULT_MODEL = os.getenv("LLM_MODEL", "llama3")


def expected_kind_match(plan: IntentPlan, expected: IntentKind) -> bool:
    return plan.kind == expected


def expected_sub_kinds_match(plan: IntentPlan, scenario: Scenario) -> bool:
    if not scenario.expected_sub_kinds:
        return True
    actual = [s.kind.value for s in plan.sub_intents]
    return actual == scenario.expected_sub_kinds


async def run_scenario(scenario: Scenario, repeat: int, provider: str, model: str) -> dict:
    """Run a single (scenario, repeat) trial."""
    start = time.monotonic()
    error: str | None = None
    plan = IntentPlan(kind=IntentKind.CONVERSE, rationale="placeholder")
    route_retried = False
    refine_retried = False
    route_status_codes: list[int] = []
    refine_status_codes: list[int] = []
    try:
        plan = await IntentRouter.route(
            scenario.user_message,
            scenario.context,
            provider_name=provider,
            model_name=model,
        )
        stats = get_last_retry_stats()
        if stats is not None:
            route_retried = stats.retried
            route_status_codes = list(stats.status_codes)
        # Layer 2 refinement for multi_intent / plan_and_act plans.
        if plan.kind in (IntentKind.MULTI_INTENT, IntentKind.PLAN_AND_ACT):
            plan = await IntentRouter.refine_sub_intents(
                plan, scenario.context, provider_name=provider, model_name=model
            )
            stats2 = get_last_retry_stats()
            if stats2 is not None:
                refine_retried = stats2.retried
                refine_status_codes = list(stats2.status_codes)
    except Exception as e:  # pragma: no cover - depends on real LLM
        error = repr(e)
    elapsed_ms = (time.monotonic() - start) * 1000
    return {
        "scenario_id": scenario.id,
        "repeat": repeat,
        "kind": plan.kind.value,
        "sub_kinds": [s.kind.value for s in plan.sub_intents],
        "rationale": plan.rationale,
        "expected_kind": scenario.expected_kind.value,
        "expected_sub_kinds": scenario.expected_sub_kinds or [],
        "kind_correct": expected_kind_match(plan, scenario.expected_kind),
        "sub_kinds_correct": expected_sub_kinds_match(plan, scenario),
        "ambiguous": scenario.ambiguous,
        "latency_ms": elapsed_ms,
        "error": error,
        "route_retried": route_retried,
        "refine_retried": refine_retried,
        "rate_limit_retried": route_retried or refine_retried,
        "route_status_codes": route_status_codes,
        "refine_status_codes": refine_status_codes,
    }


async def run_phase(
    phase: str,
    repeats: int,
    provider: str,
    model: str,
    concurrency: int = 1,
) -> list[dict]:
    """Run all scenarios times repeats with bounded concurrency."""
    sem = asyncio.Semaphore(concurrency)

    async def bound(scenario: Scenario, repeat: int) -> dict:
        async with sem:
            return await run_scenario(scenario, repeat, provider, model)

    tasks = [bound(s, r) for s in SCENARIOS for r in range(1, repeats + 1)]
    return await asyncio.gather(*tasks)


def summarize(results: list[dict]) -> dict:
    n = len(results)
    kind_correct = sum(1 for r in results if r["kind_correct"])
    sub_correct = sum(1 for r in results if r["sub_kinds_correct"])
    latencies = [r["latency_ms"] for r in results if r["latency_ms"]]
    amb = [r for r in results if r["ambiguous"]]
    amb_correct = sum(1 for r in amb if r["kind_correct"])
    retried = sum(1 for r in results if r.get("rate_limit_retried"))
    return {
        "n": n,
        "kind_accuracy": kind_correct / n if n else 0.0,
        "sub_kinds_accuracy": sub_correct / n if n else 0.0,
        "amb_kind_accuracy": amb_correct / len(amb) if amb else 0.0,
        "median_latency_ms": statistics.median(latencies) if latencies else 0.0,
        "errors": sum(1 for r in results if r["error"]),
        "rate_limit_retried": retried,
    }


def render_report(phase: str, summary: dict, results: list[dict]) -> str:
    lines: list[str] = []
    lines.append(f"# OFAT Phase 4 — {phase}")
    lines.append("")
    lines.append(
        f"Total trials: {summary['n']} | "
        f"kind accuracy: {summary['kind_accuracy']:.1%} | "
        f"sub_kinds accuracy: {summary['sub_kinds_accuracy']:.1%} | "
        f"ambiguous kind acc: {summary['amb_kind_accuracy']:.1%}"
    )
    lines.append(f"Median latency: {summary['median_latency_ms']:.0f} ms")
    lines.append(f"Errors: {summary['errors']}")
    lines.append(f"Rate-limit retried trials: {summary.get('rate_limit_retried', 0)}")
    lines.append("")
    lines.append("## Per-scenario breakdown")
    lines.append("")
    by_scn: dict[str, list[dict]] = {}
    for r in results:
        by_scn.setdefault(r["scenario_id"], []).append(r)
    lines.append("| scenario | n | kind acc | sub acc | expected |")
    lines.append("|---|---|---|---|---|")
    for scn_id, runs in by_scn.items():
        n = len(runs)
        ka = sum(1 for r in runs if r["kind_correct"]) / n
        sa = sum(1 for r in runs if r["sub_kinds_correct"]) / n
        exp = runs[0]["expected_kind"]
        lines.append(f"| {scn_id} | {n} | {ka:.0%} | {sa:.0%} | {exp} |")
    return "\n".join(lines)


async def run_specific_trials(
    trials: list[tuple[str, int]],
    provider: str,
    model: str,
    concurrency: int,
) -> list[dict]:
    """Re-run only the specified (scenario_id, repeat) trials."""
    scn_by_id = {s.id: s for s in SCENARIOS}
    sem = asyncio.Semaphore(concurrency)

    async def bound(scn_id: str, repeat: int) -> dict:
        async with sem:
            scenario = scn_by_id[scn_id]
            return await run_scenario(scenario, repeat, provider, model)

    return await asyncio.gather(*[bound(s, r) for s, r in trials])


async def main() -> None:
    parser = argparse.ArgumentParser(description="OFAT phase 4 routing experiment runner.")
    parser.add_argument(
        "phase",
        choices=["baseline", "ofat", "winner", "redo-rate-limited"],
        help="phase to run",
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--provider", default=DEFAULT_PROVIDER)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--output", type=str, default="reports/ofat_phase4.json")
    parser.add_argument(
        "--input",
        type=str,
        default=None,
        help="Input JSON for redo-rate-limited; defaults to --output.",
    )
    parser.add_argument(
        "--latency-threshold",
        type=int,
        default=10000,
        help="Legacy latency heuristic (ms) for identifying rate-limited trials.",
    )
    args = parser.parse_args()

    if args.phase == "redo-rate-limited":
        await _cmd_redo_rate_limited(args)
        return

    print(
        f"Running phase={args.phase}, repeats={args.repeats}, "
        f"concurrency={args.concurrency}, provider={args.provider}, model={args.model}"
    )
    results = await run_phase(
        args.phase,
        repeats=args.repeats,
        provider=args.provider,
        model=args.model,
        concurrency=args.concurrency,
    )
    summary = summarize(results)
    print(json.dumps(summary, indent=2))

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump({"phase": args.phase, "summary": summary, "results": results}, f, ensure_ascii=False, indent=2)

    report_path = args.output.replace(".json", ".md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(render_report(args.phase, summary, results))
    print(f"Report written to {report_path}")


async def _cmd_redo_rate_limited(args) -> None:
    """Re-run only trials marked rate_limit_retried in the input JSON, then
    merge the fresh results back into a new output JSON.

    For older JSON files that don't have the ``rate_limit_retried`` field,
    fall back to a latency-based heuristic (>10s) to identify likely
    rate-limited trials.
    """
    src = args.input or args.output
    with open(src, encoding="utf-8") as f:
        data = json.load(f)
    old_results = data.get("results", [])

    # Identify trials to redo.
    explicit = [r for r in old_results if r.get("rate_limit_retried")]
    if explicit:
        to_redo = explicit
        print(f"Identified {len(to_redo)} trial(s) via explicit rate_limit_retried flag.")
    else:
        # Latency heuristic for legacy data.
        threshold_ms = args.latency_threshold
        to_redo = [r for r in old_results if r.get("latency_ms", 0) > threshold_ms]
        print(
            f"No rate_limit_retried flag in input; "
            f"using latency > {threshold_ms}ms heuristic → {len(to_redo)} trial(s)."
        )

    if not to_redo:
        print(f"No trials to redo in {src}; nothing to do.")
        return

    trials = [(r["scenario_id"], r["repeat"]) for r in to_redo]
    print(
        f"Re-running {len(trials)} rate-limited trial(s) "
        f"(provider={args.provider}, model={args.model}, concurrency={args.concurrency})"
    )

    fresh = await run_specific_trials(trials, args.provider, args.model, args.concurrency)

    # Replace old results with fresh ones (key by scenario_id+repeat).
    by_key = {(r["scenario_id"], r["repeat"]): r for r in fresh}
    new_results = []
    replaced = 0
    for r in old_results:
        key = (r["scenario_id"], r["repeat"])
        if key in by_key:
            new_results.append(by_key[key])
            replaced += 1
        else:
            new_results.append(r)
    print(f"Replaced {replaced} trial(s) in {src}")

    summary = summarize(new_results)
    print(json.dumps(summary, indent=2))

    # Write to a sibling file (don't clobber the original).
    out_path = src.replace(".json", "_redo.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "phase": data.get("phase", "baseline") + "+redo",
                "summary": summary,
                "results": new_results,
                "redo_source": src,
                "redo_count": replaced,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    md_path = out_path.replace(".json", ".md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_report(data.get("phase", "baseline") + "+redo", summary, new_results))
    print(f"Updated report written to {md_path}")


if __name__ == "__main__":
    asyncio.run(main())
