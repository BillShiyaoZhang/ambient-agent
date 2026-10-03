import asyncio
import copy
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
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
from backend.agent.feature_review import review_feature_coverage
from backend.app_types import get_app_type_prompt_reference
from backend.capabilities.catalog import AgentRole, SystemCapabilityCatalog
from backend.capabilities.models import normalize_grants
from backend.graph_db import GraphDatabase
from backend.llm_config import LLMConfigError
from backend.llm_runtime import primary_selection, selection_ids

logger = logging.getLogger("schema_alignment")

_NETWORK_SOURCE_GROUNDING_GUIDANCE = (
    "**Grounded Network Scope**: Each `network.request` source must use a concrete public HTTPS origin and exact "
    "paths supported by authoritative provider documentation or user-supplied evidence. Do not infer an endpoint "
    "from the service or feature name. Keep different API hosts as separate sources even when they belong to the "
    "same provider; do not move paths between hosts or combine them under one base URL. The approved origin and "
    "paths are exact capability boundaries, not placeholders for later implementation. If reliable evidence is "
    "missing, explicitly mark it for endpoint verification and confirm the actual origin/path before requesting "
    "approval; do not describe an endpoint as verified without evidence. This guidance does not automatically verify "
    "external endpoints."
)


class RequiredFeatureReviewError(ValueError):
    """A semantic rejection cannot be retried as a Schema-selection conflict."""


class SchemaProposalValidationError(ValueError):
    """The generated proposal and its single targeted repair both failed validation."""


def _app_spec_prompt_context(language: str) -> str:
    """Render the exact App type vocabulary and authoring contract for schema generation."""
    reference = get_app_type_prompt_reference(language)
    return json.dumps(reference, ensure_ascii=False, separators=(",", ":"))


def _schema_repair_diagnostic(initial_error: Exception, repair_error: Exception) -> str:
    return (
        "Initial proposal validation failed: "
        f"{str(initial_error)[:2_000]}\n"
        "Targeted repair validation failed: "
        f"{str(repair_error)[:2_000]}"
    )


def capability_change_summary(previous: Any, proposed: Any) -> dict[str, list[dict[str, Any]]]:
    """Describe the exact grants offered for approval, including scope reductions."""
    before = {grant.id: grant.to_dict() for grant in normalize_grants(previous)}
    after = {grant.id: grant.to_dict() for grant in normalize_grants(proposed)}
    return {
        "added": [after[key] for key in sorted(after.keys() - before.keys())],
        "changed": [
            {"before": before[key], "after": after[key]}
            for key in sorted(before.keys() & after.keys())
            if before[key] != after[key]
        ],
        "removed": [before[key] for key in sorted(before.keys() - after.keys())],
    }


