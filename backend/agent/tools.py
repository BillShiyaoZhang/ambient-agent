from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import TypeAdapter, ValidationError

logger = logging.getLogger("agent.tools")


class ToolEffect(StrEnum):
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    EXECUTE = "execute"
    NETWORK = "network"


class ApprovalPolicy(StrEnum):
    NEVER = "never"
    ALWAYS = "always"


class ToolError(RuntimeError):
    """Base error for failures enforced by the tool boundary."""


class ToolPolicyError(ToolError):
    pass


class ToolValidationError(ToolError):
    pass


class ToolTimeoutError(ToolError):
    pass


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    effect: ToolEffect = ToolEffect.READ
    scopes: frozenset[str] = frozenset()
    approval_policy: ApprovalPolicy = ApprovalPolicy.NEVER
    timeout_s: float = 30.0
    max_output_bytes: int = 64 * 1024
    requires_idempotency: bool = False
    sensitive_fields: frozenset[str] = frozenset()

    def llm_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


@dataclass
class ToolExecutionContext:
    db_session: Any = None
    session_id: str | None = None
    run_id: str | None = None
    step_id: str | None = None
    attempt: int | None = None
    trace_id: str | None = None
    scopes: frozenset[str] = frozenset()
    approved_effects: frozenset[ToolEffect] = frozenset()
    idempotency_key: str | None = None
    cancellation_event: asyncio.Event | None = None
    on_event: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None

    @classmethod
    def from_mapping(cls, value: dict[str, Any] | None) -> ToolExecutionContext:
        value = value or {}
        approved: set[ToolEffect] = set()
        for effect in value.get("approved_effects", ()):
            approved.add(effect if isinstance(effect, ToolEffect) else ToolEffect(str(effect)))
        return cls(
            db_session=value.get("db_session"),
            session_id=value.get("session_id"),
            run_id=value.get("run_id"),
            step_id=value.get("step_id"),
            attempt=int(value["attempt"]) if value.get("attempt") is not None else None,
            trace_id=str(value["trace_id"]) if value.get("trace_id") is not None else None,
            scopes=frozenset(str(scope) for scope in value.get("scopes", ())),
            approved_effects=frozenset(approved),
            idempotency_key=value.get("idempotency_key"),
            cancellation_event=value.get("cancellation_event"),
            on_event=value.get("on_event"),
        )


@dataclass
class _RegisteredTool:
    func: Callable[..., Any]
    spec: ToolSpec
    public_parameters: dict[str, inspect.Parameter] = field(default_factory=dict)


def _annotation_schema(annotation: Any) -> dict[str, Any]:
    if annotation is inspect.Parameter.empty or annotation is inspect.Signature.empty:
        return {}
    try:
        return TypeAdapter(annotation).json_schema()
    except Exception:
        return {}


