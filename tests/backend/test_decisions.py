import asyncio
import copy
import json

import httpx
import pytest
from pydantic import ValidationError

from backend.agent.decisions import DecisionBundle, DecisionConfig, DecisionService, remaining_budget
from backend.agent.errors import BudgetExhaustedError
from backend.agent.jev_router import MAX_JEV_RESPONSE_BYTES, JevJSONClient, JevRouterError
from backend.agent.providers import ToolLoopBudget


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "decision-test-secret")
    for name in (
        "MODE",
        "MODEL",
        "TIMEOUT_SECONDS",
        "MIN_PROBABILITY",
        "MIN_MARGIN",
        "MAX_STATE_CHARS",
        "MAX_CANDIDATES",
        "MAX_QUESTIONS",
        "STAGE_MODES",
    ):
        monkeypatch.delenv("JEV_DECISION_" + name, raising=False)


class AuditStore:
    def __init__(self):
        self.logs = []

    def add(self, record):
        self.logs.append(record)

    def commit(self):
        pass


def _questions():
    return {
        "selection": {
            "type": "choice",
            "instructions": "Which candidate applies?",
            "criteria": {"a": "Reuse", "none": "No reuse"},
        },
        "suitable": {
            "type": "noul",
            "instructions": "Is candidate suitable?",
            "criteria": {"true": "Exact match", "false": "Not a match"},
        },
        "quality": {
            "type": "score",
            "instructions": "Rate completeness",
            "criteria": ["Missing", {"description": "Complete"}],
        },
    }


def _payload():
    return {
        "model": "jev-1.13.0",
        "answers": {
            "selection": {
                "type": "choice",
                "choice": "a",
                "probabilities": {"a": 0.98, "none": 0.02},
                "confidence": 0.96,
            },
            "suitable": {"type": "noul", "noul": 0.99},
            "quality": {
                "type": "score",
                "score": 0.9,
                "probabilities": {"0": 0.1, "1": 0.9},
                "confidence": 0.8,
                "legend": {"0": "Missing", "1": {"description": "Complete"}},
            },
        },
        "usage": {"input_tokens": 120, "output_tokens": 30},
    }


def _install(monkeypatch, payload=None, *, status=200, handler=None):
    requests = []

    def default_handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(status, content=json.dumps(payload if payload is not None else _payload()).encode())

    client = JevJSONClient(transport=httpx.MockTransport(handler or default_handler))
    monkeypatch.setattr("backend.agent.decisions.JevJSONClient", lambda: client)
    return requests


async def _evaluate(config=None, *, state=None, questions=None, **kwargs):
    return await DecisionService.evaluate(
        "schema_selection",
        state if state is not None else {"candidates": [{"id": "schema-a"}]},
        questions if questions is not None else _questions(),
        config if config is not None else DecisionConfig(mode="cascade"),
        **kwargs,
    )


def test_configuration_snapshot_is_frozen_nonsecret_and_complete():
    config = DecisionConfig.from_env()
    assert config.snapshot() == {
        "mode": "off",
        "model": "jev-1.13.0",
        "timeout_s": 1.5,
        "min_probability": 0.95,
        "min_margin": 0.15,
        "max_state_chars": 12000,
        "max_candidates": 48,
        "max_questions": 64,
        "stage_modes": {},
        "criteria_version": "ambient-decision-v1",
    }
    assert "decision-test-secret" not in json.dumps(config.snapshot())
    assert DecisionConfig.model_validate(config.snapshot()) == config
    with pytest.raises(ValidationError):
        config.mode = "cascade"
    with pytest.raises(ValidationError):
        DecisionConfig(api_key="forbidden")


def test_configuration_environment(monkeypatch):
    monkeypatch.setenv("JEV_DECISION_MODE", "shadow")
    monkeypatch.setenv("JEV_DECISION_MODEL", "jev-1.14.0")
    monkeypatch.setenv("JEV_DECISION_TIMEOUT_SECONDS", "0.7")
    monkeypatch.setenv("JEV_DECISION_MAX_CANDIDATES", "32")
    config = DecisionConfig.from_env()
    assert (config.mode, config.model, config.timeout_s, config.max_candidates) == ("shadow", "jev-1.14.0", 0.7, 32)


