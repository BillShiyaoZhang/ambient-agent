import json
import logging
from typing import Any

from backend.agent.providers import ToolLoopBudget, get_llm_provider
from backend.agent.errors import BudgetExhaustedError, VerificationError
from backend.capabilities.catalog import AgentRole, SystemCapabilityCatalog
from backend.llm_config import LLMConfigError
from backend.llm_runtime import primary_selection, selection_ids
from backend.schema_diff import (
    SchemaExtractor,
    TypeMismatch,
    UnknownProperty,
    UnknownType,
    VerificationDiff,
    diff_controller_js,
)

logger = logging.getLogger("schema_verification")


def _model_diff(data: Any) -> VerificationDiff:
    """Validate the fallback's complete findings before deriving ``is_clean``."""
    fields = {"unknown_props", "type_mismatches", "unknown_types"}
    if not isinstance(data, dict) or set(data) != fields:
        raise ValueError("Schema verification response must contain all three finding lists")

    def findings(name: str, text_fields: set[str], *, occurrences: bool = False) -> list[dict[str, Any]]:
        items = data[name]
        if not isinstance(items, list):
            raise ValueError(f"Schema verification {name} must be a list")
        expected_fields = text_fields | ({"occurrences"} if occurrences else set())
        for item in items:
            if not isinstance(item, dict) or set(item) != expected_fields:
                raise ValueError(f"Schema verification {name} has a malformed finding")
            if any(not isinstance(item[field], str) for field in text_fields):
                raise ValueError(f"Schema verification {name} has a non-text field")
            identity_fields = text_fields & {"node_type", "property_name", "type_name", "schema_type"}
            if any(not item[field].strip() for field in identity_fields):
                raise ValueError(f"Schema verification {name} has an empty identity")
            if occurrences and (
                not isinstance(item["occurrences"], int)
                or isinstance(item["occurrences"], bool)
                or item["occurrences"] < 0
            ):
                raise ValueError(f"Schema verification {name} has an invalid occurrence count")
        return items

    unknown_props = [
        UnknownProperty(**item)
        for item in findings("unknown_props", {"node_type", "property_name", "sample_value_repr"}, occurrences=True)
    ]
    type_mismatches = [
        TypeMismatch(**item)
        for item in findings("type_mismatches", {"node_type", "property_name", "schema_type", "observed_value_repr"})
    ]
    unknown_types = [UnknownType(**item) for item in findings("unknown_types", {"type_name"}, occurrences=True)]
    # VerificationDiff keeps is_clean as a dataclass field for the UML contract.
    # Its __post_init__ must see the populated lists rather than an empty diff.
    return VerificationDiff(
        unknown_props=unknown_props,
        type_mismatches=type_mismatches,
        unknown_types=unknown_types,
    )


class SchemaVerificationService:
    """Return a structured diff between Widget code and approved Graph schemas."""

    @staticmethod
    async def diff(
        app_id: str,
        widget_code: dict[str, str],
        registered_schemas: list[dict[str, Any]],
        db_session: Any = None,
        audit_context: dict[str, Any] | None = None,
        budget: ToolLoopBudget | None = None,
        capability_catalog: SystemCapabilityCatalog | None = None,
    ) -> VerificationDiff:
        """Compute a deterministic diff between widget JS and registered schemas.

        Falls back to an LLM call only when regex parsing fails completely.
        """
        if not isinstance(widget_code, dict):
            raise VerificationError("Widget artifact is missing or malformed")
        js_source = widget_code.get("js", "")
        if not isinstance(js_source, str) or not js_source.strip():
            raise VerificationError("Widget artifact has no controller JavaScript to verify")
        try:
            return diff_controller_js(js_source, registered_schemas)
        except Exception as e:
            logger.warning(f"Deterministic diff failed for {app_id}: {e}; falling back to LLM")

        # LLM fallback: ask the model to enumerate the unknown props by reading
        # the source. Bounded to a single call.

        schemas_info = ""
        for s in registered_schemas:
            schemas_info += f"- Schema ID: '{s['id']}'\n"
            schemas_info += f"  Properties: {json.dumps(s.get('properties', {}))}\n\n"

        system_prompt = (
            "You are a JSON extractor. Read the JavaScript code and the schema "
            "list. Output ONLY a JSON object with this exact shape:\n"
            '{"unknown_props": [{"node_type": str, "property_name": str, '
            '"sample_value_repr": str, "occurrences": int}], '
            '"type_mismatches": [{"node_type": str, "property_name": str, '
            '"schema_type": str, "observed_value_repr": str}], '
            '"unknown_types": [{"type_name": str, "occurrences": int}]}\n'
            "No other text. No markdown fences."
            "\n\n" + (capability_catalog or SystemCapabilityCatalog.build()).render(AgentRole.VERIFICATION)
        )
        user_prompt = f"Schemas:\n{schemas_info}\n\nJavaScript:\n```js\n{js_source[:8000]}\n```"

        provider_name, model_name = selection_ids(primary_selection())
        provider = get_llm_provider(provider_name, model_name)
        try:
            raw = await provider.generate(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                db_session=db_session,
                budget=budget,
                audit_context={**(audit_context or {}), "stage": "schema_verification"},
            )
            cleaned = raw.strip()
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start != -1 and end != -1:
                cleaned = cleaned[start : end + 1]
            data = json.loads(cleaned)
            return _model_diff(data)
        except (LLMConfigError, BudgetExhaustedError):
            raise
        except Exception as e:
            logger.error(f"LLM fallback for diff also failed: {e}")
            raise VerificationError("Both deterministic and model schema verification failed", retryable=True) from e

    @staticmethod
    def extract_actions_sync(js_source: str) -> list[dict[str, Any]]:
        """Synchronous helper exposed for tests and other modules."""
        return SchemaExtractor.extract_actions(js_source)

    @staticmethod
    def extract_subscriptions_sync(js_source: str) -> list[dict[str, Any]]:
        return SchemaExtractor.extract_subscriptions(js_source)
