# Detailed Design: State-Driven Graph OS

This document outlines the detailed system design for transforming the Ambient Agent into an LLM-native, state-driven Knowledge Graph Operating System.

---

## 1. Data Schema: Knowledge Graph (SQLite)

We will implement a Property Graph representation in SQLite using `SQLModel`. The schema consists of two main entities: `GraphNode` and `GraphEdge`.

### 1.1 SQLModel Definitions
```python
from typing import Optional, Dict, Any
from sqlmodel import SQLModel, Field
import json

class GraphNode(SQLModel, table=True):
    __tablename__ = "graph_nodes"
    
    id: str = Field(primary_key=True) # UUID or human-readable ID
    type: str = Field(index=True)     # e.g., "Task", "CalendarEvent", "WeatherReport"
    properties_json: str = Field(default="{}", nullable=False) # Serialized JSON dict

    @property
    def properties(self) -> Dict[str, Any]:
        try:
            return json.loads(self.properties_json)
        except Exception:
            return {}

    @properties.setter
    def properties(self, val: Dict[str, Any]):
        self.properties_json = json.dumps(val)

class GraphEdge(SQLModel, table=True):
    __tablename__ = "graph_edges"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    from_id: str = Field(foreign_key="graph_nodes.id", index=True)
    to_id: str = Field(foreign_key="graph_nodes.id", index=True)
    type: str = Field(index=True)     # e.g., "ASSOCIATED_WITH", "BELONGS_TO"
    properties_json: str = Field(default="{}", nullable=False)

    @property
    def properties(self) -> Dict[str, Any]:
        try:
            return json.loads(self.properties_json)
        except Exception:
            return {}

    @properties.setter
    def properties(self, val: Dict[str, Any]):
        self.properties_json = json.dumps(val)
```

---

## 2. Declarative Graph Query Engine

Apps (widgets) subscribe to subsets of the graph. The Orchestrator also queries the graph. To avoid complex Cypher parser dependencies, we define a structured, declarative JSON-based Graph Query schema.

### 2.1 JSON-GraphQL Query Structure
An app defines its query as:
```json
{
  "type": "Task",
  "properties": { "status": "pending" },
  "include": [
    {
      "relation": "ASSOCIATED_WITH",
      "target_type": "CalendarEvent"
    }
  ]
}
```
The Query Engine in `backend/graph_query_engine.py` translates this query into SQLite queries:
1. Find all `GraphNode` records where `type == Task` and properties match `status == pending`.
2. For each task node, look up `GraphEdge` where `from_id == task_node.id` and `type == ASSOCIATED_WITH`.
3. Fetch the linked `GraphNode` records where `type == CalendarEvent`.
4. Assemble and return a relational JSON list of objects:
```json
[
  {
    "id": "task-1",
    "type": "Task",
    "properties": { "title": "Buy milk", "status": "pending" },
    "relations": [
      {
        "edge_type": "ASSOCIATED_WITH",
        "target": {
          "id": "event-1",
          "type": "CalendarEvent",
          "properties": { "summary": "Grocery trip", "time": "2026-07-12T10:00" }
        }
      }
    ]
  }
]
```

---

## 3. Real-Time State Sync (WebSocket)

We will extend the FastAPI WebSocket connection to support reactive subscriptions.

```
[SandboxWidget] --(WS: subscribe)--> [FastAPI (main.py)] --(register query)--> [SubscriptionManager]
                                                                                      |
[LLM (Mutation)] --(update DB)-----> [GraphDatabase] --(trigger notify)-------------->|
                                                                                      |
[SandboxWidget] <--(WS: query_update)-- [FastAPI] <--(push new query results)---------+
```

1. **Subscription Registration**:
   * A client sends a message via WebSocket: `{"type": "graph_subscribe", "subscription_id": "sub_123", "query": {...}}`.
   * The backend registers this query against the WebSocket connection in a `SubscriptionManager`.
2. **Reactive Updates**:
   * When any mutation happens on the graph db, the backend calls `SubscriptionManager.notify_change()`.
   * For each active subscription, the backend re-runs the query and compares the result. If the result has changed, it pushes the updated list to the client: `{"type": "graph_query_update", "subscription_id": "sub_123", "data": [...]}`.

---

## 4. Graph Mutation Engine & Agent ReAct Loop

### 4.1 Graph Mutation Actions
Mutations are processed via structured actions, allowing function calling to execute them safely:
```json
[
  {
    "action": "create_node",
    "id": "node-1",
    "type": "Task",
    "properties": { "title": "Review Plan", "status": "pending" }
  },
  {
    "action": "create_edge",
    "from_id": "node-1",
    "to_id": "node-2",
    "type": "ASSOCIATED_WITH"
  }
]
```

### 4.2 ReAct Orchestration Loop (`backend/agent/harness.py`)
Currently, the agent is run in a single LLM request. We will refactor `AgentOrchestrator.handle_message()`'s conversational path:
```python
async def run_react_loop(self, session_id: str, prompt_messages: list) -> str:
    # Max loop iterations = 5
    messages = prompt_messages.copy()
    for _ in range(5):
        response = await self.provider.generate(messages, tools=self.tools)
        # Check if response has tool calls
        if not response.tool_calls:
            return response.content
            
        # Execute tool calls
        for tool_call in response.tool_calls:
            result = await self.registry.execute(tool_call.name, tool_call.args)
            messages.append({"role": "assistant", "content": None, "tool_calls": [...]})
            messages.append({"role": "tool", "name": tool_call.name, "content": result})
```

---

## 5. Front-End Sandbox Graph SDK Extension

We will extend the `ambient` object injected into `SandboxWidget`:

```javascript
const ambient = {
  // ... old model API kept for fallback compatibility ...
  graph: {
    subscribe: (query, callback) => {
      const subId = `sub-${Math.random().toString(36).substr(2, 9)}`;
      
      // Register callback locally
      window.addEventListener(`graph_update:${subId}`, (e) => {
        callback(e.detail);
      });
      
      // Send subscription message over WS
      wsService.sendMessage({
        type: "graph_subscribe",
        subscription_id: subId,
        query: query
      });
      
      return () => {
        // Unsubscribe cleanup
        wsService.sendMessage({
          type: "graph_unsubscribe",
          subscription_id: subId
        });
      };
    },
    mutate: async (actions) => {
      // Direct post request to mutate the database
      const res = await fetch(`${API_BASE}/api/graph/mutate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ actions })
      });
      return res.json();
    }
  }
};
```
