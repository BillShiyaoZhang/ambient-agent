# Chat and Run Information Experience

> Status: Phases A–D are implemented. This document records the information architecture, event contracts, and current implementation.

## 1. Goals and principles

The current chat projects phase notices, Coding Agent text, tool calls, verification reports, approvals, and final results as equivalent message bubbles. Users cannot easily tell what the system is doing, whether they need to act, or what the final deliverable is.

The new experience follows four principles:

1. **Results first**: the main conversation emphasizes user input, the current task card, and the final answer.
2. **Inspectable process**: tools, verification, automatic repair, and timing live in a collapsed-by-default Run timeline without losing auditability.
3. **Explicit interaction**: approval, permission, and input requests use dedicated action cards instead of natural-language logs.
4. **Streaming/durability separation**: real-time deltas provide responsiveness while durable Run events own replay, recovery, and final consistency.

Codex organizes long tasks into separate threads and keeps change review inside the thread; Codex cloud also emphasizes real-time progress, terminal/test evidence, and post-completion review. Task state, process evidence, and final results should therefore be separate levels instead of equally weighted messages. [Codex app](https://openai.com/index/introducing-the-codex-app/) · [Codex cloud](https://openai.com/index/introducing-codex/)

GitHub Copilot Agent similarly separates session overview, live session logs, tool/validation evidence, and final review. Its SDK explicitly distinguishes real-time, non-persisted ephemeral deltas from complete, replayable persisted events. That split fits Ambient's existing durable RunStore. [Agent sessions](https://docs.github.com/en/enterprise-cloud%40latest/copilot/how-tos/copilot-on-github/use-copilot-agents/manage-and-track-agents) · [Streaming events](https://docs.github.com/en/copilot/how-tos/copilot-sdk/use-copilot-sdk/streaming-events)

## 2. Problems in the current path

The current implementation has four concrete limits:

- `Message` only has `sender/content/timestamp`; it cannot represent phases, activities, artifacts, approvals, or errors.
- The old implementation concatenated ACP `agent_message_chunk` and tool updates into one string, then projected it as an ordinary `reply` with a negative sentinel ID; the frontend could replace only one pending message.
- Durable reducer `_emit()` first writes the step event buffer and projects only after step commit. Even when the Coding Agent calls back per chunk, users receive a burst at phase completion rather than true streaming.
- `/ws/runs` already supports sequence, epoch, replay, and deduplication, but chat consumes only selected business payloads instead of projecting `step_started`, `progress`, and `step_committed` into readable task state.

Changing bubble CSS is therefore insufficient. A chat projection model must come first, followed by true live deltas.

## 3. Recommended information hierarchy

The main conversation has only three top-level item types:

| Top-level item | Default presentation | Purpose |
| --- | --- | --- |
| User message | Full | User intent and later steering |
| Run card | Expanded summary while active, collapsed after completion | Current phase, elapsed time, user-attention state, and process entry point |
| Final answer/deliverable | Full | Result, validation evidence, and App/Artifact actions |

Secondary activities inside a Run card are grouped semantically:

- understanding and planning;
- Schema/capability alignment;
- file generation or modification;
- tool execution;
- independent verification;
- automatic repair;
- publication and delivery.

Repeated low-value events are aggregated. For example, 18 file reads become “Inspected 12 files · 18 operations” instead of occupying 18 rows. Failed tools, changed files, test results, and permission actions remain individually visible.

Raw model chain-of-thought is not displayed. Activities contain only verifiable action summaries, tools, bounded inputs/outputs, and results.

## 4. Interaction sketch

While running:

```text
You
Build a full weather App

Ambient · Building weather-app                            02:14
✓ Plan   ✓ Schema   ↻ Code   · Verify   · Publish

  Repairing verification issue (attempt 4)
  graph query type must be a string literal

  [View process 12]                                      [Stop]
```

After success:

```text
Ambient · Completed
The weather App was generated and passed static, Schema, and runtime smoke checks.

[Open App] [View validation] [View process]
```

Repeated-error stall:

```text
Ambient · Automatic repair stopped
The same verifier finding repeated, so another identical attempt would not add information.
The draft was retained and was not published.

Error: Unexpected token (1100:3)
[View error details] [Continue with specific guidance]
```

## 5. Streaming event model

Keep two event lanes.

### 5.1 Durable lane

Durable events are written to SQLite before delivery and support epoch/sequence replay:

- `run_phase_started` / `run_phase_completed`;
- `activity_completed`;
- `assistant_message_completed`;
- `interaction_requested` / `interaction_resolved`;
- `artifact_ready`;
- `run_succeeded` / `run_failed`.

Existing `step_started`, `step_committed`, and business payloads can initially map to these UI semantics through a projection adapter without an immediate database-schema migration.

### 5.2 Live lane

Live events are not chat history and may be discarded. They provide in-progress feedback:

- `assistant_message_delta`;
- `activity_delta`;
- `tool_progress`;
- `run_heartbeat`.

Current v1 envelope (carried by `run_live_event.event` on `/ws/run-live?session_id=...`):

```json
{
  "schema_version": 1,
  "run_id": "run-id",
  "session_id": "session-id",
  "step_id": "stage_code",
  "attempt": 1,
  "stream_id": "run-id:stage_code:1:activity",
  "chunk_sequence": 17,
  "kind": "activity_delta",
  "delta": "Inspecting controller.js",
  "replace": false,
  "created_at": "2026-07-26T06:00:00Z"
}
```

Rules:

1. Append idempotently by `stream_id + chunk_sequence`; discard duplicates.
2. Live deltas update only `liveStreams`, never persisted `messages`.
3. A matching completed event atomically replaces the live buffer with durable content.
4. On disconnect, discard incomplete deltas and replay durable events from `/ws/runs`; never persist half-streamed text as a final answer.
5. Losing live deltas on a backend crash is acceptable because Run checkpoints and completed events are the correctness source.
6. Coalesce chunks per animation frame or every 40–60 ms to avoid one React render per token.

## 6. Frontend projection state

Replace the current `Message[]` with a projection store:

```ts
interface ConversationProjection {
  items: ConversationItem[];
  runs: Record<string, RunCardState>;
  liveStreams: Record<string, LiveStreamState>;
  liveStepWatermarks: Record<string, number>;
  interactions: Record<string, InteractionCardState>;
}

type ConversationItem =
  | UserMessageItem
  | RunCardItem
  | FinalAnswerItem
  | ArtifactItem;
```

A `run_id` appears at most once as a top-level Run card. `step_id + activity_id` deduplicates secondary activities; `event_id` and the Run stream cursor continue to deduplicate durable replay.

`mergeIncomingMessage()` now accepts only user messages and final answers with positive persisted integer IDs. In-progress state is represented entirely by the Run projection.

## 7. Streaming rendering and scrolling

- Auto-follow only when the user is within 80 px of the bottom. Once they scroll upward, stop stealing position and show a “New progress” button.
- Text deltas may use a subtle caret; activities should not use token-by-token typing animation.
- Render Markdown from a chunk buffer. Keep an incomplete code fence as a temporary code block and apply final syntax highlighting after completion.
- `aria-live` announces phase changes, user-attention requests, completion, and failure only—not every token.
- Reduced-motion mode disables displacement animation and nonessential streaming motion.
- A collapsed completed Run card retains status, elapsed time, repair count, and validation summary.

## 8. Approvals, errors, and automatic repair

Approvals are structured cards:

- ordinary plan/Schema choices may be inline;
- permission, irreversible action, or large diff cards show a summary and open the existing blocking dialog;
- resolved interactions become read-only history instead of disappearing.

Errors have two layers:

1. an actionable natural-language summary;
2. details with stable code, stage, bounded diagnostic, attempt, and artifact revision.

Automatic repair updates one activity:

```text
Independent verification → failed → automatic repair 1 → reverify → automatic repair 2
```

Show the latest finding by default and keep prior findings in the expanded timeline. When an exact finding stalls the loop, the primary card must state that repair stopped, nothing was published, and the draft was retained instead of encouraging an unconditional `/repair`.

## 9. Rollout order

### Phase A: projection model

- Add `ConversationProjection` and `RunCard`, initially consuming existing durable events.
- Move phase notices and old accumulated logs into the activity timeline.
- Change the desktop chat overlay default to about 432 × 600 px and add viewport-constrained resizing with persisted dimensions.
- Preserve current reply/widget/approval API compatibility.

### Phase B: true live deltas

- Add a separate live WebSocket projection for ACP callbacks that bypasses the reducer commit buffer.
- Add `stream_id/chunk_sequence`, throttling, coalescing, and completed replacement.
- Test disconnect, replay, duplicate, out-of-order, and session-switch behavior.

Implemented behavior:

- The backend projects `assistant_message_delta`, `activity_delta`, and `tool_progress` through session-scoped bounded in-memory queues. Slow clients drop only their oldest ephemeral events and never block the workflow.
- ACP cumulative snapshots are converted to deltas at the workflow boundary. A reset snapshot uses `replace=true` instead of duplicating prior text.
- The frontend coalesces events every 50 ms, deduplicates by `stream_id + chunk_sequence`, and ignores out-of-order fragments. Sequence gaps are surfaced as a reminder that durable events will reconcile the final state.
- Durable `step_committed`, final replies, waiting states, and terminal states clear the matching live buffer. Disconnects and session switches discard incomplete buffers before `/ws/runs` restores factual state.

### Phase C: structured activities and interactions

- Convert tool call, verification, repair, artifact, and approval output to typed events.
- Keep high-risk approval in blocking dialogs while moving ordinary choices inline.
- Add a debug mode for bounded raw event JSON.

Implemented behavior:

- Run protocol v1 now registers `activity_updated` and `artifact_ready`, plus the existing `tool_started`, `tool_succeeded`, `tool_failed`, and `tool_cancelled` events. The legacy `/ws/chat` projection order is unchanged.
- `activity_updated` updates a stable `activity_id` in place for plan, schema, code, verification, repair, artifact, and approval work. Tools aggregate by `phase + tool` instead of occupying one top-level row per call.
- Plan, schema, and verification interactions show summaries and common actions inside the Run card. The full dialog opens only when the user explicitly chooses review/edit. Sensitive OpenCode and Backend/MCP permissions remain blocking dialogs.
- Resolved and cancelled interactions remain as read-only records. Refresh restores them from the durable Run snapshot instead of relying on in-memory dialog state.
- The active conversation requests a detailed events/interactions snapshot with `include_details=true` and deduplicates by `event_id`. Even if Task Center advanced the global Run cursor first, opening chat later restores the full process without double-counting tools.
- Each Run card retains at most 24 debug events. Each payload is capped at 2.4 KB and fields named like authorization, credential, password, secret, token, or API key are redacted.

Structured activity example:

```json
{
  "type": "activity_updated",
  "activity_id": "verification:contract",
  "activity_type": "verification",
  "status": "failed",
  "summary": "Verification found required changes",
  "detail": "Unknown property: temperature",
  "metadata": {
    "finding_count": 1,
    "clean": false
  }
}
```

### Phase D: retire the old protocol

- Stop carrying phase progress through ordinary `reply`.
- Remove the negative-sentinel single-pending-message convention.
- Persist only user messages, final answers, and necessary interaction summaries in chat history.

Implemented behavior:

- The workflow durable `_emit()` accepts only structured objects with a `type`. Free-form callback text goes only to bounded live activity and is never written as a Run event or chat message.
- Plan, Schema, Coding Agent, verification, and waiting notices no longer become chat bubbles. Their facts are carried by `activity_updated`, tool events, and interaction events.
- A successful Widget now produces a concise delivery answer. Full Coding Agent output, verification reports, and repair evidence remain available in Run state, activity, and debug instead of occupying the main conversation.
- Publishing an App no longer writes a synthetic `role=code` chat message. Later Runs recover up to 32 App references from successful Runs in the same conversation.
- The frontend merges only `ack/reply` messages with positive persisted integer IDs, so old negative or non-persisted messages cannot enter chat history.

## 10. Confirmed product choices and acceptance criteria

The following choices are confirmed:

1. Collapse process after Run completion and leave the final answer expanded.
2. Show human-readable actions by default and expose raw commands only in detail/debug views.
3. Use a desktop default width around 420–440 px while allowing user resizing; keep mobile as a full-screen drawer.
4. Put low-risk plan/Schema choices inline and retain blocking dialogs for high-risk permission requests.

Desktop resize contract:

- Default to about 432 × 600 CSS px; use a suggested minimum of 360 × 420 px, maximum width `min(720px, viewport - 32px)`, and maximum height `viewport - 96px`.
- Keep the overlay anchored to the lower-right corner. Dragging the top, left, or top-left edges changes size without moving the chat launcher.
- Store dimensions in a dedicated workspace UI preference. Reopening chat and refreshing restore the size; changing conversations does not.
- Clamp to the visible area when the viewport shrinks without overwriting the saved preference, so the prior size can return when space is available again.
- Below 720 px, ignore desktop dimensions, use the existing full-screen drawer, and hide resize handles.
- Provide Compact, Default, Wide, and Reset size presets. Resize handles support arrow keys so precise pointer input is not required.
- Use pointer capture and animation frames while resizing, and persist only when the gesture ends.
- Resizing must preserve the message scroll anchor and keep the composer, stop button, and interaction actions visible.

Acceptance criteria:

- A 10-minute Run with hundreds of tool updates still occupies one Run card in the main conversation.
- The first live delta is visible within 200 ms of the backend receiving an ACP chunk.
- Reconnect produces no duplicate activity, half-final answer, or regressed approval state.
- Consecutive repairs update one activity instead of producing repeated manual-repair messages.
- Streaming does not steal scroll position while the user reads older content.
- A resized desktop overlay restores after refresh, never overflows narrow viewports, and preserves the user's preferred size when returning to desktop.
- Keyboard and screen-reader users can expand, approve, stop, and open artifacts.
