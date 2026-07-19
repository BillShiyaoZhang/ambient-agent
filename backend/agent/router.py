"""Intent routing for Ambient Agent.

Provides ``IntentRouter.route(content, context)`` returning a structured
``IntentPlan``. Two-layer LLM:

1. ``route()`` calls LLM #1 with the ``classify_intent`` function-calling
   schema to obtain the top-level ``kind`` (and ``sub_intents[]`` when
   ``kind == MULTI_INTENT``).
2. For ``MULTI_INTENT`` and ``PLAN_AND_ACT`` plans, the harness may call
   ``refine_sub_intents()`` (LLM #2) which specialises sub-intents into
   concrete actions, schema extensions, etc., based on the latest graph and
   widget context.

Falls back to a regex-based triage when the LLM is unreachable or returns no
tool call.
"""

import asyncio
import hashlib
import json
import logging
import re
import time
from typing import Any

from sqlmodel import Session

from backend.agent.intent_plan import (
    IntentKind,
    IntentPlan,
)
from backend.agent.errors import BudgetExhaustedError
from backend.agent.providers import ToolLoopBudget
from backend.agent.prompts.manager import PromptManager
from backend.llm_service import call_llm_api
from backend.llm_config import LLMConfigError
from backend.llm_runtime import fast_selection, primary_selection, selection_ids
from backend.router_context import RouterContext

logger = logging.getLogger("agent.router")

_SLASH_APP_PATTERN = re.compile(r"^/app\s+([a-zA-Z0-9_-]+)(?:\s+(.*))?$", re.IGNORECASE)


def _default_context_sections() -> list[str]:
    return ["widgets", "graph_counts", "history"]


