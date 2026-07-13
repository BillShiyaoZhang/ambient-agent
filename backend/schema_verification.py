import json
import logging
from typing import Any

from backend.agent.providers import get_llm_provider
from backend.schema_diff import (
    SchemaExtractor,
    VerificationDiff,
    diff_controller_js,
)

logger = logging.getLogger("schema_verification")


class SchemaVerificationService:
    """Verifies whether widget JavaScript conforms to registered graph schemas.

    The new entry point is :meth:`diff`, which returns a structured
    ``VerificationDiff``. The legacy :meth:`verify` returns the same diff
    rendered to Markdown for back-compat with the audit panel UI.
    """

    @staticmethod
    async def diff(
        app_id: str,
        widget_code: dict[str, str],
        registered_schemas: list[dict[str, Any]],
        db_session: Any = None,
    ) -> VerificationDiff:
        """Compute a deterministic diff between widget JS and registered schemas.

        Falls back to an LLM call only when regex parsing fails completely.
        """
        js_source = widget_code.get("js", "")
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
        )
        user_prompt = (
            f"Schemas:\n{schemas_info}\n\n"
            f"JavaScript:\n```js\n{js_source[:8000]}\n```"
        )

        import os

        provider_name = os.getenv("LLM_PROVIDER", "ollama")
        model_name = os.getenv("LLM_MODEL", "llama3")
        provider = get_llm_provider(provider_name, model_name)
        try:
            raw = await provider.generate(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                db_session=db_session,
            )
            cleaned = raw.strip()
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start != -1 and end != -1:
                cleaned = cleaned[start : end + 1]
            data = json.loads(cleaned)
            diff = VerificationDiff()
            for u in data.get("unknown_props", []) or []:
                from backend.schema_diff import UnknownProperty

                diff.unknown_props.append(
                    UnknownProperty(
                        node_type=u.get("node_type", ""),
                        property_name=u.get("property_name", ""),
                        sample_value_repr=u.get("sample_value_repr", ""),
                        occurrences=int(u.get("occurrences", 0) or 0),
                    )
                )
            for tm in data.get("type_mismatches", []) or []:
                from backend.schema_diff import TypeMismatch

                diff.type_mismatches.append(
                    TypeMismatch(
                        node_type=tm.get("node_type", ""),
                        property_name=tm.get("property_name", ""),
                        schema_type=tm.get("schema_type", ""),
                        observed_value_repr=tm.get("observed_value_repr", ""),
                    )
                )
            for ut in data.get("unknown_types", []) or []:
                from backend.schema_diff import UnknownType

                diff.unknown_types.append(
                    UnknownType(
                        type_name=ut.get("type_name", ""),
                        occurrences=int(ut.get("occurrences", 0) or 0),
                    )
                )
            return diff
        except Exception as e:
            logger.error(f"LLM fallback for diff also failed: {e}")
            return VerificationDiff()

    @staticmethod
    async def verify(
        app_id: str,
        widget_code: dict[str, str],
        registered_schemas: list[dict[str, Any]],
        db_session: Any = None,
    ) -> str:
        """Legacy text-report entry point. Returns Markdown."""
        try:
            diff = await SchemaVerificationService.diff(
                app_id=app_id,
                widget_code=widget_code,
                registered_schemas=registered_schemas,
                db_session=db_session,
            )
            return diff.to_markdown()
        except Exception as e:
            logger.error(f"verify() failed: {e}")
            return "⚠️ Schema Verification Failed: Could not contact verification model service."

    @staticmethod
    def extract_actions_sync(js_source: str) -> list[dict[str, Any]]:
        """Synchronous helper exposed for tests and other modules."""
        return SchemaExtractor.extract_actions(js_source)

    @staticmethod
    def extract_subscriptions_sync(js_source: str) -> list[dict[str, Any]]:
        return SchemaExtractor.extract_subscriptions(js_source)
