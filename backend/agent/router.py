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
import time
from typing import Any

from sqlmodel import Session

from backend.agent.intent_plan import (
    IntentKind,
    IntentPlan,
    SubIntent,
    SubIntentKind,
)
from backend.agent.slash_commands import (
    ParsedSlashCommand,
    SlashCommandParseError,
    parse_slash_commands,
)
from backend.agent.errors import BudgetExhaustedError
from backend.agent.providers import ToolLoopBudget
from backend.agent.prompts.manager import PromptManager
from backend.app_manifest import ManifestValidationError, validate_app_id
from backend.capabilities.catalog import AgentRole, SystemCapabilityCatalog
from backend.llm_service import call_llm_api
from backend.llm_config import LLMConfigError
from backend.llm_runtime import fast_selection, primary_selection, selection_ids
from backend.router_context import RouterContext

logger = logging.getLogger("agent.router")

def _default_context_sections() -> list[str]:
    return ["widgets", "graph_counts", "history"]


class IntentRouter:
    """Routes a user message into an IntentPlan."""

    @classmethod
    async def route(
        cls,
        content: str,
        context: RouterContext | None = None,
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
        capability_catalog: SystemCapabilityCatalog | None = None,
    ) -> IntentPlan:
        """Classify a user message.

        Two-layer LLM: this call returns the top-level IntentPlan; if the
        plan kind is ``MULTI_INTENT`` or ``PLAN_AND_ACT``, the harness may
        additionally call :meth:`refine_sub_intents` to specialise the
        ``sub_intents`` into concrete actions.
        """
        content_stripped = (content or "").strip()

        # 1. Normalize the optional structured context.
        ctx = context or RouterContext()

        sections = context_sections if context_sections is not None else _default_context_sections()

        # 2. Slash commands are explicit routing directives.  They compile to
        # the same IntentPlan consumed by the durable reducer; they never
        # execute App, Graph, Tool, or Skill effects here.
        try:
            slash_commands = parse_slash_commands(content_stripped)
        except SlashCommandParseError as exc:
            return cls._slash_clarification(str(exc), language)
        if slash_commands:
            runtime_provider, runtime_model = selection_ids(fast_selection())
            return await cls._route_explicit_slash_commands(
                slash_commands,
                context=ctx,
                db_session=db_session,
                provider_name=provider_name or runtime_provider,
                model_name=model_name or runtime_model,
                language=language,
                audit_context=audit_context,
                budget=budget,
                capability_catalog=capability_catalog,
            )

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
                capability_catalog=capability_catalog,
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
    async def _route_explicit_slash_commands(
        cls,
        commands: list[ParsedSlashCommand],
        *,
        context: RouterContext,
        db_session: Any,
        provider_name: str,
        model_name: str,
        language: str,
        audit_context: dict[str, Any] | None,
        budget: ToolLoopBudget | None,
        capability_catalog: SystemCapabilityCatalog | None,
    ) -> IntentPlan:
        plans: list[IntentPlan] = []
        app_by_id = {
            str(item.get("id")): item
            for item in context.app_manifests
            if item.get("id")
        }
        for command in commands:
            instruction = command.arguments.get("instruction", "").strip()
            if command.name == "ask":
                if not instruction:
                    return cls._slash_clarification(
                        "请在 `/ask` 后输入问题。" if language == "zh" else "Enter a question after `/ask`.",
                        language,
                    )
                plans.append(
                    IntentPlan(
                        kind=IntentKind.CONVERSE,
                        confidence=1.0,
                        rationale="explicit slash command",
                        instruction=instruction,
                    )
                )
                continue

            if command.name == "skill":
                skill_id = command.arguments.get("skill_id", "").strip()
                if not skill_id or not instruction:
                    return cls._slash_clarification(
                        (
                            "请选择 Skill ID，并在其后输入指令。"
                            if language == "zh"
                            else "Choose a Skill ID and enter an instruction after it."
                        ),
                        language,
                    )
                # SkillManager resolves and pins every selected ID before the
                # router can admit this read-only conversation step.
                plans.append(
                    IntentPlan(
                        kind=IntentKind.CONVERSE,
                        confidence=1.0,
                        rationale="explicit slash command",
                        instruction=instruction,
                    )
                )
                continue

            if command.name == "app":
                app_id = command.arguments.get("app_id", "").strip()
                if not app_id or (app_by_id and app_id not in app_by_id):
                    options = [
                        {
                            "value": candidate_id,
                            "label": str(item.get("title") or candidate_id),
                        }
                        for candidate_id, item in sorted(app_by_id.items())
                    ]
                    return IntentPlan(
                        kind=IntentKind.CLARIFY,
                        confidence=1.0,
                        rationale="explicit slash command references an unknown app",
                        clarification_message=(
                            "请选择一个已安装的 App ID。"
                            if language == "zh"
                            else "Choose an installed App ID."
                        ),
                        clarification_options=options,
                    )
                plans.append(
                    IntentPlan(
                        kind=IntentKind.WIDGET_MODIFY,
                        confidence=1.0,
                        rationale="explicit slash command",
                        app_id=app_id,
                        instruction=instruction or (
                            "检查并说明这个 App。"
                            if language == "zh"
                            else "Inspect and explain this App."
                        ),
                    )
                )
                continue

            if command.name == "create":
                app_id = command.arguments.get("app_id", "").strip()
                try:
                    validate_app_id(app_id)
                except ManifestValidationError:
                    return cls._slash_clarification(
                        (
                            "请为 `/create` 输入新的小写 kebab-case App ID。"
                            if language == "zh"
                            else "Enter a new lowercase kebab-case App ID after `/create`."
                        ),
                        language,
                    )
                if app_id in app_by_id:
                    return cls._slash_clarification(
                        (
                            f"App `{app_id}` 已存在；请改用 `/app {app_id}` 或选择新的 ID。"
                            if language == "zh"
                            else f"App `{app_id}` already exists; use `/app {app_id}` or choose a new ID."
                        ),
                        language,
                    )
                if not instruction:
                    return cls._slash_clarification(
                        (
                            "请在新 App ID 后描述要创建的内容。"
                            if language == "zh"
                            else "Describe what to create after the new App ID."
                        ),
                        language,
                    )
                plans.append(
                    IntentPlan(
                        kind=IntentKind.WIDGET_CREATE,
                        confidence=1.0,
                        rationale="explicit slash command",
                        app_id=app_id,
                        instruction=instruction,
                    )
                )
                continue

            expected_kind = (
                IntentKind.GRAPH_QUERY
                if command.name == "query"
                else IntentKind.GRAPH_MUTATION
            )
            if not instruction:
                return cls._slash_clarification(
                    (
                        f"请在 `/{command.name}` 后输入具体指令。"
                        if language == "zh"
                        else f"Enter a concrete instruction after `/{command.name}`."
                    ),
                    language,
                )
            constrained_prompt = cls._slash_router_prompt(expected_kind, language)
            try:
                plan = await cls._route_with_llm(
                    content_stripped=instruction,
                    context=context,
                    provider_name=provider_name,
                    model_name=model_name,
                    db_session=db_session,
                    language=language,
                    override_system_prompt=constrained_prompt,
                    context_sections=["graph_counts", "recent_nodes", "schemas"],
                    audit_context=audit_context,
                    budget=budget,
                    capability_catalog=capability_catalog,
                )
            except (LLMConfigError, BudgetExhaustedError):
                raise
            except Exception:
                logger.warning("Explicit slash command routing failed", exc_info=True)
                plan = None
            if (
                plan is None
                or plan.kind != expected_kind
                or (
                    expected_kind == IntentKind.GRAPH_QUERY
                    and not isinstance(plan.query, dict)
                )
                or (
                    expected_kind == IntentKind.GRAPH_MUTATION
                    and not plan.actions
                )
            ):
                return cls._slash_clarification(
                    (
                        f"`/{command.name}` 无法生成安全、完整的结构化计划，请补充更多信息。"
                        if language == "zh"
                        else f"`/{command.name}` could not produce a safe, complete structured plan. Add more detail."
                    ),
                    language,
                )
            plan.confidence = 1.0
            plan.rationale = "explicit slash command"
            plans.append(plan)

        if len(plans) == 1:
            return plans[0]
        return IntentPlan(
            kind=IntentKind.MULTI_INTENT,
            confidence=1.0,
            rationale="explicit slash command sequence",
            instruction="",
            sub_intents=[cls._slash_sub_intent(plan) for plan in plans],
        )

    @staticmethod
    def _slash_sub_intent(plan: IntentPlan) -> SubIntent:
        kind_by_intent = {
            IntentKind.CONVERSE: SubIntentKind.CONVERSE,
            IntentKind.GRAPH_QUERY: SubIntentKind.GRAPH_QUERY,
            IntentKind.GRAPH_MUTATION: SubIntentKind.GRAPH_MUTATION,
            IntentKind.WIDGET_CREATE: SubIntentKind.WIDGET_CREATE,
            IntentKind.WIDGET_MODIFY: SubIntentKind.WIDGET_MODIFY,
        }
        kind = kind_by_intent.get(plan.kind)
        if kind is None:
            raise ValueError(f"Unsupported slash command intent: {plan.kind}")
        return SubIntent(
            kind=kind,
            app_id=plan.app_id,
            instruction=plan.instruction,
            actions=list(plan.actions),
            query=plan.query,
        )

    @staticmethod
    def _slash_router_prompt(expected_kind: IntentKind, language: str) -> str:
        field_rule = (
            'Fill `query` with an object shaped like {"type": string, "properties": object, "include": array}.'
            if expected_kind == IntentKind.GRAPH_QUERY
            else (
                "Fill `actions` with concrete create_node, update_node_property, "
                "delete_node, create_edge, or delete_edge action objects."
            )
        )
        language_rule = (
            "Write natural-language fields in Chinese."
            if language == "zh"
            else "Write natural-language fields in English."
        )
        return (
            "You are Ambient Agent's constrained explicit-command router.\n"
            f"The user selected `/{'query' if expected_kind == IntentKind.GRAPH_QUERY else 'mutate'}`. "
            f"You MUST call classify_intent exactly once with kind `{expected_kind.value}`. "
            "Do not change the route, execute anything, or return prose.\n"
            f"{field_rule}\n{language_rule}\n\n"
            "# Available Graph context\n{{ router_context }}"
        )

    @staticmethod
    def _slash_clarification(message: str, language: str) -> IntentPlan:
        del language
        return IntentPlan(
            kind=IntentKind.CLARIFY,
            confidence=1.0,
            rationale="invalid explicit slash command",
            clarification_message=message,
        )

    @classmethod
    async def refine_sub_intents(
        cls,
        plan: IntentPlan,
        context: RouterContext | None = None,
        db_session: Session | None = None,
        provider_name: str | None = None,
        model_name: str | None = None,
        extra_context: dict[str, Any] | None = None,
        language: str = "zh",
        audit_context: dict[str, Any] | None = None,
        budget: ToolLoopBudget | None = None,
        capability_catalog: SystemCapabilityCatalog | None = None,
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

        ctx = context or RouterContext()

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
        system_prompt += "\n\n" + (capability_catalog or SystemCapabilityCatalog.build()).render(
            AgentRole.INTENT_ROUTER
        )

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
        capability_catalog: SystemCapabilityCatalog | None = None,
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
        system_prompt += "\n\n" + (capability_catalog or SystemCapabilityCatalog.build()).render(
            AgentRole.INTENT_ROUTER
        )

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
