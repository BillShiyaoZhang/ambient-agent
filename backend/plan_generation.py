import logging
import os
from typing import Any

from backend.agent.providers import get_llm_provider

logger = logging.getLogger("plan_generation")


class PlanGenerationService:
    @staticmethod
    async def generate_plan(instruction: str, app_id: str, schemas_context: str, db_session: Any = None) -> str:
        """
        Generates a high-level summary implementation plan describing the widget and its UI elements.
        """
        system_prompt = """You are an Ambient Agent Development Architect.
Your task is to generate a concise, high-level implementation plan for a new or modified widget.
The plan must only cover:
1. The main purpose of the widget.
2. The key user interface elements/features that will be added or modified.

Keep the plan extremely short, clear, and direct. Do NOT include code, files, or technical configuration steps."""

        user_prompt = f"""We are designing/modifying a widget app:
App ID: "{app_id}"
User Instruction: "{instruction}"
Database Schema Context:
{schemas_context if schemas_context else "(No custom database schemas required)"}

Please write a brief implementation plan for this widget."""

        provider_name = os.getenv("LLM_PROVIDER", "ollama")
        model_name = os.getenv("LLM_MODEL", "llama3")
        provider = get_llm_provider(provider_name, model_name)

        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]

        try:
            raw_response = await provider.generate(messages, db_session=db_session)
            return raw_response.strip()
        except Exception as e:
            logger.error(f"Failed to generate implementation plan: {e}")
            return (
                f"Implementation Plan for '{app_id}': Build widget UI conforming to the instruction: '{instruction}'."
            )

    @staticmethod
    async def refine_plan(
        instruction: str, app_id: str, schemas_context: str, current_plan: str, feedback: str, db_session: Any = None
    ) -> str:
        """
        Refines the current plan using direct natural language feedback from the user.
        """
        system_prompt = """You are an Ambient Agent Development Architect.
Your task is to refine the implementation plan based on direct feedback from the user.
Keep it a concise, high-level plan covering the widget's purpose and UI elements. Do NOT write source code."""

        user_prompt = f"""We are building/modifying a widget app:
App ID: "{app_id}"
Original Instruction: "{instruction}"
Database Schema Context:
{schemas_context if schemas_context else "(No custom database schemas required)"}

Current Implementation Plan:
{current_plan}

User Feedback:
"{feedback}"

Update and output the refined implementation plan."""

        provider_name = os.getenv("LLM_PROVIDER", "ollama")
        model_name = os.getenv("LLM_MODEL", "llama3")
        provider = get_llm_provider(provider_name, model_name)

        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]

        try:
            raw_response = await provider.generate(messages, db_session=db_session)
            return raw_response.strip()
        except Exception as e:
            logger.error(f"Failed to refine implementation plan: {e}")
            return current_plan
