"""Per-task model selection snapshots for concurrent agent runs."""

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator

from backend.llm_config import ModelSelection


_primary_model: ContextVar[ModelSelection | None] = ContextVar("primary_llm_model", default=None)
_fast_model: ContextVar[ModelSelection | None] = ContextVar("fast_llm_model", default=None)


@contextmanager
def use_model_selections(primary: ModelSelection, fast: ModelSelection | None = None) -> Iterator[None]:
    primary_token = _primary_model.set(primary)
    fast_token = _fast_model.set(fast or primary)
    try:
        yield
    finally:
        _fast_model.reset(fast_token)
        _primary_model.reset(primary_token)


def primary_selection() -> ModelSelection | None:
    return _primary_model.get()


def fast_selection() -> ModelSelection | None:
    return _fast_model.get() or _primary_model.get()


def selection_ids(selection: ModelSelection | None) -> tuple[str, str]:
    if selection:
        return selection.provider_id, selection.model_id
    return "unconfigured", "unconfigured"