class IntentRouter:
    """Routes a user message into an IntentPlan."""

    @classmethod
    async def route(
        cls,
        content: str,
        context: RouterContext | list[dict[str, Any]] | None = None,
        db_session: Session | None = None,
        provider_name: str | None = None,
        model_name: str | None = None,
        language: str = "zh",
        *,
        override_system_prompt: str | None = None,
        context_sections: list[str] | None = None,
        include_widget_keyword_hint: bool = False,
        fallback_keywords: list[str] | None = None,
        audit_context: dict[str, Any] | None = None,
        budget: ToolLoopBudget | None = None,
    ) -> IntentPlan:
        """Classify a user message.

        Two-layer LLM: this call returns the top-level IntentPlan; if the
        plan kind is ``MULTI_INTENT`` or ``PLAN_AND_ACT``, the harness may
        additionally call :meth:`refine_sub_intents` to specialise the
        ``sub_intents`` into concrete actions.
        """
        content_stripped = (content or "").strip()

        # 1. Fast-path: explicit /app command wins over LLM classification.
        m = _SLASH_APP_PATTERN.match(content_stripped)
        if m:
            app_id = m.group(1).strip()
            instruction = (m.group(2) or "Refactor or inspect the app.").strip()
            return IntentPlan(
                kind=IntentKind.WIDGET_MODIFY,
                confidence=1.0,
                rationale="explicit /app command",
                app_id=app_id,
                instruction=instruction,
            )

        # 2. Normalize legacy context (list-of-apps, or anything else) into a RouterContext.
        if isinstance(context, RouterContext):
            ctx = context
        elif isinstance(context, list):
            ctx = RouterContext(app_manifests=list(context))
        elif context is None:
            ctx = RouterContext()
        else:
            ctx = context

        sections = context_sections if context_sections is not None else _default_context_sections()

        # 3. LLM-driven routing via function-calling.
        try:
            runtime_provider, runtime_model = selection_ids(fast_selection())
            plan = await cls._route_with_llm(
                content_stripped=content_stripped,
                context=ctx,
                provider_name=provider_name or runtime_provider,
                model_name=model_name or runtime_model,
                db_session=db_session,
                override_system_prompt=override_system_prompt,
                context_sections=sections,
                include_widget_keyword_hint=include_widget_keyword_hint,
                language=language,
                audit_context=audit_context,
                budget=budget,
            )
            if plan is not None:
                return plan
        except (LLMConfigError, BudgetExhaustedError):
            raise
        except Exception as e:
            logger.warning(f"LLM routing failed: {e}")

        # 4. Keyword-based fallback (deprecated path; off by default).
        if fallback_keywords:
            plan = cls._fallback_with_keywords(content_stripped, ctx, fallback_keywords)
            if plan is not None:
                return plan

        # 5. Final fallback: CONVERSE
        return IntentPlan(
            kind=IntentKind.CONVERSE,
            confidence=0.0,
            rationale="fallback heuristic",
            instruction=content_stripped,
        )

    @classmethod
    async def refine_sub_intents(
        cls,
        plan: IntentPlan,
        context: RouterContext | list[dict[str, Any]] | None = None,
        db_session: Session | None = None,
        provider_name: str | None = None,
        model_name: str | None = None,
        extra_context: dict[str, Any] | None = None,
        language: str = "zh",
        audit_context: dict[str, Any] | None = None,
        budget: ToolLoopBudget | None = None,
    ) -> IntentPlan:
        """Layer 2 of the router: specialise sub-intents.

        Called by the harness when the top-level ``kind`` is
        ``MULTI_INTENT`` or ``PLAN_AND_ACT``. Returns a new plan with
        concrete actions, extend_schema_props, etc., populated. Falls back
        to the input plan unchanged on error.
        """
        if plan.kind not in (IntentKind.MULTI_INTENT, IntentKind.PLAN_AND_ACT):
            return plan
        if not plan.sub_intents:
            return plan

        if isinstance(context, RouterContext):
            ctx = context
        elif isinstance(context, list):
            ctx = RouterContext(app_manifests=list(context))
        else:
            ctx = RouterContext()

        runtime_provider, runtime_model = selection_ids(primary_selection())
        provider_name = provider_name or runtime_provider
        model_name = model_name or runtime_model

        prompt_manager = PromptManager()
        try:
            system_prompt = prompt_manager.get_prompt(
                "refine_sub_intent.md",
                router_context=ctx.render_for_prompt(
                    sections=["widgets", "graph_counts", "schemas"],
                ),
                extra_context=json.dumps(extra_context or {}, ensure_ascii=False),
                language=language,
            )
        except LLMConfigError:
            raise
        except Exception as e:
            logger.warning(f"Could not load refine_sub_intent.md prompt: {e}")
            return plan

        plan_json = plan.to_dict()
        user_prompt = (
            "Top-level plan:\n"
            f"```json\n{json.dumps(plan_json, ensure_ascii=False)}\n```\n\n"
            "Refine each sub_intent into a concrete, executable form. "
            "For widget_extend_schema, fill extend_schema_props with concrete "
            "{node_type: {prop_name: type_string}} entries. For graph_mutation, "
            "fill actions[] with concrete create_node / update_node_property / "
            "delete_node / create_edge / delete_edge actions. "
            "Respond by calling classify_intent again with the refined plan."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        tools = [IntentPlan.tool_schema()]

        started = time.monotonic()
        try:
            response = await cls._call_llm_with_budget(
                provider_name,
                model_name,
                messages,
                tools,
                budget,
            )
        except (LLMConfigError, BudgetExhaustedError):
            raise
        except Exception as e:
            cls._record_audit(
                db_session,
                provider_name,
                model_name,
                messages,
                tools,
                None,
                time.monotonic() - started,
                audit_context,
                stage="route_refine",
                error=f"{type(e).__name__}: {e}",
            )
            logger.warning(f"LLM #2 refine_sub_intents failed: {e}")
            return plan
        cls._record_audit(
            db_session,
            provider_name,
            model_name,
            messages,
            tools,
            response,
            time.monotonic() - started,
            audit_context,
            stage="route_refine",
        )

        if not isinstance(response, dict):
            return plan
        tool_calls = response.get("tool_calls") or []
        for tc in tool_calls:
            fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
            if fn.get("name") != "classify_intent":
                continue
            raw_args = fn.get("arguments", "{}")
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except Exception:
                args = {}
            refined = IntentPlan.from_tool_call_args(args)
            # Preserve top-level kind from caller; only take sub_intents back.
            if refined.sub_intents:
                plan.sub_intents = refined.sub_intents
            return plan
        return plan

    @staticmethod
    async def _call_llm_with_budget(
        provider_name: str,
        model_name: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        budget: ToolLoopBudget | None,
    ) -> Any:
        if budget is not None and budget.on_model_call is not None:
            budget.on_model_call()
        invocation = call_llm_api(provider_name, model_name, messages, tools)
        response = (
            await asyncio.wait_for(invocation, timeout=budget.llm_call_timeout_s)
            if budget is not None
            else await invocation
        )
        if budget is not None and budget.on_usage is not None and isinstance(response, dict):
            usage = response.get("usage")
            budget.on_usage(usage if isinstance(usage, dict) else {})
        return response

    @classmethod
    async def route_legacy(
        cls,
        content: str,
        existing_apps: list[dict[str, Any]] | None = None,
        db_session: Session | None = None,
    ) -> IntentPlan:
        return await cls.route(
            content=content,
            context=existing_apps or [],
            db_session=db_session,
        )

    @classmethod
    async def _route_with_llm(
        cls,
        content_stripped: str,
        context: RouterContext,
        provider_name: str,
        model_name: str,
        db_session: Any = None,
        language: str = "zh",
        *,
        override_system_prompt: str | None = None,
        context_sections: list[str] | None = None,
        include_widget_keyword_hint: bool = False,
        audit_context: dict[str, Any] | None = None,
        budget: ToolLoopBudget | None = None,
    ) -> IntentPlan | None:
        if override_system_prompt is not None:
            rendered_ctx = context.render_for_prompt(
                sections=context_sections,
                include_widget_keyword_hint=include_widget_keyword_hint,
            )
            system_prompt = override_system_prompt.replace("{{ router_context }}", rendered_ctx)
        else:
            prompt_manager = PromptManager()
            try:
                system_prompt = prompt_manager.get_prompt(
                    "router_v2.md",
                    router_context=context.render_for_prompt(
                        sections=context_sections,
                        include_widget_keyword_hint=include_widget_keyword_hint,
                    ),
                    language=language,
                )
            except Exception as e:
                logger.warning(f"Could not load router_v2.md prompt: {e}")
                system_prompt = "You are Ambient Agent's intent router. Reply by calling the classify_intent function."

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content_stripped},
        ]
        tools = [IntentPlan.tool_schema()]

        started = time.monotonic()
        try:
            response = await cls._call_llm_with_budget(
                provider_name,
                model_name,
                messages,
                tools,
                budget,
            )
        except BaseException as exc:
            cls._record_audit(
                db_session,
                provider_name,
                model_name,
                messages,
                tools,
                None,
                time.monotonic() - started,
                audit_context,
                stage="route",
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

        cls._record_audit(
            db_session,
            provider_name,
            model_name,
            messages,
            tools,
            response,
            time.monotonic() - started,
            audit_context,
            stage="route",
        )

        if not isinstance(response, dict):
            return None

        tool_calls = response.get("tool_calls") or []
        if not tool_calls:
            return None

        for tc in tool_calls:
            fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
            if fn.get("name") != "classify_intent":
                continue
            raw_args = fn.get("arguments", "{}")
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args)
                except Exception:
                    args = {}
            elif isinstance(raw_args, dict):
                args = raw_args
            else:
                args = {}
            plan = IntentPlan.from_tool_call_args(args)
            if plan.kind == IntentKind.CONVERSE and not plan.instruction:
                plan.instruction = content_stripped
            if plan.kind == IntentKind.WIDGET_MODIFY and plan.app_id and isinstance(context, RouterContext):
                plan = cls._resolve_widget_modify_ambiguity(plan, context)
            return plan
        return None

    @staticmethod
    def _record_audit(
        db_session: Any,
        provider_name: str,
        model_name: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        response: Any,
        elapsed_seconds: float,
        audit_context: dict[str, Any] | None,
        *,
        stage: str,
        error: str | None = None,
    ) -> None:
        if db_session is None or not hasattr(db_session, "add") or not hasattr(db_session, "commit"):
            return
        try:
            from backend.models import LLMAuditLog

            prompt = json.dumps(messages, ensure_ascii=False, default=str)
            tool_payload = json.dumps(tools, ensure_ascii=False, sort_keys=True, default=str)
            context = audit_context or {}
            audit_log = LLMAuditLog(
                provider=provider_name,
                model=model_name,
                prompt=prompt,
                response=json.dumps(response, ensure_ascii=False, default=str) if response is not None else "",
                stage=stage,
                run_id=context.get("run_id"),
                session_id=context.get("session_id"),
                step_id=context.get("step_id"),
                attempt=context.get("attempt"),
                trace_id=context.get("trace_id"),
                latency_ms=elapsed_seconds * 1000,
                error=error,
                prompt_hash=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                tool_schema_hash=hashlib.sha256(tool_payload.encode("utf-8")).hexdigest(),
                artifact_hashes=dict(context.get("artifact_hashes") or {}),
            )
            db_session.add(audit_log)
            db_session.commit()
        except Exception:
            logger.warning("Unable to persist router audit trace", exc_info=True)

    @staticmethod
    def _fallback_with_keywords(
        content: str,
        context: RouterContext,
        keywords: list[str],
    ) -> IntentPlan | None:
        content_lower = content.lower()
        creation_kw = {
            "创建",
            "建一个",
            "制作",
            "build",
            "create",
            "make",
            "生成",
            "开发",
            "design",
            "develop",
            "generate",
        }
        modification_kw = {
            "修改",
            "改下",
            "fix",
            "update",
            "modify",
            "添加",
            "加上",
            "add",
            "change",
            "refresh",
            "重新做",
            "redo",
        }

        is_create = any(k in content or k.lower() in content_lower for k in keywords if k in creation_kw)
        is_modify = any(k in content or k.lower() in content_lower for k in keywords if k in modification_kw)

        if not is_create and not is_modify:
            return None

        target_app_id: str | None = None
        for app in context.app_manifests or []:
            title = (app.get("title") or "").lower()
            if title and title in content_lower:
                target_app_id = app.get("id")
                break

        if is_modify and target_app_id:
            return IntentPlan(
                kind=IntentKind.WIDGET_MODIFY,
                confidence=0.6,
                rationale="keyword fallback (modification)",
                app_id=target_app_id,
                instruction=content,
            )

        if is_create:
            topic = "app"
            for app in context.app_manifests or []:
                t = (app.get("title") or "").lower()
                if t and t in content_lower:
                    topic = app.get("id", "app").split("-")[0]
                    break
            return IntentPlan(
                kind=IntentKind.WIDGET_CREATE,
                confidence=0.5,
                rationale="keyword fallback (creation)",
                app_id=f"{topic}-app-XXXX",
                instruction=content,
            )

        return None

    @staticmethod
    def _resolve_widget_modify_ambiguity(plan: IntentPlan, context: RouterContext) -> IntentPlan:
        requested = plan.app_id or ""
        if not requested:
            return plan
        requested_base = requested.split("-")[0] if "-" in requested else requested

        candidates: list[dict[str, Any]] = []
        for app in context.app_manifests or []:
            app_id = app.get("id", "")
            if app_id == requested:
                return plan
            if app_id.split("-")[0] == requested_base or app_id == requested_base:
                candidates.append({"value": app_id, "label": app.get("title", app_id)})

        if len(candidates) > 1:
            ids_str = ", ".join(f"`{c['value']}`" for c in candidates)
            return IntentPlan(
                kind=IntentKind.CLARIFY,
                confidence=plan.confidence,
                rationale=f"multiple apps match base '{requested_base}'",
                clarification_message=(
                    f"我发现您有多个同类型应用（{ids_str}），请使用 `/app <Widget ID> <指令>` 明确指定您想修改哪一个。"
                ),
                clarification_options=candidates,
            )
        return plan
