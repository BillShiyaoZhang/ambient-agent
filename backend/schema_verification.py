import os
import logging
import json
from typing import Dict, Any, List
from backend.agent.providers import get_llm_provider

logger = logging.getLogger("schema_verification")

class SchemaVerificationService:
    @staticmethod
    async def verify(
        app_id: str,
        widget_code: Dict[str, str],
        registered_schemas: List[Dict[str, Any]],
        db_session: Any = None
    ) -> str:
        """
        Uses LLM to verify if database mutations and subscriptions in widget code match registered schemas.
        """
        schemas_info = ""
        for s in registered_schemas:
            schemas_info += f"- Schema ID: '{s['id']}'\n"
            schemas_info += f"  Properties: {json.dumps(s.get('properties', {}))}\n\n"

        system_prompt = """You are a Graph Database Schema Verification Architect.
Your task is to analyze a widget's controller JavaScript code and verify if its graph database calls (subscriptions and mutations) conform to our registered Graph Database Schemas.

### Database Interaction APIs to inspect in the code:
1. `ambient.graph.subscribe(query, callback)`:
   - Check the query object (e.g. `type: "Task"`).
   - Ensure the entity type exists in the registered schemas.
   - Verify if any property filters query fields matching the schema properties and their types (e.g. status must match schema).
2. `ambient.graph.mutate(actions_array)`:
   - Inspect actions like `create_node`, `update_node_property`, etc.
   - Check if the entity `type` exists in registered schemas.
   - Check if written properties conform to registered schema keys and value types (e.g., if schema has status: "string", verify it is not written as boolean/integer/etc).

### Output Format:
Please output a clean Markdown validation report containing:
- **Verification Status**: Start with "✅ PASSED" if there are no mismatches, or "⚠️ WARNING" / "❌ DISCREPANCY DETECTED" if there are field name, type, or entity name mismatches.
- **Detailed Checks**:
  - List detected subscription types and mutations.
  - Assert if they align with the registered schemas.
- **Recommendations**: If there are mismatch issues, provide specific, clear action items/recommendations for fixing the code.

Keep the report concise, professional, and easy to read."""

        user_prompt = f"""We developed a widget app:
App ID: "{app_id}"

Here is the JavaScript controller code (`controller.js`):
```javascript
{widget_code.get("js", "")}
```

Here are the registered schemas:
{schemas_info if schemas_info else "(No registered schemas)"}

Please perform the validation and output the Markdown report."""

        provider_name = os.getenv("LLM_PROVIDER", "ollama")
        model_name = os.getenv("LLM_MODEL", "llama3")
        provider = get_llm_provider(provider_name, model_name)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        try:
            raw_response = await provider.generate(messages, db_session=db_session)
            return raw_response.strip()
        except Exception as e:
            logger.error(f"Failed to verify schema alignment: {e}")
            return "⚠️ Schema Verification Failed: Could not contact verification model service."
