import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.run_events import (
    CORE_RUN_EVENT_MODELS,
    InteractionResolvedEvent,
    RunCreatedEvent,
    StepCommittedEvent,
    UnknownRunEvent,
    parse_run_event,
)
from scripts.generate_run_event_types import GENERATED_PATH, render_typescript
from scripts import generate_run_event_types


FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "run_events_v1.json"


def _golden_tape() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_golden_v1_tape_validates_known_events_and_preserves_unknown_event():
    tape = _golden_tape()
    parsed = [parse_run_event(event) for event in tape["events"]]

    assert isinstance(parsed[0], RunCreatedEvent)
    assert isinstance(parsed[3], StepCommittedEvent)
    assert isinstance(parsed[5], InteractionResolvedEvent)
    assert isinstance(parsed[6], UnknownRunEvent)
    assert parsed[6].payload == {"opaque": True, "projection_version": 2}
    assert [event.sequence for event in parsed] == list(range(1, 8))
    assert {event.stream_epoch for event in parsed} == {tape["stream_epoch"]}
    assert {event.schema_version for event in parsed} == {1}


def test_known_event_contract_is_fail_closed_but_unknown_type_is_forward_compatible():
    event = _golden_tape()["events"][0]
    malformed = {**event, "payload": {}}
    with pytest.raises(ValidationError):
        parse_run_event(malformed)

    future_version = {**event, "schema_version": 2, "payload": {"future": True}}
    parsed = parse_run_event(future_version)
    assert isinstance(parsed, UnknownRunEvent)
    assert parsed.schema_version == 2

    invalid_envelope = {**event, "sequence": 0}
    with pytest.raises(ValidationError):
        parse_run_event(invalid_envelope)


def test_core_event_registry_has_unique_discriminators():
    event_types = [model.model_fields["type"].default for model in CORE_RUN_EVENT_MODELS]
    assert event_types == [
        "run_created",
        "status_changed",
        "step_started",
        "step_committed",
        "interaction_requested",
        "interaction_resolved",
    ]
    assert len(event_types) == len(set(event_types))


def test_generated_typescript_is_current():
    assert GENERATED_PATH.read_text(encoding="utf-8") == render_typescript()


def test_generated_typescript_check_mode_is_read_only(tmp_path, monkeypatch):
    generated = tmp_path / "run-events.generated.ts"
    monkeypatch.setattr(generate_run_event_types, "GENERATED_PATH", generated)
    generated.write_text(render_typescript(), encoding="utf-8")

    generate_run_event_types.main(["--check"])

    generated.write_text("stale\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="out of date"):
        generate_run_event_types.main(["--check"])
    assert generated.read_text(encoding="utf-8") == "stale\n"