@pytest.mark.parametrize(
    "name,value",
    [
        ("MODE", "unknown"),
        ("MODEL", "jev-latest"),
        ("MODEL", "jev-preview"),
        ("TIMEOUT_SECONDS", "nan"),
        ("TIMEOUT_SECONDS", "0"),
        ("MIN_PROBABILITY", "1.1"),
        ("MIN_MARGIN", "-0.1"),
        ("MAX_CANDIDATES", "254"),
        ("MAX_QUESTIONS", "0"),
        ("MAX_STATE_CHARS", "1.5"),
        ("STAGE_MODES", "not-json"),
        ("STAGE_MODES", "null"),
        ("STAGE_MODES", "[]"),
        ("STAGE_MODES", '{"unknown_stage":"cascade"}'),
        ("STAGE_MODES", '{"schema_selection":"unexpected_mode"}'),
        ("STAGE_MODES", '{"schema_selection":true}'),
    ],
)
def test_invalid_configuration_environment_is_sanitized(monkeypatch, name, value):
    monkeypatch.setenv("JEV_DECISION_" + name, value)
    with pytest.raises(JevRouterError) as failure:
        DecisionConfig.from_env()
    assert str(failure.value) == "jev_decision_config_invalid"


def test_stage_overrides_copy_and_preserve_frozen_snapshot(monkeypatch):
    monkeypatch.setenv("JEV_DECISION_MODE", "off")
    monkeypatch.setenv("JEV_DECISION_STAGE_MODES", '{"intent_parameters":"cascade","schema_selection":"shadow"}')
    config = DecisionConfig.from_env()
    snapshot = config.snapshot()
    overridden = config.for_purpose("intent_parameters")
    assert overridden.mode == "cascade"
    assert config.for_purpose("schema_selection").mode == "shadow"
    assert config.for_purpose("composite_review").mode == "off"
    assert config.for_purpose("other_valid_purpose").mode == "off"
    assert config.snapshot() == snapshot
    assert DecisionConfig.model_validate(snapshot) == config
    assert overridden.stage_modes == config.stage_modes
    assert overridden.stage_modes is not config.stage_modes
    config.stage_modes["schema_selection"] = "invalid_nested_override"
    with pytest.raises(ValidationError):
        config.for_purpose("schema_selection")


@pytest.mark.parametrize("value", ["", "   ", "{}"])
def test_empty_stage_overrides_use_default_modes(monkeypatch, value):
    monkeypatch.setenv("JEV_DECISION_MODE", "shadow")
    monkeypatch.setenv("JEV_DECISION_STAGE_MODES", value)
    config = DecisionConfig.from_env()
    assert config.stage_modes == {}
    assert config.for_purpose("schema_selection").mode == "shadow"


@pytest.mark.parametrize(
    "overrides",
    [
        [],
        [("schema_selection", "cascade")],
        {"schema_selection": "CASCADE"},
        {"schema_selection": False},
        {"schema_selection": "cascade", "unexpected": "off"},
    ],
)
def test_stage_override_types_and_names_are_strict(overrides):
    with pytest.raises(ValidationError):
        DecisionConfig(stage_modes=overrides)


@pytest.mark.asyncio
async def test_evaluate_applies_stage_mode_before_http_and_binds_effective_snapshot(monkeypatch):
    requests = _install(monkeypatch)
    enabled = DecisionConfig(mode="off", stage_modes={"schema_selection": "shadow"})
    db = AuditStore()
    active = await _evaluate(enabled, db_session=db)
    disabled = await _evaluate(DecisionConfig(mode="cascade", stage_modes={"schema_selection": "off"}))
    assert active.error is None
    assert disabled.error == "jev_decision_disabled"
    assert len(requests) == 1
    recorded = json.loads(db.logs[0].response)["config"]
    assert recorded["mode"] == "shadow"
    assert recorded["stage_modes"] == {"schema_selection": "shadow"}
    assert enabled.mode == "off"
    altered_mode = await _evaluate(DecisionConfig(mode="off", stage_modes={"schema_selection": "cascade"}))
    assert altered_mode.error is None
    assert altered_mode.request_hash != active.request_hash


