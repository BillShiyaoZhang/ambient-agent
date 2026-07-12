"""Intent routing for Ambient Agent.

Provides ``IntentRouter.route(content, context)`` returning a structured
``IntentPlan``. The router is graph-aware: it ingests a ``RouterContext`` that
includes both the widget inventory and a lightweight graph snapshot.

Designed to use the LLM's function-calling capability to obtain a typed
``classify_intent`` payload. Falls back to a regex-based triage when the LLM
is unreachable or returns no tool call.
"""

import json
import logging
import os
import re
from typing import Any

from sqlmodel import Session

from backend.agent.intent_plan import IntentKind, IntentPlan
from backend.agent.prompts.manager import PromptManager
from backend.llm_service import call_llm_api
from backend.router_context import RouterContext

logger = logging.getLogger("agent.router")

_SLASH_APP_PATTERN = re.compile(r"^/app\s+([a-zA-Z0-9_-]+)(?:\s+(.*))?$", re.IGNORECASE)


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
        *,
        override_system_prompt: str | None = None,
        context_sections: list[str] | None = None,
        include_widget_keyword_hint: bool = False,
        fallback_keywords: list[str] | None = None,
        plan_and_act_enabled: bool = True,
    ) -> IntentPlan:
        """Classify a user message.

        ``context`` may be a ``RouterContext`` (preferred) or a legacy
        list-of-apps dicts (kept for harness compatibility).

        Experimental knobs (used by routing experiments):
        - ``override_system_prompt``: replace the router_v2.md template.
        - ``context_sections``: subset of {widgets, graph_counts, recent_nodes, schemas, history}.
        - ``include_widget_keyword_hint``: append the C4-lenient keyword hint.
        - ``fallback_keywords``: list of substring keywords to apply on LLM failure.
        - ``plan_and_act_enabled``: if False, downgrade PLAN_ANDACT to GRAPH_MUTATION.
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
            # Duck-typed: keep as-is for backward compat (will fall back to converse
            # if not a RouterContext).
            ctx = context

        # 3. LLM-driven routing via function-calling.
        try:
            plan = await cls._route_with_llm(
                content_stripped=content_stripped,
                context=ctx,
                provider_name=provider_name or os.getenv("LLM_PROVIDER", "ollama"),
                model_name=model_name or os.getenv("LLM_MODEL", "llama3"),
                db_session=db_session,
                override_system_prompt=override_system_prompt,
                context_sections=context_sections,
                include_widget_keyword_hint=include_widget_keyword_hint,
            )
            if plan is not None:
                if not plan_and_act_enabled and plan.kind == IntentKind.PLAN_AND_ACT:
                    plan = IntentPlan(
                        kind=IntentKind.GRAPH_MUTATION,
                        confidence=plan.confidence,
                        rationale=plan.rationale or "downgraded from plan_and_act",
                        actions=plan.actions,
                        instruction=plan.instruction,
                    )
                return plan
        except Exception as e:
            logger.warning(f"LLM routing failed: {e}")

        # 4. Keyword-based fallback (experimental, C7).
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
    async def route_legacy(
        cls,
        content: str,
        existing_apps: list[dict[str, Any]] | None = None,
        db_session: Session | None = None,
    ) -> IntentPlan:
        """Legacy entry point kept for harness compatibility."""
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
        *,
        override_system_prompt: str | None = None,
        context_sections: list[str] | None = None,
        include_widget_keyword_hint: bool = False,
    ) -> IntentPlan | None:
        """Call the LLM with the classify_intent tool schema.

        Returns ``None`` if no tool_call was issued (so caller can decide fallback).
        """
        # call_llm_api is imported at module level so tests can monkeypatch
        # ``backend.agent.router.call_llm_api``.

        if override_system_prompt is not None:
            # Render the {{ router_context }} placeholder if present.
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
                )
            except Exception as e:
                logger.warning(f"Could not load router_v2.md prompt: {e}")
                system_prompt = (
                    "You are Ambient Agent's intent router. "
                    "Reply by calling the classify_intent function."
                )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content_stripped},
        ]
        tools = [IntentPlan.tool_schema()]

        response = await call_llm_api(provider_name, model_name, messages, tools)

        # Best-effort audit logging with stage="route" so the audit panel can
        # distinguish routing LLM calls from regular conversation/plan calls.
        try:
            if db_session is not None and isinstance(response, dict):
                from backend.models import LLMAuditLog as _LLClass

                _LLog = _LLClass(
                    provider=provider_name,
                    model=model_name,
                    prompt=json.dumps(messages, ensure_ascii=False),
                    response=str(response.get("content") or ""),
                    stage="route",
                )
                if hasattr(db_session, "add") and hasattr(db_session, "commit"):
                    try:
                        db_session.add(_LLog)
                        db_session.commit()
                    except Exception:
                        pass
        except Exception:
            pass

        if not isinstance(response, dict):
            return None

        tool_calls = response.get("tool_calls") or []
        if not tool_calls:
            return None

        # Find classify_intent call
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
            # For chat-style intents, mirror the user's text into instruction so
            # downstream handlers can fall back to the original message.
            if plan.kind == IntentKind.CONVERSE and not plan.instruction:
                plan.instruction = content_stripped
            # Post-process: enforce ambiguity resolution for widget_modify.
            if (
                plan.kind == IntentKind.WIDGET_MODIFY
                and plan.app_id
                and isinstance(context, RouterContext)
            ):
                plan = cls._resolve_widget_modify_ambiguity(plan, context)
            return plan
        return None

    @staticmethod
    def _fallback_with_keywords(
        content: str,
        context: RouterContext,
        keywords: list[str],
    ) -> IntentPlan | None:
        """Last-resort heuristic: if any keyword matches the content, classify
        to widget_create or widget_modify based on substring family.

        Creation keywords: 创建 / 建一个 / 制作 / build / create / make / ...
        Modification keywords: 修改 / 改下 / fix / update / modify / ...

        Returns ``None`` if no keyword matches (caller falls back to CONVERSE).
        """
        content_lower = content.lower()
        creation_kw = {"创建", "建一个", "制作", "build", "create", "make",
                       "生成", "开发", "design", "develop", "generate"}
        modification_kw = {"修改", "改下", "fix", "update", "modify",
                           "添加", "加上", "add", "change", "refresh", "重新做", "redo"}

        # Match against the user-supplied keywords list (filtered).
        is_create = any(k in content or k.lower() in content_lower for k in keywords if k in creation_kw)
        is_modify = any(k in content or k.lower() in content_lower for k in keywords if k in modification_kw)

        if not is_create and not is_modify:
            return None

        # Try to find an existing widget whose title matches the content.
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
            # Heuristic: pick the first noun-like token to form a kebab-case id.
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
        """If multiple existing apps share the requested widget's base name, downgrade
        the plan to a `clarify` request enumerating the candidates.
        """
        requested = plan.app_id or ""
        if not requested:
            return plan
        requested_base = requested.split("-")[0] if "-" in requested else requested

        candidates: list[dict[str, Any]] = []
        for app in context.app_manifests or []:
            app_id = app.get("id", "")
            if app_id == requested:
                return plan  # exact match — no ambiguity
            if app_id.split("-")[0] == requested_base or app_id == requested_base:
                candidates.append(
                    {"value": app_id, "label": app.get("title", app_id)}
                )

        if len(candidates) > 1:
            ids_str = ", ".join(f"`{c['value']}`" for c in candidates)
            return IntentPlan(
                kind=IntentKind.CLARIFY,
                confidence=plan.confidence,
                rationale=f"multiple apps match base '{requested_base}'",
                clarification_message=(
                    f"我发现您有多个同类型应用（{ids_str}），请使用 "
                    f"`/app <Widget ID> <指令>` 明确指定您想修改哪一个。"
                ),
                clarification_options=candidates,
            )
        return plan
