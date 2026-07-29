from __future__ import annotations


class WorkflowError(RuntimeError):
    """A typed, user-safe failure that must not be converted into success text."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "workflow_failed",
        retryable: bool = False,
        effect_state: str = "none",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.effect_state = effect_state

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": str(self),
            "retryable": self.retryable,
            "effect_state": self.effect_state,
        }


class VerificationError(WorkflowError):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message, code="verification_failed", retryable=retryable)


class BudgetExhaustedError(WorkflowError):
    def __init__(self, message: str = "Workflow budget exhausted") -> None:
        super().__init__(message, code="budget_exhausted", retryable=False)
