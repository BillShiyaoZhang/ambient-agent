from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from backend.app_manifest import AppManifest
from backend.coding_agent_acp import CodingAgentStagedResult
from backend.coding_agent_repair import finding_from_exception
from backend.graph_db import GraphDatabase
from backend.widget_runtime_smoke import (
    WidgetRuntimeSmokeError,
    WidgetRuntimeSmokeTester,
)


class FakeRuntimeConnection:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self.messages: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        for message in messages:
            self.messages.put_nowait(message)
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    async def send_json(self, message: dict[str, Any]) -> None:
        self.sent.append(message)

    async def receive_json(self) -> dict[str, Any]:
        return await self.messages.get()

    async def close(self) -> None:
        self.closed = True


def staged_result(tmp_path: Path) -> CodingAgentStagedResult:
    staging = tmp_path / ".notes-app.staging-00000000000000000000000000000000"
    live = tmp_path / "notes-app"
    staging.mkdir()
    (staging / "controller.js").write_text(
        "export default function App() { return ambient.html`<div>Ready</div>`; }",
        encoding="utf-8",
    )
    AppManifest.from_dict(
        {
            "manifest_version": 2,
            "id": "notes-app",
            "title": "Notes",
            "description": "",
            "app_version": "0.1.0",
            "intents": [],
            "schema_refs": [],
            "capabilities": [],
        },
        expected_app_id="notes-app",
    ).write_atomic(staging / "manifest.json")
    return CodingAgentStagedResult(
        output="",
        app_id="notes-app",
        staging_dir=staging,
        live_dir=live,
    )


@pytest.mark.asyncio
async def test_smoke_tester_requires_a_real_first_frame(tmp_path: Path) -> None:
    connection = FakeRuntimeConnection(
        [
            {"type": "ready"},
            {
                "type": "frame",
                "format": "jpeg",
                "data": "ZmFrZQ==",
                "width": 640,
                "height": 480,
            },
        ]
    )
    tester = WidgetRuntimeSmokeTester(
        graph_db=GraphDatabase(str(tmp_path / "graph.db")),
        connector=lambda: connection,
        timeout_seconds=1,
    )

    result = await tester.verify(staged_result(tmp_path))

    assert result["status"] == "rendered"
    assert result["frame"]["width"] == 640
    assert connection.sent[0]["type"] == "start"
    assert connection.sent[0]["app_id"] == "notes-app"
    assert connection.sent[-1]["type"] == "close"
    assert connection.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("classification", "expected_code", "expected_repairability"),
    [
        ("code_only", "widget_runtime_code_error", "code_only"),
        ("authorization_or_design", "design_change_required", "design_change"),
        ("operator", "widget_runtime_unavailable", "operator"),
        ("abuse_or_budget", "widget_runtime_budget_exceeded", "operator"),
    ],
)
async def test_smoke_error_classification_controls_repair_authority(
    tmp_path: Path,
    classification: str,
    expected_code: str,
    expected_repairability: str,
) -> None:
    connection = FakeRuntimeConnection(
        [
            {
                "type": "runtime_error",
                "error": {
                    "code": "controller_failed",
                    "message": "boom",
                    "classification": classification,
                },
            }
        ]
    )
    tester = WidgetRuntimeSmokeTester(
        graph_db=GraphDatabase(str(tmp_path / "graph.db")),
        connector=lambda: connection,
        timeout_seconds=1,
    )

    with pytest.raises(WidgetRuntimeSmokeError) as caught:
        await tester.verify(staged_result(tmp_path))

    assert caught.value.code == expected_code
    finding = finding_from_exception(caught.value, attempt=1, artifact_revision="hash")
    assert finding.repairability == expected_repairability
