# Widget Generation Information Contract

This page defines the end-to-end information protocol from user intent through publication and runtime observation. The goal is not to add more prompt text, but to make Plan, Schema, Capability, code, and verification reports share one verifiable set of design facts, with explicit downstream invalidation after every edit.

## 1. Structural problems found

The current production path is `Plan → Plan approval → Schema/Capability → Schema approval → Runtime Contract → Coding → Verify → Promote`. It has four systemic gaps:

1. The Plan is generated without Schema, the Capability Catalog, the current Manifest, or data-flow information. It describes UI but cannot prove that the final grants can implement the features.
2. Schema and Capability share one proposal, but UI edits do not maintain grant references. Removing an entity can leave dangling `graph.query` or `graph.mutate` scopes.
3. The Runtime Contract expresses authorization scope but not the complete SDK call grammar. For example, grant operation `create` maps to controller action `create_node`; they are not the same payload value.
4. Coding, static verification, and Schema verification diagnostics do not share one structured repair classification, so the same failure can repeat across multiple `/repair` Runs.

The real weather Widget history demonstrated all four. After four new entities were removed, Graph grants still referenced them and the Run terminated after approval. The Coding Agent then repeatedly wrote approved operation `create` as `action: "create"`, failing the capability verifier.

## 2. Decision: joint generation and atomic approval

Plan, Schema, and Capability should be generated jointly and approved atomically as one `WidgetDesignSpec`. “Joint” does not mean concatenating three natural-language sections into one model response. It means one structured design with shared feature IDs, data flows, and constraints:

```json
{
  "design_version": 1,
  "app_id": "weather-app",
  "goal": "Display and save weather information",
  "features": [
    {
      "id": "forecast",
      "acceptance": ["Show a seven-day forecast", "Offer retry after request failure"],
      "data_flow_ids": ["forecast-network", "forecast-history"]
    }
  ],
  "data_flows": [
    {
      "id": "forecast-network",
      "source": {"kind": "network", "source_id": "open-meteo", "path": "/v1/forecast", "method": "GET"},
      "sink": {"kind": "ui"},
      "freshness": "manual_refresh",
      "failure_ui": "inline_retry"
    },
    {
      "id": "forecast-history",
      "source": {"kind": "feature", "feature_id": "forecast"},
      "sink": {"kind": "graph", "entity": "Document", "operation": "create"},
      "fields": ["title", "content", "captured_at"]
    }
  ],
  "plan": {"ui_summary": "...", "non_goals": [], "degradations": []},
  "schema_proposal": {"reused_schemas": [], "new_schemas": []},
  "capability_proposal": [],
  "coverage": [
    {"feature_id": "forecast", "status": "covered", "evidence": ["forecast-network", "forecast-history"]}
  ]
}
```

The joint design provides three guarantees:

- Every user-visible feature traces to a data source, Schema fields, and grant scopes.
- When the user edits Schema or permissions, the system identifies affected features instead of asking the Coding Agent to guess.
- Approval freezes a normalized design digest; neither the Coding Agent nor the Manifest can expand it.

## 3. Information required at each stage

| Stage | Required input | Required output | Deterministic checks |
| --- | --- | --- | --- |
| Intent | User text, session language, target App, current App/Manifest summary, recent diagnostics | Structured goal, change kind, app_id, acceptance intent, unresolved questions | app_id, target existence, operation kind |
| Design synthesis | Intent, current ontology, Capability Catalog, current grants, available installed capabilities, runtime boundary | `WidgetDesignSpec`: features, data flows, Plan, Schema, grants, coverage, degradation | Structure, references, versions, least privilege |
| Design approval | Complete spec, diff from the published version, risk, lint results | Complete user-edited spec, approval identity, design digest | All references closed; no blockers |
| Contract compile | Approved design, catalog/ontology versions | Immutable Runtime Contract, Manifest template, SDK call contract, design digest | Canonical grants, digest, scope subset |
| Coding | User goal, approved design, Runtime Contract, existing allowed files, exact SDK grammar, recent structured findings | Staging artifact and generation provenance | Only allowed files; Manifest equals contract |
| Static verify | Staging, Runtime Contract, SDK AST rules | Capability/syntax/security findings | Code use is a grants subset; resource identifiers are statically provable |
| Schema verify | Controller AST, approved effective schemas, data-flow fields | Field/type diff and affected features | Entities and fields exist; types are compatible |
| Repair | Original artifact, finding code, location, expected/observed values, repair class, remaining budget | A new revision in the same staging directory | Finding removed; contract cannot expand |
| Promote | Verified artifact, schema proposal, contract/digest, idempotency key | Atomic publication result, artifact hash, schema snapshot, audit event | Final TOCTOU recheck; compensation on failure |
| Observe | Runtime error, feature/data-flow ID, Manifest revision, contract digest | Bounded diagnostic and retry/redesign recommendation | Redaction, aggregation, version linkage |

