# Backend Runtime UML

This page documents scheduler-owned chat, the durable reducer, and unified side-effect execution boundaries. Python references in flowcharts are checked by `scripts/verify_uml.py`.

## 1. One control plane

```mermaid
flowchart TB
    Chat[Browser /ws/chat] --> Main[main.py: websocket_chat]
    RunAPI[REST /api/runs and /api/run-interactions] --> Coordinator[run_service.py: RunCoordinator]
    RunWS[Browser /ws/runs] <-->|versioned event replay| Store[run_service.py: RunStore]

    Main -->|persist message / submit Run / resolve| Coordinator
    Coordinator --> Store
    Coordinator -->|internal_agent| Workflow[durable_workflow.py: DurableAgentWorkflow]
    Workflow --> State[run_service.py: AgentRunState]
    Workflow --> Outcome[run_service.py: StepOutcome]
    Outcome --> Commit[run_service.py: commit_step]
    Commit --> Store

    Workflow --> Domain[harness.py: AgentOrchestrator]
    Workflow --> Tools[tools.py: ToolGateway]
    Workflow --> ACP[opencode_service.py: run_opencode_agent_acp]
    Coordinator --> MCP[backend_manager.py: StdioJsonRpcClient]
    Coordinator --> Remote[backend_manager.py: handle_agent_message]

    Store -->|project Run events to session| Main
```

The WebSocket creates only lightweight submission/response-projection bridges; it does not execute agent, MCP, or remote-Agent effects in the connection task. The reducer reuses `AgentOrchestrator` as a domain helper, while `RunCoordinator` owns execution. Browser disconnection does not change authoritative Run state.

## 2. Persistent data model

```mermaid
erDiagram
    RUNS ||--o{ RUN_STEPS : attempts
    RUNS ||--o{ RUN_INTERACTIONS : waits_for
    RUNS ||--o{ RUN_EVENTS : emits
    RUNS ||--o{ RUNS : parent_or_retry

    RUNS {
        string id PK
        string status
        json state_json
        string workflow_type
        int workflow_version
        int version
        string lease_owner
        string lease_expires_at
        int lease_epoch
        json checkpoint_json
        json result_json
        json error_json
    }
    RUN_STEPS {
        int id PK
        string run_id FK
        string step_key
        int attempt
        string status
        json output_json
    }
    RUN_INTERACTIONS {
        string id PK
        string run_id FK
        string status
        int run_version
        json payload_json
        json response_json
    }
    RUN_EVENTS {
        int sequence PK
        string event_id UK
        int schema_version
        string stream_epoch
        string run_id FK
        string session_id
        string step_id
        int attempt
        string trace_id
        float duration_ms
        json model_usage_json
        bool redacted
        string type
        json payload_json
    }
```

## 3. Claim, execution, and fencing

```mermaid
sequenceDiagram
    participant WS as WebSocket/API
    participant S as RunStore
    participant C as RunCoordinator
    participant W as DurableAgentWorkflow
    participant X as stale callback

    WS->>S: persist user message
    WS->>C: submit_internal_agent(state v2)
    C->>S: claim_next(worker, limits, session lane)
    S-->>C: running Run + lease_epoch=E
    C->>S: begin_step_attempt(phase, E)
    C->>W: reducer(run, state)
    W-->>C: typed StepOutcome
    C->>S: commit_step(state, outcome, E)
    S->>S: step + checkpoint + status + events in one transaction

    Note over S: recovery/cancel/new claim changes ownership
    X->>S: commit_step(..., old E)
    S-->>X: StaleLeaseError
```

A later same-session Run cannot be claimed while an earlier one is `running`, `waiting_user`, `cancel_requested`, or `needs_attention`. `waiting_user` releases a worker slot but retains the session lane.

## 4. Version 2 workflow states

```mermaid
flowchart TB
    Route[route] --> Converse[converse bounded tool loop]
    Route --> Query[graph_query]
    Route --> GPre[graph_preflight]
    GPre --> GWait[wait_graph_approval]
    GWait -->|approve| GCommit[graph_commit]
    GWait -->|deny| Failed[failed]

    Route --> Plan[plan]
    Plan --> WPlan[wait_plan]
    WPlan -->|approve| Align[align_schema]
    WPlan -->|refine| Plan
    Align --> WSchema[wait_schema]
    WSchema -->|approve| Stage[stage_code]
    WSchema -->|refine| Align
    WSchema -->|rework plan| Plan
    Stage --> Verify[verify]
    Verify -->|clean| Promote[promote]
    Verify -->|findings| Override[wait_override]
    Override -->|approve| Promote
    Override -->|rework code| Stage
    Override -->|rework schema| Align
    Override -->|rework plan| Plan
    Promote --> Success[succeeded]

    Route --> Multi[multi_preflight]
    Multi -->|whole plan valid| Saga[multi_dispatch saga]
    Saga -->|next sub-intent| RouteSub[matching subflow]
    RouteSub --> Saga
```

