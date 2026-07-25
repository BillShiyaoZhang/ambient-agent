from __future__ import annotations

import json
import subprocess
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
QUEUE_MODULE = REPOSITORY_ROOT / "widget-runtime" / "serial_task_queue.mjs"


def test_runtime_socket_tasks_are_serialized_across_async_open_and_close() -> None:
    script = f"""
      import {{ SerialTaskQueue }} from {json.dumps(QUEUE_MODULE.as_uri())};
      const queue = new SerialTaskQueue();
      const events = [];
      let releaseOpen;
      const openGate = new Promise((resolve) => {{ releaseOpen = resolve; }});
      const opening = queue.enqueue(async () => {{
        events.push("open:start");
        await openGate;
        events.push("open:end");
      }});
      const closing = queue.enqueue(async () => {{
        events.push("close");
      }});
      await new Promise((resolve) => setImmediate(resolve));
      if (JSON.stringify(events) !== JSON.stringify(["open:start"])) process.exit(2);
      releaseOpen();
      await Promise.all([opening, closing]);
      if (JSON.stringify(events) !== JSON.stringify(["open:start", "open:end", "close"])) {{
        process.exit(3);
      }}
    """

    completed = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_runtime_socket_queue_continues_after_a_failed_message() -> None:
    script = f"""
      import {{ SerialTaskQueue }} from {json.dumps(QUEUE_MODULE.as_uri())};
      const queue = new SerialTaskQueue();
      const failed = queue.enqueue(async () => {{ throw new Error("expected"); }});
      const next = queue.enqueue(async () => "continued");
      try {{ await failed; }} catch {{}}
      if (await next !== "continued") process.exit(2);
    """

    completed = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
