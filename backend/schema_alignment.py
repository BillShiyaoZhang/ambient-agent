import json
import logging
import re
from typing import Any

from backend.agent.providers import get_llm_provider
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
        system_prompt = f"""You are a Graph Database Schema Alignment Architect.
Your task is to analyze a request to build a widget application and match its data storage requirements against our existing Graph Database Schemas.

### Guidelines:
1. **Reusability First**: If the application needs to store standard concepts like tasks, to-do lists, calendar events, notes, contacts, or documents, you MUST reuse the existing core schemas (e.g. 'Task', 'Event', 'Note') rather than creating duplicate concepts (e.g., do NOT create 'TodoItem' if 'Task' is suitable, do NOT create 'WorkoutSession' if it can be represented as an 'Event' with extended properties).
2. **Property Extensions**: If you reuse an existing schema, you can propose extra custom fields under 'extended_properties'.
3. **New Schemas**: Propose new schemas only if the concept is entirely new and does not overlap with any existing schemas.
4. **Supported Data Types**: Property fields must use one of these types: "string", "integer", "number", "boolean".

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
      }}
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
      }}
    }}
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
            raw_response = await provider.generate(messages, db_session=db_session)

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

            return proposal

        except LLMConfigError:
            raise
        except Exception as e:
            logger.error(f"Failed to generate or parse schema alignment: {e}. Raw response: {raw_response}")
            # Safe fallback: assume no schemas to reuse or create, just let it proceed
            return {"reused_schemas": [], "new_schemas": []}

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
        system_prompt = f"""You are a Graph Database Schema Alignment Architect.
Your task is to refine an existing database schema proposal based on direct natural language feedback from the user.

### Guidelines:
1. Maintain existing schema selections unless the user's feedback specifically requests modifications to them.
2. Property fields must use one of these types: "string", "integer", "number", "boolean".
3. Implement exactly what the user requests in their feedback.

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
      }}
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
      }}
    }}
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
            raw_response = await provider.generate(messages, db_session=db_session)
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
            return proposal
        except LLMConfigError:
            raise
        except Exception as e:
            logger.error(f"Failed to refine schema alignment: {e}. Raw response: {raw_response}")
            return current_proposal
