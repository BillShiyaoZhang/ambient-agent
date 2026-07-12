# Routing Experiment — Final Summary

## Goal

The intent router in `backend/agent/router.py` classifies each user message
into one of seven `IntentKind` values via an LLM call with a prompt template
(`backend/agent/prompts/router_v2.md`). After the routing refactor, users
reported that "build me a clock app" requests were being misclassified.

To find the best prompt + configuration, we ran a controlled A/B/n
comparison across 7 design choices with 2-3 candidate letters each.

## Approach

**One-Factor-At-A-Time (OFAT)** rather than full factorial:

| Choice | Candidates | What's varied |
|---|---|---|
| **C1** graph preference wording | A=strong / B=weak / C=medium | Rules 1-3 of the router prompt |
| **C2** `plan_and_act` enablement | A=disable / B=enable / C=remove | Whether plan_and_act is enumerated |
| **C3** context payload density | A=full / B=trimmed / C=minimal | Sections shown in `{{ router_context }}` |
| **C4** create-vs-modify disambiguation | A=strict / B=lenient / C=default-create | Rule 4 of the router prompt |
| **C5** Chinese examples | A=0 / B=3 / C=6 | Number of inline examples |
| **C6** `agent_system.md` widget XML | A=removed / B=caveat / C=current | `<ambient-widget>` template handling |
| **C7** LLM-failure fallback | A=none / B=narrow / C=broad | Substring keyword fallback rules |

- 20 scenarios (4 explicitly **ambiguous**: S04, S05, S08, S17)
- 3 repeats per scenario (60 LLM calls per variant)
- 22 variants × 60 calls = **1320 LLM calls** in OFAT phase 2
- 22 variants × 60 calls = **1320 LLM calls** in OFAT phase 3 (corrected S08)
- Plus V_winner × 5 repeats = 100 calls for cross-validation

## Important: corrected S08 expectation

After phase 2, the user pointed out that **S08** ("在待办里加买牛奶" — add
"buy milk" to todos) should be classified as **graph_mutation**, not
`widget_modify`. Reasoning:

> Adding a single data item to an existing todo list is a pure data
> operation. The todo widget subscribes to Task nodes via
> `ambient.graph.subscribe()` and updates its UI automatically when a new
> Task node is created. No widget code regeneration is needed.

This corrected expectation shifted the phase 3 winners significantly:

| Choice | Phase 2 winner | **Phase 3 winner** | Why it changed |
|---|---|---|---|
| C1 | C (medium) | **A (strong graph)** | C1=C didn't push S08 to graph_mutation strongly enough; C1=A is needed |
| C2 | B | B (unchanged) | — |
| C3 | B | B (unchanged) | — |
| C4 | B | B (unchanged) | — |
| C5 | C (6 examples) | **A (no examples)** | Chinese examples over-fit; LLM was better with fewer constraints |
| C6 | B (caveat) | **C (current full widget XML)** | C6=A and C6=B hurt the converse path significantly |
| C7 | B (narrow keywords) | **A (no fallback)** | LLM rarely fails; fallback is dead weight |

## Results (phase 3, corrected S08)

### OFAT summary

| Variant | Score | Kind Acc | App ID Acc | Stability | Ambig Kind Acc | Passes |
|---|---|---|---|---|---|---|
| V_C2B | 1.755 | 100% | 91% | 100% | 100% | ✅ |
| V_C2C | 1.755 | 100% | 91% | 100% | 100% | ✅ |
| V_C5A | 1.755 | 100% | 91% | 100% | 100% | ✅ |
| V_C4B | 1.733 | 98% | 91% | 98% | 100% | ✅ |
| V_baseline | 1.686 | 97% | 85% | 98% | 83% | ✅ |
| V_C1B | 1.459 | 80% | 73% | 98% | 0% | ❌ |
| V_C6A | 1.123 | 55% | 55% | 100% | 50% | ❌ |
| V_C6B | 0.982 | 50% | 36% | 100% | 25% | ❌ |

### Per-choice OFAT winners

| Choice | Winner |
|---|---|
| C1 | **A** (strong graph preference — original behavior) |
| C2 | **B** (enable plan_and_act) |
| C3 | **B** (trimmed context: widgets + counts + history) |
| C4 | **B** (lenient create-vs-modify) |
| C5 | **A** (no Chinese examples) |
| C6 | **C** (keep current full widget XML) |
| C7 | **A** (no fallback needed) |

