# Graph Data Visualization

Graph data visualization is a shared exploration capability used by the trusted React Host. It serves the knowledge graph, ontology, Agent orchestration, privacy data map, and Widget Schema approval. It is not part of the Widget SDK and does not change Graph, Run, Audit, or Capability authorization boundaries.

## 1. Shared graph model and component

Every scene first projects into the same read-only presentation model:

```text
GraphDataset
├── nodes[]: id, label, kind, status, summary, details, badges, action
├── edges[]: id, source, target, label, kind, status, details
└── metadata: title, description, counts, time window, coverage, blind spots
```

`GraphExplorer` uses the mature React Flow canvas for pan, zoom, drag, box selection, MiniMap, fit view, and keyboard interaction. Business adapters do not duplicate the canvas implementation. The component must also provide:

- search over node label, kind, summary, and details;
- node-kind visibility controls and one-hop-neighbor focus after selection;
- horizontal/vertical layout switching and one-click restoration of the complete topology;
- node and edge details, legend, empty and error states, and data-truncation notices;
- status communicated with text/icons in addition to color, following Host light, dark, and reduced-motion settings;
- a stacked canvas/details layout on narrow screens and accessible names for every control.

Node position is browser-session presentation state only. It is not written to the KG, Run, or Manifest. No scene may infer permission or a business fact from display state.

## 2. Ontology and KG Explorer

System chrome exposes a Graph Explorer entry with two distinct workspace views:

- **Ontology**: each canonical ontology entity is a node and `subclass_of` is a directed edge. Details show description, property types, canonical IRI, external equivalent IRIs, core/abstract flags, and record count.
- **Knowledge Graph**: each `ContextRecord` is a node and each real Graph edge is directed. Details show entity type and record properties. Infrastructure effects, rollback, migration, and ontology-storage nodes never appear.

`GET /api/graph/explorer?record_limit=N` is for the trusted system Host only and returns `Cache-Control: no-store`. Every request must come from loopback or a container/proxy network explicitly configured in `AMBIENT_TRUSTED_HOST_PEERS`; browser requests carrying `Origin` must additionally match `AMBIENT_FRONTEND_ORIGINS`, so spoofing an allowed Origin cannot bypass the peer boundary. Same-machine native tools and diagnostics without `Origin` remain available. The backend reads through the public Graph-adapter contract rather than private SQLite tables, bounds `record_limit` to a safe maximum, and returns at most 1,000 KG relationships. The response includes total counts, returned counts, node/edge limits, and topology `truncated`, so a bounded snapshot cannot be mistaken for the complete KG. Detail values have separate string, collection, depth, and per-value byte limits; non-finite floating-point values are replaced by explicit JSON-safe markers; independent `detail_truncation` metadata and a UI notice report clipping without conflating it with topology truncation. A relationship is returned only when both endpoints are present in the snapshot. Stored node IDs, relationship endpoints, and relationship types are preserved exactly; display-text cleanup never changes graph identity.

This system view does not grant Graph access to Widgets or bypass App grants. Widgets still use only approved `ambient.graph` scopes.

## 3. Agent orchestration design and runtime

The Agent orchestration view uses the same component for the durable workflow:

- the design view shows phases, connections, branch conditions, user waits, terminal states, and rework loops;
- node details include responsibility, implementation file, symbol, and an openable source-code link;
- the runtime view overlays the design using Run-event `step_id`, current checkpoint, result, and error, distinguishing not-started, running, waiting, succeeded, and failed;
- the dataset retains the Run `workflow_type`, `workflow_version`, and visualization-descriptor versions; a mismatch keeps evidence nodes visible while warning that topology and phase coverage may be inaccurate;
- a missing event does not prove a phase did not execute; details identify incomplete event windows and old Run versions.

Selecting a Run in the Task Center displays its runtime graph without removing existing input, result, artifact, and error details. Unknown workflows or phases remain inspectable nodes instead of being discarded.

## 4. Privacy data map

The privacy data map answers “which categories of data may have, or did, flow where.” It does not replace the raw Audit Log. It is derived on request from retained Audit metadata and current App Manifests, without creating another log or persisting the topology.

Three semantics must remain simultaneously visible and distinct:

- `observed`: flows aggregated from runtime evidence in the retained window, with count, earliest/latest time, and category;
- `declared`: potential flows derived from Manifest capability/schema declarations; declaration is not proof of approval, compliance, or runtime behavior;
- `unknown`: possible flows outside instrumentation coverage, representing blind spots; missing evidence never means no data flow occurred.

Map edges and details contain only minimal metadata such as local source category, stage, destination provider/capability category, count, and time. They never contain prompts, responses, tool arguments, record properties, credentials, raw provider payloads, or excerpts/summaries/hashes of them. There is no raw Audit Log payload path into the graph response.

Retention, access, and deletion follow these rules:

- the map covers only the source Audit Log's current retained window and never extends retention;
- only the trusted system Host calls `GET /api/data-map`; browser `Origin` uses the same explicit Host allowlist, the response is `no-store`, and the endpoint is not injected into Widgets;
- raw `GET /api/audit-logs` uses the same peer/Origin boundary and `no-store`; native requests without `Origin` are restricted to loopback or an explicit trusted peer so they cannot bypass the redacted map to read raw payloads remotely;
- after Audit Log cleanup, App deletion, or Manifest update, the next derivation disappears or changes automatically, with no separate deletion path;
- the response states its time window, evidence count, declared-App count, instrumentation scope, known blind spots, and limitations;
- the Audit Log remains the raw evidence view, the Capability authorizer remains the runtime permission boundary, and the data map makes no compliance decision.

## 5. Widget Schema approval

The Schema and Capability alignment dialog adds a graph preview above the existing editable form:

- reused entities, new entities, parent entities, Graph grants, and other capabilities use distinct node kinds;
- `subclass_of`, `graph.query`, `graph.mutate`, and capability scopes use labeled edges;
- parents outside the proposal first appear as `unknown`, so an unconfirmed reference is never mislabeled as existing; dangling grants, duplicate entities, and backend errors for the current submission appear as warnings/errors and block approval;
- editing an entity ID, parent, property, or grant immediately rebuilds the graph and error list from the same proposal;
- after an edit, diagnostics from the previous backend submission remain visible as stale context but no longer deadlock approval; currently recomputable client dependency errors still block, and the backend authoritatively validates the resubmission;
- the graph is a comprehension and navigation layer. It does not persist another proposal or bypass the form, natural-language refinement, or approval actions.

## 6. Acceptance criteria

- One shared `GraphExplorer` is reused by Ontology/KG, Agent orchestration, privacy-data-map, and Schema-approval adapters.
- Users can zoom, pan, drag, search, filter, switch layout, focus neighbors, and inspect node/edge details.
- Ontology and KG use a storage-independent bounded backend snapshot with explicit total counts and truncation state.
- The Agent design graph shows connections, conditions, and implementation entry points; selecting a Run exposes runtime status, result, and errors.
- The privacy map distinguishes `observed`, `declared`, and `unknown`, shows its window and blind spots, and contains no raw payload.
- Schema-approval edits update the topology immediately while existing validation and authorization boundaries remain effective.
- Pure adapter tests, component interaction tests, and API contract tests cover the new behavior; the complete frontend suite/build, Ruff, backend suite, and documentation checks pass.
