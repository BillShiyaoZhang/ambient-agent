"""Offline outcome-and-trajectory evaluation for the Agent harness.

The module deliberately has no model-provider dependency.  Production-model
evaluations inject a runner explicitly; deterministic CI uses ``ScriptedTape``.
"""

from __future__ import annotations

import inspect
import math
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from backend.run_service import RunStore

EvaluationMode = Literal["deterministic", "real_model"]


class TrajectoryEvent(BaseModel):
    """One normalized event used for trajectory scoring and usage metrics."""

    model_config = ConfigDict(extra="allow", allow_inf_nan=False)

    type: str = Field(min_length=1)
    source: Literal["scripted", "run", "attempt", "event", "audit", "derived"] = "scripted"
    run_id: str | None = None
    sequence: int | None = Field(default=None, ge=1)
    step_id: str | None = None
    attempt: int | None = Field(default=None, ge=1)
    trace_id: str | None = None
    status: str | None = None
    tool_name: str | None = None
    effect: Literal["read", "write", "delete", "execute", "network"] | None = None
    unsafe: bool = False
    tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0)
    latency_ms: float = Field(default=0.0, ge=0)


class EvaluationTrace(BaseModel):
    """Serializable result returned by one scenario repetition."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    outcome: Any = None
    events: list[TrajectoryEvent] = Field(default_factory=list)
    latency_ms: float | None = Field(default=None, ge=0)
    recovery_attempts: int = Field(default=0, ge=0)
    recoveries_succeeded: int = Field(default=0, ge=0)
    error: str | None = None

    @model_validator(mode="after")
    def validate_recovery_counts(self) -> EvaluationTrace:
        if self.recoveries_succeeded > self.recovery_attempts:
            raise ValueError("recoveries_succeeded cannot exceed recovery_attempts")
        return self


AuditLoader = Callable[[], Sequence[Any]]

_TERMINAL_RUN_STATUSES = {"succeeded", "failed", "cancelled", "needs_attention"}
_TOOL_EFFECTS = {"read", "write", "delete", "execute", "network"}
_EFFECTFUL_TOOL_EFFECTS = {"write", "delete", "execute", "network"}
_EXPLICIT_UNSAFE_EVENT_TYPES = {
    "policy_violation",
    "side_effect_without_approval",
    "tool_policy_violation",
    "unsafe_action",
}


def _value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)


def _timestamp(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        resolved = value
    elif isinstance(value, str):
        try:
            resolved = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return fallback
    else:
        return fallback
    return resolved.replace(tzinfo=UTC) if resolved.tzinfo is None else resolved.astimezone(UTC)


def _duration_ms(started_at: Any, finished_at: Any, fallback: datetime) -> float:
    started = _timestamp(started_at, fallback)
    finished = _timestamp(finished_at, started)
    return max(0.0, (finished - started).total_seconds() * 1000)


def _nonnegative_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else None


def _usage_totals(value: Any) -> tuple[int, float]:
    usage = value if isinstance(value, Mapping) else {}
    total = _nonnegative_number(usage.get("total_tokens"))
    if total is None:
        input_tokens = _nonnegative_number(usage.get("input_tokens"))
        if input_tokens is None:
            input_tokens = _nonnegative_number(usage.get("prompt_tokens")) or 0.0
        output_tokens = _nonnegative_number(usage.get("output_tokens"))
        if output_tokens is None:
            output_tokens = _nonnegative_number(usage.get("completion_tokens")) or 0.0
        total = input_tokens + output_tokens
    cost = 0.0
    for key in ("cost_usd", "response_cost", "cost"):
        resolved = _nonnegative_number(usage.get(key))
        if resolved is not None:
            cost = resolved
            break
    return int(total), cost


def _effect_state_unknown(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    if value.get("effect_state") == "unknown":
        return True
    outcome = value.get("outcome")
    return isinstance(outcome, Mapping) and outcome.get("effect_state") == "unknown"


def _production_event_is_unsafe(event_type: str, payload: Mapping[str, Any]) -> bool:
    """Derive definite safety failures from persisted production signals.

    A denied permission is a successful guardrail, not an unsafe action.  We
    therefore flag only explicit policy violations, explicit unsafe markers,
    or an effectful tool that production recorded as unapproved.
    """

    if event_type in _EXPLICIT_UNSAFE_EVENT_TYPES:
        return True
    if payload.get("unsafe") is True or payload.get("policy_violation") is True:
        return True
    effect = str(payload.get("effect") or "")
    return (
        event_type in {"tool_call", "tool_started"}
        and effect in _EFFECTFUL_TOOL_EFFECTS
        and payload.get("approved") is False
    )


class RunStoreTraceAdapter:
    """Build an evaluation trace from one persisted durable Run.

    The adapter reads the canonical Run row, step-attempt ledger and event
    stream from :class:`RunStore`, then joins LLM audit records by ``run_id``
    from the same workspace.  Audit records own per-call token/cost metrics;
    cumulative usage copied onto Run events is retained as metadata only, so
    aggregate evaluation metrics do not double count it.
    """

    def __init__(self, store: RunStore, *, audit_loader: AuditLoader | None = None) -> None:
        self.store = store
        if audit_loader is None:
            workspace_dir = getattr(store, "workspace_dir", None)
            if not isinstance(workspace_dir, str) or not workspace_dir:
                raise ValueError("RunStoreTraceAdapter requires a workspace-backed RunStore")
            from backend.workspace_storage import WorkspaceStorage

            storage = WorkspaceStorage(workspace_dir)
            audit_loader = storage.get_audit_logs
        self.audit_loader = audit_loader

    def trace(self, run_id: str, *, require_terminal: bool = True) -> EvaluationTrace:
        run = self.store.get_run(run_id, include_events=True)
        if run is None:
            raise KeyError(run_id)
        status = str(run.get("status") or "unknown")
        if require_terminal and status not in _TERMINAL_RUN_STATUSES:
            raise ValueError(f"Run {run_id} is not terminal: {status}")

        epoch = datetime.fromtimestamp(0, UTC)
        created_at = _timestamp(run.get("created_at"), epoch)
        timeline: list[tuple[datetime, int, int, TrajectoryEvent]] = []

        recovery_attempts = 0
        recoveries_succeeded = 0
        unsafe_attempt_present = False
        for index, step in enumerate(run.get("steps") or []):
            step_attempt = int(step.get("attempt") or 1)
            if step_attempt > 1:
                recovery_attempts += 1
                if step.get("status") == "succeeded":
                    recoveries_succeeded += 1
            output = step.get("output") if isinstance(step.get("output"), Mapping) else {}
            unsafe = _effect_state_unknown(output)
            unsafe_attempt_present = unsafe_attempt_present or unsafe
            started_at = _timestamp(step.get("started_at"), created_at)
            timeline.append(
                (
                    started_at,
                    0,
                    index,
                    TrajectoryEvent(
                        type="step_attempt",
                        source="attempt",
                        run_id=run_id,
                        step_id=str(step.get("step_key") or "") or None,
                        attempt=step_attempt,
                        status=str(step.get("status") or "unknown"),
                        unsafe=unsafe,
                        latency_ms=_duration_ms(
                            step.get("started_at"),
                            step.get("finished_at"),
                            started_at,
                        ),
                        outcome_kind=(output or {}).get("kind"),
                        effect_state=(output or {}).get("effect_state"),
                    ),
                )
            )

        for index, event in enumerate(run.get("events") or []):
            payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
            event_type = str(event.get("type") or "run_event")
            raw_effect = str(payload.get("effect") or "")
            effect = raw_effect if raw_effect in _TOOL_EFFECTS else None
            tool_name = None
            if event_type in {"tool_call", "tool_started"}:
                raw_tool_name = payload.get("tool_name", payload.get("tool"))
                tool_name = str(raw_tool_name) if raw_tool_name else None
            event_time = _timestamp(event.get("created_at"), created_at)
            event_latency = _nonnegative_number(event.get("duration_ms")) or 0.0
            timeline.append(
                (
                    event_time,
                    1,
                    int(event.get("sequence") or index + 1),
                    TrajectoryEvent(
                        type=event_type,
                        source="event",
                        run_id=run_id,
                        sequence=int(event["sequence"]) if event.get("sequence") is not None else None,
                        step_id=str(event.get("step_id")) if event.get("step_id") else None,
                        attempt=int(event["attempt"]) if event.get("attempt") is not None else None,
                        trace_id=str(event.get("trace_id")) if event.get("trace_id") else None,
                        status=(
                            str(payload.get("to"))
                            if event_type == "status_changed" and payload.get("to")
                            else None
                        ),
                        tool_name=tool_name,
                        effect=effect,
                        unsafe=_production_event_is_unsafe(event_type, payload),
                        latency_ms=event_latency,
                        event_id=event.get("event_id"),
                        model_usage=event.get("model_usage"),
                        payload=dict(payload),
                        redacted=bool(event.get("redacted")),
                    ),
                )
            )

        audit_tokens = 0
        audit_cost = 0.0
        audits = [audit for audit in self.audit_loader() if _value(audit, "run_id") == run_id]
        for index, audit in enumerate(audits):
            tokens, cost = _usage_totals(_value(audit, "usage"))
            audit_tokens += tokens
            audit_cost += cost
            audit_time = _timestamp(_value(audit, "timestamp"), created_at)
            audit_error = _value(audit, "error")
            timeline.append(
                (
                    audit_time,
                    2,
                    index,
                    TrajectoryEvent(
                        type="model_call",
                        source="audit",
                        run_id=run_id,
                        step_id=_value(audit, "step_id"),
                        attempt=_value(audit, "attempt"),
                        trace_id=_value(audit, "trace_id"),
                        status="failed" if audit_error else "succeeded",
                        tokens=tokens,
                        cost_usd=cost,
                        latency_ms=_nonnegative_number(_value(audit, "latency_ms")) or 0.0,
                        provider=_value(audit, "provider"),
                        model=_value(audit, "model"),
                        stage=_value(audit, "stage"),
                        finish_reason=_value(audit, "finish_reason"),
                        error=str(audit_error) if audit_error else None,
                        prompt_hash=_value(audit, "prompt_hash"),
                        tool_schema_hash=_value(audit, "tool_schema_hash"),
                        artifact_hashes=dict(_value(audit, "artifact_hashes") or {}),
                    ),
                )
            )

        state = run.get("state") if isinstance(run.get("state"), Mapping) else {}
        budget = state.get("budget") if isinstance(state.get("budget"), Mapping) else {}
        persisted_tokens = int(_nonnegative_number(budget.get("tokens_used")) or 0)
        persisted_cost = _nonnegative_number(budget.get("cost_usd")) or 0.0
        residual_tokens = max(0, persisted_tokens - audit_tokens)
        residual_cost = max(0.0, persisted_cost - audit_cost)
        if residual_tokens or residual_cost:
            timeline.append(
                (
                    _timestamp(run.get("finished_at") or run.get("updated_at"), created_at),
                    3,
                    0,
                    TrajectoryEvent(
                        type="model_usage",
                        source="derived",
                        run_id=run_id,
                        status="audit_missing",
                        tokens=residual_tokens,
                        cost_usd=residual_cost,
                    ),
                )
            )

        run_error = run.get("error") if isinstance(run.get("error"), Mapping) else {}
        checkpoint = run.get("checkpoint") if isinstance(run.get("checkpoint"), Mapping) else {}
        if not unsafe_attempt_present and (
            _effect_state_unknown(run_error) or _effect_state_unknown(checkpoint)
        ):
            timeline.append(
                (
                    _timestamp(run.get("finished_at") or run.get("updated_at"), created_at),
                    3,
                    1,
                    TrajectoryEvent(
                        type="unknown_effect",
                        source="derived",
                        run_id=run_id,
                        status=status,
                        unsafe=True,
                        effect_state="unknown",
                    ),
                )
            )

        timeline.sort(key=lambda item: (item[0], item[1], item[2]))
        finished_at = _timestamp(run.get("finished_at") or run.get("updated_at"), created_at)
        error: str | None = None
        if status != "succeeded":
            message = run_error.get("message") if isinstance(run_error, Mapping) else None
            error = f"run: {status}" + (f": {message}" if message else "")
        return EvaluationTrace(
            outcome={
                "run_id": run_id,
                "status": status,
                "result": run.get("result"),
                "artifacts": run.get("artifacts") or [],
            },
            events=[item[3] for item in timeline],
            latency_ms=max(0.0, (finished_at - created_at).total_seconds() * 1000),
            recovery_attempts=recovery_attempts,
            recoveries_succeeded=recoveries_succeeded,
            error=error,
        )


TraceValue = EvaluationTrace | dict[str, Any]
TraceRunner = Callable[[int], TraceValue | Awaitable[TraceValue]]
TraceScorer = Callable[[EvaluationTrace], float | bool | Awaitable[float | bool]]


@dataclass(frozen=True, slots=True)
class ScriptedTape:
    """A finite deterministic runner suitable for CI and regression tests."""

    traces: Sequence[TraceValue]

    def __post_init__(self) -> None:
        normalized = tuple(
            trace.model_copy(deep=True)
            if isinstance(trace, EvaluationTrace)
            else EvaluationTrace.model_validate(trace)
            for trace in self.traces
        )
        if not normalized:
            raise ValueError("A scripted tape must contain at least one trace")
        object.__setattr__(self, "traces", normalized)

    async def __call__(self, repetition: int) -> EvaluationTrace:
        if repetition < 0 or repetition >= len(self.traces):
            raise IndexError(f"Scripted tape has no repetition {repetition}")
        trace = self.traces[repetition]
        if not isinstance(trace, EvaluationTrace):  # Defensive for mutated/untyped callers.
            trace = EvaluationTrace.model_validate(trace)
        return trace.model_copy(deep=True)


@dataclass(frozen=True, slots=True)
class EvaluationScenario:
    """An injectable scenario with independent outcome and trajectory judges."""

    name: str
    mode: EvaluationMode
    repetitions: int
    runner: TraceRunner
    outcome_scorer: TraceScorer
    trajectory_scorer: TraceScorer
    outcome_threshold: float = 1.0
    trajectory_threshold: float = 1.0

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Evaluation scenario name must be non-empty")
        if self.mode not in {"deterministic", "real_model"}:
            raise ValueError(f"Unsupported evaluation mode: {self.mode}")
        if isinstance(self.repetitions, bool) or not isinstance(self.repetitions, int) or self.repetitions < 1:
            raise ValueError("Evaluation repetitions must be a positive integer")
        if self.mode == "real_model" and self.repetitions < 3:
            raise ValueError("Real-model evaluation scenarios require repetitions >= 3")
        for label, value in (
            ("outcome_threshold", self.outcome_threshold),
            ("trajectory_threshold", self.trajectory_threshold),
        ):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                raise ValueError(f"{label} must be a finite number")
            if not 0 <= float(value) <= 1:
                raise ValueError(f"{label} must be between 0 and 1")

    @classmethod
    def from_tape(
        cls,
        name: str,
        tape: ScriptedTape,
        *,
        outcome_scorer: TraceScorer,
        trajectory_scorer: TraceScorer,
        outcome_threshold: float = 1.0,
        trajectory_threshold: float = 1.0,
    ) -> EvaluationScenario:
        return cls(
            name=name,
            mode="deterministic",
            repetitions=len(tape.traces),
            runner=tape,
            outcome_scorer=outcome_scorer,
            trajectory_scorer=trajectory_scorer,
            outcome_threshold=outcome_threshold,
            trajectory_threshold=trajectory_threshold,
        )


class EvaluationSample(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    scenario: str
    mode: EvaluationMode
    repetition: int = Field(ge=1)
    outcome_score: float = Field(ge=0, le=1)
    trajectory_score: float = Field(ge=0, le=1)
    passed: bool
    unsafe_actions: int = Field(ge=0)
    action_count: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0)
    latency_ms: float = Field(ge=0)
    recovery_attempts: int = Field(ge=0)
    recoveries_succeeded: int = Field(ge=0)
    error: str | None = None


class EvaluationMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    runs: int = Field(ge=0)
    outcome_score: float = Field(ge=0, le=1)
    trajectory_score: float = Field(ge=0, le=1)
    success_rate: float = Field(ge=0, le=1)
    unsafe_action_rate: float = Field(ge=0, le=1)
    tool_calls: int = Field(ge=0)
    tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0)
    latency_ms: float = Field(ge=0)
    recovery_rate: float = Field(ge=0, le=1)


class ScenarioEvaluationReport(EvaluationMetrics):
    name: str
    mode: EvaluationMode
    repetitions: int = Field(ge=1)
    samples: list[EvaluationSample]


class EvaluationReport(EvaluationMetrics):
    scenarios: list[ScenarioEvaluationReport]
    samples: list[EvaluationSample]
    ci_gate_passed: bool


class EvaluationGateError(AssertionError):
    """Raised when a deterministic tape fails its configured CI thresholds."""

    def __init__(self, report: EvaluationReport):
        self.report = report
        failures = [
            f"{sample.scenario}#{sample.repetition}"
            for sample in report.samples
            if sample.mode == "deterministic" and not sample.passed
        ]
        super().__init__(f"Deterministic Agent evaluation gate failed: {', '.join(failures)}")


def exact_outcome(expected: Any) -> TraceScorer:
    """Return an outcome scorer based on deep Python equality."""

    def score(trace: EvaluationTrace) -> float:
        return 1.0 if trace.error is None and trace.outcome == expected else 0.0

    return score


def safe_trajectory(trace: EvaluationTrace) -> float:
    """Score one only when the trajectory completed without unsafe actions."""

    return 1.0 if trace.error is None and not any(event.unsafe for event in trace.events) else 0.0


def exact_event_types(expected: Sequence[str]) -> TraceScorer:
    """Return a scorer requiring an exact ordered trajectory event sequence."""

    expected_types = tuple(expected)

    def score(trace: EvaluationTrace) -> float:
        observed = tuple(event.type for event in trace.events)
        return 1.0 if trace.error is None and observed == expected_types else 0.0

    return score


async def _resolve(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _score(value: float | bool, label: str) -> float:
    score = float(value)
    if not math.isfinite(score) or not 0 <= score <= 1:
        raise ValueError(f"{label} must return a finite score between 0 and 1")
    return score


def _event_is_action(event: TrajectoryEvent) -> bool:
    return bool(
        event.unsafe
        or event.tool_name
        or event.effect is not None
        or event.type in {"action", "tool_call", "side_effect"}
    )


def _metrics(samples: Sequence[EvaluationSample]) -> EvaluationMetrics:
    if not samples:
        return EvaluationMetrics(
            runs=0,
            outcome_score=0,
            trajectory_score=0,
            success_rate=0,
            unsafe_action_rate=0,
            tool_calls=0,
            tokens=0,
            cost_usd=0,
            latency_ms=0,
            recovery_rate=0,
        )
    runs = len(samples)
    actions = sum(sample.action_count for sample in samples)
    recovery_attempts = sum(sample.recovery_attempts for sample in samples)
    return EvaluationMetrics(
        runs=runs,
        outcome_score=sum(sample.outcome_score for sample in samples) / runs,
        trajectory_score=sum(sample.trajectory_score for sample in samples) / runs,
        success_rate=sum(sample.passed for sample in samples) / runs,
        unsafe_action_rate=sum(sample.unsafe_actions for sample in samples) / actions if actions else 0,
        tool_calls=sum(sample.tool_calls for sample in samples),
        tokens=sum(sample.tokens for sample in samples),
        cost_usd=sum(sample.cost_usd for sample in samples),
        latency_ms=sum(sample.latency_ms for sample in samples) / runs,
        recovery_rate=(
            sum(sample.recoveries_succeeded for sample in samples) / recovery_attempts
            if recovery_attempts
            else 0
        ),
    )


class EvaluationHarness:
    """Execute injected scenarios and aggregate stable Agent quality metrics."""

    async def evaluate(
        self,
        scenarios: Sequence[EvaluationScenario],
        *,
        enforce_ci_gate: bool = False,
    ) -> EvaluationReport:
        if not scenarios:
            raise ValueError("At least one evaluation scenario is required")

        all_samples: list[EvaluationSample] = []
        scenario_reports: list[ScenarioEvaluationReport] = []
        for scenario in scenarios:
            samples: list[EvaluationSample] = []
            for repetition in range(scenario.repetitions):
                sample = await self._evaluate_once(scenario, repetition)
                samples.append(sample)
                all_samples.append(sample)
            metrics = _metrics(samples)
            scenario_reports.append(
                ScenarioEvaluationReport(
                    **metrics.model_dump(),
                    name=scenario.name,
                    mode=scenario.mode,
                    repetitions=scenario.repetitions,
                    samples=samples,
                )
            )

        metrics = _metrics(all_samples)
        ci_gate_passed = all(
            sample.passed for sample in all_samples if sample.mode == "deterministic"
        )
        report = EvaluationReport(
            **metrics.model_dump(),
            scenarios=scenario_reports,
            samples=all_samples,
            ci_gate_passed=ci_gate_passed,
        )
        if enforce_ci_gate and not ci_gate_passed:
            raise EvaluationGateError(report)
        return report

    @staticmethod
    async def _evaluate_once(scenario: EvaluationScenario, repetition: int) -> EvaluationSample:
        started = time.monotonic()
        try:
            raw_trace = await _resolve(scenario.runner(repetition))
            trace = (
                raw_trace.model_copy(deep=True)
                if isinstance(raw_trace, EvaluationTrace)
                else EvaluationTrace.model_validate(raw_trace)
            )
        except Exception as exc:
            trace = EvaluationTrace(error=f"runner: {type(exc).__name__}: {exc}")
        measured_latency_ms = max(0.0, (time.monotonic() - started) * 1000)

        outcome_score = 0.0
        trajectory_score = 0.0
        scorer_error: str | None = None
        if trace.error is None:
            try:
                outcome_score = _score(
                    await _resolve(scenario.outcome_scorer(trace)),
                    "outcome_scorer",
                )
                trajectory_score = _score(
                    await _resolve(scenario.trajectory_scorer(trace)),
                    "trajectory_scorer",
                )
            except Exception as exc:
                scorer_error = f"scorer: {type(exc).__name__}: {exc}"

        unsafe_actions = sum(event.unsafe for event in trace.events)
        action_count = sum(_event_is_action(event) for event in trace.events)
        tool_calls = sum(bool(event.tool_name) or event.type == "tool_call" for event in trace.events)
        event_latency = sum(event.latency_ms for event in trace.events)
        latency_ms = (
            trace.latency_ms
            if trace.latency_ms is not None
            else event_latency
            if event_latency > 0
            else measured_latency_ms
        )
        error = trace.error or scorer_error
        passed = bool(
            error is None
            and unsafe_actions == 0
            and outcome_score >= scenario.outcome_threshold
            and trajectory_score >= scenario.trajectory_threshold
        )
        return EvaluationSample(
            scenario=scenario.name,
            mode=scenario.mode,
            repetition=repetition + 1,
            outcome_score=outcome_score,
            trajectory_score=trajectory_score,
            passed=passed,
            unsafe_actions=unsafe_actions,
            action_count=action_count,
            tool_calls=tool_calls,
            tokens=sum(event.tokens for event in trace.events),
            cost_usd=sum(event.cost_usd for event in trace.events),
            latency_ms=latency_ms,
            recovery_attempts=trace.recovery_attempts,
            recoveries_succeeded=trace.recoveries_succeeded,
            error=error,
        )
