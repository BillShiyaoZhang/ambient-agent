"""Evaluate Jev classification on synthetic cases without executing effects.

Explicitly invokes the paid TypeSafe API. Run with python -m from the repo root;
results belong outside the checkout. Credentials are loaded from the local .env
or process environment and are never included in predictions or console output.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from dotenv import load_dotenv

from backend.agent.decision_context import project_routing_context
from backend.agent.jev_router import JevDecisionClient, JevRouterConfig, JevRouterError, build_request
from backend.capabilities.catalog import AgentRole, SystemCapabilityCatalog
from backend.router_context import GraphSnapshot, RouterContext

REPO_ROOT = Path(__file__).resolve().parent.parent


async def evaluate(args: argparse.Namespace) -> None:
    load_dotenv(REPO_ROOT / ".env", override=False)
    config = JevRouterConfig.from_env()
    if args.context_version is not None:
        config = JevRouterConfig.model_validate({**config.snapshot(), "context_version": args.context_version})
    if args.timeout_seconds is not None:
        config = JevRouterConfig.model_validate({**config.snapshot(), "timeout_s": args.timeout_seconds})
    payload = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = [case for case in payload["cases"] if case.get("route_origin") == "llm"][: args.limit]
    if not cases:
        raise ValueError("No model-origin cases selected")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    catalog = SystemCapabilityCatalog.build().render(AgentRole.INTENT_ROUTER)
    client = JevDecisionClient()
    successes = 0
    # Exclusive creation prevents accidentally replacing an earlier experiment.
    with args.output.open("x", encoding="utf-8") as output:
        args.output.chmod(0o600)
        for case in cases:
            started = time.monotonic()
            row = {"id": case["id"], "source": "jev_live_synthetic_classification", "config": config.snapshot()}
            try:
                raw_context = case["context"]
                context = RouterContext(
                    app_manifests=raw_context.get("app_manifests", []),
                    graph_snapshot=GraphSnapshot(**raw_context.get("graph_snapshot", {})),
                    session_recent=raw_context.get("session_recent", []),
                    session_summary=raw_context.get("session_summary"),
                )
                sections = ["widgets", "graph_counts", "history"]
                if config.context_version == "routing-context-v2":
                    projection = project_routing_context(case["message"], context, sections)
                    context_text, candidates = projection.context_text, projection.app_candidates
                    row["projection"] = projection.metadata
                else:
                    context_text = context.render_for_prompt(sections=sections) + "\n\n" + catalog
                    candidates = context.app_manifests
                request = build_request(
                    case["message"],
                    context_text,
                    candidates,
                    case["language"],
                    config,
                )
                decision, raw = await client.decide(request, config)
                ordered = sorted(decision.probabilities.values(), reverse=True)
                row.update(
                    kind=decision.kind.value,
                    probabilities=decision.probabilities,
                    confidence=decision.provider_confidence,
                    model=decision.actual_model,
                    abstain=ordered[0] < config.min_probability or ordered[0] - ordered[1] < config.min_margin,
                    target_choice=decision.target_choice,
                    target_probabilities=decision.target_probabilities,
                )
                if decision.target_app_id is not None:
                    row["app_id"] = decision.target_app_id
                row.update(raw.get("usage") or {})
                successes += 1
            except JevRouterError as exc:
                row["error"] = exc.code
                if exc.model is not None:
                    row["model"] = exc.model
                row.update(exc.usage or {})
            row["latency_ms"] = (time.monotonic() - started) * 1000
            output.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            output.flush()
    print(json.dumps({"cases": len(cases), "valid_responses": successes, "output": str(args.output)}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=REPO_ROOT / "proposals/jev-intent-router/cases.json")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--timeout-seconds", type=float)
    parser.add_argument("--context-version", choices=["routing-context-v1", "routing-context-v2"])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")
    try:
        asyncio.run(evaluate(args))
    except JevRouterError as exc:
        raise SystemExit(exc.code) from None


if __name__ == "__main__":
    main()
