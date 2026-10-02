"""Re-run the production first-frame Widget Runtime smoke against retained artifacts."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from backend.coding_agent_acp import CodingAgentStagedResult
from backend.graph_db import GraphDatabase
from backend.widget_runtime_smoke import WidgetRuntimeSmokeTester


def artifact_hash(directory: Path) -> str:
    digest = hashlib.sha256()
    files = [directory / name for name in ("controller.js", "manifest.json", "README.md")]
    data_dir = directory / "data"
    if data_dir.is_dir():
        files.extend(data_dir.rglob("*"))
    for item in sorted(file for file in files if file.is_file() and not file.is_symlink()):
        digest.update(item.relative_to(directory).as_posix().encode())
        digest.update(item.read_bytes())
    return digest.hexdigest()


async def smoke(artifact_root: Path, app_ids: list[str]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="ambient-widget-smoke-eval-") as temp:
        graph = GraphDatabase(str(Path(temp) / "graph"))
        tester = WidgetRuntimeSmokeTester(graph_db=graph)
        results = []
        try:
            for app_id in app_ids:
                artifact = artifact_root / app_id
                staged = CodingAgentStagedResult(
                    output="",
                    app_id=app_id,
                    staging_dir=artifact,
                    live_dir=artifact.parent / app_id,
                    artifact_hash=artifact_hash(artifact),
                )
                try:
                    frame = await tester.verify(staged)
                    results.append(
                        {
                            "id": app_id,
                            "status": "passed",
                            "artifact_hash": staged.artifact_hash,
                            "width": frame["frame"].get("width"),
                            "height": frame["frame"].get("height"),
                        }
                    )
                except Exception as exc:
                    results.append(
                        {
                            "id": app_id,
                            "status": "failed",
                            "artifact_hash": staged.artifact_hash,
                            "error_code": str(getattr(exc, "code", type(exc).__name__)),
                            "runtime_code": str(getattr(exc, "runtime_code", "")),
                            "classification": str(getattr(exc, "classification", "")),
                        }
                    )
        finally:
            graph.close()
    return {"executor": "WidgetRuntimeSmokeTester", "results": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--app", action="append", required=True, dest="apps")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(smoke(args.artifact_root, args.apps))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if all(item["status"] == "passed" for item in result["results"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
