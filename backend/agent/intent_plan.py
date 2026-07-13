from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class IntentKind(StrEnum):
    """Top-level routing decisions produced by IntentRouter.route (LLM #1).

    Sub-intents within a multi-intent plan are dispatched individually to
    SubExecutors (see ``backend/agent/sub_executors.py``).
    """

    WIDGET_CREATE = "widget_create"
    WIDGET_MODIFY = "widget_modify"
    GRAPH_MUTATION = "graph_mutation"
    GRAPH_QUERY = "graph_query"
    PLAN_AND_ACT = "plan_and_act"
    MULTI_INTENT = "multi_intent"  # NEW: composite request with sub_intents[]
    CLARIFY = "clarify"
    CONVERSE = "converse"


# Sub-intent kinds (what each entry in sub_intents[] can be). Mirrors
# IntentKind for the cases we allow inside multi_intent plans.
class SubIntentKind(StrEnum):
    GRAPH_MUTATION = "graph_mutation"
    GRAPH_QUERY = "graph_query"
    WIDGET_CREATE = "widget_create"
    WIDGET_MODIFY = "widget_modify"
    WIDGET_EXTEND_SCHEMA = "widget_extend_schema"  # NEW: granular schema extension
    WIDGET_FIX_CODE = "widget_fix_code"            # NEW: targeted code patch
    WIDGET_REWRITE = "widget_rewrite"              # NEW: full re-plan + regen


@dataclass
class SubIntent:
    """A single, focused action within a multi-intent plan.

    Each SubIntent is executed by a matching SubExecutor (see
    ``backend.agent.sub_executors``). Sub-intents are sequential: the
    outputs of one become part of the context for the next.
    """

    kind: SubIntentKind
    app_id: str | None = None
    instruction: str | None = None
    actions: list[dict[str, Any]] = field(default_factory=list)         # for graph_mutation
    query: dict[str, Any] | None = None                                # for graph_query
    # For widget_extend_schema: {node_type: {prop_name: type_string}}
    extend_schema_props: dict[str, dict[str, str]] | None = None
    # For widget_fix_code: free-text feedback used by OpenCode regen
    feedback: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind.value}
        if self.app_id is not None:
            d["app_id"] = self.app_id
        if self.instruction is not None:
            d["instruction"] = self.instruction
        if self.actions:
            d["actions"] = list(self.actions)
        if self.query is not None:
            d["query"] = self.query
        if self.extend_schema_props:
            d["extend_schema_props"] = {
                k: dict(v) for k, v in self.extend_schema_props.items()
            }
        if self.feedback is not None:
            d["feedback"] = self.feedback
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SubIntent":
        if not isinstance(data, dict):
            data = {}
        kind_value = data.get("kind", "graph_mutation")
        try:
            kind = SubIntentKind(kind_value)
        except ValueError:
            kind = SubIntentKind.GRAPH_MUTATION
        return cls(
            kind=kind,
            app_id=data.get("app_id"),
            instruction=data.get("instruction"),
            actions=list(data.get("actions") or []),
            query=data.get("query"),
            extend_schema_props=data.get("extend_schema_props"),
            feedback=data.get("feedback"),
        )


