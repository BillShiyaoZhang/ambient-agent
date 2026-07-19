"""Versioned Pydantic contract for the durable Run event stream."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ModelUsage(BaseModel):
    model_config = ConfigDict(extra="allow")

    model_turns: int = Field(default=0, ge=0)
    tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0)


class RunEventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(gt=0)
    event_id: str = Field(min_length=1)
    schema_version: int = Field(default=1, ge=1)
    stream_epoch: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    session_id: str | None = None
    step_id: str | None = None
    attempt: int | None = Field(default=None, ge=1)
    trace_id: str = Field(min_length=1)
    duration_ms: float | None = Field(default=None, ge=0)
    model_usage: ModelUsage | None = None
    redacted: bool = False
    type: str = Field(min_length=1)
    payload: Any
    created_at: datetime


class RunCreatedPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str = Field(min_length=1)


class StatusChangedPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    from_status: str = Field(alias="from", min_length=1)
    to: str = Field(min_length=1)


class StepStartedPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    step_key: str = Field(min_length=1)
    attempt: int = Field(ge=1)
    lease_epoch: int = Field(ge=0)


class StepCommittedPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    step_key: str = Field(min_length=1)
    attempt: int = Field(ge=1)
    lease_epoch: int = Field(ge=0)
    run_version: int = Field(ge=1)
    outcome: dict[str, Any]


class InteractionRequestedPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    interaction_id: str = Field(min_length=1)
    type: str = Field(min_length=1)


class InteractionResolvedPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    interaction_id: str = Field(min_length=1)
    run_version: int = Field(ge=1)
    status: str = Field(min_length=1)


class RunCreatedEvent(RunEventEnvelope):
    type: Literal["run_created"] = "run_created"
    payload: RunCreatedPayload


class StatusChangedEvent(RunEventEnvelope):
    type: Literal["status_changed"] = "status_changed"
    payload: StatusChangedPayload


class StepStartedEvent(RunEventEnvelope):
    type: Literal["step_started"] = "step_started"
    payload: StepStartedPayload


class StepCommittedEvent(RunEventEnvelope):
    type: Literal["step_committed"] = "step_committed"
    payload: StepCommittedPayload


class InteractionRequestedEvent(RunEventEnvelope):
    type: Literal["interaction_requested"] = "interaction_requested"
    payload: InteractionRequestedPayload


class InteractionResolvedEvent(RunEventEnvelope):
    type: Literal["interaction_resolved"] = "interaction_resolved"
    payload: InteractionResolvedPayload


class UnknownRunEvent(RunEventEnvelope):
    """Forward-compatible carrier for unknown types and future versions."""


CORE_RUN_EVENT_MODELS = (
    RunCreatedEvent,
    StatusChangedEvent,
    StepStartedEvent,
    StepCommittedEvent,
    InteractionRequestedEvent,
    InteractionResolvedEvent,
)
_MODEL_BY_TYPE = {model.model_fields["type"].default: model for model in CORE_RUN_EVENT_MODELS}


def parse_run_event(value: Any) -> RunEventEnvelope:
    envelope = UnknownRunEvent.model_validate(value)
    if envelope.schema_version != 1:
        return envelope
    model = _MODEL_BY_TYPE.get(envelope.type)
    return model.model_validate(value) if model is not None else envelope
