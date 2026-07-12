from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class IntentKind(StrEnum):
    """Routing decisions produced by IntentRouter.route."""

    WIDGET_CREATE = "widget_create"
    WIDGET_MODIFY = "widget_modify"
    GRAPH_MUTATION = "graph_mutation"
    GRAPH_QUERY = "graph_query"
    PLAN_AND_ACT = "plan_and_act"
    CLARIFY = "clarify"
    CONVERSE = "converse"


@dataclass
class IntentPlan:
    """Structured output from the intent router.

    The ``kind`` field drives downstream branching in ``AgentOrchestrator``.
    Only fields relevant to a given kind are populated.
    """

    kind: IntentKind
    confidence: float = 0.0
    rationale: str = ""

    # widget_create / widget_modify
    app_id: str | None = None
    instruction: str | None = None

    # graph_mutation / plan_and_act
    actions: list[dict[str, Any]] = field(default_factory=list)

    # graph_query
    query: dict[str, Any] | None = None

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
        return cls(
            kind=kind,
            confidence=float(data.get("confidence", 0.0) or 0.0),
            rationale=str(data.get("rationale") or ""),
            app_id=data.get("app_id"),
            instruction=data.get("instruction"),
            actions=list(data.get("actions") or []),
            query=data.get("query"),
            clarification_message=str(data.get("clarification_message") or ""),
            clarification_options=list(data.get("clarification_options") or []),
            deprecated=bool(data.get("deprecated", False)),
        )

    @classmethod
    def from_tool_call_args(cls, args: dict[str, Any]) -> "IntentPlan":
        """Build from OpenAI-style function-calling args (already parsed JSON)."""
        if not isinstance(args, dict):
            args = {}
        return cls.from_dict(args)

    @staticmethod
    def tool_schema() -> dict[str, Any]:
        """Return the OpenAI-compatible tool schema describing an IntentPlan.

        This is used by ``IntentRouter.route`` to obtain structured output from
        the LLM via function-calling.
        """
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
                                "request can be served purely via the graph."
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
                                "For graph_mutation/plan_and_act: list of graph actions "
                                "(create_node, update_node_property, delete_node, "
                                "create_edge, delete_edge)."
                            ),
                        },
                        "query": {
                            "type": ["object", "null"],
                            "description": "Declarative query for graph_query.",
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