def existing_app_context(existing_app_manifest: dict[str, Any] | None) -> str:
    """Project public App metadata without credentials or executable instructions."""
    if existing_app_manifest is None:
        return "(New App; no previous approval baseline)"
    if not isinstance(existing_app_manifest, dict):
        raise ValueError("Existing App manifest must be an object")
    public = {
        field: existing_app_manifest[field]
        for field in ("id", "title", "description", "schema_refs", "app_spec")
        if field in existing_app_manifest
    }
    public["capabilities"] = [
        grant.to_dict() for grant in normalize_grants(existing_app_manifest.get("capabilities", []))
    ]
    return json.dumps(public, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _revision_ids(proposal: dict[str, Any], field: str, baseline_ids: set[str]) -> set[str]:
    values = proposal.get(field, [])
    if not isinstance(values, list) or len(values) > 100:
        raise ValueError(f"{field} must be an array of at most 100 capability IDs")
    if any(not isinstance(item, str) or item not in baseline_ids for item in values):
        raise ValueError(f"{field} entries must identify currently approved capability categories")
    if len(values) != len(set(values)):
        raise ValueError(f"{field} must not contain duplicate capability IDs")
    return set(values)


def _merge_grant_scopes(category_id: str, before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(before)
    for field, value in after.items():
        old = before.get(field)
        if old is None or old == value:
            merged[field] = copy.deepcopy(value)
        elif isinstance(old, list) and isinstance(value, list):
            merged[field] = sorted(set(old) | set(value))
        elif category_id == "network.request" and field == "sources":
            sources = copy.deepcopy(old)
            for source_id, source in value.items():
                if source_id not in sources:
                    sources[source_id] = copy.deepcopy(source)
                    continue
                previous = sources[source_id]
                if previous["base_url"] != source["base_url"]:
                    raise ValueError(
                        f"Changing network source '{source_id}' origin requires an explicit "
                        "network.request capability_replacements entry"
                    )
                sources[source_id] = {
                    "base_url": previous["base_url"],
                    "methods": sorted(set(previous["methods"]) | set(source["methods"])),
                    "paths": sorted(set(previous["paths"]) | set(source["paths"])),
                    "response_limit": max(previous["response_limit"], source["response_limit"]),
                }
            merged[field] = sources
        elif type(old) is int and type(value) is int:
            merged[field] = max(old, value)
        else:
            raise ValueError(f"Changing {category_id}.{field} requires an explicit capability_replacements entry")
    return merged


def preserve_existing_app_grants(
    proposal: dict[str, Any],
    existing_app_manifest: dict[str, Any] | None,
    existing_schemas: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compile a generated proposal before approval, never modify an approved payload.

    Omission in model output is not a request to revoke a prior approval. Explicit
    removal/replacement directives remain visible alongside the computed diff.
    Graph dependencies are reused only from the complete real Schema inventory.
    """
    result = copy.deepcopy(proposal)
    if not isinstance(result, dict):
        raise ValueError("Schema proposal must be a JSON object")
    result.pop("preserved_schema_refs", None)
    result.pop("preserved_capability_ids", None)
    if existing_app_manifest is None:
        _revision_ids(result, "capability_removals", set())
        _revision_ids(result, "capability_replacements", set())
        return result
    baseline = {grant.id: grant.to_dict() for grant in normalize_grants(existing_app_manifest.get("capabilities", []))}
    generated = {grant.id: grant.to_dict() for grant in normalize_grants(result.get("capabilities", []))}
    removals = _revision_ids(result, "capability_removals", set(baseline))
    replacements = _revision_ids(result, "capability_replacements", set(baseline))
    if removals & replacements:
        raise ValueError("A capability cannot be both removed and replaced")
    if removals & generated.keys():
        raise ValueError("Removed capability categories must not also appear in proposed capabilities")
    if replacements - generated.keys():
        raise ValueError("Every capability_replacements entry requires its complete replacement grant")
    merged = copy.deepcopy(generated)
    for category_id, grant in baseline.items():
        if category_id in removals or category_id in replacements:
            continue
        if category_id in merged:
            merged[category_id]["scope"] = _merge_grant_scopes(
                category_id, grant["scope"], merged[category_id]["scope"]
            )
        else:
            merged[category_id] = copy.deepcopy(grant)
    result["capabilities"] = [grant.to_dict() for grant in normalize_grants(list(merged.values()))]
    schema_entries = [*result.get("reused_schemas", []), *result.get("new_schemas", [])]
    if any(not isinstance(item, dict) for item in schema_entries):
        raise ValueError("Schema proposal entries must be objects")
    declared_ids = {item.get("id") for item in schema_entries}
    inventory = {item["id"]: item for item in existing_schemas}
    preserved_entities = {
        entity
        for category_id, grant in baseline.items()
        if category_id in {"graph.query", "graph.mutate"} and category_id not in removals
        for entity in set(grant["scope"]["entities"]) & set(merged[category_id]["scope"]["entities"])
    }
    preserved_refs: list[str] = []
    for entity_id in sorted(preserved_entities - declared_ids):
        if entity_id not in inventory:
            raise ValueError(f"Previously approved Graph entity '{entity_id}' is absent from the Schema inventory")
        result.setdefault("reused_schemas", []).append(
            {
                "id": entity_id,
                "reason": "Preserves the existing App's approved Graph capability dependency",
                "extended_properties": {},
                "data_scope": "user_context",
            }
        )
        preserved_refs.append(entity_id)
    result["capability_removals"] = sorted(removals)
    result["capability_replacements"] = sorted(replacements)
    result["capability_changes"] = capability_change_summary(list(baseline.values()), result["capabilities"])
    # Host-owned bookkeeping for the generation envelope, never approval authority.
    result["preserved_schema_refs"] = preserved_refs
    result["preserved_capability_ids"] = sorted(baseline.keys() - generated.keys() - removals)
    return result


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
    if "required_features" in proposal:
        from backend.widget_requirements import validate_feature_requirements

        proposal["required_features"] = validate_feature_requirements(
            proposal["required_features"], catalog, normalized_grants
        )
    return proposal


def _prepare_generated_proposal(
    proposal: dict[str, Any],
    existing_app_manifest: dict[str, Any] | None,
    existing_schemas: list[dict[str, Any]],
    require_feature_requirements: bool,
) -> dict[str, Any]:
    result = preserve_existing_app_grants(proposal, existing_app_manifest, existing_schemas)
    if require_feature_requirements and (
        not isinstance(result.get("required_features"), list) or not result["required_features"]
    ):
        raise ValueError("A new App proposal must declare at least one required_features acceptance criterion")
    return result


def _parse_and_validate_proposal(
    raw_response: str,
    catalog: SystemCapabilityCatalog,
    prepare_proposal: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
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
    if prepare_proposal is not None:
        proposal = prepare_proposal(proposal)
    return validate_schema_capability_proposal(proposal, catalog)


async def _generate_validated_proposal(
    provider: Any,
    messages: list[dict[str, str]],
    *,
    catalog: SystemCapabilityCatalog,
    db_session: Any,
    budget: ToolLoopBudget | None,
    audit_context: dict[str, Any],
    prepare_proposal: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    review_proposal: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
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

    async def validate(raw: str) -> dict[str, Any]:
        proposal = _parse_and_validate_proposal(raw, catalog, prepare_proposal)
        if review_proposal is not None:
            await review_proposal(proposal)
        return proposal

    raw_response = await generate(messages, audit_context)
    try:
        return await validate(raw_response), raw_response
    except (LLMConfigError, BudgetExhaustedError):
        raise
    except (ValueError, TypeError) as validation_error:
        repair_prompt = (
            "Your previous JSON failed Schema, capability, or required-feature validation.\n"
            f"Validation diagnostic: {str(validation_error)[:2_000]}\n"
            f"{_NETWORK_SOURCE_GROUNDING_GUIDANCE}\n"
            "For semantic coverage feedback, address each actionable feedback item separately. Preserve every still-correct "
            "required feature ID, behavior and approved dependency; add or revise separate feature rows to cover each "
            "uncovered goal instead of replacing the list with one broad summary or merging unrelated feedback. "
            "Keep every network source ID and path exactly within the declared capability grants.\n"
            "Return the complete corrected JSON object only. Preserve the requested schemas and least-privilege intent. "
            "Declare the precise capabilities needed for the requested behavior before approval; do not add unrelated "
            "permissions, invent placeholders, silently downgrade required functionality, or omit required nested fields."
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
        try:
            return await validate(repaired_response), repaired_response
        except (LLMConfigError, BudgetExhaustedError):
            raise
        except (ValueError, TypeError) as repair_error:
            raise SchemaProposalValidationError(
                _schema_repair_diagnostic(validation_error, repair_error)
            ) from repair_error


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
    prepare_proposal: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    review_proposal: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
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
            prepare_proposal=prepare_proposal,
            review_proposal=review_proposal,
        )
        remaining_budget(budget, started)
        envelope_proposal = copy.deepcopy(proposal)
        preserved_refs = set(proposal.get("preserved_schema_refs", [])) - set(selection.selected_ids)
        envelope_proposal["reused_schemas"] = [
            item for item in proposal.get("reused_schemas", []) if item["id"] not in preserved_refs
        ]
        if selection.disposition == "NO_GRAPH_DATA":
            envelope_proposal["capabilities"] = [
                grant
                for grant in proposal.get("capabilities", [])
                if grant["id"] not in proposal.get("preserved_capability_ids", [])
            ]
        task.validate(envelope_proposal)
    except (LLMConfigError, BudgetExhaustedError, RequiredFeatureReviewError, SchemaProposalValidationError):
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
            prepare_proposal=prepare_proposal,
            review_proposal=review_proposal,
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
        existing_app_manifest: dict[str, Any] | None = None,
        require_feature_requirements: bool = False,
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
        app_type_reference = _app_spec_prompt_context(language)
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
{_NETWORK_SOURCE_GROUNDING_GUIDANCE}
8. **Existing App Baseline**: A modification preserves currently approved grants and their required Graph schemas by default. List exact existing capability category IDs in `capability_removals` only when the user requests revocation, or in `capability_replacements` to deliberately replace the complete scope (including removing a network source). Omitting grants, sources, paths, or methods does not revoke them. Every change will be displayed for approval; never treat catalog availability as approval.
9. **Required Features**: Declare every mandatory requested behavior in `required_features`, linked to its exact App `app_spec.features[].id`. Each row contains `id`, `description`, `capability_ids`, and `network_sources` (objects containing `source_id` and `path`). Include the real grants and exact approved network sources/paths needed for those behaviors. A required live feature cannot be replaced by unavailable/error labels. Explain infeasibility through validation/refinement when the runtime lacks the SDK capability, rather than silently removing the requested behavior. Static features can use empty capability/source arrays; an empty required_features array is appropriate only when no functional behavior was requested.
10. **Required Feature IDs**: Use standard feature IDs from this catalog or custom IDs matching `custom:<lowercase-kebab-case-namespace>.<lowercase-kebab-case-feature>`. These IDs are the approved behavior criteria that the later App implementation must declare exactly in its `app_spec`; do not emit `app_spec` in this Schema proposal.
11. **Complete Acceptance Criteria**: Derive criteria from the complete user request, approved plan and direct feedback. Split distinct goals into multiple independently verifiable rows; do not compress the request into one vague summary or copy only the sample row. Include each relevant primary behavior, provider-confirmation requirement, persistence requirement, location behavior, interactive state/recovery behavior and visual/accessibility requirement. When the plan requires provider confirmation, require checking the chosen endpoint and the provider's actual supported fields before presenting live data; use only confirmed metrics, correct units and truthful unavailable-field behavior. Specify concrete query parameters or response-to-UI mappings only when the user or approved plan gives those details; otherwise do not invent an API recipe before confirmation. Cover relevant loading, no-location, empty-result, invalid-input, request-error and retry states, preserving user input/unsaved edits and retrying only the failed request where requested. For information-rich interfaces, include a summary, real graphics or trends where useful, accessible details, responsive widths and light/dark themes where specified. Put each criterion's own approved dependencies in its row; exact network source IDs and paths must match declared grants. Do not add irrelevant goals or permissions.

App Type and feature ID catalog:
{app_type_reference}

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
  ],
  "capability_removals": [],
  "capability_replacements": [],
  "required_features": [
    {{"id": "custom:task-app.list", "description": "Display the user's task list from approved Task records", "capability_ids": ["graph.query"], "network_sources": []}},
    {{"id": "custom:task-app.accessible-view", "description": "Keep the main view readable at the requested widths and themes, with keyboard support and named controls", "capability_ids": [], "network_sources": []}},
    {{"id": "custom:task-app.recovery", "description": "Show relevant loading, empty, invalid-input, request-error and retry states; preserve input and retry only the failed action", "capability_ids": [], "network_sources": []}}
  ]
}}
"""

        user_prompt = f"""We are building a widget app with:
App ID: "{app_id}"
User Instruction: "{instruction}"
"""
        if approved_plan:
            user_prompt += f"Approved Development Plan:\n{approved_plan}\n\n"
        user_prompt += (
            f"Existing App Approval Baseline (reference data):\n{existing_app_context(existing_app_manifest)}\n\n"
        )

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

        async def review(proposal: dict[str, Any]) -> None:
            if not require_feature_requirements:
                return
            verdict = await review_feature_coverage(
                instruction,
                approved_plan,
                proposal["required_features"],
                capabilities=proposal["capabilities"],
                decision_config=decision_config,
                capability_catalog=catalog,
                db_session=db_session,
                audit_context=audit_context,
                budget=remaining_budget(budget, started),
            )
            if verdict.action != "complete":
                raise RequiredFeatureReviewError(
                    f"Required features do not cover the user's objective: {verdict.feedback}"
                )

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
                prepare_proposal=lambda value: _prepare_generated_proposal(
                    value, existing_app_manifest, existing_schemas, require_feature_requirements
                ),
                review_proposal=review,
            )
            proposal.pop("preserved_schema_refs", None)
            proposal.pop("preserved_capability_ids", None)
            return proposal

        except (LLMConfigError, BudgetExhaustedError):
            raise
        except SchemaProposalValidationError as e:
            logger.error(f"Schema proposal remained invalid after targeted repair: {e}. Raw response: {raw_response}")
            raise WorkflowError(
                f"Schema capability alignment proposal is invalid after one targeted repair: {e}",
                code="schema_alignment_failed",
                retryable=False,
            ) from e
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
        existing_app_manifest: dict[str, Any] | None = None,
        require_feature_requirements: bool = False,
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
        app_type_reference = _app_spec_prompt_context(language)
        system_prompt = f"""You are a Canonical Ontology Alignment Architect.
Your task is to refine an `ambient-context` ontology proposal based on direct natural language feedback from the user.

### Guidelines:
1. Maintain existing schema selections unless the user's feedback specifically requests modifications to them.
2. Property fields must use one of these types: "string", "integer", "number", "boolean".
3. Implement exactly what the user requests in their feedback.
4. Keep all entities in the single canonical ontology and preserve `subclass_of`/`equivalent_to` alignments.
5. Never model App-only runtime data; caches, cursors, credentials, UI state, checkpoints, and raw provider payloads stay in the App directory.
6. Refine capability grants from the supplied Capability Ontology with least privilege and follow every complete `scope_contract`. Do not invent category ids, scope fields, installed catalog ids, or installed actions. Declare full public HTTPS source objects for `network.request`.
{_NETWORK_SOURCE_GROUNDING_GUIDANCE}
7. Preserve the existing App's approved grants unless user feedback requests revocation. Use `capability_removals` for exact existing category IDs being revoked, or `capability_replacements` for complete deliberate scope replacement. Omissions are preserved before approval, and the exact resulting change summary is reviewable.
8. Preserve mandatory requested behaviors in `required_features` rows containing `id`, `description`, `capability_ids`, and `network_sources` with `source_id`/`path`. These feature IDs link to App app_spec feature declarations. Supply their actual required grants and exact sources/paths. A missing SDK or permission must cause correction/refinement rather than substituting unavailable labels for the required behavior. Empty dependency arrays support static features; empty required_features is only appropriate if no behavior was requested.
9. **Required Feature IDs**: Use standard feature IDs from this catalog or custom IDs matching `custom:<lowercase-kebab-case-namespace>.<lowercase-kebab-case-feature>`. These IDs are the approved behavior criteria that the later App implementation must declare exactly in its `app_spec`; do not emit `app_spec` in this Schema proposal.
10. **Complete Acceptance Criteria**: Derive criteria from the complete user request, approved plan and direct feedback. Split distinct goals into multiple independently verifiable rows; do not compress the request into one vague summary or copy only the sample row. Include each relevant primary behavior, provider-confirmation requirement, persistence requirement, location behavior, interactive state/recovery behavior and visual/accessibility requirement. When the plan requires provider confirmation, require checking the chosen endpoint and the provider's actual supported fields before presenting live data; use only confirmed metrics, correct units and truthful unavailable-field behavior. Specify concrete query parameters or response-to-UI mappings only when the user or approved plan gives those details; otherwise do not invent an API recipe before confirmation. Cover relevant loading, no-location, empty-result, invalid-input, request-error and retry states, preserving user input/unsaved edits and retrying only the failed request where requested. For information-rich interfaces, include a summary, real graphics or trends where useful, accessible details, responsive widths and light/dark themes where specified. Put each criterion's own approved dependencies in its row; exact network source IDs and paths must match declared grants. Do not add irrelevant goals or permissions.

App Type and feature ID catalog:
{app_type_reference}

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
  ],
  "capability_removals": [],
  "capability_replacements": [],
  "required_features": [
    {{"id": "custom:task-app.list", "description": "Display the user's task list from approved Task records", "capability_ids": ["graph.query"], "network_sources": []}},
    {{"id": "custom:task-app.accessible-view", "description": "Keep the main view readable at the requested widths and themes, with keyboard support and named controls", "capability_ids": [], "network_sources": []}},
    {{"id": "custom:task-app.recovery", "description": "Show relevant loading, empty, invalid-input, request-error and retry states; preserve input and retry only the failed action", "capability_ids": [], "network_sources": []}}
  ]
}}
"""

        user_prompt = f"""We are building a widget app with:
App ID: "{app_id}"
User Instruction: "{instruction}"
"""
        if approved_plan:
            user_prompt += f"Approved Development Plan:\n{approved_plan}\n\n"
        user_prompt += (
            f"Existing App Approval Baseline (reference data):\n{existing_app_context(existing_app_manifest)}\n\n"
        )

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

        async def review(proposal: dict[str, Any]) -> None:
            if not require_feature_requirements:
                return
            verdict = await review_feature_coverage(
                instruction,
                approved_plan,
                proposal["required_features"],
                capabilities=proposal["capabilities"],
                feedback=feedback,
                decision_config=decision_config,
                capability_catalog=catalog,
                db_session=db_session,
                audit_context=audit_context,
                budget=remaining_budget(budget, started),
            )
            if verdict.action != "complete":
                raise RequiredFeatureReviewError(
                    f"Required features do not cover the user's objective: {verdict.feedback}"
                )

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
                prepare_proposal=lambda value: _prepare_generated_proposal(
                    value, existing_app_manifest, existing_schemas, require_feature_requirements
                ),
                review_proposal=review,
            )
            proposal.pop("preserved_schema_refs", None)
            proposal.pop("preserved_capability_ids", None)
            return proposal
        except (LLMConfigError, BudgetExhaustedError):
            raise
        except SchemaProposalValidationError as e:
            logger.error(f"Schema refinement remained invalid after targeted repair: {e}. Raw response: {raw_response}")
            raise WorkflowError(
                f"Schema capability alignment refinement is invalid after one targeted repair: {e}",
                code="schema_alignment_refinement_failed",
                retryable=False,
            ) from e
        except Exception as e:
            logger.error(f"Failed to refine schema alignment: {e}. Raw response: {raw_response}")
            raise WorkflowError(
                "Schema capability alignment refinement failed",
                code="schema_alignment_refinement_failed",
                retryable=True,
            ) from e