class ToolGateway:
    """The single enforcement point for model-requested tool calls."""

    def __init__(self) -> None:
        self._tools: dict[str, _RegisteredTool] = {}
        self._idempotency_results: dict[tuple[str, str], tuple[str, Any]] = {}

    def add(self, func: Callable[..., Any], spec: ToolSpec) -> None:
        signature = inspect.signature(func)
        public_parameters = {
            name: parameter
            for name, parameter in signature.parameters.items()
            if name not in {"self", "db_session", "session_id", "run_id", "step_id", "idempotency_key"}
        }
        self._tools[spec.name] = _RegisteredTool(func=func, spec=spec, public_parameters=public_parameters)

    def remove(self, name: str) -> None:
        self._tools.pop(name, None)

    def spec(self, name: str) -> ToolSpec:
        try:
            return self._tools[name].spec
        except KeyError as exc:
            raise ToolValidationError(f"Tool '{name}' is not registered") from exc

    def specs(
        self,
        *,
        allowed_effects: set[ToolEffect] | None = None,
        scopes: set[str] | None = None,
    ) -> list[ToolSpec]:
        result: list[ToolSpec] = []
        available_scopes = scopes or set()
        for item in self._tools.values():
            spec = item.spec
            if allowed_effects is not None and spec.effect not in allowed_effects:
                continue
            if spec.scopes and not spec.scopes.issubset(available_scopes):
                continue
            result.append(spec)
        return result

    async def _emit(self, context: ToolExecutionContext, payload: dict[str, Any]) -> None:
        if context.on_event is None:
            return
        result = context.on_event(payload)
        if inspect.isawaitable(result):
            await result

    @staticmethod
    def _validate_arguments(item: _RegisteredTool, args: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(args, dict):
            raise ToolValidationError("Tool arguments must be a JSON object")
        unknown = sorted(set(args) - set(item.public_parameters))
        if unknown:
            raise ToolValidationError(f"Unexpected tool arguments: {', '.join(unknown)}")

        validated: dict[str, Any] = {}
        for name, parameter in item.public_parameters.items():
            if name not in args:
                if parameter.default is inspect.Parameter.empty:
                    raise ToolValidationError(f"Missing required tool argument: {name}")
                continue
            value = args[name]
            if parameter.annotation is not inspect.Parameter.empty:
                try:
                    value = TypeAdapter(parameter.annotation).validate_python(value)
                except ValidationError as exc:
                    raise ToolValidationError(f"Invalid tool argument '{name}': {exc}") from exc
            validated[name] = value
        return validated

    async def execute(self, name: str, args: dict[str, Any], context: ToolExecutionContext) -> Any:
        try:
            item = self._tools[name]
        except KeyError as exc:
            raise ToolValidationError(f"Tool '{name}' is not registered") from exc
        spec = item.spec

        if spec.scopes and not spec.scopes.issubset(context.scopes):
            raise ToolPolicyError(f"Tool '{name}' requires scopes: {sorted(spec.scopes)}")
        if spec.approval_policy == ApprovalPolicy.ALWAYS and spec.effect not in context.approved_effects:
            raise ToolPolicyError(f"Tool '{name}' requires explicit approval for effect '{spec.effect}'")
        if spec.requires_idempotency and not context.idempotency_key:
            raise ToolPolicyError(f"Tool '{name}' requires an idempotency key")
        if context.cancellation_event is not None and context.cancellation_event.is_set():
            raise asyncio.CancelledError

        validated = self._validate_arguments(item, args)
        cache_key = (name, context.idempotency_key or "")
        canonical_args = json.dumps(validated, ensure_ascii=False, sort_keys=True, default=str)
        if spec.requires_idempotency and cache_key in self._idempotency_results:
            cached_args, cached_result = self._idempotency_results[cache_key]
            if cached_args != canonical_args:
                raise ToolPolicyError(f"Tool '{name}' idempotency key was reused with different arguments")
            return cached_result
        signature = inspect.signature(item.func)
        injected = {
            "db_session": context.db_session,
            "session_id": context.session_id,
            "run_id": context.run_id,
            "step_id": context.step_id,
            "idempotency_key": context.idempotency_key,
        }
        for parameter_name in signature.parameters:
            if parameter_name in injected and injected[parameter_name] is not None:
                validated[parameter_name] = injected[parameter_name]

        redacted_args = {
            key: "[REDACTED]" if key in spec.sensitive_fields else value for key, value in args.items()
        }
        await self._emit(
            context,
            {
                "type": "tool_started",
                "run_id": context.run_id,
                "step_id": context.step_id,
                "attempt": context.attempt,
                "trace_id": context.trace_id,
                "tool": name,
                "effect": spec.effect,
                "arguments": redacted_args,
            },
        )

        started = time.monotonic()

        async def invoke() -> Any:
            if inspect.iscoroutinefunction(item.func):
                return await item.func(**validated)
            return await asyncio.to_thread(item.func, **validated)

        try:
            result = await asyncio.wait_for(invoke(), timeout=spec.timeout_s)
        except TimeoutError as exc:
            await self._emit(
                context,
                {
                    "type": "tool_failed",
                    "run_id": context.run_id,
                    "step_id": context.step_id,
                    "attempt": context.attempt,
                    "trace_id": context.trace_id,
                    "tool": name,
                    "error": "timeout",
                    "duration_ms": (time.monotonic() - started) * 1000,
                },
            )
            raise ToolTimeoutError(f"Tool '{name}' exceeded {spec.timeout_s:g}s") from exc
        except asyncio.CancelledError:
            await self._emit(
                context,
                {
                    "type": "tool_cancelled",
                    "run_id": context.run_id,
                    "step_id": context.step_id,
                    "attempt": context.attempt,
                    "trace_id": context.trace_id,
                    "tool": name,
                    "duration_ms": (time.monotonic() - started) * 1000,
                },
            )
            raise
        except Exception as exc:
            await self._emit(
                context,
                {
                    "type": "tool_failed",
                    "run_id": context.run_id,
                    "step_id": context.step_id,
                    "attempt": context.attempt,
                    "trace_id": context.trace_id,
                    "tool": name,
                    "error": type(exc).__name__,
                    "duration_ms": (time.monotonic() - started) * 1000,
                },
            )
            raise

        try:
            return_annotation = inspect.signature(item.func).return_annotation
            if return_annotation is not inspect.Signature.empty:
                result = TypeAdapter(return_annotation).validate_python(result)
            encoded = json.dumps(result, ensure_ascii=False, default=str).encode("utf-8")
            if len(encoded) > spec.max_output_bytes:
                raise ToolValidationError(
                    f"Tool '{name}' output exceeded {spec.max_output_bytes} bytes ({len(encoded)} bytes received)"
                )
        except (ValidationError, ToolValidationError) as exc:
            await self._emit(
                context,
                {
                    "type": "tool_failed",
                    "run_id": context.run_id,
                    "step_id": context.step_id,
                    "attempt": context.attempt,
                    "trace_id": context.trace_id,
                    "tool": name,
                    "error": "invalid_output",
                    "duration_ms": (time.monotonic() - started) * 1000,
                },
            )
            if isinstance(exc, ToolValidationError):
                raise
            raise ToolValidationError(f"Tool '{name}' returned an invalid result: {exc}") from exc
        if spec.requires_idempotency:
            self._idempotency_results[cache_key] = (canonical_args, result)
        await self._emit(
            context,
            {
                "type": "tool_succeeded",
                "run_id": context.run_id,
                "step_id": context.step_id,
                "attempt": context.attempt,
                "trace_id": context.trace_id,
                "tool": name,
                "output_bytes": len(encoded),
                "duration_ms": (time.monotonic() - started) * 1000,
            },
        )
        return result


class ToolRegistry:
    """Tool declaration facade backed by the policy-enforcing gateway."""

    def __init__(self):
        self.tools: dict[str, Callable[..., Any]] = {}
        self.schemas: dict[str, dict[str, Any]] = {}
        self.specs: dict[str, ToolSpec] = {}
        self.gateway = ToolGateway()

    def register(
        self,
        func: Callable[..., Any] | None = None,
        *,
        effect: ToolEffect = ToolEffect.READ,
        scopes: set[str] | frozenset[str] = frozenset(),
        approval_policy: ApprovalPolicy = ApprovalPolicy.NEVER,
        timeout_s: float = 30.0,
        max_output_bytes: int = 64 * 1024,
        requires_idempotency: bool = False,
        sensitive_fields: set[str] | frozenset[str] = frozenset(),
    ) -> Callable[..., Any]:
        def decorate(target: Callable[..., Any]) -> Callable[..., Any]:
            name = target.__name__
            signature = inspect.signature(target)
            doc_lines = [line.strip() for line in (target.__doc__ or "").splitlines() if line.strip()]
            description = doc_lines[0] if doc_lines else f"Execute tool function {name}"
            properties: dict[str, Any] = {}
            required: list[str] = []
            for parameter_name, parameter in signature.parameters.items():
                if parameter_name in {
                    "self",
                    "db_session",
                    "session_id",
                    "run_id",
                    "step_id",
                    "idempotency_key",
                }:
                    continue
                schema = _annotation_schema(parameter.annotation)
                schema.setdefault("type", "string")
                parameter_description = f"Parameter {parameter_name}"
                for line in doc_lines[1:]:
                    if line.startswith(f":param {parameter_name}:") or line.startswith(f"{parameter_name}:"):
                        parameter_description = line.split(":", 2)[-1].strip()
                        break
                schema["description"] = parameter_description
                properties[parameter_name] = schema
                if parameter.default is inspect.Parameter.empty:
                    required.append(parameter_name)
            input_schema = {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            }
            spec = ToolSpec(
                name=name,
                description=description,
                input_schema=input_schema,
                output_schema=_annotation_schema(signature.return_annotation),
                effect=effect,
                scopes=frozenset(scopes),
                approval_policy=approval_policy,
                timeout_s=timeout_s,
                max_output_bytes=max_output_bytes,
                requires_idempotency=requires_idempotency,
                sensitive_fields=frozenset(sensitive_fields),
            )
            self.tools[name] = target
            self.specs[name] = spec
            self.schemas[name] = spec.llm_schema()
            self.gateway.add(target, spec)
            logger.info("Registered tool %s (%s)", name, effect)
            return target

        return decorate(func) if func is not None else decorate

    def unregister(self, name: str) -> None:
        self.tools.pop(name, None)
        self.schemas.pop(name, None)
        self.specs.pop(name, None)
        self.gateway.remove(name)

    def get_tool_schemas(
        self,
        *,
        allowed_effects: set[ToolEffect] | None = None,
        scopes: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        return [spec.llm_schema() for spec in self.gateway.specs(allowed_effects=allowed_effects, scopes=scopes)]

    async def execute(self, name: str, args: dict[str, Any], context: dict[str, Any] | None = None) -> Any:
        return await self.gateway.execute(name, args, ToolExecutionContext.from_mapping(context))


registry = ToolRegistry()


@registry.register(scopes={"workspace:read"})
def list_available_apps() -> list[str]:
    """Returns IDs of all widget applications configured in the workspace."""
    from backend.app_manager import AppManager

    return [app["id"] for app in AppManager().list_apps()]


@registry.register(scopes={"workspace:read"})
def query_graph(query_json: str) -> str:
    """Query the workspace graph with a declarative JSON query.
    :param query_json: The declarative graph query as JSON.
    """
    from backend.graph_query_engine import execute_graph_query
    from backend.main import graph_db

    try:
        query = json.loads(query_json)
    except json.JSONDecodeError as exc:
        raise ToolValidationError(f"query_json is not valid JSON: {exc}") from exc
    return json.dumps(execute_graph_query(query, graph_db), ensure_ascii=False)