@pytest.mark.asyncio
async def test_typed_answers_budget_provenance_and_audit(monkeypatch):
    requests = _install(monkeypatch)
    db = AuditStore()
    calls = []
    usage = []
    bundle = await _evaluate(
        db_session=db,
        audit_context={"run_id": "run-a", "session_id": "session-a", "step_id": "schema", "attempt": 2},
        budget=ToolLoopBudget(on_model_call=lambda: calls.append(1), on_usage=usage.append),
    )
    assert bundle.error is None
    assert bundle.answers == _payload()["answers"]
    assert bundle.actual_model == "jev-1.13.0"
    assert calls == [1]
    assert usage == [bundle.usage]
    assert bundle.usage["total_tokens"] == 150
    assert bundle.usage["cost_usd"] == pytest.approx(120 * 0.042 / 1_000_000)
    assert len(bundle.state_hash) == len(bundle.request_hash) == 64
    assert set(requests[0]) == {"model", "state", "questions"}
    assert "purpose" not in requests[0]["state"]
    assert "independently" in requests[0]["questions"]["selection"]["instructions"]["boundary"]
    assert len(db.logs) == 1
    log = db.logs[0]
    assert log.stage == "decision:schema_selection"
    assert log.prompt_hash == bundle.request_hash
    assert log.run_id == "run-a" and log.attempt == 2
    assert json.loads(log.response)["state_hash"] == bundle.state_hash
    assert "decision-test-secret" not in log.model_dump_json()
    with pytest.raises(ValidationError):
        bundle.error = "changed"


@pytest.mark.parametrize(
    "probabilities,score,valid",
    [
        ([0.01, 0.0, 0.99], 1.98, True),
        ([0.0, 0.01, 0.99], 1.98, True),
        ([0.19, 0.40, 0.41], 1.23, True),
        ([0.19, 0.40, 0.41], 1.21, True),
        ([0.19, 0.40, 0.41], 1.24, False),
        ([0.19, 0.40, 0.41], 1.20, False),
        ([0.0, 0.50, 0.50], 1.52, False),
        ([1.0, 0.0, 0.0], 0.02, False),
        ([0.0, 0.0, 1.0], 1.98, False),
        ([0.193, 0.401, 0.406], 1.213, True),
        ([0.193, 0.401, 0.406], 1.22, False),
        ([0.19, 0.40, 0.41], 1.230001, False),
        ([0.19, 0.40, 0.40], 1.20, False),
        ([0.1] * 10, 4.56, True),
        ([0.1] * 10, 4.64, False),
    ],
)
@pytest.mark.asyncio
async def test_score_accepts_only_exact_or_jointly_possible_two_decimal_rounding(
    monkeypatch, probabilities, score, valid
):
    criteria = [f"Level {index}" for index in range(len(probabilities))]
    answer = {
        "type": "score",
        "score": score,
        "probabilities": {str(index): probability for index, probability in enumerate(probabilities)},
        "confidence": 0.0,
        "legend": {str(index): label for index, label in enumerate(criteria)},
    }
    payload = {**_payload(), "answers": {"coverage": answer}}
    _install(monkeypatch, payload)
    db = AuditStore()
    bundle = await _evaluate(
        questions={"coverage": {"type": "score", "instructions": "Rate coverage", "criteria": criteria}},
        db_session=db,
    )
    assert (bundle.error is None) is valid
    assert bundle.usage["total_tokens"] == 150
    assert len(db.logs) == 1
    if valid:
        # Preserve official evidence, including rounded score and zero
        # self-confidence. Score never becomes a Choice/Noul permission gate.
        assert bundle.answers["coverage"] == answer
        assert json.loads(db.logs[0].response)["answers"]["coverage"]["score"] == score
        assert not DecisionService.accepts_choice(bundle.answers["coverage"], DecisionConfig(mode="cascade"))
        assert not DecisionService.accepts_noul(bundle.answers["coverage"], DecisionConfig(mode="cascade"))
    else:
        assert bundle.error == "jev_decision_response_invalid"
        assert not bundle.answers


