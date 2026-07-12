"""Prompt variants for routing experiments.

Each variant is a complete (system_prompt, context_options) pair that can be
substituted into IntentRouter at runtime. We avoid touching source files so
that OFAT experiments remain isolated.

Variant matrix (see plan for full description):

  C1 graph preference wording   A=strong | B=weak | C=medium
  C2 plan_and_act               A=disable | B=enable | C=full-remove
  C3 context payload density    A=full   | B=trimmed | C=minimal
  C4 create-vs-modify disambig  A=strict  | B=lenient | C=default-create
  C5 Chinese examples           A=none    | B=three  | C=six
  C6 agent_system widget XML    A=removed | B=caveat | C=current
  C7 fallback keywords          A=none    | B=narrow | C=broad

Each variant dict maps ``c1`` ... ``c7`` to a letter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from backend.router_context import RouterContext


@dataclass
class Variant:
    """A single self-contained prompt variant."""

    id: str  # human-readable id, e.g. "V_baseline", "V_C1B"
    description: str
    choices: dict[str, str]  # e.g. {"c1": "B", "c2": "A", ...}
    system_prompt: str
    context_options: dict[str, Any] = field(default_factory=dict)
    fallback_keywords: list[str] | None = None  # for C7
    agent_system_prompt: str | None = None  # for C6 (only affects converse path)
    plan_and_act_enabled: bool = True  # for C2


# ── Shared fragments ──────────────────────────────────────────────────────

_INTRO_BASE = """You are Ambient Agent's intent router.

Your job is to classify the user's latest message into exactly ONE routing decision.
The agent accesses a unified Knowledge Graph; many requests that *look* like widget
tasks can be satisfied by mutating or querying the graph directly. Prefer the
cheapest, most data-grounded path.
"""

_INTRO_WEAK = """You are Ambient Agent's intent router.

Your job is to classify the user's latest message into exactly ONE routing decision.
"""

_INTRO_MEDIUM = """You are Ambient Agent's intent router.

Your job is to classify the user's latest message into exactly ONE routing decision.
Prefer graph_mutation or graph_query only when the request is purely about data
(not about a visible UI element).
"""

# ── Routing decision list fragments ───────────────────────────────────────

_DECISIONS_FULL = """# Routing decisions (pick exactly one)

- `widget_create`: User asks to **build a brand-new widget** not yet on the canvas.
- `widget_modify`: User asks to **change an existing widget** (mention its id or a known alias).
- `graph_mutation`: User asks to **add / update / delete data** in the unified graph
  (e.g. "add 'buy milk' to my todos", "mark task 3 done", "create a calendar event").
- `graph_query`: User asks a question that can be answered by **reading the graph**
  (e.g. "what's on my schedule today?", "list pending tasks").
- `plan_and_act`: User asks something that **needs multiple read-then-write steps**
  and may affect multiple widgets (only emit this when simpler kinds cannot apply).
- `clarify`: Genuinely ambiguous and you cannot resolve it — propose 2–4 options.
- `converse`: Anything else (chitchat, explanation, general knowledge).
"""

_DECISIONS_NO_PLAN = """# Routing decisions (pick exactly one)

- `widget_create`: User asks to **build a brand-new widget** not yet on the canvas.
- `widget_modify`: User asks to **change an existing widget** (mention its id or a known alias).
- `graph_mutation`: User asks to **add / update / delete data** in the unified graph
  (e.g. "add 'buy milk' to my todos", "mark task 3 done", "create a calendar event").
- `graph_query`: User asks a question that can be answered by **reading the graph**
  (e.g. "what's on my schedule today?", "list pending tasks").
- `clarify`: Genuinely ambiguous and you cannot resolve it — propose 2–4 options.
- `converse`: Anything else (chitchat, explanation, general knowledge).

