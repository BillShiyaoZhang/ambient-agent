# Routing Experiments

A/B/n comparison harness for the Ambient Agent intent router.

## Goal

The intent router is a prompt-engineered LLM call that classifies each user
message into one of seven `IntentKind` values (`widget_create`, `widget_modify`,
`graph_mutation`, `graph_query`, `plan_and_act`, `clarify`, `converse`). Because
prompt wording, context payload density, and disambiguation rules all influence
this classification, we run controlled experiments to find the best
combination.

## What is tested

Seven **choices**, each with up to three candidate letters:

| Choice | Candidates | What's varied |
|---|---|---|
| C1 | A / B / C | graph preference wording in the router prompt |
| C2 | A / B / C | `plan_and_act` enablement |
| C3 | A / B / C | router context payload density (widgets + counts + recent + schemas + history) |
| C4 | A / B / C | create-vs-modify disambiguation |
| C5 | A / B / C | number of Chinese examples in the prompt |
| C6 | A / B / C | `<ambient-widget>` XML handling in `agent_system.md` |
| C7 | A / B / C | LLM-failure fallback keyword rules |

Default baseline: `A/A/A/A/A/C/A`.

## Scenarios

`backend/experiments/scenarios.py` defines 20 test scenarios covering:

- Chinese creation (no existing widget)
- Chinese creation (widget already exists — ambiguous)
- Chinese modification (explicit id + keyword)
- Chinese graph mutation / query
- Chinese converse
- English equivalents of all of the above
- 4 explicitly **ambiguous** scenarios (S04, S05, S08, S17) that exercise
  the router's disambiguation logic

Each scenario ships with a fixed `RouterContext` so all variants see
identical workspace state.

## Running

```bash
# Baseline (V_baseline × 20 scenarios × 3 repeats = 60 calls)
python -m scripts.run_routing_experiments baseline --repeats 3

# Single variant
python -m scripts.run_routing_experiments single --variant V_C1B --repeats 5

# Full OFAT (~22 min with concurrency=2)
python -m scripts.run_routing_experiments ofat --repeats 3 --concurrency 2

# Use a different provider
python -m scripts.run_routing_experiments ofat --provider openai --model gpt-4o-mini
```

Output: a markdown report at `reports/routing_experiment_latest.md`.

## Scoring

For each `(variant, scenario)` group we compute:

| Metric | Weight | Definition |
|---|---|---|
| `kind_accuracy` | 1.0 | Fraction of repeats where `kind == expected` |
| `app_id_accuracy` | 0.5 | Fraction of repeats where generated `app_id` matches expected |
| `stability` | 0.3 | Fraction of repeats that produced the modal `kind` |

Variant total score = sum of metrics × weights averaged over 20 scenarios.

A variant **passes thresholds** if:
- overall `kind_accuracy > 0`
- `stability >= 0.8`
- `kind_accuracy` on the 4 ambiguous scenarios >= 0.6

## OFAT methodology

**One-Factor-At-A-Time** rather than full factorial (22 × 18 = 396 variants
would take 8+ hours). For each choice we hold the others at baseline and
swap in candidates A/B/C, picking the winner by total score. The winners
of each choice are then combined into a `V_winner` for cross-validation.

## Architecture

```
backend/experiments/
├── __init__.py
├── scenarios.py      # 20 scenarios + fixed RouterContext per scenario
├── variants.py       # 22 variants (1 baseline + 21 OFAT)
├── runner.py         # Async LLM caller + aggregation
├── scoring.py        # Metric computation + per-choice winner selection
└── report.py         # Markdown report renderer

scripts/
└── run_routing_experiments.py   # CLI entry point

tests/experiments/
├── test_scenarios.py
├── test_variants.py
├── test_runner.py
└── test_report.py
```

## Caching / retry

The runner uses `backend/llm_service.call_llm_api` which has built-in retry
with exponential backoff for rate-limited responses (HTTP 200 with
`base_resp.status_code == 2062`). Set `LLM_BACKOFF_BASE` to tune.

## Notes

- Each prompt is rendered **in code** (in `variants.py`) and overrides the
  router_v2.md template via `IntentRouter.route(override_system_prompt=...)`.
  Source files are not modified during the experiment.
- The router context payload density (C3) is controlled by the
  `RouterContext.render_for_prompt(sections=[...])` parameter.
- The keyword fallback (C7) is exercised only when the LLM returns no
  tool_call or raises an exception.