import { describe, expect, it } from "vitest";

import {
  KNOWN_RUN_EVENT_TYPES,
  RUN_EVENT_SCHEMA_VERSION,
  isKnownRunEvent,
  isKnownRunEventType,
  type RunEvent,
} from "../../frontend/src/types/run-events.generated";


function envelope(type: string, payload: unknown, schemaVersion = 1): RunEvent {
  return {
    sequence: 1,
    event_id: "event-1",
    schema_version: schemaVersion,
    stream_epoch: "epoch-1",
    run_id: "run-1",
    session_id: null,
    step_id: null,
    attempt: null,
    trace_id: "trace-1",
    type,
    payload,
    created_at: "2026-07-19T00:00:00Z",
  };
}


describe("generated Run event contract", () => {
  it("exposes all v1 discriminators in stable order", () => {
    expect(RUN_EVENT_SCHEMA_VERSION).toBe(1);
    expect(KNOWN_RUN_EVENT_TYPES).toEqual([
      "run_created",
      "status_changed",
      "step_started",
      "step_committed",
      "interaction_requested",
      "interaction_resolved",
    ]);
  });

  it("narrows known events while safely carrying unknown event types and versions", () => {
    const known = envelope("run_created", { status: "queued" });
    const unknownType = envelope("future_projection_hint", { opaque: true });
    const unknownVersion = envelope("run_created", { future: true }, 2);

    expect(isKnownRunEventType(known.type)).toBe(true);
    expect(isKnownRunEvent(known)).toBe(true);
    expect(isKnownRunEvent(unknownType)).toBe(false);
    expect(isKnownRunEvent(unknownVersion)).toBe(false);
    expect(unknownType.payload).toEqual({ opaque: true });
  });
});
