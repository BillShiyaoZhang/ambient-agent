from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RunContext(BaseModel):
    """Explicit, serializable identity propagated through one agent step."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    session_id: str
    step_id: str
    attempt: int = Field(ge=1)
    trace_id: str
    primary_model: dict[str, Any] = Field(default_factory=dict)
    fast_model: dict[str, Any] = Field(default_factory=dict)
    artifact_hashes: dict[str, str] = Field(default_factory=dict)

    def audit_context(self, *, stage: str | None = None) -> dict[str, Any]:
        context: dict[str, Any] = {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "step_id": self.step_id,
            "attempt": self.attempt,
            "trace_id": self.trace_id,
            "artifact_hashes": dict(self.artifact_hashes),
        }
        if stage:
            context["stage"] = stage
        return context

    def tool_context(
        self,
        *,
        scopes: set[str] | None = None,
        approved_effects: set[str] | None = None,
        idempotency_key: str | None = None,
        cancellation_event: Any = None,
        on_event: Any = None,
    ) -> dict[str, Any]:
        return {
            **self.audit_context(),
            "scopes": set(scopes or ()),
            "approved_effects": set(approved_effects or ()),
            "idempotency_key": idempotency_key,
            "cancellation_event": cancellation_event,
            "on_event": on_event,
        }