Note: `plan_and_act` is reserved as an advanced multi-step path. The harness will
route it identically to `graph_mutation`. Prefer the simpler kinds.
"""

# ── C1: graph preference wording ─────────────────────────────────────────

_C1A_PREFERENCE = (
    "1. If the request says e.g. \"建一个待办\" but a widget for that concept already exists,\n"
    "   prefer `widget_modify` and use the existing widget's exact `id`.\n"
    "2. If the request is \"add this to my todos\" / \"在待办里加\" / similar, prefer\n"
    "   `graph_mutation` (no codegen needed).\n"
    "3. If the request can be answered by reading the graph, prefer `graph_query`.\n"
)

_C1B_PREFERENCE = (
    "1. If the user mentions a visible UI element (clock face, todo list, calendar grid,\n"
    "   chart, calculator buttons, etc.) or uses creation verbs (\"建/做/build/create\"),\n"
    "   default to `widget_create` or `widget_modify`. Only consider `graph_*` paths if\n"
    "   the request is purely about data with no UI implication.\n"
    "2. If the user explicitly says \"add X to my todos\" / \"在待办里加\" and a todo widget\n"
    "   already exists, prefer `widget_modify`; otherwise prefer `graph_mutation`.\n"
    "3. If the request can be answered by reading the graph, you may use `graph_query`.\n"
)

_C1C_PREFERENCE = (
    "1. If the request is purely about data manipulation (\"add buy milk\", \"mark task 3\n"
    "   done\", \"list pending tasks\"), prefer `graph_mutation` or `graph_query`.\n"
    "2. If the request mentions a visible UI element, screen, or widget, prefer\n"
    "   `widget_create` or `widget_modify`.\n"
    "3. If the request says \"建一个 X\" but a widget for that concept already exists,\n"
    "   prefer `widget_modify` and use the existing widget's exact `id`.\n"
)

# ── C4: create-vs-modify disambiguation ──────────────────────────────────

_C4A_STRICT = (
    "4. If the user uses personal shorthand (\"todos\", \"日历\"), match it against the\n"
    "   registered widgets AND the graph state; only mark `clarify` if truly ambiguous.\n"
)

_C4B_LENIENT = (
    "4. If the user uses a creation verb (\"建/做/build/create/make\") → prefer\n"
    "   `widget_create`. If they use a modification verb (\"改/改下/fix/update/modify/\n"
    "   change\") → prefer `widget_modify`. Only switch to `widget_modify` for creation\n"
    "   verbs when the widget for that concept already exists AND the user used\n"
    "   phrasing implying modification of the existing one (\"改下\", \"重新做\").\n"
)

_C4C_DEFAULT_CREATE = (
    "4. Only choose `widget_modify` when the user explicitly mentions an existing\n"
    "   widget's id or title (e.g. \"clock-app-abcd\", \"我的时钟\"). For everything else,\n"
    "   prefer `widget_create` so the user gets a fresh widget rather than mutating\n"
    "   an old one. If multiple existing widgets match, downgrade to `clarify`.\n"
)

# ── C5: Chinese examples ─────────────────────────────────────────────────

_C5A_NONE = ""
_C5B_THREE = """
# Examples

- "建一个时钟" (no clock widget yet) -> {"kind": "widget_create", "app_id": "clock-app-XXXX", "instruction": "建一个时钟"}
- "改下待办加删除按钮" (todo widget exists) -> {"kind": "widget_modify", "app_id": "todo-app-efgh", "instruction": "加上删除按钮"}
- "今天有什么日程" -> {"kind": "graph_query", "query": {"type": "CalendarEvent"}}
"""
_C5C_SIX = """
# Examples

- "建一个时钟" (no clock widget yet) -> {"kind": "widget_create", "app_id": "clock-app-XXXX"}
- "做一个待办" -> {"kind": "widget_create", "app_id": "todo-app-XXXX"}
- "建一个时钟" (clock-app-abcd already exists) -> {"kind": "widget_modify", "app_id": "clock-app-abcd"}
- "改下待办加删除按钮" -> {"kind": "widget_modify", "app_id": "todo-app-efgh"}
- "在待办里加买牛奶" (todo widget exists) -> {"kind": "widget_modify", "app_id": "todo-app-efgh", "instruction": "加上买牛奶"}
- "今天有什么日程" -> {"kind": "graph_query", "query": {"type": "CalendarEvent"}}
"""

# ── Common tail (rules 5-10, mostly invariant) ───────────────────────────

_TAIL_BASE = """
5. You MUST call the `classify_intent` function with a JSON payload following the
   provided schema. Respond with that function call only — no chat prose.
6. When `kind` is `widget_create` or `widget_modify`, set `app_id`:
   - For existing widgets: use the EXACT id from the widget inventory above.
   - For new widgets: suggest a kebab-case name + 4-char hex suffix (e.g. `weather-app-8f3a`).
7. When `kind` is `graph_mutation`, fill `actions` with concrete graph actions
   using one of these shapes:
   - `create_node`: `{action: "create_node", id: str, type: str, properties: dict}`
   - `update_node_property`: `{action: "update_node_property", id: str, properties: dict}`
   - `delete_node`: `{action: "delete_node", id: str}`
   - `create_edge`: `{action: "create_edge", from_id: str, to_id: str, type: str, properties: dict}`
   - `delete_edge`: `{action: "delete_edge", from_id: str, to_id: str, type: str}`