@pytest.mark.asyncio
async def test_hashes_bind_state_purpose_rubric_and_model_with_canonical_order(monkeypatch):
    _install(monkeypatch)
    first = await _evaluate(state={"b": 2, "a": 1})
    reordered = await _evaluate(state={"a": 1, "b": 2})
    changed_state = await _evaluate(state={"a": 1, "b": 3})
    changed_questions = _questions()
    changed_questions["selection"]["instructions"] = "A different rubric question"
    changed_rubric = await _evaluate(state={"a": 1, "b": 2}, questions=changed_questions)
    changed_purpose = await DecisionService.evaluate(
        "other", {"a": 1, "b": 2}, _questions(), DecisionConfig(mode="cascade")
    )
    assert first.request_hash == reordered.request_hash
    assert first.state_hash == reordered.state_hash
    assert changed_state.state_hash != first.state_hash
    assert changed_state.request_hash != first.request_hash
    assert changed_rubric.request_hash != first.request_hash and changed_rubric.state_hash == first.state_hash
    assert changed_purpose.request_hash != first.request_hash and changed_purpose.state_hash == first.state_hash
    changed_model = await _evaluate(DecisionConfig(mode="cascade", model="jev-1.14.0"), state={"a": 1, "b": 2})
    assert changed_model.request_hash != first.request_hash
    assert changed_model.error == "jev_response_invalid"
    assert changed_model.usage["total_tokens"] == 150


@pytest.mark.asyncio
async def test_credentials_and_unknown_response_fields_cannot_enter_request_or_audit(monkeypatch):
    payload = _payload()
    payload["headers"] = {"Authorization": "decision-test-secret"}
    payload["answers"]["suitable"]["debug"] = "decision-test-secret"
    requests = _install(monkeypatch, payload)
    db = AuditStore()
    state = {
        "request": "Classify this decision-test-secret",
        "nested": {"api_key": "other-secret", "credentials": {"password": "other"}},
    }
    bundle = await _evaluate(state=state, db_session=db, audit_context={"trace_id": "decision-test-secret"})
    assert bundle.error is None
    serialized = json.dumps(
        {
            "requests": requests,
            "bundle": bundle.model_dump(mode="json"),
            "logs": [log.model_dump(mode="json") for log in db.logs],
        }
    )
    assert "decision-test-secret" not in serialized
    assert "other-secret" not in serialized
    assert bundle.answers["suitable"] == {"type": "noul", "noul": 0.99}
    assert state["nested"]["api_key"] == "other-secret"


@pytest.mark.asyncio
async def test_off_invalid_config_and_invalid_input_never_call_http(monkeypatch):
    def no_http(request):
        pytest.fail("disabled/invalid inputs must not reach HTTP")

    _install(monkeypatch, handler=no_http)
    off = await _evaluate(DecisionConfig())
    invalid = await _evaluate({"mode": "cascade", "model": "secret-marker"})
    nonjson = await _evaluate(state={"object": object()})
    nonfinite = await _evaluate(state={"number": float("nan")})
    assert off.error == "jev_decision_disabled"
    assert invalid.error == "jev_decision_config_invalid"
    assert nonjson.error == nonfinite.error == "jev_decision_input_invalid"
    assert "secret-marker" not in invalid.model_dump_json()


@pytest.mark.asyncio
async def test_invalid_question_numbers_still_produce_a_failure_audit(monkeypatch):
    requests = _install(monkeypatch)
    db = AuditStore()
    questions = {"q": {"type": "noul", "instructions": {"number": float("nan"), "question": "Does it apply?"}}}
    result = await _evaluate(questions=questions, db_session=db)
    assert result.error == "jev_decision_input_invalid"
    assert requests == []
    assert len(db.logs) == 1
    assert json.loads(db.logs[0].prompt)["input_unavailable"] is True


