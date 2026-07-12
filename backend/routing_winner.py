"""Constants for the winning routing configuration.

These are derived from the OFAT routing experiments (see
``backend/experiments/README.md``). They define how the production
``IntentRouter.route()`` should be invoked to match V_winner's behavior.

If you re-run the experiments and pick a different winner, update this
file to match.

## Source

OFAT phase 3 (corrected S08 expectation) — see reports/ofat_phase3.md
and reports/SUMMARY.md.

Winner combination:
- C1 → A: strong graph preference ("Prefer the cheapest, most data-grounded path")
        correctly routes "在待办里加买牛奶" to graph_mutation.
- C2 → B: enable plan_and_act (no harm if unused; matches original IntentKind enum)
- C3 → B: trimmed context (widgets + counts + history; drops recent_nodes/schemas)
- C4 → B: lenient create-vs-modify (creation verb → widget_create; mod verb → widget_modify)
- C5 → A: no Chinese examples (the 3-6 examples in phase 2 were over-fitting)
- C6 → C: keep current agent_system.md intact (deleting widget XML hurt score)
- C7 → A: no LLM-failure fallback needed
"""

from __future__ import annotations


# Sections to include in the rendered router context, in order.
# C3=B: trim "recent_nodes" and "schemas" so the LLM is less likely to over-rotate
# toward graph_* paths.
WINNER_CONTEXT_SECTIONS: list[str] = [
    "widgets",
    "graph_counts",
    "history",
]

# LLM-failure fallback keywords. C7=A: no fallback needed.
WINNER_FALLBACK_KEYWORDS: list[str] | None = None

# Whether plan_and_act is enabled. C2=B: keep it available (harness handles it).
WINNER_PLAN_AND_ACT_ENABLED: bool = True