8. When `kind` is `graph_query`, fill `query` with a declarative query
   (e.g. `{"type": "Task", "properties": {"status": "pending"}}`).
9. Always set `instruction` to a refined version of the user's request that preserves
   the original intent.
10. If you are unsure, pick `converse` rather than guessing.
"""


def _assemble_router_prompt(c1: str, c4: str, c5: str, c2: str) -> str:
    """Build a router system prompt from choice fragments."""
    if c1 == "A":
        intro = _INTRO_BASE
    elif c1 == "B":
        intro = _INTRO_WEAK
    else:
        intro = _INTRO_MEDIUM

    decisions = _DECISIONS_NO_PLAN if c2 == "C" else _DECISIONS_FULL

    if c4 == "A":
        c4_block = _C4A_STRICT
    elif c4 == "B":
        c4_block = _C4B_LENIENT
    else:
        c4_block = _C4C_DEFAULT_CREATE

    if c5 == "A":
        c5_block = _C5A_NONE
    elif c5 == "B":
        c5_block = _C5B_THREE
    else:
        c5_block = _C5C_SIX

    return (
        intro
        + "\n"
        + decisions
        + "\n# Available context\n\n{{ router_context }}\n\n# System rules\n\n"
        + c1.replace("1.", c1_block_prefix(c1, "1."), 1)
        + c4_block
        + _TAIL_BASE
        + c5_block
    )


def c1_block_prefix(c1: str, marker: str) -> str:
    """No-op helper; kept for symmetry. The actual content is selected above."""
    return marker


# Patch the call site: we replace the c1 text wholesale (since C1 already
# encodes its preference rules 1-3).
def _assemble(c1: str, c4: str, c5: str, c2: str) -> str:
    if c1 == "A":
        pref = _C1A_PREFERENCE
    elif c1 == "B":
        pref = _C1B_PREFERENCE
    else:
        pref = _C1C_PREFERENCE

    if c4 == "A":
        c4_block = _C4A_STRICT
    elif c4 == "B":
        c4_block = _C4B_LENIENT
    else:
        c4_block = _C4C_DEFAULT_CREATE

    if c5 == "A":
        c5_block = _C5A_NONE
    elif c5 == "B":
        c5_block = _C5B_THREE
    else:
        c5_block = _C5C_SIX

    intro = {"A": _INTRO_BASE, "B": _INTRO_WEAK, "C": _INTRO_MEDIUM}[c1]
    decisions = _DECISIONS_NO_PLAN if c2 == "C" else _DECISIONS_FULL

    return (
        intro
        + "\n"
        + decisions
        + "\n# Available context\n\n{{ router_context }}\n\n# System rules\n\n"
        + pref
        + c4_block
        + _TAIL_BASE
        + c5_block
    )


# ── C6: agent_system widget XML section ──────────────────────────────────

_AGENT_SYSTEM_BASE = """You are Ambient Agent, an agentic personal coding and productivity assistant.
You can communicate in normal text, but you also have the special ability to spawn dynamic UI widgets on the user's workspace screen when they request something visual (like weather, todo lists, notes, calculators, calendars, system monitoring, charts, etc.).

# System Architecture & Capabilities
1. **Dual Execution Pipelines**:
   - **Conversational (Current)**: You handle general QA, explanations, and lightweight updates. You can output `<ambient-widget>` blocks to display interactive widgets.
   - **Coding (Automated)**: When the user asks to build or heavily modify an app, a specialized router sends their request to the **OpenCode Developer Agent** (via Client Protocol). The OpenCode agent runs terminal commands, reads/writes files directly, and compiles the code.
2. **Tool Execution**:
   - You have access to real-time workspace tools (like listing all apps, deleting apps, etc.). You should use them to satisfy user commands when appropriate.
3. **SQLite Knowledge Graph & Schema Alignment**:
   - The system utilizes an indexed, SQLite-backed Graph Database. All application data is stored as nodes and edges conforming to registered Schemas.
   - Core schemas like `Task`, `Event`, and `Note` are shared globally to allow widgets to collaborate (e.g. calendar displaying tasks).
   - In App design, schemas are aligned and confirmed by the user before code generation.

# Spawning Widgets
To spawn or update a widget, output a block in this exact XML-like format anywhere in your reply:

<ambient-widget id="UNIQUE_WIDGET_ID" title="WIDGET_TITLE_NAME">
<html-content>
  <!-- Raw HTML body using Tailwind/CSS classes and custom components -->
</html-content>
<css-styles>
  /* Scoped CSS rules targeting classes inside the widget */
</css-styles>
<js-script>
  // Scoped JavaScript. You are passed 'root' (the widget's HTML content div) and 'ambient' (the client SDK).
  // Use root.querySelector to select elements. Do NOT write global variables.
  // To persist and sync data/state using Knowledge Graph:
  //   // Subscribe to graph data (real-time reactive updates)
  //   const unsubscribe = ambient.graph.subscribe({ type: "Task", properties: { status: "pending" } }, (nodesList) => { ... });
  //   // Mutate graph data (create/update/delete nodes and edges)
  //   await ambient.graph.mutate([{ action: "create_node", id: "task-1", type: "Task", properties: { title: "Buy groceries" } }]);
  // To interact with chat:
  //   ambient.sendMessage("message text"); // sends user message in chat
  // To control window:
  //   ambient.fullscreen(); // requests fullscreen view
  //   ambient.minimize();   // minimizes/restores grid view
</js-script>
</ambient-widget>

# Design System Guidelines
Always make widgets look visually stunning, glassmorphic, responsive, and functional! Keep user data private and run locally when possible.
"""

_AGENT_SYSTEM_NO_WIDGET = """You are Ambient Agent, an agentic personal coding and productivity assistant.
You handle general questions, explanations, and lightweight graph-data updates through tool calls.

# Important: You do NOT generate widgets
Widget creation and modification are handled by an automated coding pipeline that runs
*before* your response. If the user asks to build or modify an app, the router has already
classified that request — you will not see it. Treat widget-creation requests as chitchat
and gently clarify what they want.

# Available tools
You have these tools (call them directly via the tool protocol when appropriate):
- `list_available_apps()` — list all widget ids currently on the canvas.
- `delete_widget_app(app_id: str)` — delete a widget by id.
- `query_graph(query_json: str)` — read graph data (declarative JSON query).
- `mutate_graph(actions_json: str)` — write graph data (list of create/update/delete actions).

When you need graph data, call `query_graph` instead of guessing. When the user wants to
add or update graph data, call `mutate_graph`.

# Style
Be concise, friendly, and use markdown. Do not fabricate widget HTML/CSS/JS.
"""

_AGENT_SYSTEM_CAVEAT = """You are Ambient Agent, an agentic personal coding and productivity assistant.
You can communicate in normal text, but you also have the special ability to spawn dynamic UI widgets on the user's workspace screen when they request something visual (like weather, todo lists, notes, calculators, calendars, system monitoring, charts, etc.).

# Note on widget creation
Under the current architecture, an upstream router classifies "build a widget" requests to
an automated coding pipeline. **You should NOT output `<ambient-widget>` XML blocks.**
If a widget-creation request reaches you, treat it as ambiguous and ask the user to clarify.

# Spawning Widgets (LEGACY — do not use)
<ambient-widget id="UNIQUE_WIDGET_ID" title="WIDGET_TITLE_NAME">
<html-content>...</html-content>
<css-styles>...</css-styles>
<js-script>...</js-script>
</ambient-widget>

