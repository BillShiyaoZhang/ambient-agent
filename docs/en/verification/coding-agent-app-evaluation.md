# Coding Agent System Audit and App-Development Evaluation

This audit identified integration problems that can reduce Coding Agent effectiveness. The fixes are running in the local Backend and Widget Runtime. Actual generation and repair used the production `backend.coding_agent.run_coding_agent` ACP path with native Codex `gpt-6-luna` selected explicitly.

## Issues and fixes

| Issue | Effect on development | Fix |
| --- | --- | --- |
| Downstream stages relied on Router paraphrases | Details and constraints from the user request could disappear | Preserve the complete original request through single-App planning, schema work, and coding; keep composite and slash steps scoped |
| Planning demanded an extremely short, UI-only description | Persistence, interactions, recovery, and acceptance criteria could be omitted | Cover applicable behavior, data, persistence, states, and observable acceptance criteria |
| Repair prompts omitted the original request and required features | Repair could lose the intended functional objective | Carry the request, approved plan, and required-feature criteria into bounded repair context |
| Schema fallback read only the first 8,000 characters | Findings in the controller tail could be missed | Review complete source and reject inputs over 2 MiB rather than declaring partial source clean |
| Node 26 warnings mixed with verifier output | Specific findings became generic failures | Extract bounded structured diagnostics from stdout and stderr |
| Component guidance omitted the List interface | Generated interactive rows were ignored despite successful file writes | Document text-only List/Table interfaces and use Row children inside Column for interactive records |
| Smoke Runtime lacked native storage/lifecycle APIs | Valid draft-storage code was rejected | Expose bounded ephemeral storage and lifecycle registration only in host-enabled smoke sessions |
| Missing files lacked a distinct error code | First use could be treated as a permission or loading failure | Return `file_not_found` after authorization; retain denial for out-of-scope reads and visible errors for other failures |
| Default state could write before async hydration | Saved drafts could be overwritten by empty values | Require hydration to complete before write-through |
| Timeout validation checked positivity alone | NaN/Infinity could escape validation | Require finite positive timeouts in ACP launch and compatibility paths |

The audit confirmed the model-selection path from `native_model` to the Codex launch configuration. Coding ACP and the main Agent's native inference transport are separate paths. Staging, allowed-file, Manifest, schema, and capability authorization constraints remain enforced.

## Observed app results (2026-10-03)

The initial batch generated three cases. Two subsequent model repair turns targeted the private reading list, for five actual production ACP model calls. Every generation used an isolated workspace and `promote=False`. Generated app code was not patched manually.

| Case | Final first frame | Browser checks | Behavior covered |
| --- | --- | --- | --- |
| Private reading list | Passed, 640×480 | 14 passed | First-use empty state, CRUD, read/unread, file contents, reload, read retry, and write errors |
| Graph task board | Passed, 640×480 | 14 passed | Task subscription, CRUD, completion, filtering, reload, draft restoration, and Graph errors |
| Service status | Passed, 640×480 | 10 passed | Approved request, loading, success, empty response, failure retry, and malformed-data rejection |

The final result is **3/3 complete passes**. The merger requires successful production staging validation and matching artifact SHA-256 values across generation, first-frame, and browser evidence. Hashes cover controller, Manifest, optional README, and data files, excluding test screenshots and reports. Changed code cannot inherit an older passing interaction result.

The initial reading list sent a successful `files.write`, but records were invisible because List ignores children. Repair attempt 1 corrected the row layout. Its temporary test workspace lacked the verifier script, producing the operator failure `widget_verifier_unavailable`. The artifact was preserved and subsequently passed independent static and feature-declaration checks; the browser then found a genuine first-use bug: missing books.json was treated as a load failure. After the typed file-error contract and guidance were added, repair attempt 2 passed all checks. JSON `repair_attempts: 0` means no automatic repair inside that particular ACP call; it does not erase the two later model turns.

The board's initial storage use followed the SDK, but the old smoke Runtime rejected it. The same retained source passed first-frame and browser checks after the Runtime update. Smoke storage exists only in memory for one verification, with at most 256 keys and 1 MiB of JSON values. Browser state fixtures separately verify reload and persistence behavior.

## Evidence and reproduction

- [Final results and hashes](../../verification/coding-agent-app-evaluation-results-final.json), [final first-frame evidence](../../verification/coding-agent-app-evaluation-smoke-final.json).
- [Reading-list source](../../verification/coding-agent-app-evaluation-final-artifacts/private-reading-list/controller.js), [task-board source](../../verification/coding-agent-app-evaluation-final-artifacts/project-task-board/controller.js), [service-status source](../../verification/coding-agent-app-evaluation-final-artifacts/service-status/controller.js).
- [Reading-list interactions](../../verification/coding-agent-app-evaluation-final-interactions/private-reading-list/interaction-result.json), [task-board interactions](../../verification/coding-agent-app-evaluation-final-interactions/project-task-board/interaction-result.json), [service-status interactions](../../verification/coding-agent-app-evaluation-final-interactions/service-status/interaction-result.json).
- [Initial batch](../../verification/coding-agent-app-evaluation-results-initial.json), [initial failing list source](../../verification/coding-agent-app-evaluation-artifacts/private-reading-list/controller.js), [repair attempt 1](../../verification/coding-agent-app-evaluation-repair-attempt-1.json), [repair attempt 2](../../verification/coding-agent-app-evaluation-repair-attempt-2.json).

Full Backend regression: **2,015 passed, 16 skipped**, with one existing Starlette deprecation warning. The final evaluation and prompt targeted run also passed all 29 tests. Widget Runtime: **52/52 passed**, using the Compose `init` setting, production resource limits, no network, and Chromium's sandbox. Omitting `--init` from the disposable test container caused a cumulative Chromium multi-session failure; matching Compose resolved it. Ruff, documentation links and bilingual structure, event types, and UML contracts also passed.

Listing cases makes no model calls:

```sh
python scripts/evaluate_coding_agent_apps.py --list
```

`--execute` in a Backend environment with managed Codex login and the ACP bridge performs real model calls. Browser verification uses `scripts/verify_coding_agent_apps.mjs` and requires Chromium and Widget Runtime dependencies. Retained evidence can be checked directly:

```sh
python scripts/merge_coding_agent_eval_results.py --batch docs/verification/coding-agent-app-evaluation-batch-final.json --artifact-root docs/verification/coding-agent-app-evaluation-final-artifacts --interaction-root docs/verification/coding-agent-app-evaluation-final-interactions --smoke docs/verification/coding-agent-app-evaluation-smoke-final.json --output /tmp/coding-agent-evaluation-confirmed.json
```

## Scope and limitations

The browser uses the production native frame and SDK with deterministic host fixtures for files, Graph, and networking. The network source has an `.invalid` address and no live upstream is contacted, so these results do not establish external-service availability. A first frame does not prove persistence or suspend flushing. This was one three-case batch and two targeted repair turns, with no direct Codex comparison; it does not establish statistical performance, parity, or superiority.
