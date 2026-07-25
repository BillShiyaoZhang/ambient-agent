from __future__ import annotations

import json
import subprocess
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
QUEUE_MODULE = REPOSITORY_ROOT / "widget-runtime" / "serial_task_queue.mjs"
FRAME_GEOMETRY_MODULE = REPOSITORY_ROOT / "widget-runtime" / "frame_geometry.mjs"
PRESENTATION_CONTEXT_MODULE = REPOSITORY_ROOT / "widget-runtime" / "presentation_context.mjs"


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


def test_screencast_dimensions_use_physical_pixels_with_a_hard_cap() -> None:
    script = f"""
      import {{ screencastDimensions }} from {json.dumps(FRAME_GEOMETRY_MODULE.as_uri())};
      const retina = screencastDimensions({{
        width: 640,
        height: 480,
        deviceScaleFactor: 2,
      }});
      if (JSON.stringify(retina) !== JSON.stringify({{ maxWidth: 1280, maxHeight: 960 }})) {{
        process.exit(2);
      }}
      const capped = screencastDimensions({{
        width: 4096,
        height: 4096,
        deviceScaleFactor: 2,
      }});
      if (JSON.stringify(capped) !== JSON.stringify({{ maxWidth: 4096, maxHeight: 4096 }})) {{
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


def test_runtime_normalizes_presentation_context_and_media_preferences() -> None:
    script = f"""
      import {{
        normalizePresentationContext,
        presentationMedia,
      }} from {json.dumps(PRESENTATION_CONTEXT_MODULE.as_uri())};
      const context = normalizePresentationContext({{
        theme: {{ preference: "system", effective: "light" }},
        locale: "zh-CN",
        reduced_motion: true,
      }});
      if (JSON.stringify(context) !== JSON.stringify({{
        theme: {{ preference: "system", effective: "light" }},
        locale: "zh-CN",
        reduced_motion: true,
      }})) process.exit(2);
      if (JSON.stringify(presentationMedia(context)) !== JSON.stringify({{
        colorScheme: "light",
        reducedMotion: "reduce",
      }})) process.exit(3);
      const fallback = normalizePresentationContext({{
        theme: {{ preference: "invalid", effective: "invalid" }},
        locale: "../../bad",
        reduced_motion: "true",
      }});
      if (JSON.stringify(fallback) !== JSON.stringify({{
        theme: {{ preference: "system", effective: "dark" }},
        locale: "en-US",
        reduced_motion: false,
      }})) process.exit(4);
    """

    completed = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