The above template is legacy documentation; do not produce it.
"""


def _agent_system_for(c6: str) -> str:
    if c6 == "A":
        return _AGENT_SYSTEM_NO_WIDGET
    if c6 == "B":
        return _AGENT_SYSTEM_CAVEAT
    return _AGENT_SYSTEM_BASE


# ── C7: fallback keyword sets ────────────────────────────────────────────

_C7A_NONE: list[str] | None = None
_C7B_NARROW = ["创建", "建一个", "制作", "build", "create", "make",
               "修改", "改下", "fix", "update", "modify"]
_C7C_BROAD = _C7B_NARROW + ["生成", "开发", "设计", "添加", "加上",
                            "generate", "develop", "design", "add",
                            "refresh", "redo", "重新做"]


def _fallback_for(c7: str) -> list[str] | None:
    if c7 == "A":
        return None
    if c7 == "B":
        return _C7B_NARROW
    return _C7C_BROAD


# ── Variant matrix assembly ──────────────────────────────────────────────

def _build_variant(name: str, choices: dict[str, str], desc: str) -> Variant:
    c1 = choices["c1"]
    c2 = choices["c2"]
    c3 = choices["c3"]
    c4 = choices["c4"]
    c5 = choices["c5"]
    c6 = choices["c6"]
    c7 = choices["c7"]

    sys_prompt = _assemble(c1=c1, c4=c4, c5=c5, c2=c2)
    return Variant(
        id=name,
        description=desc,
        choices=choices,
        system_prompt=sys_prompt,
        context_options={"c3": c3},
        fallback_keywords=_fallback_for(c7),
        agent_system_prompt=_agent_system_for(c6),
        plan_and_act_enabled=(c2 != "C"),
    )


# Baseline = current state of router_v2.md before any experiment.
BASELINE_CHOICES = {"c1": "A", "c2": "A", "c3": "A", "c4": "A", "c5": "A", "c6": "C", "c7": "A"}


def all_ofat_variants() -> list[Variant]:
    """One-factor-at-a-time variants. For each choice, hold others at baseline and vary it."""
    out: list[Variant] = []
    out.append(_build_variant("V_baseline", dict(BASELINE_CHOICES),
                              "Current router_v2.md before any change."))

    # C1: graph preference wording
    for v in ("A", "B", "C"):
        ch = dict(BASELINE_CHOICES); ch["c1"] = v
        out.append(_build_variant(f"V_C1{v}", ch, f"C1 graph preference = {v}"))

    # C2: plan_and_act
    for v in ("A", "B", "C"):
        ch = dict(BASELINE_CHOICES); ch["c2"] = v
        out.append(_build_variant(f"V_C2{v}", ch, f"C2 plan_and_act = {v}"))

    # C3: context payload density
    for v in ("A", "B", "C"):
        ch = dict(BASELINE_CHOICES); ch["c3"] = v
        out.append(_build_variant(f"V_C3{v}", ch, f"C3 context density = {v}"))

    # C4: create-vs-modify
    for v in ("A", "B", "C"):
        ch = dict(BASELINE_CHOICES); ch["c4"] = v
        out.append(_build_variant(f"V_C4{v}", ch, f"C4 disambig = {v}"))

    # C5: Chinese examples
    for v in ("A", "B", "C"):
        ch = dict(BASELINE_CHOICES); ch["c5"] = v
        out.append(_build_variant(f"V_C5{v}", ch, f"C5 Chinese examples = {v}"))

    # C6: agent_system widget XML
    for v in ("A", "B", "C"):
        ch = dict(BASELINE_CHOICES); ch["c6"] = v
        out.append(_build_variant(f"V_C6{v}", ch, f"C6 agent_system widget XML = {v}"))

    # C7: fallback keywords
    for v in ("A", "B", "C"):
        ch = dict(BASELINE_CHOICES); ch["c7"] = v
        out.append(_build_variant(f"V_C7{v}", ch, f"C7 fallback keywords = {v}"))

    return out


def variant_by_id(vid: str) -> Variant:
    for v in all_ofat_variants():
        if v.id == vid:
            return v
    raise KeyError(f"Unknown variant: {vid}")


# ── Composite winner from OFAT phase 3 (corrected S08 expectation) ──────
#
# Phase 3 re-ran with S08 expected as graph_mutation (the correct semantics:
# adding "buy milk" to todos is a pure data op; the widget subscribes to Task
# nodes and updates its UI automatically). This shifted the winners:
#
#   Phase 2 winners           →   Phase 3 winners
#   ─────────────────────────────────────────────
#   C1=C  (medium graph)      →   C1=A  (strong graph preference)
#   C5=C  (6 Chinese examples)→   C5=A  (no examples; LLM was over-fitting)
#   C6=B  (caveat widget XML) →   C6=C  (keep current full widget XML)
#   C7=B  (narrow fallback)   →   C7=A  (no fallback needed)
#
# C2, C3, C4 winners unchanged.
DEFAULT_WINNER_CHOICES: dict[str, str] = {
    "c1": "A",  # strong graph preference (handles "在待办里加买牛奶" correctly)
    "c2": "B",  # enable plan_and_act (no harm if unused)
    "c3": "B",  # trimmed context (widgets + counts + history)
    "c4": "B",  # lenient create-vs-modify (creation verb → create)
    "c5": "A",  # no Chinese examples — they were hurting more than helping
    "c6": "C",  # keep current agent_system.md widget XML intact
    "c7": "A",  # no fallback keyword rule needed
}


def make_winner_variant(winner_choices: dict[str, str] | None = None,
                        variant_id: str = "V_winner") -> Variant:
    """Build a single composite variant that combines the OFAT winners."""
    wc = winner_choices or DEFAULT_WINNER_CHOICES
    return _build_variant(variant_id, wc, "Composite winner from OFAT choices")