# Intent Router (IntentRouter)

Every user message in Ambient Agent passes through a two-layer routing layout to specify target executions.

---

## 1. Routing Diagram

```mermaid
graph TD
    UserRequest["User Request"] --> Router1["First Layer (IntentRouter.route)"]
    Router1 -->|LLM #1 Classify| Plan["IntentPlan"]
    Plan --> Kind{IntentKind}
    
    Kind -->|CONVERSE| Chat["Converse (Plain text chat)"]
    Kind -->|CLARIFY| Ask["Clarify (Ask user for details)"]
    Kind -->|WIDGET_CREATE| WCreate["Widget Build"]
    Kind -->|WIDGET_MODIFY| WModify["Widget Edit"]
    Kind -->|GRAPH_QUERY| GQuery["Graph Query"]
    Kind -->|GRAPH_MUTATION| GMutate["Graph Mutation"]
    
    Kind -->|PLAN_AND_ACT| Router2["Second Layer (refine_sub_intents)"]
    Kind -->|MULTI_INTENT| Router2
    Router2 -->|LLM #2 Details| SubIntents["SubIntents Queue"]
```

---

## 2. Intent Kinds (`IntentKind`)

*   `CONVERSE`: Plain text chat.
*   `CLARIFY`: Emitted when arguments are ambiguous (e.g. updating a card with multiple matches). Triggers dropdown prompt to ask user.
*   `WIDGET_CREATE` / `WIDGET_MODIFY`: Card code generation.
*   `GRAPH_QUERY` / `GRAPH_MUTATION`: SQLite graph database reads and writes.
*   `MULTI_INTENT`: Triggers the second-layer refinement `refine_sub_intents()` to detail action schemas.