Every wait phase persists its interaction before returning `Wait`. Resolution checks `expected_run_version` and atomically stores the response, closes sibling pending interactions, requeues the Run, and appends events.

## 5. Tool, MCP, and OpenCode boundaries

```mermaid
flowchart LR
    Model[Model tool call] --> Registry[tools.py: ToolRegistry]
    Registry --> Gateway[tools.py: ToolGateway]
    Gateway -->|schema / effect / scope / approval / timeout / idempotency| LocalTool[Local Python tool]

    Run[Durable capability Run] --> Backend[backend_manager.py: BackendManager]
    Backend --> MCP[backend_manager.py: StdioJsonRpcClient]
    MCP -->|initialize / deadline / cancel / bounded I/O| Process[MCP subprocess]

    Stage[stage_code] --> Prepare[opencode_service.py: _prepare_staging_app]
    Prepare --> ACP[opencode_service.py: run_opencode_agent_acp]
    ACP --> Validate[opencode_service.py: validate_opencode_staging]
    Validate --> Verify[verify reads staging]
    Verify -->|pass / approved override| Marker[durable promotion marker]
    Marker --> Promote[opencode_service.py: promote_opencode_staging]
    Verify -->|failure / rework / cancel| Discard[opencode_service.py: discard_opencode_staging]
    Promote --> Live[Live App]
```

`ToolGateway` currently unifies model-requested local Python tools. Capability, MCP, remote-Agent, and ACP execution retain separate adapter/permission policies. OpenCode now has path, argv, environment, output, process-group, and staging controls, but not an OS-level filesystem/network sandbox.

The backend image must include both Node.js and the `@babel/standalone` version pinned by the frontend lockfile. `validate_opencode_staging` applies Babel parsing, host/network-global rejection, and a restricted-VM smoke test to the staging shared by OpenCode and Codex. A missing or failed verifier must never be promoted to a live App.

## 6. Event and recovery boundaries

Run event payloads are redacted and bounded before insertion, while the envelope records duration, model usage, and `redacted` metadata; terminal events are retained for 30 days by default. A Graph effect ledger closes the checkpoint window against duplicate writes, and an App promotion marker distinguishes published artifacts from staging awaiting publication. Only saga steps with complete compensation data are rolled back automatically.

## 7. Canonical ontology and KG storage boundary

```mermaid
classDiagram
    class GraphDatabase {
        +list_schemas()
        +routing_snapshot(recent_per_type)
        +preflight_actions(actions)
        +apply_actions_atomic(actions)
        +apply_schema_proposal_atomic(proposal)
    }
    class Neo4jGraphDatabase {
        +from_env(workspace_dir)
        +migrate_from_sqlite(path)
    }
    class OntologyEntity {
        +id: str
        +ontology_iri: str
        +equivalent_to: tuple
        +subclass_of: str
        +properties: dict
        +abstract: bool
    }

    GraphDatabase <|-- Neo4jGraphDatabase
    Neo4jGraphDatabase --> OntologyEntity
```

`create_graph_database()` is the runtime factory: deployments select Neo4j, while the SQLite `GraphDatabase` remains a test and migration compatibility adapter. Both adapters enforce the same `ambient-context` ontology contract; unknown entities, abstract entities, and unknown properties cannot be written as records.

## 8. Coding Agent Runtime and model ownership

```mermaid
flowchart LR
    Settings[coding_agent.py: CodingAgentConfigStore] --> Runtime[coding_agent_runtime.py: CodingAgentRuntime]
    Runtime -->|on-demand install / status / auth / model-list| Codex[codex_service.py: run_codex_agent]
    Settings --> Dispatch[coding_agent.py: run_coding_agent]
    Dispatch --> Codex
    Dispatch --> OpenCode[opencode_service.py: run_opencode_agent_acp]

    Provider[Central Provider Registry] --> Ambient[primary / fast]
    Provider -->|per-agent shared binding| OpenCode
    Native[Codex-native login and subscription] --> Codex
```

Built-in adapters form a trusted capability catalog, while each CLI is downloaded to a dedicated persistent volume only after the user requests installation. Installation, authentication, dynamic model discovery, and execution share an agent-specific state directory; Ambient Provider credentials never enter a native-mode Codex process. The Codex model catalog comes from app-server `model/list` rather than an Ambient-maintained hard-coded list. Provider connections remain centralized, but consumer model roles are bound independently: Ambient uses `primary/fast`, OpenCode uses an inherited or dedicated `shared_binding`, and Codex uses a `native` binding. Submission snapshots the agent, its model configuration, and any resolved shared model so recovery cannot drift after later settings changes.

Docker's default seccomp profile blocks the unprivileged user namespace required by Codex bubblewrap. Compose relaxes that syscall layer so Codex can keep its `workspace-write` sandbox inside the outer container boundary; it does not use `SYS_ADMIN` or `danger-full-access`.
