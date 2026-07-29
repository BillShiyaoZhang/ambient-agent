import json
from datetime import UTC, datetime, timedelta

import pytest

from backend.agent.providers import OllamaProvider
from backend.agent.tools import registry
from backend.models import ChatMessage, LLMAuditLog
from backend.workspace_storage import AUDIT_TEXT_MAX_BYTES, WorkspaceStorage


def test_audit_jsonl_round_trips_trace_metadata_and_reads_legacy_rows(tmp_path):
    storage = WorkspaceStorage(str(tmp_path / "workspace"))
    audit_path = tmp_path / "workspace" / "audit_logs.jsonl"
    old_timestamp = (datetime.now(UTC) - timedelta(days=1)).replace(tzinfo=None)
    audit_path.write_text(
        json.dumps(
            {
                "id": 1,
                "timestamp": old_timestamp.isoformat(),
                "provider": "legacy",
                "model": "old-model",
                "prompt": "old prompt",
                "response": "old response",
            }
        )
        + "\nnot-json\n",
        encoding="utf-8",
    )

    storage.add(
        LLMAuditLog(
            provider="openai",
            model="new-model",
            prompt="new prompt",
            response="new response",
            stage="verify",
            run_id="run-1",
            session_id="session-1",
            step_id="step-1",
            attempt=2,
            trace_id="trace-1",
            latency_ms=12.5,
            usage={"input_tokens": 3, "output_tokens": 4},
            finish_reason="stop",
            prompt_hash="a" * 64,
            tool_schema_hash="b" * 64,
            artifact_hashes={"app-1": "c" * 64},
        )
    )
    storage.commit()

    logs = storage.get_audit_logs()
    assert len(logs) == 2
    current = logs[0]
    assert current.run_id == "run-1"
    assert current.session_id == "session-1"
    assert current.step_id == "step-1"
    assert current.attempt == 2
    assert current.trace_id == "trace-1"
    assert current.latency_ms == 12.5
    assert current.usage == {"input_tokens": 3, "output_tokens": 4}
    assert current.finish_reason == "stop"
    assert current.prompt_hash == "a" * 64
    assert current.tool_schema_hash == "b" * 64
    assert current.artifact_hashes == {"app-1": "c" * 64}

    legacy = logs[1]
    assert legacy.prompt == "old prompt"
    assert legacy.stage == "chat"
    assert legacy.run_id is None
    assert legacy.usage is None
    assert legacy.artifact_hashes == {}


def test_storage_bounds_audit_previews_and_deduplicates_run_projection(tmp_path):
    storage = WorkspaceStorage(str(tmp_path / "workspace"))
    oversized = "界" * AUDIT_TEXT_MAX_BYTES
    storage.add(LLMAuditLog(provider="test", model="test", prompt=oversized, response=oversized))
    first = ChatMessage(session_id="s1", run_id="r1", role="agent", sender="agent", content="first")
    storage.add(first)
    storage.commit()

    replay = ChatMessage(session_id="s1", run_id="r1", role="agent", sender="agent", content="replayed")
    storage.add(replay)
    storage.commit()

    raw_log = json.loads((tmp_path / "workspace" / "audit_logs.jsonl").read_text(encoding="utf-8"))
    assert len(raw_log["prompt"].encode("utf-8")) <= AUDIT_TEXT_MAX_BYTES
    assert "[truncated" in raw_log["prompt"]
    messages = storage.get_messages("s1")
    assert len(messages) == 1
    assert messages[0].run_id == "r1"
    assert messages[0].content == "replayed"
    assert replay.id == first.id


