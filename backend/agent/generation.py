"""Compile generated parameters without letting generation reselect a decision."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator

from backend.agent.decision_context import content_hash
from backend.agent.intent_plan import IntentKind, IntentPlan, SubIntentKind
from backend.app_manifest import validate_app_id


class GenerationContractError(ValueError):
    pass


def validate_complete_plan(plan: IntentPlan) -> None:
    """Structural admission only; Graph/schema/effect validation stays durable."""
    if plan.kind == IntentKind.CONVERSE and not (plan.instruction or "").strip():
        raise GenerationContractError("converse_instruction_missing")
    if plan.kind == IntentKind.CLARIFY and not plan.clarification_message.strip():
        raise GenerationContractError("clarification_message_missing")
    if plan.kind in (IntentKind.WIDGET_CREATE, IntentKind.WIDGET_MODIFY):
        validate_app_id(plan.app_id)
        if not (plan.instruction or "").strip():
            raise GenerationContractError("app_instruction_missing")
    if plan.kind == IntentKind.GRAPH_QUERY and (not isinstance(plan.query, dict) or not plan.query):
        raise GenerationContractError("graph_query_missing")
    if plan.kind == IntentKind.GRAPH_MUTATION and (
        not plan.actions or any(not isinstance(item, dict) or not item.get("action") for item in plan.actions)
    ):
        raise GenerationContractError("graph_actions_missing")
    if plan.kind in (IntentKind.MULTI_INTENT, IntentKind.PLAN_AND_ACT):
        if not plan.sub_intents or len(plan.sub_intents) > 32:
            raise GenerationContractError("composite_steps_missing_or_excessive")
        for sub in plan.sub_intents:
            mapped = {
                SubIntentKind.WIDGET_EXTEND_SCHEMA: IntentKind.WIDGET_MODIFY,
                SubIntentKind.WIDGET_FIX_CODE: IntentKind.WIDGET_MODIFY,
                SubIntentKind.WIDGET_REWRITE: IntentKind.WIDGET_MODIFY,
            }.get(sub.kind)
            kind = mapped or IntentKind(sub.kind.value)
            validate_complete_plan(
                IntentPlan(
                    kind=kind,
                    instruction=sub.instruction or sub.feedback,
                    app_id=sub.app_id,
                    query=sub.query,
                    actions=sub.actions,
                )
            )
            if sub.kind == SubIntentKind.WIDGET_EXTEND_SCHEMA and not sub.extend_schema_props:
                raise GenerationContractError("schema_extension_missing")


@dataclass(frozen=True)
class IntentGenerationTask:
    kind: IntentKind
    instruction: str
    target_app_id: str | None
    decision_hash: str
    confidence: float

    def tool_schema(self) -> dict[str, Any]:
        source = copy.deepcopy(IntentPlan.tool_schema()["function"]["parameters"])
        for field in ("kind", "confidence", "rationale"):
            source["properties"].pop(field, None)
        editable = {
            IntentKind.GRAPH_QUERY: {"query"},
            IntentKind.GRAPH_MUTATION: {"actions"},
            IntentKind.WIDGET_CREATE: {"app_id", "instruction"},
            IntentKind.WIDGET_MODIFY: {"instruction"},
            IntentKind.MULTI_INTENT: {"sub_intents"},
            IntentKind.PLAN_AND_ACT: {"sub_intents"},
            IntentKind.CLARIFY: {"clarification_message", "clarification_options"},
            IntentKind.CONVERSE: {"instruction"},
        }[self.kind]
        source["properties"] = {key: value for key, value in source["properties"].items() if key in editable}
        if self.target_app_id is not None:
            source["properties"].pop("app_id", None)
        source["additionalProperties"] = False
        required = {
            IntentKind.GRAPH_QUERY: ["query"],
            IntentKind.GRAPH_MUTATION: ["actions"],
            IntentKind.WIDGET_CREATE: ["app_id", "instruction"],
            IntentKind.WIDGET_MODIFY: ["instruction"],
            IntentKind.MULTI_INTENT: ["sub_intents"],
            IntentKind.PLAN_AND_ACT: ["sub_intents"],
            IntentKind.CLARIFY: ["clarification_message"],
            IntentKind.CONVERSE: ["instruction"],
        }[self.kind]
        source["required"] = [key for key in required if key in source["properties"]]
        action_schema = {
            "type": "object",
            "required": ["action"],
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "create_node",
                        "update_node_property",
                        "delete_node",
                        "create_edge",
                        "delete_edge",
                    ],
                }
            },
        }
        if "actions" in source["properties"]:
            source["properties"]["actions"]["items"] = action_schema
        if "sub_intents" in source["properties"]:
            nested = source["properties"]["sub_intents"]["items"]
            nested["required"] = ["kind"]
            nested["additionalProperties"] = False
            nested["properties"]["extend_schema_props"].update(
                additionalProperties={
                    "type": "object",
                    "additionalProperties": {
                        "type": "string",
                        "enum": ["string", "integer", "number", "boolean"],
                    },
                }
            )
            nested["properties"]["actions"]["items"] = action_schema
        return {
            "type": "function",
            "function": {
                "name": "generate_intent_parameters",
                "description": "Fill only the missing parameters for the fixed routing decision.",
                "parameters": source,
            },
        }

    def compile(self, parameters: dict[str, Any], *, current_decision_hash: str) -> IntentPlan:
        if current_decision_hash != self.decision_hash:
            raise GenerationContractError("stale_generation_decision")
        allowed = self.tool_schema()["function"]["parameters"]["properties"]
        if not isinstance(parameters, dict) or set(parameters) - set(allowed):
            raise GenerationContractError("generation_changed_fixed_fields")
        required = self.tool_schema()["function"]["parameters"]["required"]
        if set(required) - set(parameters):
            raise GenerationContractError("generated_parameters_missing")
        if not Draft202012Validator(self.tool_schema()["function"]["parameters"]).is_valid(parameters):
            raise GenerationContractError("generated_parameters_invalid")
        for key in ("app_id", "instruction", "clarification_message"):
            if key in parameters and not isinstance(parameters[key], str):
                raise GenerationContractError("generated_text_invalid")
        if "sub_intents" in parameters:
            subs = parameters["sub_intents"]
            nested = allowed["sub_intents"]["items"]["properties"]
            if not isinstance(subs, list) or any(
                not isinstance(sub, dict) or "kind" not in sub or set(sub) - set(nested) for sub in subs
            ):
                raise GenerationContractError("generated_steps_invalid")
            for sub in subs:
                SubIntentKind(sub["kind"])
                for key in ("instruction", "app_id", "feedback"):
                    if key in sub and sub[key] is not None and not isinstance(sub[key], str):
                        raise GenerationContractError("generated_step_text_invalid")
        fixed = {"kind": self.kind.value, "confidence": self.confidence, "rationale": "decision plus generation"}
        if self.target_app_id is not None:
            fixed["app_id"] = self.target_app_id
        plan = IntentPlan.from_dict({**parameters, **fixed})
        validate_complete_plan(plan)
        return plan

    def binding(self) -> dict[str, Any]:
        return {
            "purpose": "intent_parameters",
            "kind": self.kind.value,
            "target_app_id": self.target_app_id,
            "decision_hash": self.decision_hash,
            "input_hash": content_hash(self.instruction),
        }