@dataclass
class IntentPlan:
    """Structured output from the intent router.

    Top-level ``kind`` drives the high-level orchestrator choice; for
    ``MULTI_INTENT`` and ``PLAN_AND_ACT`` plans, ``sub_intents`` carries the
    concrete action sequence.

    Legacy fields (``app_id``, ``actions``, ``query``, etc.) are kept for
    back-compat with single-intent callers.
    """

    kind: IntentKind
    confidence: float = 0.0
    rationale: str = ""

    # widget_create / widget_modify
    app_id: str | None = None
    instruction: str | None = None

    # graph_mutation / plan_and_act (legacy single-action field)
    actions: list[dict[str, Any]] = field(default_factory=list)

    # graph_query
    query: dict[str, Any] | None = None

    # NEW: composite requests
    sub_intents: list[SubIntent] = field(default_factory=list)

    # clarify
    clarification_message: str = ""
    clarification_options: list[dict[str, Any]] = field(default_factory=list)

    # Marks an intent as unresolved by downstream handlers; harness should downgrade.
    deprecated: bool = False

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "kind": self.kind.value,
            "confidence": self.confidence,
            "rationale": self.rationale,
        }
        if self.app_id is not None:
            d["app_id"] = self.app_id
        if self.instruction is not None:
            d["instruction"] = self.instruction
        if self.actions:
            d["actions"] = list(self.actions)
        if self.query is not None:
            d["query"] = self.query
        if self.sub_intents:
            d["sub_intents"] = [s.to_dict() for s in self.sub_intents]
        if self.clarification_message:
            d["clarification_message"] = self.clarification_message
        if self.clarification_options:
            d["clarification_options"] = list(self.clarification_options)
        if self.deprecated:
            d["deprecated"] = True
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IntentPlan":
        kind_value = data.get("kind", "converse")
        try:
            kind = IntentKind(kind_value)
        except ValueError:
            kind = IntentKind.CONVERSE
        subs_raw = data.get("sub_intents") or []
        subs = [SubIntent.from_dict(s) for s in subs_raw if isinstance(s, dict)]
        return cls(
            kind=kind,
            confidence=float(data.get("confidence", 0.0) or 0.0),
            rationale=str(data.get("rationale") or ""),
            app_id=data.get("app_id"),
            instruction=data.get("instruction"),
            actions=list(data.get("actions") or []),
            query=data.get("query"),
            sub_intents=subs,
            clarification_message=str(data.get("clarification_message") or ""),
            clarification_options=list(data.get("clarification_options") or []),
            deprecated=bool(data.get("deprecated", False)),
        )

    @classmethod
    def from_tool_call_args(cls, args: dict[str, Any]) -> "IntentPlan":
        if not isinstance(args, dict):
            args = {}
        return cls.from_dict(args)

    @staticmethod
    def tool_schema() -> dict[str, Any]:
        """Return the OpenAI-compatible tool schema describing an IntentPlan."""
        return {
            "type": "function",
            "function": {
                "name": "classify_intent",
                "description": (
                    "Classify the user's intent into a routing decision. "
                    "Always call this function with one of the enum 'kind' values."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": [k.value for k in IntentKind],
                            "description": (
                                "Routing decision. Prefer graph_mutation/graph_query when the "
                                "request can be served purely via the graph. Use multi_intent "
                                "when the request requires multiple sub-actions (e.g. mutate "
                                "graph AND extend a widget's schema in one go)."
                            ),
                        },
                        "confidence": {
                            "type": "number",
                            "description": "Self-assessed confidence between 0 and 1.",
                        },
                        "rationale": {
                            "type": "string",
                            "description": "One-sentence reason for the choice.",
                        },
                        "app_id": {
                            "type": ["string", "null"],
                            "description": "Widget ID (existing exact match or kebab-case new).",
                        },
                        "instruction": {
                            "type": "string",
                            "description": "Refined coding or mutation instruction.",
                        },
                        "actions": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": (
                                "For graph_mutation / plan_and_act: list of graph actions "
                                "(create_node, update_node_property, delete_node, "
                                "create_edge, delete_edge)."
                            ),
                        },
                        "query": {
                            "type": ["object", "null"],
                            "description": "Declarative query for graph_query.",
                        },
                        "sub_intents": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "kind": {
                                        "type": "string",
                                        "enum": [k.value for k in SubIntentKind],
                                    },
                                    "app_id": {"type": ["string", "null"]},
                                    "instruction": {"type": "string"},
                                    "actions": {"type": "array"},
                                    "query": {"type": ["object", "null"]},
                                    "extend_schema_props": {
                                        "type": "object",
                                        "description": (
                                            "{node_type: {prop_name: type_string}}. Only for "
                                            "widget_extend_schema sub-intents."
                                        ),
                                    },
                                    "feedback": {"type": "string"},
                                },
                            },
                            "description": (
                                "For multi_intent / plan_and_act: ordered list of sub-actions. "
                                "Executed sequentially; outputs flow forward."
                            ),
                        },
                        "clarification_message": {
                            "type": "string",
                            "description": "Question shown to the user when kind=clarify.",
                        },
                        "clarification_options": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "Choices presented to the user on clarify.",
                        },
                    },
                    "required": ["kind", "rationale"],
                },
            },
        }