def test_audit_retention_discards_expired_and_corrupt_entries(tmp_path):
    storage = WorkspaceStorage(str(tmp_path / "workspace"))
    audit_path = tmp_path / "workspace" / "audit_logs.jsonl"
    expired = (datetime.now(UTC) - timedelta(days=60)).isoformat()
    current = datetime.now(UTC).isoformat()
    audit_path.write_text(
        "\n".join(
            [
                json.dumps({"timestamp": expired, "provider": "old"}),
                "not-json",
                json.dumps({"timestamp": current, "provider": "current"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert storage.cleanup_audit_logs(days=30) == 2
    retained = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert [item["provider"] for item in retained] == ["current"]


@pytest.mark.asyncio
async def test_provider_records_trace_usage_hashes_and_redacts_sensitive_tool_arguments(tmp_path, monkeypatch):
    storage = WorkspaceStorage(str(tmp_path / "workspace"))
    secret = "super-secret-token"
    calls = 0

    @registry.register(sensitive_fields={"token"})
    def audit_sensitive_tool(token: str, label: str) -> str:
        return f"accepted:{label}:{len(token)}"

    async def mock_call_llm_api(provider, model, messages, tools=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "content": f"Using {secret}",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "audit_sensitive_tool",
                            "arguments": json.dumps({"token": secret, "label": "demo"}),
                        },
                    }
                ],
                "usage": {"input_tokens": 5},
                "finish_reason": "tool_calls",
            }
        return {
            "content": f"Completed without repeating {secret}",
            "tool_calls": None,
            "usage": {"output_tokens": 7},
            "finish_reason": "stop",
        }

    monkeypatch.setattr("backend.llm_service.call_llm_api", mock_call_llm_api)
    provider = OllamaProvider(model="trace-model")
    tools = registry.get_tool_schemas()
    try:
        result = await provider.generate(
            [{"role": "user", "content": f"Use token {secret}"}],
            db_session=storage,
            tools=tools,
            tool_context={"session_id": "fallback-session"},
            audit_context={
                "run_id": "run-7",
                "session_id": "session-7",
                "step_id": "step-7",
                "attempt": 3,
                "trace_id": "trace-7",
                "stage": "agent_loop",
                "artifact_hashes": {"app-7": "d" * 64},
            },
        )
    finally:
        registry.unregister("audit_sensitive_tool")

    assert result.startswith("Completed")
    logs = storage.get_audit_logs()
    assert len(logs) == 2
    assert {log.finish_reason for log in logs} == {"tool_calls", "stop"}
    assert {tuple(sorted((log.usage or {}).items())) for log in logs} == {
        (("input_tokens", 5),),
        (("output_tokens", 7),),
    }
    for log in logs:
        assert log.run_id == "run-7"
        assert log.session_id == "session-7"
        assert log.step_id == "step-7"
        assert log.attempt == 3
        assert log.trace_id == "trace-7"
        assert log.stage == "agent_loop"
        assert log.latency_ms is not None and log.latency_ms >= 0
        assert log.prompt_hash is not None and len(log.prompt_hash) == 64
        assert log.tool_schema_hash is not None and len(log.tool_schema_hash) == 64
        assert log.artifact_hashes == {"app-7": "d" * 64}
        assert secret not in log.prompt
        assert secret not in log.response
    assert _REDACTION_MARKER in "\n".join(log.prompt + log.response for log in logs)
    assert secret not in (tmp_path / "workspace" / "audit_logs.jsonl").read_text(encoding="utf-8")


_REDACTION_MARKER = "[REDACTED]"


@pytest.mark.asyncio
async def test_provider_audits_failed_model_call_with_context_fallback(tmp_path, monkeypatch):
    storage = WorkspaceStorage(str(tmp_path / "workspace"))

    async def failed_call(provider, model, messages, tools=None):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr("backend.llm_service.call_llm_api", failed_call)
    provider = OllamaProvider(model="trace-model")
    with pytest.raises(RuntimeError, match="provider exploded"):
        await provider.generate(
            [{"role": "user", "content": "hello"}],
            db_session=storage,
            tool_context={
                "run_id": "fallback-run",
                "session_id": "fallback-session",
                "step_id": "fallback-step",
                "trace_id": "fallback-trace",
            },
        )

    [log] = storage.get_audit_logs()
    assert log.run_id == "fallback-run"
    assert log.session_id == "fallback-session"
    assert log.step_id == "fallback-step"
    assert log.trace_id == "fallback-trace"
    assert log.error == "RuntimeError: provider exploded"
    assert log.response == ""
    assert log.latency_ms is not None
    assert log.prompt_hash is not None
