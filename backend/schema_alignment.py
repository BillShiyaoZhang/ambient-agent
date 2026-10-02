import asyncio
import copy
import json
import logging
import re
import time
from typing import Any

from backend.agent.decisions import remaining_budget
from backend.agent.schema_decisions import (
    SchemaGenerationTask,
    SchemaSelection,
    record_schema_selection,
    select_schema_candidates,
)
from backend.agent.providers import ToolLoopBudget, get_llm_provider
from backend.agent.errors import BudgetExhaustedError, WorkflowError
from backend.capabilities.catalog import AgentRole, SystemCapabilityCatalog
from backend.capabilities.models import normalize_grants
from backend.graph_db import GraphDatabase
from backend.llm_config import LLMConfigError
from backend.llm_runtime import primary_selection, selection_ids

logger = logging.getLogger("schema_alignment")


def validate_schema_capability_proposal(
    raw_proposal: dict[str, Any],
    catalog: SystemCapabilityCatalog,
) -> dict[str, Any]:
    """Canonicalize and validate an editable schema + capability proposal.

    Model output and user-edited approval payloads must pass through the same
    validator.  In particular, Graph grants may reference only entities that
    remain in the proposal after an edit.
    """

    if not isinstance(raw_proposal, dict):
        raise ValueError("Schema proposal must be a JSON object")
    proposal = json.loads(json.dumps(raw_proposal))
    proposal.setdefault("reused_schemas", [])
    proposal.setdefault("new_schemas", [])
    if not isinstance(proposal["reused_schemas"], list) or not isinstance(proposal["new_schemas"], list):
        raise ValueError("Schema proposal reused_schemas and new_schemas must be arrays")

    schema_entries = [*proposal["reused_schemas"], *proposal["new_schemas"]]
    if any(not isinstance(item, dict) for item in schema_entries):
        raise ValueError("Schema proposal entries must be objects")
    schema_ids = [str(item.get("id") or "").strip() for item in schema_entries]
    if any(not schema_id for schema_id in schema_ids):
        raise ValueError("Every schema proposal entry must have a non-empty id")
    duplicate_ids = sorted({schema_id for schema_id in schema_ids if schema_ids.count(schema_id) > 1})
    if duplicate_ids:
        raise ValueError(f"Duplicate schema proposal entities: {', '.join(duplicate_ids)}")

    normalized_grants = normalize_grants(proposal.get("capabilities", []))
    catalog.validate_grants(normalized_grants, graph_entity_ids=set(schema_ids))
    proposal["capabilities"] = [grant.to_dict() for grant in normalized_grants]
    return proposal


def _parse_and_validate_proposal(
    raw_response: str,
    catalog: SystemCapabilityCatalog,
) -> dict[str, Any]:
    cleaned = raw_response.strip()
    code_block_match = re.search(r"```json\s*(.*?)\s*```", cleaned, re.DOTALL)
    if code_block_match:
        cleaned = code_block_match.group(1).strip()
    else:
        code_block_match = re.search(r"```\s*(.*?)\s*```", cleaned, re.DOTALL)
        if code_block_match:
            cleaned = code_block_match.group(1).strip()
    start_idx = cleaned.find("{")
    end_idx = cleaned.rfind("}")
    if start_idx != -1 and end_idx != -1:
        cleaned = cleaned[start_idx : end_idx + 1]

    proposal = json.loads(cleaned)
    return validate_schema_capability_proposal(proposal, catalog)