@pytest.mark.asyncio
async def test_key_in_metadata_field_name_is_redacted(monkeypatch):
    requests = _install(monkeypatch)
    db = AuditStore()
    result = await _evaluate(state={"decision-test-secret": "description"}, db_session=db)
    assert result.error is None
    assert "decision-test-secret" not in json.dumps(requests)
    assert "decision-test-secret" not in db.logs[0].model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "audit_context",
    [
        {"artifact_hashes": ["invalid"]},
        {"attempt": "not-an-integer"},
        {"attempt": True},
        {"run_id": {"not": "a string"}},
        ["not-a-mapping"],
        {"artifact_hashes": {"good": "a" * 64, "invalid": "decision-test-secret"}, "trace_id": object()},
    ],
)
async def test_malformed_optional_audit_metadata_does_not_lose_paid_record(monkeypatch, audit_context):
    _install(monkeypatch)
    db = AuditStore()
    result = await _evaluate(db_session=db, audit_context=audit_context)
    assert result.error is None
    assert len(db.logs) == 1
    assert db.logs[0].usage["total_tokens"] == 150
    assert db.logs[0].attempt is None
    assert "decision-test-secret" not in db.logs[0].model_dump_json()


@pytest.mark.asyncio
async def test_http_response_is_bounded_and_stream_is_closed_after_rejection(monkeypatch):
    class LargeStream(httpx.AsyncByteStream):
        def __init__(self):
            self.closed = False
            self.reads = 0

        async def __aiter__(self):
            for _ in range(10000):
                self.reads += 1
                yield b"a" * 16384

        async def aclose(self):
            self.closed = True

    stream = LargeStream()
    _install(monkeypatch, handler=lambda request: httpx.Response(200, stream=stream))
    db = AuditStore()
    result = await _evaluate(db_session=db)
    assert result.error == "jev_response_too_large"
    assert stream.reads * 16384 <= MAX_JEV_RESPONSE_BYTES + 16384
    assert stream.closed
    assert db.logs[0].error == result.error


@pytest.mark.asyncio
async def test_json_decoding_is_inside_the_overall_timeout(monkeypatch):
    _install(monkeypatch)
    entered = asyncio.Event()

    async def slow_decode(function, *args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("backend.agent.jev_router.asyncio.to_thread", slow_decode)
    result = await _evaluate(DecisionConfig(mode="cascade", timeout_s=0.02))
    assert entered.is_set()
    assert result.error == "jev_timeout"
    assert not result.usage


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case,code",
    [
        ("state", "jev_decision_state_too_large"),
        ("candidates", "jev_decision_too_many_candidates"),
        ("questions", "jev_decision_too_many_questions"),
        ("question_size", "jev_decision_questions_too_large"),
    ],
)
async def test_input_limits_do_not_silently_truncate(monkeypatch, case, code):
    requests = _install(monkeypatch)
    state = (
        {"data": "a" * 13000} if case == "state" else {"candidates": list(range(49))} if case == "candidates" else {}
    )
    questions = _questions()
    if case == "questions":
        questions = {f"q{index}": {"type": "noul", "instructions": "Is this true?"} for index in range(65)}
    if case == "question_size":
        questions["suitable"]["instructions"] = "x" * 50000
    result = await _evaluate(state=state, questions=questions)
    assert result.error == code
    assert not requests


@pytest.mark.asyncio
async def test_topic_choices_are_not_candidate_limited_and_duplicate_lists_are_not_summed(monkeypatch):
    questions = {
        "topic": {
            "type": "choice",
            "instructions": "Which topic?",
            "criteria": {f"topic-{index}": None for index in range(60)},
        }
    }
    payload = {
        "model": "jev-1.13.0",
        "usage": _payload()["usage"],
        "answers": {
            "topic": {
                "type": "choice",
                "choice": "topic-0",
                "probabilities": {f"topic-{index}": float(index == 0) for index in range(60)},
                "confidence": 1.0,
            }
        },
    }
    requests = _install(monkeypatch, payload)
    result = await _evaluate(
        state={"app_candidates": list(range(48)), "schema_candidates": list(range(48))}, questions=questions
    )
    assert result.error is None
    assert len(requests) == 1


