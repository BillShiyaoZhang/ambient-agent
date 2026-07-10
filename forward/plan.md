# Implementation Plan: State-Driven Graph OS

This plan lists the concrete implementation steps, files to be modified, and verification tests to build the Graph OS.

---

## Milestone 1: Knowledge Graph Schema & Backend Mutations

### Proposed Changes:
1. **[NEW] [graph_db.py](file:///Users/shiyaozhang/Developer/ambient-agent/backend/graph_db.py)**:
   - Define SQLModel models `GraphNode` and `GraphEdge`.
   - Implement basic CRUD helper functions: `create_node`, `delete_node`, `update_node_property`, `create_edge`, `delete_edge`.
   - Update `models.py` or base setup to initialize tables.
2. **[NEW] [test_graph_db.py](file:///Users/shiyaozhang/Developer/ambient-agent/tests/backend/test_graph_db.py)**:
   - Test creating nodes, creating edges, updating properties, deleting nodes, verifying Cascade deletions (deleting node deletes incoming/outgoing edges).

---

## Milestone 2: Declarative Graph Query Engine & Mutations API

### Proposed Changes:
1. **[NEW] [graph_query_engine.py](file:///Users/shiyaozhang/Developer/ambient-agent/backend/graph_query_engine.py)**:
   - Implement `execute_graph_query(query: dict, db_session: Session) -> list`: Parses our declarative JSON-GraphQL schema and queries DB.
2. **[MODIFY] [main.py](file:///Users/shiyaozhang/Developer/ambient-agent/backend/main.py)**:
   - Add POST endpoint `/api/graph/mutate` to run a list of graph mutation actions.
3. **[NEW] [test_graph_query.py](file:///Users/shiyaozhang/Developer/ambient-agent/tests/backend/test_graph_query.py)**:
   - Test queries matching properties, matching types, including related nodes over edges.
   - Test mutation API `/api/graph/mutate`.

---

## Milestone 3: Real-time Sync & WebSocket Subscriptions

### Proposed Changes:
1. **[NEW] [graph_subscription.py](file:///Users/shiyaozhang/Developer/ambient-agent/backend/graph_subscription.py)**:
   - Class `SubscriptionManager` to register active WebSocket query subscriptions.
   - Mechanism to trigger re-evaluation of active queries when graph db commits occur.
2. **[MODIFY] [main.py](file:///Users/shiyaozhang/Developer/ambient-agent/backend/main.py)**:
   - Hook into the WebSocket loop to handle `graph_subscribe` and `graph_unsubscribe` message types.
   - Wire database mutations to notify the `SubscriptionManager` to broadcast updates.
3. **[NEW] [test_graph_sync.py](file:///Users/shiyaozhang/Developer/ambient-agent/tests/backend/test_graph_sync.py)**:
   - Test subscribing to a graph query over WebSocket, mutating the graph, and verifying that the WebSocket client receives the updated query results automatically.

---

## Milestone 4: ReAct Orchestrator Loop & Tools

### Proposed Changes:
1. **[MODIFY] [tools.py](file:///Users/shiyaozhang/Developer/ambient-agent/backend/agent/tools.py)**:
   - Register new tool `query_graph(query_json: str)`
   - Register new tool `mutate_graph(actions_json: str)`
2. **[MODIFY] [harness.py](file:///Users/shiyaozhang/Developer/ambient-agent/backend/agent/harness.py)**:
   - Implement the ReAct loop inside conversational processing path.
3. **[NEW] [test_graph_agent.py](file:///Users/shiyaozhang/Developer/ambient-agent/tests/backend/test_graph_agent.py)**:
   - Test that the agent correctly queries/mutates the graph using tool calls to complete multi-app orchestration instructions.

---

## Milestone 5: Frontend Graph SDK & Sandboxed UI Integration

### Proposed Changes:
1. **[MODIFY] [SandboxWidget.tsx](file:///Users/shiyaozhang/Developer/ambient-agent/frontend/src/components/SandboxWidget.tsx)**:
   - Inject `ambient.graph.subscribe` and `ambient.graph.mutate` into the sandbox Javascript run execution context.
2. **[MODIFY] [opencode_system.md](file:///Users/shiyaozhang/Developer/ambient-agent/backend/agent/prompts/opencode_system.md)**:
   - Teach the code-gen agent to write Semantic Manifest headers and read/write graph state via new Graph APIs.
