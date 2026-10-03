# Coding Agent UI Quality Evaluation

## Purpose

This is a separate, case-specific evaluation of graphical apps produced by the native `gpt-6-luna` coding-agent path. It supplements the existing generic coding-agent evaluation. The pre-existing live weather app source remains untouched; the baseline diagnosis is a wall of forecast rows with a Unicode temperature trend. It is a source baseline, not a retained screenshot or a generated evaluation artifact.

The live weather controller baseline is `workspace/apps/weather-app-8f3a/controller.js`, SHA-256 `ce29615b1442eed8aee40328204124cbfdc9489d13e5a3e3edd80142862ee905`. It requests 11 current-condition fields, 24 hourly rows, and seven daily rows, then renders `makeTrend` output as Unicode text.

The evaluation uses two prompts with different visual demands:

1. A dense seven-day forecast and service-status dashboard. It must show a data-driven SVG or CSS chart with visible geometric marks, keep extended forecast detail collapsed initially, and reveal the full fixture data on request.
2. A compact private CRUD dashboard for operational follow-ups. It must support create, read, update, and delete without needing a chart or explanatory prose.

## Execution and data boundaries

- Generate each app through production `backend.coding_agent.run_coding_agent` with native model `gpt-6-luna`, `promote=False`, an isolated temporary workspace, and the app's normal staging validator.
- Fixtures are deterministic and explicitly approved. Any network source uses a `.invalid` base URL and is answered by the browser host fixture; the browser must not make live network requests.
- Retain the generated controller and manifest unchanged. A model repair may be requested only after a recorded observable failure; the repair report must retain the initial artifact and link the repair to the failed check.
- Run generated code in the production widget frame and SDK under Chromium with the normal sandbox. Capture first-frame screenshots at 320 CSS pixels and 640 CSS pixels in both light and dark themes.
- Keep the browser host `color-scheme` and background aligned with the effective frame theme on every start, including interaction screenshots. This matters for the production frame's transparent background.
- Each report records the case-set SHA-256, artifact SHA-256, browser/verifier identity, renderer source hashes, viewport, theme, screenshot path, and observed checks. Screenshot files are excluded from the app artifact hash.

## Observable acceptance criteria

Every case must pass production staging validation, production frame startup, and static controller coverage before browser interaction. The browser report must identify the same artifact hash as the generation record.

For both cases, the verifier checks that the generated frame produces a screenshot at each specified viewport/theme pair; its document has no horizontal overflow at 320 or 640 pixels; visible interactive controls have accessible names; and an initial host or widget error is concise and does not expose a raw provider stack trace. The verifier also checks the case-specific interactions below.

The forecast dashboard must render the supplied location and current conditions, display the fixture's forecast values in a visible data-driven plot with non-empty SVG or CSS geometry, show fewer than seven full daily detail rows initially, and reveal all seven daily details when its accessible details control is activated. Changing a fixture temperature must change the plotted geometry and corresponding visible value. The host RPC log must show requests only to the approved fixture source, path, and method. Both light and dark frames must render without browser or runtime errors.

The follow-up dashboard must initially present a compact empty state and labeled entry controls, then demonstrate create, edit, and delete against private app-file storage. It must render the changed row after each successful write, and its 320-pixel layout must remain usable without horizontal scrolling. The verifier injects read and write failures: errors must stay concise, failed writes must not be reported as persisted, and Retry must persist the pending item. It is not required to contain a chart or long descriptive copy.

## Reporting and interpretation

Produce a generation report, a browser report, and a paired summary. Each case keeps the app under `case-id/app`; its browser report and screenshots are siblings so they cannot alter the app digest. The paired summary recomputes the retained app digest and verifies each renderer-source hash against the current checked-in runtime files; it rejects missing or mismatched cases, case-set hashes, required checks, screenshots, or viewport/theme pairs. It also binds production staging, static feature coverage, and the production first-frame result to the same app. Keep failed and repaired attempts as separate immutable artifact directories. Record objective check results and screenshot locations for human review.

These two prompts and their explicit checks provide evidence for these cases only. Passing them does not establish universal visual quality. Screenshots are evidence for human judgment; automated checks do not score beauty or replace review.

## Reproduction

The commands below rerun the browser checks and pairing against the retained final evidence bundle without making model calls. Run them from the current repository checkout so renderer files can be rehashed:

```sh
node scripts/verify_coding_agent_ui_quality.mjs --case forecast-operations-dashboard \
  --cases docs/verification/coding-agent-ui-quality-generated-cases.json \
  --case-root docs/verification/coding-agent-ui-quality-final-artifacts/forecast-operations-dashboard
node scripts/verify_coding_agent_ui_quality.mjs --case compact-follow-up-dashboard \
  --cases docs/verification/coding-agent-ui-quality-generated-cases.json \
  --case-root docs/verification/coding-agent-ui-quality-final-artifacts/compact-follow-up-dashboard
UV_CACHE_DIR=/tmp/ambient-ui-uv-cache uv run --offline python scripts/merge_coding_agent_ui_quality_results.py \
  --generation docs/verification/coding-agent-ui-quality-selected-generation-evidence.json \
  --browser-root docs/verification/coding-agent-ui-quality-final-artifacts \
  --output docs/verification/coding-agent-ui-quality-paired.json
```

To make a new production model run, first use `--list` to inspect cases, then provide a fresh run ID in both output paths. The runner rejects any retained target before model dispatch. Replace `UNIQUE_ID` with a new alphanumeric identifier each time:

```sh
UV_CACHE_DIR=/tmp/ambient-ui-uv-cache uv run --offline python scripts/evaluate_coding_agent_ui_quality.py --execute \
  --artifact-dir docs/verification/coding-agent-ui-quality-artifacts/run-UNIQUE_ID \
  --output docs/verification/coding-agent-ui-quality-generation-run-UNIQUE_ID.json
```

The generation runner invokes production `run_coding_agent`, stages the result without promotion, validates declared features, and captures the production first-frame result. Each final browser report lives beside its app as `browser-report.json`; the pairing report binds app and renderer hashes and checks the four standard screenshots plus interaction-state screenshots. See the [evaluation results](coding-agent-ui-quality-results.md) for this run's output and source-report lineage. The live weather controller source remains unchanged; its source hash above is a text-only baseline.
