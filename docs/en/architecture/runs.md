# Background Runs and Runtimes

Ambient Agent separates installed capabilities, execution environments, and individual units of work:

- An **App / Capability** declares actions and may have a Canvas UI or be headless.
- A **Runtime** executes actions. The platform manages MCP processes, observes remote HTTP agents, and exposes an internal Agent runtime.
- A **Run** is a durable execution that survives window closure, chat switching, and browser disconnection.

## State and recovery

The main flow is `queued → running → succeeded | failed`. A Run uses `waiting_user` for durable interactions, `cancel_requested` for cooperative cancellation, and `needs_attention` when an external side effect cannot be replayed safely.

Runs, events, interactions, and step checkpoints are stored in `workspace/.ambient/runs.db`. Workers claim work with leases and heartbeats. On restart, queued and waiting Runs remain available, `restart_safe` actions resume from checkpoints, and opaque external calls become needs-attention items.

The queue defaults to four concurrent Runs globally and one per owner. Configure these limits with `RUNNER_MAX_CONCURRENCY` and `RUNNER_MAX_PER_APP`.

## Capability V2 and APIs

V2 manifests declare an `actions` list with input/result schemas, invocation adapters, and recovery policy. V1 manifests are normalized to a single `run` action. Headless capabilities open a schema-driven action launcher in App Center.

REST endpoints under `/api/runs`, `/api/run-interactions`, and `/api/runtimes` manage execution. `/ws/runs?after_sequence=N` provides a replayable workspace event stream. Widgets use `ambient.runs.start/get/cancel/subscribe`; `ambient.capabilities.invoke` remains a terminal-result compatibility wrapper.

The global Task Center shows active work, attention items, history, and Runtime health. App uninstall and managed Runtime shutdown return `409` while affected Runs are active.