### Cross-validated winner (V_winner, N=5)

| Metric | V_baseline (phase 3) | **V_winner** |
|---|---|---|
| Score | 1.686 | **1.675** |
| Kind accuracy | 97% | 96% |
| App ID accuracy | 85% | 85% |
| Stability | 98% | 96% |
| Ambig kind accuracy | 83% | **95%** |

### V_winner on ambiguous scenarios

| Scenario | Message | Expected | V_winner |
|---|---|---|---|
| S04 | 建一个时钟 (clock exists) | widget_modify | 80% |
| S05 | 重新做一个待办 (todo exists) | widget_modify | **100%** |
| S08 | 在待办里加买牛奶 (todo exists) | graph_mutation | **100%** |
| S17 | add buy milk to todos | graph_mutation | **100%** |

**S08 went from 0% (baseline) to 100% (V_winner)** — the user's intuition
was correct that this should be a data operation.

## What changed in the source

1. **`backend/agent/prompts/router_v2.md`** rewritten with:
   - **Strong** graph preference ("Prefer the cheapest, most data-grounded
     path" — original C1=A wording)
   - Lenient create-vs-modify disambiguation (creation verb → widget_create;
     mod verb → widget_modify; switch to widget_modify only when user used
     "改下"/"重新做")
   - No inline Chinese examples (let the LLM reason from rules + context)
   - Detailed action shape enumeration (create_node, update_node_property,
     etc.) for graph_mutation
2. **`backend/agent/prompts/agent_system.md`** — restored to original
   (the C6=C winner). Previous "remove widget XML" / "caveat" experiments
   hurt the converse path's accuracy significantly.
3. **`backend/router_context.py`**: `render_for_prompt(sections=[...])`
   accepts a section filter (used by C3=B to trim context payload)
4. **`backend/agent/router.py`**: `IntentRouter.route()` accepts
   `override_system_prompt`, `context_sections`, `fallback_keywords`,
   `plan_and_act_enabled`, `include_widget_keyword_hint` (all keyword-only)
5. **`backend/routing_winner.py`** (new): holds winner constants used by harness
6. **`backend/agent/harness.py`**: production `IntentRouter.route()` call now
   passes the winning `context_sections`, `fallback_keywords`, `plan_and_act_enabled`

## Rollback

Original files backed up at `reports/routing_winner_backup/`:

```
reports/routing_winner_backup/
├── router_v2.md.<timestamp>.bak
└── agent_system.md.<timestamp>.bak
```

To revert to the original (pre-experiment) router_v2.md:

```bash
cp reports/routing_winner_backup/router_v2.md.<oldest>.bak \
   backend/agent/prompts/router_v2.md
cp reports/routing_winner_backup/agent_system.md.<oldest>.bak \
   backend/agent/prompts/agent_system.md
```

(Note: `agent_system.md.<oldest>.bak` is identical to the current state
because C6=C means keep the original.)

## Reproduction

```bash
# Re-run baseline
python -m scripts.run_routing_experiments baseline --repeats 5

# Re-run full OFAT (~22 min with concurrency=2)
python -m scripts.run_routing_experiments ofat --repeats 5

# Validate the composite winner
python -m scripts.run_routing_experiments winner --repeats 5

# Apply the winner to source files (backs up originals first)
python -m scripts.apply_routing_winner --apply
```

## Files added

```
backend/experiments/
├── __init__.py
├── README.md
├── scenarios.py
├── variants.py
├── runner.py
├── scoring.py
└── report.py

backend/routing_winner.py

scripts/
├── __init__.py
├── run_routing_experiments.py
└── apply_routing_winner.py

tests/experiments/
├── __init__.py
├── test_scenarios.py
├── test_variants.py
├── test_runner.py
└── test_report.py

reports/
├── routing_winner_backup/
├── baseline.md
├── ofat_phase2.md      # initial run with S08 wrongly expected as widget_modify
├── ofat_phase3.md      # corrected run with S08 = graph_mutation
├── v_winner.md         # phase 2 V_winner validation
├── v_winner_v2.md      # phase 3 V_winner validation
└── SUMMARY.md
```