async def _generate_validated_proposal(
    provider: Any,
    messages: list[dict[str, str]],
    *,
    catalog: SystemCapabilityCatalog,
    db_session: Any,
    budget: ToolLoopBudget | None,
    audit_context: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    started = time.monotonic()

    async def generate(call_messages: list[dict[str, str]], call_context: dict[str, Any]) -> str:
        limits = remaining_budget(budget, started)
        invocation = provider.generate(call_messages, db_session=db_session, budget=limits, audit_context=call_context)
        if limits is None:
            return await invocation
        try:
            return await asyncio.wait_for(invocation, timeout=limits.wall_clock_s)
        except TimeoutError:
            raise BudgetExhaustedError("Schema generation exceeded its wall-clock budget") from None

    raw_response = await generate(messages, audit_context)
    try:
        return _parse_and_validate_proposal(raw_response, catalog), raw_response
    except Exception as validation_error:
        repair_prompt = (
            "Your previous JSON violated the supplied Capability Ontology scope contract.\n"
            f"Validation error: {str(validation_error)[:1_000]}\n"
            "Return the complete corrected JSON object only. Preserve the requested schemas and least-privilege intent. "
            "Do not broaden the requested capabilities, invent placeholders, or omit required nested fields."
        )
        repair_messages = [
            *messages,
            {"role": "assistant", "content": raw_response[-12_000:]},
            {"role": "user", "content": repair_prompt},
        ]
        repaired_response = await generate(
            repair_messages,
            {**audit_context, "stage": f"{audit_context.get('stage', 'schema_alignment')}_repair"},
        )
        return _parse_and_validate_proposal(repaired_response, catalog), repaired_response


def _schema_inventory(schemas: list[dict[str, Any]], *, include_description: bool) -> str:
    lines: list[str] = []
    for schema in schemas:
        lines.append(f"- Schema ID: '{schema['id']}'")
        if include_description:
            lines.append(f"  Name: {schema['name']}")
            lines.append(f"  Description: {schema['description']}")
        lines.append(f"  Properties: {json.dumps(schema['properties'])}\n")
    return "\n".join(lines) + ("\n" if lines else "")


async def _generate_schema_task(
    provider: Any,
    full_messages: list[dict[str, str]],
    constrained_user_prompt: str,
    selection: SchemaSelection,
    *,
    catalog: SystemCapabilityCatalog,
    db_session: Any,
    budget: ToolLoopBudget | None,
    started: float,
    audit_context: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    task = SchemaGenerationTask(selection)
    constrained = selection.constrained
    messages = (
        [full_messages[0], {"role": "user", "content": constrained_user_prompt + task.constraint_prompt()}]
        if constrained
        else full_messages
    )
    record_schema_selection(
        selection,
        db_session,
        audit_context,
        generation_status="constrained" if constrained else "shadow" if selection.mode == "shadow" else "unconstrained",
    )
    proposal: dict[str, Any] | None = None
    try:
        proposal, raw = await _generate_validated_proposal(
            provider,
            messages,
            catalog=catalog,
            db_session=db_session,
            budget=remaining_budget(budget, started),
            audit_context=audit_context,
        )
        remaining_budget(budget, started)
        task.validate(proposal)
    except (LLMConfigError, BudgetExhaustedError):
        raise
    except Exception:
        if not constrained:
            raise
        record_schema_selection(
            selection,
            db_session,
            audit_context,
            generation_status="constraint_conflict",
            generated_ids=[item["id"] for item in proposal.get("reused_schemas", [])] if proposal else None,
        )
        # Never discard the user's objective because a candidate decision or
        # generated payload disagreed. The complete original inventory and
        # request remain the fallback authority, with normal validation.
        proposal, raw = await _generate_validated_proposal(
            provider,
            full_messages,
            catalog=catalog,
            db_session=db_session,
            budget=remaining_budget(budget, started),
            audit_context={**audit_context, "stage": f"{audit_context['stage']}_fallback"},
        )
        remaining_budget(budget, started)
        record_schema_selection(
            selection,
            db_session,
            audit_context,
            generation_status="fallback_complete",
            generated_ids=[item["id"] for item in proposal.get("reused_schemas", [])],
        )
        return proposal, raw
    record_schema_selection(
        selection,
        db_session,
        audit_context,
        generation_status="compiled"
        if constrained
        else "shadow_complete"
        if selection.mode == "shadow"
        else "complete",
        generated_ids=[item["id"] for item in proposal.get("reused_schemas", [])],
    )
    return proposal, raw


class SchemaAlignmentService:
    @staticmethod
    async def align_schemas(
        instruction: str,
        app_id: str,
        db: GraphDatabase,
        db_session: Any = None,
        approved_plan: str = "",
        language: str = "zh",
        audit_context: dict[str, Any] | None = None,
        budget: ToolLoopBudget | None = None,
        capability_catalog: SystemCapabilityCatalog | None = None,
        *,
        decision_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Interacts with the LLM to perform semantic schema alignment.
        Analyzes the app instruction against existing database schemas and returns a proposal dict.
        """
        started = time.monotonic()
        # 1. Retrieve current schema inventory
        existing_schemas = copy.deepcopy(db.list_schemas())
        selection = await select_schema_candidates(
            instruction,
            approved_plan,
            existing_schemas,
            decision_config,
            db_session=db_session,
            audit_context=audit_context,
            budget=remaining_budget(budget, started),
        )
        schemas_info = _schema_inventory(existing_schemas, include_description=True)

        is_zh = language == "zh"
        catalog = capability_catalog or SystemCapabilityCatalog.build()
        rendered_capability_catalog = catalog.render(AgentRole.SCHEMA_ALIGNMENT)
        system_prompt = f"""You are a Canonical Ontology Alignment Architect.
Your task is to analyze a widget request and match only its user-context facts against the single `ambient-context` ontology.

### Guidelines:
1. **One Ontology**: Every proposed entity belongs to `ambient-context`; never create an App-owned or disconnected ontology.
2. **Context Data Only**: Do not propose entities or properties for caches, sync cursors, UI state, credentials, job checkpoints, or raw provider payloads. Those belong in the App directory. A URI/summary reference may be modeled only when it improves understanding of the user's context.
3. **Reusability First**: If the application needs standard concepts like tasks, to-do lists, calendar events, notes, people, organizations, projects, places, messages, documents, or App data references, you MUST reuse the corresponding core entity (including `SoftwareApplication` for App references) rather than creating a duplicate.
4. **Property Extensions**: If you reuse an existing entity, propose extra context fields under `extended_properties`.
5. **New Entities**: Propose a new entity only if the concept is genuinely new. Attach it to an existing `subclass_of` parent (normally `Thing`) and provide established external `equivalent_to` IRIs when available.
6. **Supported Data Types**: Property fields must use one of: "string", "integer", "number", "boolean".
7. **Capability Ontology**: Propose the smallest required Widget grants from the supplied Capability Ontology and follow each category's complete `scope_contract`. Do not invent category ids, scope fields, entity types, installed catalog ids, or installed actions. For `network.request`, propose full public HTTPS source definitions rather than placeholder names. An empty capabilities array is valid.

{rendered_capability_catalog}

IMPORTANT: You MUST write all natural-language explanations, names, and descriptions (e.g. 'reason', 'name', 'description') in {"Chinese (中文)" if is_zh else "English"}.

### Output Format:
You MUST output ONLY a valid JSON object matching the following structure, with NO surrounding chat text, no markdown explanation, just the JSON block:
{{
  "reused_schemas": [
    {{
      "id": "Task",
      "reason": "To represent individual items on the checklist",
      "extended_properties": {{
        "difficulty_level": "string"
      }},
      "data_scope": "user_context"
    }}
  ],
  "new_schemas": [
    {{
      "id": "PomodoroSession",
      "name": "Pomodoro Session",
      "description": "Represents a completed focused pomodoro block",
      "properties": {{
        "duration_minutes": "integer",
        "completed": "boolean"
      }},
      "subclass_of": "Thing",
      "ontology_iri": "https://example.org/PomodoroSession",
      "equivalent_to": ["https://schema.org/Action"],
      "data_scope": "user_context"
    }}
  ],
  "capabilities": [
    {{"id": "graph.query", "scope": {{"entities": ["Task"]}}}}
  ]
}}
"""

        user_prompt = f"""We are building a widget app with:
App ID: "{app_id}"
User Instruction: "{instruction}"
"""
        if approved_plan:
            user_prompt += f"Approved Development Plan:\n{approved_plan}\n\n"

        user_prefix = user_prompt
        user_prompt += f"""Here is the inventory of our existing database schemas:
{schemas_info if schemas_info else "(No existing schemas)"}

Propose the optimal schema alignment plan for this widget as a JSON block.
"""

        # 2. Call LLM
        provider_name, model_name = selection_ids(primary_selection())
        provider = get_llm_provider(provider_name, model_name)

        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]
        selected_info = _schema_inventory(
            [schema for schema in existing_schemas if schema["id"] in selection.selected_ids],
            include_description=True,
        )
        constrained_user_prompt = (
            user_prefix
            + "Here is the selected existing Schema inventory:\n"
            + (selected_info or "(No existing entity selected)\n")
            + "\nGenerate the remaining Schema and capability proposal fields as a JSON block.\n"
        )

        raw_response = ""
        try:
            proposal, raw_response = await _generate_schema_task(
                provider,
                messages,
                constrained_user_prompt,
                selection,
                catalog=catalog,
                db_session=db_session,
                budget=budget,
                started=started,
                audit_context={**(audit_context or {}), "stage": "schema_alignment"},
            )
            return proposal

        except (LLMConfigError, BudgetExhaustedError):
            raise
        except Exception as e:
            logger.error(f"Failed to generate or parse schema alignment: {e}. Raw response: {raw_response}")
            raise WorkflowError(
                "Schema capability alignment generation or parsing failed",
                code="schema_alignment_failed",
                retryable=True,
            ) from e

    @staticmethod
    async def refine_proposal(
        instruction: str,
        app_id: str,
        current_proposal: dict[str, Any],
        feedback: str,
        db: GraphDatabase,
        db_session: Any = None,
        approved_plan: str = "",
        language: str = "zh",
        audit_context: dict[str, Any] | None = None,
        budget: ToolLoopBudget | None = None,
        capability_catalog: SystemCapabilityCatalog | None = None,
        *,
        decision_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Refines the current schema proposal using natural language feedback from the user.
        """
        started = time.monotonic()
        existing_schemas = copy.deepcopy(db.list_schemas())
        current_proposal = copy.deepcopy(current_proposal)
        selection = await select_schema_candidates(
            instruction,
            approved_plan,
            existing_schemas,
            decision_config,
            feedback=feedback,
            current_proposal=current_proposal,
            db_session=db_session,
            audit_context=audit_context,
            budget=remaining_budget(budget, started),
        )
        schemas_info = _schema_inventory(existing_schemas, include_description=False)

        is_zh = language == "zh"
        catalog = capability_catalog or SystemCapabilityCatalog.build()
        rendered_capability_catalog = catalog.render(AgentRole.SCHEMA_ALIGNMENT)
        system_prompt = f"""You are a Canonical Ontology Alignment Architect.
Your task is to refine an `ambient-context` ontology proposal based on direct natural language feedback from the user.

### Guidelines:
1. Maintain existing schema selections unless the user's feedback specifically requests modifications to them.
2. Property fields must use one of these types: "string", "integer", "number", "boolean".
3. Implement exactly what the user requests in their feedback.
4. Keep all entities in the single canonical ontology and preserve `subclass_of`/`equivalent_to` alignments.
5. Never model App-only runtime data; caches, cursors, credentials, UI state, checkpoints, and raw provider payloads stay in the App directory.
6. Refine capability grants from the supplied Capability Ontology with least privilege and follow every complete `scope_contract`. Do not invent category ids, scope fields, installed catalog ids, or installed actions. Declare full public HTTPS source objects for `network.request`.

{rendered_capability_catalog}

IMPORTANT: You MUST write all natural-language explanations, names, and descriptions (e.g. 'reason', 'name', 'description') in {"Chinese (中文)" if is_zh else "English"}.

### Output Format:
You MUST output ONLY a valid JSON object matching the following structure, with NO surrounding chat text, no markdown explanation, just the JSON block:
{{
  "reused_schemas": [
    {{
      "id": "Task",
      "reason": "To represent individual items on the checklist",
      "extended_properties": {{
        "difficulty_level": "string"
      }},
      "data_scope": "user_context"
    }}
  ],
  "new_schemas": [
    {{
      "id": "PomodoroSession",
      "name": "Pomodoro Session",
      "description": "Represents a completed focused pomodoro block",
      "properties": {{
        "duration_minutes": "integer",
        "completed": "boolean"
      }},
      "subclass_of": "Thing",
      "ontology_iri": "https://example.org/PomodoroSession",
      "equivalent_to": ["https://schema.org/Action"],
      "data_scope": "user_context"
    }}
  ],
  "capabilities": [
    {{"id": "graph.query", "scope": {{"entities": ["Task"]}}}}
  ]
}}
"""

        user_prompt = f"""We are building a widget app with:
App ID: "{app_id}"
User Instruction: "{instruction}"
"""
        if approved_plan:
            user_prompt += f"Approved Development Plan:\n{approved_plan}\n\n"

        user_prefix = user_prompt
        refinement_suffix = f"""
Here is the CURRENT schema proposal we drafted:
{json.dumps(current_proposal, indent=2, ensure_ascii=False)}

The user provided the following natural language FEEDBACK for modifications:
"{feedback}"

Apply the adjustments requested in the feedback and output the updated JSON schema proposal.
"""
        user_prompt += f"""Here is the database schema inventory:
{schemas_info}
{refinement_suffix}"""

        provider_name, model_name = selection_ids(primary_selection())
        provider = get_llm_provider(provider_name, model_name)

        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]
        selected_info = _schema_inventory(
            [schema for schema in existing_schemas if schema["id"] in selection.selected_ids],
            include_description=False,
        )
        constrained_user_prompt = (
            user_prefix
            + "Here is the selected existing Schema inventory:\n"
            + (selected_info or "(No existing entity selected)\n")
            + refinement_suffix
        )

        raw_response = ""
        try:
            proposal, raw_response = await _generate_schema_task(
                provider,
                messages,
                constrained_user_prompt,
                selection,
                catalog=catalog,
                db_session=db_session,
                budget=budget,
                started=started,
                audit_context={**(audit_context or {}), "stage": "schema_alignment_refine"},
            )
            return proposal
        except (LLMConfigError, BudgetExhaustedError):
            raise
        except Exception as e:
            logger.error(f"Failed to refine schema alignment: {e}. Raw response: {raw_response}")
            raise WorkflowError(
                "Schema capability alignment refinement failed",
                code="schema_alignment_refinement_failed",
                retryable=True,
            ) from e