def _damage(payload, case):
    selection = payload["answers"]["selection"]
    quality = payload["answers"]["quality"]
    if case == "type":
        selection["type"] = "score"
    elif case == "missing_probability":
        del selection["probabilities"]["none"]
    elif case == "sum":
        selection["probabilities"]["a"] = 0.5
    elif case == "wrong_choice":
        selection["choice"] = "none"
    elif case == "nan":
        selection["probabilities"]["a"] = float("nan")
    elif case == "inf_confidence":
        selection["confidence"] = float("inf")
    elif case == "noul_nan":
        payload["answers"]["suitable"]["noul"] = float("nan")
    elif case == "noul_bool":
        payload["answers"]["suitable"]["noul"] = True
    elif case == "noul_range":
        payload["answers"]["suitable"]["noul"] = 1.2
    elif case == "score_mean":
        quality["score"] = 0.3
    elif case == "score_legend":
        quality["legend"]["1"] = "decision-test-secret"
    elif case == "score_keys":
        quality["probabilities"]["2"] = quality["probabilities"].pop("1")
    elif case == "score_nan":
        quality["score"] = float("nan")
    elif case == "score_confidence":
        quality["confidence"] = -1
    elif case == "missing_answer":
        del payload["answers"]["selection"]
    elif case == "extra_answer":
        payload["answers"]["unknown"] = {"secret": "decision-test-secret"}
    return payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "type",
        "missing_probability",
        "sum",
        "wrong_choice",
        "nan",
        "inf_confidence",
        "noul_nan",
        "noul_bool",
        "noul_range",
        "score_mean",
        "score_legend",
        "score_keys",
        "score_nan",
        "score_confidence",
        "missing_answer",
        "extra_answer",
    ],
)
async def test_invalid_typed_answers_preserve_known_usage_and_audit_only_safe_fields(monkeypatch, case):
    _install(monkeypatch, _damage(_payload(), case))
    db = AuditStore()
    usage = []
    result = await _evaluate(db_session=db, budget=ToolLoopBudget(on_usage=usage.append))
    assert result.error == "jev_decision_response_invalid"
    assert result.answers == {}
    assert result.actual_model == "jev-1.13.0"
    assert result.usage["total_tokens"] == 150
    assert usage == [result.usage]
    assert db.logs[0].error == result.error
    assert "decision-test-secret" not in db.logs[0].model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "questions",
    [
        {},
        {"q": {"type": "noul"}},
        {"q": {"type": "unknown", "instructions": "Decide"}},
        {"q": {"type": "choice", "instructions": "Decide", "criteria": {"one": "Only one"}}},
        {"q": {"type": "score", "instructions": "Rate", "criteria": ["Only one"]}},
        {"q": {"type": "noul", "instructions": "Decide", "criteria": {"maybe": "Maybe"}}},
        {"q": {"type": "noul", "instructions": "Decide", "headers": {"Authorization": "secret"}}},
    ],
)
async def test_invalid_question_schema_rejected_before_http(monkeypatch, questions):
    requests = _install(monkeypatch)
    result = await _evaluate(questions=questions)
    assert result.error == "jev_decision_questions_invalid"
    assert requests == []


def test_gates_use_probabilities_and_margin_not_provider_self_confidence():
    answer = copy.deepcopy(_payload()["answers"]["selection"])
    answer["confidence"] = 0.0
    config = DecisionConfig(mode="cascade")
    assert DecisionService.accepts_choice(answer, config)
    assert not DecisionService.accepts_choice(answer, DecisionConfig(min_probability=0.99))
    assert not DecisionService.accepts_choice(answer, DecisionConfig(min_probability=0.5, min_margin=0.99))
    assert DecisionService.accepts_noul({"type": "noul", "noul": 0.99}, config)
    assert DecisionService.accepts_noul({"type": "noul", "noul": 0.01}, config, expected=False)
    assert not DecisionService.accepts_noul({"type": "noul", "noul": 0.5}, config)
    assert not DecisionService.accepts_choice(_payload()["answers"]["quality"], config)
    assert not DecisionService.accepts_noul({"type": "noul", "noul": float("nan")}, config)


