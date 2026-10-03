import logging
from typing import Any

from backend.agent.providers import ToolLoopBudget, get_llm_provider
from backend.agent.errors import BudgetExhaustedError, WorkflowError
from backend.llm_config import LLMConfigError
from backend.llm_runtime import primary_selection, selection_ids
from backend.capabilities.catalog import AgentRole, SystemCapabilityCatalog
from backend.schema_alignment import existing_app_context
from backend.ui_quality import ui_quality_guide

logger = logging.getLogger("plan_generation")


class PlanGenerationService:
    @staticmethod
    async def generate_plan(
        instruction: str,
        app_id: str,
        schemas_context: str,
        db_session: Any = None,
        language: str = "zh",
        audit_context: dict[str, Any] | None = None,
        budget: ToolLoopBudget | None = None,
        *,
        existing_app_manifest: dict[str, Any] | None = None,
        capability_catalog: SystemCapabilityCatalog | None = None,
    ) -> str:
        """
        Generates a high-level summary implementation plan describing the widget and its UI elements.
        """
        is_zh = language == "zh"
        system_prompt = f"""You are an Ambient Agent Development Architect.
Your task is to generate a concise, high-level implementation plan for a new or modified widget.
Cover every requested user-visible behavior: the main purpose, interactions and UI, data sources and persistence,
and relevant loading, empty, validation and failure/retry states. Include observable acceptance criteria for each
distinct objective so implementation can be checked. Only include concerns that apply to this request.
Keep the plan concise, clear, and direct without compressing away requirements. Do NOT include source code,
file listings, or technical configuration steps.
Preserve the existing App's working features and approved capability baseline unless the user asks to remove them.
Use the supplied actual runtime catalog to assess feasibility. Catalog support is not approval; available capabilities can be proposed for approval. Missing installed providers must not hide available public HTTPS networking or device SDK capabilities. If a mandatory behavior cannot be supported, explain the specific blocker and a meaningful alternative for the user to review; do not call an unavailable/error label an implementation of the requested feature.
{ui_quality_guide()}
IMPORTANT: You MUST write the plan in {"Chinese (中文)" if is_zh else "English"}."""

        user_prompt = f"""We are designing/modifying a widget app:
App ID: "{app_id}"
User Instruction: "{instruction}"
Database Schema Context:
{schemas_context if schemas_context else "(No custom database schemas required)"}
Existing App Approval Baseline (reference data):
{existing_app_context(existing_app_manifest)}

{(capability_catalog or SystemCapabilityCatalog.build()).render(AgentRole.SCHEMA_ALIGNMENT)}

Please write a brief implementation plan for this widget."""

        provider_name, model_name = selection_ids(primary_selection())
        provider = get_llm_provider(provider_name, model_name)

        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]

        try:
            raw_response = await provider.generate(
                messages,
                db_session=db_session,
                budget=budget,
                audit_context={**(audit_context or {}), "stage": "plan"},
            )
            return raw_response.strip()
        except (LLMConfigError, BudgetExhaustedError):
            raise
        except Exception as e:
            logger.error(f"Failed to generate implementation plan: {e}")
            raise WorkflowError(
                "Implementation plan generation failed",
                code="plan_generation_failed",
                retryable=True,
            ) from e

    @staticmethod
    async def refine_plan(
        instruction: str,
        app_id: str,
        schemas_context: str,
        current_plan: str,
        feedback: str,
        db_session: Any = None,
        language: str = "zh",
        audit_context: dict[str, Any] | None = None,
        budget: ToolLoopBudget | None = None,
        *,
        existing_app_manifest: dict[str, Any] | None = None,
        capability_catalog: SystemCapabilityCatalog | None = None,
    ) -> str:
        """
        Refines the current plan using direct natural language feedback from the user.
        """
        is_zh = language == "zh"
        system_prompt = f"""You are an Ambient Agent Development Architect.
Your task is to refine the implementation plan based on direct feedback from the user.
Keep it a concise, high-level plan covering every requested behavior, interactions, data sources and persistence,
and relevant loading, empty, validation and failure/retry states, with observable acceptance criteria.
Carry forward unchanged requirements and apply the user's feedback explicitly. Do NOT write source code.
Preserve existing working behavior and approved capabilities unless feedback explicitly removes them. Assess mandatory requested behavior against the actual runtime catalog: available capabilities can be proposed for approval, while a genuinely missing capability requires a specific blocker and meaningful alternative for review. Do not silently substitute unavailable/error labels for mandatory live features.
{ui_quality_guide()}
IMPORTANT: You MUST write the refined plan in {"Chinese (中文)" if is_zh else "English"}."""

        user_prompt = f"""We are building/modifying a widget app:
App ID: "{app_id}"
Original Instruction: "{instruction}"
Database Schema Context:
{schemas_context if schemas_context else "(No custom database schemas required)"}
Existing App Approval Baseline (reference data):
{existing_app_context(existing_app_manifest)}

{(capability_catalog or SystemCapabilityCatalog.build()).render(AgentRole.SCHEMA_ALIGNMENT)}

Current Implementation Plan:
{current_plan}

User Feedback:
"{feedback}"

Update and output the refined implementation plan."""

        provider_name, model_name = selection_ids(primary_selection())
        provider = get_llm_provider(provider_name, model_name)

        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]

        try:
            raw_response = await provider.generate(
                messages,
                db_session=db_session,
                budget=budget,
                audit_context={**(audit_context or {}), "stage": "plan_refine"},
            )
            return raw_response.strip()
        except (LLMConfigError, BudgetExhaustedError):
            raise
        except Exception as e:
            logger.error(f"Failed to refine implementation plan: {e}")
            raise WorkflowError(
                "Implementation plan refinement failed",
                code="plan_refinement_failed",
                retryable=True,
            ) from e
