import json
import logging
import re
from typing import Any

from backend.agent.providers import ToolLoopBudget, get_llm_provider
from backend.agent.errors import BudgetExhaustedError, WorkflowError
from backend.capabilities.catalog import AgentRole, SystemCapabilityCatalog
from backend.capabilities.models import normalize_grants
from backend.graph_db import GraphDatabase
from backend.llm_config import LLMConfigError
from backend.llm_runtime import primary_selection, selection_ids

logger = logging.getLogger("schema_alignment")


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
    ) -> dict[str, Any]:
        """
        Interacts with the LLM to perform semantic schema alignment.
        Analyzes the app instruction against existing database schemas and returns a proposal dict.
        """
        # 1. Retrieve current schema inventory
        existing_schemas = db.list_schemas()

        # Format inventory as string for prompt
        schemas_info = ""
        for schema in existing_schemas:
            schemas_info += f"- Schema ID: '{schema['id']}'\n"
            schemas_info += f"  Name: {schema['name']}\n"
            schemas_info += f"  Description: {schema['description']}\n"
            schemas_info += f"  Properties: {json.dumps(schema['properties'])}\n\n"

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
7. **Capability Ontology**: Propose the smallest required Widget grants from the supplied Capability Ontology. Do not invent category ids, scope fields, sources, entity types, catalog ids, or actions. An empty array is valid.

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

        user_prompt += f"""Here is the inventory of our existing database schemas:
{schemas_info if schemas_info else "(No existing schemas)"}

Propose the optimal schema alignment plan for this widget as a JSON block.
"""

        # 2. Call LLM
        provider_name, model_name = selection_ids(primary_selection())
        provider = get_llm_provider(provider_name, model_name)

        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]

        raw_response = ""
        try:
            raw_response = await provider.generate(
                messages,
                db_session=db_session,
                budget=budget,
                audit_context={**(audit_context or {}), "stage": "schema_alignment"},
            )

            # Clean response and parse JSON
            cleaned = raw_response.strip()
            # If wrapped in codeblock, extract it
            code_block_match = re.search(r"```json\s*(.*?)\s*```", cleaned, re.DOTALL)
            if code_block_match:
                cleaned = code_block_match.group(1).strip()
            else:
                # If wrapped in simple code block
                code_block_match2 = re.search(r"```\s*(.*?)\s*```", cleaned, re.DOTALL)
                if code_block_match2:
                    cleaned = code_block_match2.group(1).strip()

            # Find the first '{' and last '}'
            start_idx = cleaned.find("{")
            end_idx = cleaned.rfind("}")
            if start_idx != -1 and end_idx != -1:
                cleaned = cleaned[start_idx : end_idx + 1]

            proposal = json.loads(cleaned)

            # Validate format integrity
            if "reused_schemas" not in proposal:
                proposal["reused_schemas"] = []
            if "new_schemas" not in proposal:
                proposal["new_schemas"] = []
            normalized_grants = normalize_grants(proposal.get("capabilities", []))
            graph_entity_ids = {
                str(item.get("id"))
                for item in [*proposal["reused_schemas"], *proposal["new_schemas"]]
                if isinstance(item, dict) and item.get("id")
            }
            catalog.validate_grants(normalized_grants, graph_entity_ids=graph_entity_ids)
            proposal["capabilities"] = [grant.to_dict() for grant in normalized_grants]

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
    ) -> dict[str, Any]:
        """
        Refines the current schema proposal using natural language feedback from the user.
        """
        existing_schemas = db.list_schemas()
        schemas_info = ""
        for schema in existing_schemas:
            schemas_info += f"- Schema ID: '{schema['id']}'\n"
            schemas_info += f"  Properties: {json.dumps(schema['properties'])}\n\n"

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
6. Refine capability grants from the supplied Capability Ontology with least privilege. Do not invent category ids or scope fields.

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

        user_prompt += f"""Here is the database schema inventory:
{schemas_info}

Here is the CURRENT schema proposal we drafted:
{json.dumps(current_proposal, indent=2, ensure_ascii=False)}

The user provided the following natural language FEEDBACK for modifications:
"{feedback}"

Apply the adjustments requested in the feedback and output the updated JSON schema proposal.
"""

        provider_name, model_name = selection_ids(primary_selection())
        provider = get_llm_provider(provider_name, model_name)

        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]

        raw_response = ""
        try:
            raw_response = await provider.generate(
                messages,
                db_session=db_session,
                budget=budget,
                audit_context={**(audit_context or {}), "stage": "schema_alignment_refine"},
            )
            cleaned = raw_response.strip()
            code_block_match = re.search(r"```json\s*(.*?)\s*```", cleaned, re.DOTALL)
            if code_block_match:
                cleaned = code_block_match.group(1).strip()
            else:
                code_block_match2 = re.search(r"```\s*(.*?)\s*```", cleaned, re.DOTALL)
                if code_block_match2:
                    cleaned = code_block_match2.group(1).strip()
            start_idx = cleaned.find("{")
            end_idx = cleaned.rfind("}")
            if start_idx != -1 and end_idx != -1:
                cleaned = cleaned[start_idx : end_idx + 1]

            proposal = json.loads(cleaned)
            if "reused_schemas" not in proposal:
                proposal["reused_schemas"] = []
            if "new_schemas" not in proposal:
                proposal["new_schemas"] = []
            normalized_grants = normalize_grants(proposal.get("capabilities", []))
            graph_entity_ids = {
                str(item.get("id"))
                for item in [*proposal["reused_schemas"], *proposal["new_schemas"]]
                if isinstance(item, dict) and item.get("id")
            }
            catalog.validate_grants(normalized_grants, graph_entity_ids=graph_entity_ids)
            proposal["capabilities"] = [grant.to_dict() for grant in normalized_grants]
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
