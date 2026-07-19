# Graph Database

`GraphDatabase` stores structured data shared by Widgets and the Agent in `workspace/graph.db` (SQLite + WAL). `graph.json` exists only for one-time legacy migration and is renamed to a backup afterward.

## 1. Tables and responsibilities

| Table | Primary key | Responsibility |
| --- | --- | --- |
| `graph_schemas` | `id` | Schema name, description, property types, and core flag |
| `graph_nodes` | `id` | Node type, JSON properties, namespace, and timestamp |
| `graph_edges` | `(from_id, to_id, type)` | Directed relationship and JSON properties |
| `graph_mutation_history` | `id` | Forward/reverse actions, pre-state snapshot, pin/consume state |
| `graph_effects` | `idempotency_key` | Action input hash and committed result to prevent duplicate effects |

Built-in core schemas are:

- `Task`: `title`, `description`, `status`, and `due_date`, all strings;
- `Event`: `title`, `description`, `start_time`, `end_time`, and `location`, all strings;
- `Note`: `title`, `content`, and `tags`, all strings.

Apps may propose schema extensions, but ordinary deletion flows cannot remove core schemas.

## 2. Queries

Widgets register live queries with `ambient.graph.subscribe(query, callback)`. The backend `GraphSubscriptionManager` retains subscriptions, reruns queries after mutations, and pushes changes. The returned unsubscribe function removes both the persistent WebSocket message and browser listener.

Agent read-only queries execute through `graph_query_engine.execute_graph_query`. Before routing, `RouterContext` creates a bounded `GraphSnapshot` so the entire database is not placed in the prompt.

## 3. Mutations

Public actions are:

- `create_node`
- `update_node_property`
- `delete_node`
- `create_edge`
- `delete_edge`

`preflight_actions` resolves temporary node dependencies and checks endpoints and schema types. `apply_actions_atomic` commits the whole action batch in one transaction and creates reverse actions. Any action failure rolls back the entire batch.

Widget HTTP mutations automatically include a `widget:<app-id>:<uuid>` idempotency key. The durable workflow uses its own stable effect keys. The same key with the same input returns the committed result; the same key with different input is rejected.

## 4. Schema verification and rollback

Widget create/modify flows extract subscribe, query, and mutate usage from `controller.js` to produce a `VerificationDiff`. Missing types or properties are handled through a schema proposal and user confirmation. Approved schema changes and app publication both retain recovery snapshots.

Mutation history stores forward and reverse actions. `MutationTicketManager` can present a preview, roll back, pin, or consume a ticket; rollback is itself checked by the backend. Internal `replace_node` and `restore_node` operations are only for reverse actions and are not public Widget actions.

## 5. Constraints

- Every public node write must match registered schema property names and types.
- Edge endpoints must exist or be created earlier in the same action batch.
- Frontend declarations and manifest `schema_refs` provide context, not authorization; the backend performs final validation.
- Do not write SQLite directly or generate the deprecated `ambient.model` or `graph.json` interfaces.