## 4. Joint design lint rules

The same pure validator runs before approval and after every user edit:

1. Every `graph.query` or `graph.mutate` entity exists in the reused or new Schema set.
2. A Graph data flow's entity, fields, and operation are covered by both Schema and grants.
3. A network flow's source, path, and method exist exactly in the `network.request` scope.
4. An installed-capability flow's catalog and action are currently available and approved.
5. Every feature has at least one complete data flow. An impossible feature is explicitly `degraded` or `blocked`; fake data is never substituted silently.
6. App-private caches, UI state, secrets, and raw provider payloads are not modeled as user-context ontology.
7. Permissions may change from the current App only during approval. Coding and Repair use approved subsets only.

Lint returns structured findings with `code`, `severity`, `path`, `feature_ids`, `message`, and `suggested_actions`, never only an exception string.

## 5. Edit and invalidation propagation

| Change | Must be invalidated or recomputed |
| --- | --- |
| User goal or acceptance criteria | Entire design, contract, code, and all verification |
| Feature or Plan | Related data flows, Schema/grant coverage, contract, code, verification |
| Schema entity ID removal/rename | Graph flows, Graph grant references, coverage, contract, code, verification |
| Schema property | Related data-flow fields, contract, code, Schema verification |
| Capability scope | Coverage/degradation, contract, code, static verification |
| Catalog/ontology version | Design lint and contract; incompatible changes require reapproval |
| Controller repair | Static and Schema verification; unchanged design is not reapproved |

The UI may perform safe deterministic reference transformations: renaming an entity also renames its Graph scopes; removing an entity removes it from scopes and drops empty grants. Any repair that increases authority or changes feature semantics returns to joint design approval.

## 6. Runtime Contract responsibilities

The Runtime Contract is the immutable execution envelope compiled from the approved design. It includes at least:

- `app_id`, `design_digest`, `contract_version`, `catalog_version`, and `ontology_revision`;
- complete effective schemas and canonical capability grants;
- the full Manifest V2 template and security-field mapping;
- the SDK call contract, including the `graph.mutate` action DSL, literal requirements, and allowed host surface;
- allowed files, resource limits, and verifier-policy versions.

Grant operations and SDK actions are represented separately. For example, `graph.mutate.operations=["create"]` authorizes `{action:"create_node"}`; the model must not infer payload values from word similarity.

## 7. Verification and repair loop

Verification order is fixed:

1. Artifact and Manifest shape.
2. Controller syntax and forbidden APIs.
3. Capability AST subset.
4. Schema/entity/property/type diff.
5. Isolated-Chromium smoke test through the production `WidgetRuntimeGateway`.
6. Recompute artifact, contract, and grants digests before promotion.

Automatic repairs run in the same staging directory and Run. Every finding enters the repair prompt and receives an exact signature, and the independent verifier runs again immediately after the edit. The loop keeps handling new findings as long as the finding changes and the artifact was modified; there is no fixed turn count. It stops and retains the failed draft when the same signature repeats consecutively or the artifact hash is unchanged instead of requiring repeated `/repair` input. Finding history is stored with the staging checkpoint and still participates in repetition detection after a cross-Run retry.

Capability, security-boundary, and unknown-entity failures cannot be bypassed. Only presentation-level warnings that cannot make Graph writes invalid may be explicitly accepted.

The durable workflow owns repair authorization decisions, while the unified Coding Agent runner owns ACP session lifetime and follow-up prompts. Every Agent must use the same `initialize → new_session → prompt → verify → follow-up prompt` state machine; the registry only supplies an ACP launch descriptor and cannot change behavior with a provider-specific loop. An adapter may prefer a still-live session, but its session handle is only an ephemeral execution optimization. Correctness depends on the persisted Runtime Contract, staging revision, findings, and independent verifier. If the session is lost, those facts form a repair capsule for a new session; losing a session never broadens authority or skips verification.

Verifier and adapter diagnostics share one `RepairFinding` shape:

- `code`, `stage`, `message`, `signature`, and `attempt`;
- `repairability`: `deterministic`, `code_only`, `design_change`, or `operator`;
- `contract_impact`: `none`, `subset_only`, `expansion`, or `unknown`;
- `expected`, `observed`, `locations`, and artifact revision/hash.

The policy is deterministic:

1. `deterministic` findings are fixed by the system without a model call;
2. `code_only + none` findings may be repaired automatically in the original session;
3. `subset_only` findings are automatic only when approved acceptance coverage remains intact; otherwise they return to design;
4. `design_change`, `expansion`, or `unknown` findings never go to the Coding Agent and require reapproval;
5. verifier/runtime infrastructure failures are `operator` findings and are not hidden by code generation;
6. distinct new findings are not truncated by a fixed repair count; the loop stops immediately when the same exact signature repeats twice, the artifact hash does not change, a turn times out, or another safety budget is exhausted, retaining a failed draft.

### 7.1 ACP compatibility policy

- Agents with native ACP support start their ACP server directly, such as `opencode acp`.
- A non-native Agent may only enter through a pinned, auditable bridge. Codex uses `@agentclientprotocol/codex-acp`; the bridge reuses the managed Codex CLI and native login through `CODEX_PATH` and maps the official app-server to ACP.
- Production images preinstall and pin bridge versions instead of downloading them for each generation. Local development may use an explicit command override.
- A bridge never owns authorization decisions, staging, the automatic-repair budget, or publication. It only translates protocols; Ambient's ACP client and durable workflow remain the control plane.
- External ACP runtimes such as `acpx` are useful references for Agent command catalogs, recovery, and interoperability, but are not embedded in the current execution path because doing so would add a second session store, queue, permission model, and cancellation contract.

The selected Codex bridge is [agentclientprotocol/codex-acp](https://github.com/agentclientprotocol/codex-acp): it is maintained under the ACP organization, published through the official Registry, maps Codex app-server directly to stdio ACP, and can reuse Ambient's managed CLI through `CODEX_PATH`. [openclaw/acpx](https://github.com/openclaw/acpx) is a useful general headless ACP client/runtime, but embedding it would duplicate Ambient's existing session, queue, cancellation, and permission controls. [cola-io/codex-acp](https://github.com/cola-io/codex-acp) and [beyond5959/acp-adapter](https://github.com/beyond5959/acp-adapter) remain interoperability references rather than current production dependencies.

### 7.2 Runtime errors and human intervention

Staging smoke tests and published Widgets use the same isolated Runtime protocol. Orchestration classifies a `runtime_error` before deciding whether to send a finding back to the Coding Agent:

| Class | Examples | Action |
| --- | --- | --- |
| `code_only` | compile, render, hook, or deterministic Controller exception | Send the structured error, artifact hash, and approved contract to the same ACP session for automatic repair |
| `authorization_or_design` | capability denial, schema mismatch, new data source/authority required | Never let the Coding Agent guess or broaden authority; return to joint design/schema/capability approval |
| `operator` | Runtime unavailable, Chromium crash, protocol mismatch, host resource exhaustion | Restart/back off and retain the draft; request operator attention after persistent failure without modifying App code |
| `abuse_or_budget` | infinite loop, message flood, resource-quota exhaustion | Terminate the session immediately; auto-repair only when a bounded code fix is identifiable without relaxing the quota |

Same-session automatic repair is limited to `code_only + contract_impact=none` and observes exact repeated-signature, unchanged-artifact-hash, per-turn timeout, and resource-budget guards; distinct new findings have no fixed three-turn ceiling. Before Runtime diagnostics enter a model, they are stripped of non-source secrets, host paths, and other-session data, with hard bounds on console, stack, DOM snapshot, and frame sizes. A runtime failure in a published App may create a new staging repair Run while preserving the current live revision; that staging cannot replace live until it passes the full verifier and smoke test.

## 8. Durable state

A Widget checkpoint stores:

- `design_candidate`, `design_revision`, and `design_findings`;
- `approved_design`, `design_digest`, approver identity, and approval time;
- `runtime_contract`, contract digest, catalog version, and ontology version;
- `staged_app` and artifact revision/hash;
- `verification_findings`, finding signatures, and repair count;
- promotion effect ledger and schema snapshot.

The state machine no longer treats Plan and Schema as independent facts. Legacy fields can be projected as `plan_candidate` and `schema_candidate` during migration, but the versioned design is the single source of truth.

## 9. Migration order

1. Immediate: synchronize Schema edits with Graph grant references; return invalid edits to approval instead of terminating the Run; publish exact SDK grammar through Catalog and prompts.
2. Near term: introduce `WidgetDesignSpec`, shared lint, and a design digest; keep two existing dialogs temporarily while they read one design candidate.
3. Medium term: merge them into one atomic Design Approval with feature/data-flow/coverage UI.
4. Later: replace CDP screencast with a more efficient WebRTC transport and add optional per-App-container `strict` isolation without changing the Gateway capability protocol.

Phase one does not change authorization boundaries; it only prevents invalid approvals and misleading diagnostics. Phases two and three require a durable workflow version bump and a compatibility reducer for old waiting Runs.
