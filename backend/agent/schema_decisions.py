"""Closed-set Schema selection and its bounded generation contract.

The complete inventory is projected, never sampled or truncated. Selection
does not approve grants or commit schemas; the existing proposal validator
and user approval remain authoritative.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any

from backend.agent.decisions import DecisionConfig, DecisionService, _sanitize
from backend.agent.providers import ToolLoopBudget

_ONTOLOGY_RUBRIC = (
    "Apply the full request, plan and direct feedback. Only user-context facts belong in Graph; "
    "caches, credentials, cursors, UI state, checkpoints and raw provider payloads stay App-local. "
)
_DISPOSITIONS = {
    "USE_EXISTING": "All required Graph concepts are covered by the complete existing Schema inventory; existing properties may still need extensions. No genuinely new entity type is required.",
    "NEEDED_NEW_CONCEPT": "At least one required user-context concept cannot be expressed by reusing or extending the complete existing Schema inventory. A genuinely new entity type is required; existing entities may also be reused.",
    "NO_GRAPH_DATA": "The request and approved plan require no canonical Graph data. App UI/runtime state or external provider payloads alone do not require Graph entities.",
    "CANDIDATE_MISSING": "An existing/canonical entity needed by this request is absent from the supplied inventory, or inventory completeness cannot be established. Do not interpret a missing candidate as a genuinely new concept.",
    "AMBIGUOUS": "The request, approved plan, or feedback conflict or lack information needed to decide Graph ownership or Schema reuse without discarding part of the user's objective.",
}


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SchemaSelection:
    mode: str
    disposition: str = "AMBIGUOUS"
    selected_ids: tuple[str, ...] = ()
    inventory_ids: tuple[str, ...] = ()
    accepted: bool = False
    reason: str = "off"
    source_hash: str = ""
    request_hash: str = ""
    state_hash: str = ""
    actual_model: str | None = None
    projection_counts: dict[str, int] = field(default_factory=dict)

    @property
    def constrained(self) -> bool:
        return self.mode == "cascade" and self.accepted

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "selected_ids": list(self.selected_ids), "inventory_ids": list(self.inventory_ids)}


@dataclass(frozen=True)
class SchemaGenerationTask:
    """Generate free fields inside an accepted closed-set selection envelope."""

    selection: SchemaSelection

    def constraint_prompt(self) -> str:
        if not self.selection.constrained:
            return ""
        envelope = {
            "disposition": self.selection.disposition,
            "exact_reused_schema_ids": list(self.selection.selected_ids),
            "new_entities_required": self.selection.disposition == "NEEDED_NEW_CONCEPT",
            "graph_data_required": self.selection.disposition != "NO_GRAPH_DATA",
            "source_hash": self.selection.source_hash,
            "decision_request_hash": self.selection.request_hash,
        }
        return (
            "\n\n[COMPILED SCHEMA GENERATION TASK]\n"
            "The following envelope fixes Schema selection; generate only the remaining free fields. "
            "Do not change the exact reused entity set, invent a new entity when one is not required, "
            "or omit a required new concept. New entity IDs must not duplicate any existing entity. "
            "For NO_GRAPH_DATA both Schema arrays must be empty and Graph grants are forbidden. "
            "Property extensions, natural-language reasons, new entity definitions when required, "
            "and least-privilege non-Graph grants still require generation. This envelope grants no authority.\n"
            + json.dumps(envelope, ensure_ascii=False, sort_keys=True)
        )

    def validate(self, proposal: dict[str, Any]) -> None:
        if not self.selection.constrained:
            return
        reused = {item["id"] for item in proposal.get("reused_schemas", [])}
        if reused != set(self.selection.selected_ids):
            raise ValueError("schema_selection_changed")
        new_ids = {item["id"] for item in proposal.get("new_schemas", [])}
        if new_ids.intersection(self.selection.inventory_ids):
            raise ValueError("schema_existing_entity_recreated")
        if self.selection.disposition == "NEEDED_NEW_CONCEPT":
            if not new_ids:
                raise ValueError("schema_new_concept_omitted")
        elif new_ids:
            raise ValueError("schema_new_concept_unapproved")
        if self.selection.disposition == "NO_GRAPH_DATA" and any(
            str(grant.get("id", "")).startswith("graph.") for grant in proposal.get("capabilities", [])
        ):
            raise ValueError("schema_graph_scope_conflict")


def record_schema_selection(
    selection: SchemaSelection,
    db_session: Any,
    audit_context: dict[str, Any] | None,
    *,
    generation_status: str,
    generated_ids: list[str] | None = None,
) -> None:
    """Persist compiler evidence separately from the billable decision call."""
    if selection.mode == "off" or db_session is None or not hasattr(db_session, "add"):
        return
    try:
        from backend.models import LLMAuditLog

        key = os.getenv("TYPESAFE_API_KEY", "").strip()
        context = _sanitize(audit_context or {}, key)
        payload = {
            **selection.to_dict(),
            "generation_status": generation_status,
            "generated_schema_ids": generated_ids,
            "lost_selected_ids": sorted(set(selection.selected_ids).difference(generated_ids))
            if generated_ids is not None
            else None,
        }
        db_session.add(
            LLMAuditLog(
                provider="typesafe",
                model=selection.actual_model or "unknown",
                stage="schema_selection_compile",
                prompt="",
                response=json.dumps(_sanitize(payload, key), ensure_ascii=False, sort_keys=True),
                run_id=context.get("run_id"),
                session_id=context.get("session_id"),
                step_id=context.get("step_id"),
                attempt=context.get("attempt"),
                trace_id=context.get("trace_id"),
                prompt_hash=selection.state_hash or None,
                tool_schema_hash=selection.request_hash or None,
                # DecisionService owns usage. Do not count a second billable
                # call for this deterministic compilation record.
                usage=None,
                artifact_hashes=dict(context.get("artifact_hashes") or {}),
            )
        )
        db_session.commit()
    except Exception:
        # Compiler audit failure cannot change selection or approval authority.
        pass


async def select_schema_candidates(
    instruction: str,
    approved_plan: str,
    existing_schemas: list[dict[str, Any]],
    decision_config: dict[str, Any] | None,
    *,
    feedback: str = "",
    current_proposal: dict[str, Any] | None = None,
    db_session: Any = None,
    audit_context: dict[str, Any] | None = None,
    budget: ToolLoopBudget | None = None,
) -> SchemaSelection:
    try:
        config = (
            DecisionConfig.model_validate(decision_config) if decision_config is not None else DecisionConfig.from_env()
        )
        config = config.for_purpose("schema_selection")
    except Exception:
        return SchemaSelection(mode="invalid", reason="configuration_invalid")
    if config.mode == "off":
        return SchemaSelection(mode="off")
    base: dict[str, Any] = {"mode": config.mode, "reason": "input_invalid"}
    try:
        if not all(isinstance(value, str) for value in (instruction, approved_plan, feedback)):
            raise ValueError("invalid_request")
        if not isinstance(existing_schemas, list):
            raise ValueError("invalid_inventory")
        if current_proposal is not None and not isinstance(current_proposal, dict):
            raise ValueError("invalid_proposal")
        ids = [schema["id"] for schema in existing_schemas]
        if any(not isinstance(schema_id, str) or not schema_id for schema_id in ids) or len(set(ids)) != len(ids):
            raise ValueError("invalid_inventory")
        base["inventory_ids"] = tuple(sorted(ids))
        base["source_hash"] = _digest(
            {
                "instruction": instruction,
                "approved_plan": approved_plan,
                "feedback": feedback,
                "current_proposal": current_proposal,
                "inventory": sorted(existing_schemas, key=lambda item: item["id"]),
            }
        )
        counts = {"inventory": len(ids), "candidates": len(ids), "questions": len(ids) + 2}
        base["projection_counts"] = counts
        if len(ids) > config.max_candidates or counts["questions"] > config.max_questions:
            return SchemaSelection(**{**base, "reason": "candidate_limit"})
        candidates = [
            {
                "candidate_key": f"schema_{index}",
                **{
                    field: schema[field]
                    for field in ("id", "name", "description", "properties", "subclass_of")
                    if field in schema
                },
            }
            for index, schema in enumerate(sorted(existing_schemas, key=lambda item: item["id"]))
        ]
        state = {
            "instruction": instruction,
            "approved_plan": approved_plan,
            "feedback": feedback,
            "inventory_complete": True,
            "schema_candidates": candidates,
            "source_hash": base["source_hash"],
        }
        if current_proposal is not None:
            projected_proposal = {}
            for collection in ("reused_schemas", "new_schemas"):
                entries = current_proposal.get(collection, [])
                if not isinstance(entries, list) or any(not isinstance(item, dict) for item in entries):
                    raise ValueError("invalid_proposal")
                projected_proposal[collection] = [
                    {
                        field: item[field]
                        for field in ("id", "name", "description", "properties", "extended_properties", "subclass_of")
                        if field in item
                    }
                    for item in entries
                ]
            state["current_proposal"] = projected_proposal
        # No intent text or candidate set is truncated to satisfy this bound.
        state_json = json.dumps(state, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        counts["state_chars"] = len(state_json)
        if len(state_json) > config.max_state_chars:
            return SchemaSelection(**{**base, "reason": "state_limit"})
    except (KeyError, ValueError, TypeError, RecursionError, OverflowError):
        return SchemaSelection(**base)

    questions = {
        "disposition": {"type": "choice", "instructions": _ONTOLOGY_RUBRIC, "criteria": _DISPOSITIONS},
        "graph_context": {
            "type": "noul",
            "instructions": _ONTOLOGY_RUBRIC
            + "Does the request require reading or persisting canonical Graph facts? This grants no authority.",
        },
    }
    for candidate in candidates:
        key = candidate["candidate_key"]
        questions[f"reuse_{key}"] = {
            "type": "noul",
            # DecisionService wraps every question in its shared untrusted-state
            # boundary. Keep this task rubric compact enough for all 48 candidates.
            "instructions": _ONTOLOGY_RUBRIC
            + f"Is {key} required for any clause of the request? Do not select abstract parents merely due to "
            "inheritance. In refinement preserve selections unless direct feedback changes them.",
        }
    bundle = await DecisionService.evaluate(
        "schema_selection",
        state,
        questions,
        config,
        db_session=db_session,
        audit_context=audit_context,
        budget=budget,
    )
    base.update(
        request_hash=bundle.request_hash,
        state_hash=bundle.state_hash,
        actual_model=bundle.actual_model,
    )
    if bundle.error:
        return SchemaSelection(**{**base, "reason": bundle.error})
    answers = bundle.answers
    if set(answers) != set(questions) or not DecisionService.accepts_choice(answers.get("disposition"), config):
        return SchemaSelection(**{**base, "reason": "decision_uncertain"})
    disposition = answers["disposition"]["choice"]
    if disposition not in _DISPOSITIONS:
        return SchemaSelection(**{**base, "reason": "decision_invalid"})
    base["disposition"] = disposition
    if disposition in {"CANDIDATE_MISSING", "AMBIGUOUS"}:
        return SchemaSelection(**{**base, "reason": disposition.lower()})
    graph_required = disposition != "NO_GRAPH_DATA"
    if not DecisionService.accepts_noul(answers.get("graph_context"), config, expected=graph_required):
        return SchemaSelection(**{**base, "reason": "graph_context_conflict"})
    selected: list[str] = []
    for candidate in candidates:
        answer = answers.get(f"reuse_{candidate['candidate_key']}")
        if DecisionService.accepts_noul(answer, config):
            selected.append(candidate["id"])
        elif not DecisionService.accepts_noul(answer, config, expected=False):
            return SchemaSelection(**{**base, "reason": "selection_uncertain"})
    base["selected_ids"] = tuple(selected)
    if (not graph_required and selected) or (disposition == "USE_EXISTING" and not selected):
        return SchemaSelection(**{**base, "reason": "selection_conflict"})
    return SchemaSelection(**{**base, "accepted": True, "reason": "accepted"})