def test_remaining_budget_preserves_callbacks_and_total_wall_time(monkeypatch):
    monkeypatch.setattr("backend.agent.decisions.time.monotonic", lambda: 5.0)

    def calls():
        pass

    def usage(value):
        pass

    original = ToolLoopBudget(wall_clock_s=10, llm_call_timeout_s=9, on_model_call=calls, on_usage=usage)
    result = remaining_budget(original, 2.0)
    assert result.wall_clock_s == result.llm_call_timeout_s == 7
    assert result.on_model_call is calls and result.on_usage is usage
    assert original.wall_clock_s == 10
    assert remaining_budget(None, 2.0) is None
    with pytest.raises(BudgetExhaustedError):
        remaining_budget(original, -6.0)


@pytest.mark.asyncio
async def test_timeout_and_http_failures_become_audited_error_bundles(monkeypatch):
    async def slow(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json=_payload())

    _install(monkeypatch, handler=slow)
    db = AuditStore()
    result = await _evaluate(DecisionConfig(mode="cascade", timeout_s=0.02), db_session=db)
    assert result.error == "jev_timeout" and not result.usage
    assert db.logs[0].error == "jev_timeout"
    requests = _install(monkeypatch, {"error": "decision-test-secret"}, status=429)
    result = await _evaluate(db_session=db)
    assert result.error == "jev_http_429"
    assert len(requests) == 1
    assert "decision-test-secret" not in db.logs[-1].model_dump_json()


@pytest.mark.asyncio
async def test_missing_credentials_returns_error_without_network(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    requests = _install(monkeypatch)
    result = await _evaluate()
    assert result.error == "jev_key_missing"
    assert requests == []


@pytest.mark.asyncio
async def test_budget_callbacks_and_exhausted_wall_budget_propagate(monkeypatch):
    requests = _install(monkeypatch)

    def exhausted(*args):
        raise BudgetExhaustedError()

    with pytest.raises(BudgetExhaustedError):
        await _evaluate(budget=ToolLoopBudget(on_model_call=exhausted))
    assert requests == []
    with pytest.raises(BudgetExhaustedError):
        await _evaluate(budget=ToolLoopBudget(on_usage=exhausted))
    with pytest.raises(BudgetExhaustedError):
        await _evaluate(budget=ToolLoopBudget(wall_clock_s=0))


@pytest.mark.asyncio
async def test_usage_budget_failure_keeps_paid_audit_and_original_exception(monkeypatch):
    _install(monkeypatch)
    db = AuditStore()
    failure = BudgetExhaustedError("Token budget exhausted")

    def exhaust(usage):
        assert usage["total_tokens"] == 150
        raise failure

    with pytest.raises(BudgetExhaustedError) as caught:
        await _evaluate(db_session=db, budget=ToolLoopBudget(on_usage=exhaust))
    assert caught.value is failure
    assert failure.decision_model == "jev-1.13.0"
    assert failure.decision_usage["total_tokens"] == 150
    assert len(db.logs) == 1
    assert db.logs[0].error == "budget_exhausted"
    assert db.logs[0].usage == failure.decision_usage
    assert json.loads(db.logs[0].response)["actual_model"] == "jev-1.13.0"
    assert "decision-test-secret" not in db.logs[0].model_dump_json()


@pytest.mark.asyncio
async def test_pre_network_budget_failure_is_audited_without_invented_usage(monkeypatch):
    requests = _install(monkeypatch)
    db = AuditStore()

    def exhaust():
        raise BudgetExhaustedError()

    with pytest.raises(BudgetExhaustedError):
        await _evaluate(db_session=db, budget=ToolLoopBudget(on_model_call=exhaust))
    assert not requests
    assert len(db.logs) == 1
    assert db.logs[0].error == "budget_exhausted"
    assert not db.logs[0].usage


@pytest.mark.asyncio
async def test_cancellation_is_never_converted_to_error_bundle(monkeypatch):
    entered = asyncio.Event()

    async def slow(request):
        entered.set()
        await asyncio.Event().wait()

    _install(monkeypatch, handler=slow)
    task = asyncio.create_task(_evaluate())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_bundle_rejects_nonhash_identifiers():
    with pytest.raises(ValidationError):
        DecisionBundle(request_hash="raw-secret")
