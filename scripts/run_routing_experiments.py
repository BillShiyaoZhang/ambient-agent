"""CLI entry point for routing experiments.

Usage examples:
    # Run baseline only (1 variant × all scenarios × 5 repeats)
    python -m scripts.run_routing_experiments baseline

    # Run all OFAT variants (18 variants, ~50 minutes)
    python -m scripts.run_routing_experiments ofat --repeats 5

    # Run a specific variant
    python -m scripts.run_routing_experiments single --variant V_C4B --repeats 5

    # Use a different model
    python -m scripts.run_routing_experiments ofat --model gpt-4 --provider openai
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

# Make project root importable when invoked as `python -m scripts.run_routing_experiments`.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Load .env from project root so LLM_API_KEY is populated.
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=str(PROJECT_ROOT / ".env"))
except Exception:
    pass

from backend.experiments.scenarios import SCENARIOS, get_ambiguous_scenarios
from backend.experiments.variants import all_ofat_variants, variant_by_id, make_winner_variant
from backend.experiments.runner import aggregate, run_variant
from backend.experiments.report import render_report

logger = logging.getLogger("experiments")


def _setup_logging(verbose: bool):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )


async def _run_with_progress(variant, scenarios, n_repeats, provider, model, concurrency):
    progress = {"done": 0, "total": len(scenarios) * n_repeats, "errors": 0}

    def on_result(r):
        progress["done"] += 1
        if r.error:
            progress["errors"] += 1
        if progress["done"] % 5 == 0 or progress["done"] == progress["total"]:
            logger.info(
                f"[{variant.id}] {progress['done']}/{progress['total']} done "
                f"(errors: {progress['errors']})"
            )

    return await run_variant(
        variant=variant,
        scenarios=scenarios,
        n_repeats=n_repeats,
        provider=provider,
        model=model,
        concurrency=concurrency,
        on_result=on_result,
    )


async def cmd_baseline(args):
    scenarios = SCENARIOS[: args.limit_scenarios] if args.limit_scenarios else SCENARIOS
    variant = variant_by_id("V_baseline")
    logger.info(f"Running BASELINE: {variant.id} on {len(scenarios)} scenarios × {args.repeats}")
    t0 = time.perf_counter()
    results = await _run_with_progress(
        variant, scenarios, args.repeats, args.provider, args.model, args.concurrency,
    )
    elapsed = time.perf_counter() - t0
    logger.info(f"Baseline complete in {elapsed:.1f}s")
    agg = aggregate(variant, results, {s.id: s for s in scenarios})
    return [agg]


async def cmd_winner(args):
    """Run the composite V_winner variant for cross-validation."""
    scenarios = SCENARIOS[: args.limit_scenarios] if args.limit_scenarios else SCENARIOS
    variant = make_winner_variant()
    logger.info(f"Running WINNER: {variant.id} on {len(scenarios)} scenarios × {args.repeats}")
    logger.info(f"Winner choices: {variant.choices}")
    t0 = time.perf_counter()
    results = await _run_with_progress(
        variant, scenarios, args.repeats, args.provider, args.model, args.concurrency,
    )
    elapsed = time.perf_counter() - t0
    logger.info(f"Winner complete in {elapsed:.1f}s")
    agg = aggregate(variant, results, {s.id: s for s in scenarios})
    return [agg]


async def cmd_ofat(args):
    scenarios = SCENARIOS[: args.limit_scenarios] if args.limit_scenarios else SCENARIOS
    variants = all_ofat_variants()
    if args.variants:
        variants = [v for v in variants if v.id in args.variants]
    logger.info(f"Running OFAT: {len(variants)} variants × {len(scenarios)} scenarios × {args.repeats}")
    aggregates = []
    t0 = time.perf_counter()
    # Incremental JSON dump path
    incremental_json = args.output.with_suffix(".json")
    incremental_json.parent.mkdir(parents=True, exist_ok=True)

    def _save_json():
        payload = {
            "metadata": {"provider": args.provider, "model": args.model, "n_repeats": args.repeats},
            "aggregates": [
                {
                    "variant_id": a.variant_id,
                    "description": a.description,
                    "choices": a.choices,
                    "n_total": a.n_total,
                    "n_errors": a.n_errors,
                    "mean_latency_ms": a.mean_latency_ms,
                    "kind_accuracy": a.kind_accuracy_overall(),
                    "stability": a.stability_overall(),
                    "per_scenario": {
                        sid: {
                            "expected_kind": sa.expected_kind,
                            "n": sa.n,
                            "n_kinds": sa.n_kinds,
                            "n_app_ids": sa.n_app_ids,
                        }
                        for sid, sa in a.per_scenario.items()
                    },
                }
                for a in aggregates
            ],
        }
        incremental_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False))

    for i, v in enumerate(variants, 1):
        logger.info(f"=== [{i}/{len(variants)}] {v.id} ({v.description}) ===")
        results = await _run_with_progress(
            v, scenarios, args.repeats, args.provider, args.model, args.concurrency,
        )
        agg = aggregate(v, results, {s.id: s for s in scenarios})
        aggregates.append(agg)
        _save_json()
        if args.early_stop_on_bad and agg.kind_accuracy_overall() < 0.3:
            logger.warning(f"{v.id} kind_acc={agg.kind_accuracy_overall():.2%} < 30%; skipping rest")
            break
    elapsed = time.perf_counter() - t0
    logger.info(f"OFAT complete in {elapsed:.1f}s ({elapsed/60:.1f} min)")
    return aggregates


async def cmd_single(args):
    scenarios = SCENARIOS[: args.limit_scenarios] if args.limit_scenarios else SCENARIOS
    variant = variant_by_id(args.variant)
    logger.info(f"Running single variant {variant.id} on {len(scenarios)} scenarios × {args.repeats}")
    results = await _run_with_progress(
        variant, scenarios, args.repeats, args.provider, args.model, args.concurrency,
    )
    agg = aggregate(variant, results, {s.id: s for s in scenarios})
    return [agg]


def _save(aggregates, output_path: Path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "metadata": {
            "provider": os.getenv("LLM_PROVIDER"),
            "model": os.getenv("LLM_MODEL"),
            "n_repeats": None,  # populated by caller
        },
        "aggregates": [
            {
                "variant_id": a.variant_id,
                "description": a.description,
                "choices": a.choices,
                "n_total": a.n_total,
                "n_errors": a.n_errors,
                "mean_latency_ms": a.mean_latency_ms,
                "kind_accuracy_overall": a.kind_accuracy_overall(),
                "app_id_accuracy_overall": a.app_id_accuracy_overall(
                    {sid: s for sid, s in [("", s) for s in SCENARIOS]}  # type: ignore
                ) if False else 0.0,  # placeholder; report will recompute
                "stability_overall": a.stability_overall(),
                "per_scenario": {
                    sid: {
                        "expected_kind": sa.expected_kind,
                        "n": sa.n,
                        "n_kinds": sa.n_kinds,
                        "n_app_ids": sa.n_app_ids,
                        "n_errors": sa.n_errors,
                        "mean_latency_ms": sa.mean_latency_ms,
                    }
                    for sid, sa in a.per_scenario.items()
                },
            }
            for a in aggregates
        ],
    }
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser(description="Run routing experiments for ambient-agent")
    p.add_argument("command", choices=["baseline", "ofat", "single", "winner"])
    p.add_argument("--variant", help="Variant id for `single` mode")
    p.add_argument("--repeats", type=int, default=5)
    p.add_argument("--provider", default=os.getenv("LLM_PROVIDER", "minimax"))
    p.add_argument("--model", default=os.getenv("LLM_MODEL", "MiniMax-M3"))
    p.add_argument("--concurrency", type=int, default=3)
    p.add_argument("--limit-scenarios", type=int, default=0,
                   help="If > 0, only use the first N scenarios (smoke test)")
    p.add_argument("--variants", nargs="+", default=None,
                   help="For OFAT mode: only run these variant ids")
    p.add_argument("--early-stop-on-bad", action="store_true",
                   help="For OFAT: skip remaining variants if a variant's kind_acc < 0.3")
    p.add_argument("--output", type=Path,
                   default=PROJECT_ROOT / "reports" / "routing_experiment_latest.md")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    _setup_logging(args.verbose)

    if args.command == "single" and not args.variant:
        p.error("`single` requires --variant")

    if args.command == "baseline":
        aggregates = asyncio.run(cmd_baseline(args))
    elif args.command == "single":
        aggregates = asyncio.run(cmd_single(args))
    elif args.command == "winner":
        aggregates = asyncio.run(cmd_winner(args))
    else:
        aggregates = asyncio.run(cmd_ofat(args))

    scenarios = SCENARIOS[: args.limit_scenarios] if args.limit_scenarios else SCENARIOS
    md = render_report(
        aggregates,
        scenarios,
        metadata={
            "provider": args.provider,
            "model": args.model,
            "n_repeats": args.repeats,
        },
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(md)
    print(f"\nReport written to: {args.output}")


if __name__ == "__main__":
    main()