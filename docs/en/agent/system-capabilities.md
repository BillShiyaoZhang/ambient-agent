# Agent System Capability Catalog

Agents must not infer system capabilities from a long-lived handwritten prompt that can drift. The system maintains a structured, versioned `SystemCapabilityCatalog` and renders bounded guidance for each Agent role. This page defines its sources, projections, and usage rules.

## 1. Single source of truth

The Catalog is assembled from runtime code; it is never reverse-parsed from prose:

- Capability Ontology: categories a Widget may request, scope schemas, and SDK mappings.
- `ambient-context` ontology: Graph entities and properties available for reads and writes.
- Tool registry: local tools the model can actually call, including effects and scopes.
- App Store: installed capability catalog IDs, actions, and input/result schemas.
- Skill Registry: metadata, version, digest, enabled state, and availability for installed Instruction Skills.
- Coding Agent registry: availability, authentication state, model constraints, and staging policy.
- Durable Run contract: interactions, cancellation, recovery, idempotency, and `needs_attention` semantics.

The Catalog has an explicit `catalog_version` and deterministic order. Prompts, UI, and APIs consume projections; they never maintain a second category list.

## 2. Domain model

```text
SystemCapabilityCatalog
├── runtime_contract
│   ├── durable_runs
│   ├── approvals
│   └── recovery
├── context_graph
│   ├── ontology_id
│   ├── entities
│   └── query/mutation grammar
├── widget_runtime
│   ├── manifest_version
│   ├── capability_categories
│   └── forbidden APIs
├── installed_capabilities
│   └── catalog_id -> actions + schemas
├── installed_skills
│   └── catalog_id -> metadata + version + digest + availability
├── model_tools
│   └── name -> input schema + effect + scope
└── coding_agents
    └── id -> availability + model mode + artifact policy
```

Every executable item includes at least a stable ID, description, availability, effect, a machine-readable `scope_contract`, approval requirements, and limits. The `scope_contract` declares required and optional fields, types, enum or numeric bounds, and one minimal valid example, so the Schema Alignment Agent never has to infer nested shapes from field names. A Skill item contains globally projectable metadata only, never the `SKILL.md` body. Secrets, credentials, absolute paths, full Graph records, and unbounded history never enter the Catalog.

`graph.mutate.edge_types` is optional: omission and an empty array both normalize to node-only mutation over approved entities, with no edge authority. `network.request.sources` is an object keyed by kebab-case source IDs, not a string list; each value fully declares a credential-free HTTPS origin, exact path/method allowlists, and a response-size limit. File scopes use relative patterns below `app://data` and do not include the `app://data/` prefix.

## 3. Role-specific projections

| Role | Receives | Does not receive |
| --- | --- | --- |
| Intent Router | Available intents, existing App summaries, Graph schemas, capability availability, and installed Skill metadata/availability | Controller source, Skill bodies, credentials, arbitrary adapter internals |
| Converse Agent | Read-only tool schemas, Graph/App summaries, Run behavior, relevant Skill bodies pinned to the current Run | Write tools, unselected Skill bodies, unapproved Widget grants, direct publication authority |
| Schema Alignment Agent | Data ontology, Capability Ontology, current App grants, user requirement | Runtime secrets or invented interfaces for uninstalled capabilities |
| Coding Agent | Exact approved schemas/grants, SDK subset, artifact constraints, recent bounded diagnostics | Full host SDK, other App grants, arbitrary file/network access |
| Verification Agent | Approved contract, staging manifest, extracted code usage | Authority to reinterpret or expand user approval |

Projections apply least information and carry `catalog_version`; a Widget's exact `grants_digest` reaches Coding and Verification only through the user-approved Runtime Contract. When accepting a proposal, the Catalog validates Graph entities, installed catalog IDs, action IDs, and current availability. The caller rejects nonexistent or unavailable IDs instead of guessing.

A Skill body is not part of `SystemCapabilityCatalog`. The Router uses metadata only to determine relevance. After selection, the Skill loader reads the body just in time from its content-addressed snapshot and pins `catalog_id + version + digest + bounded content` to the Durable Run. Later phases consume that Run snapshot instead of reparsing the current Market or installation directory. Updating a Skill cannot change an in-progress prompt, while Tool or Capability revocation still fails closed under current runtime policy.

## 4. Prompt composition

Prompt templates describe roles and decision rules. A Catalog renderer injects the dynamic capability block:

```text
[SYSTEM CAPABILITY CATALOG v2]
Capability ontology version: 1
Durable execution: plan -> alignment approval -> staging -> verification -> promotion
Widget grant categories:
- graph.query: entities[]; read only
- graph.mutate: entities[], operations[], edge_types[]
...
Installed actions:
- mcp:calendar:create-event (available; approval required)
Installed skills metadata:
- agent-skill:ambient:task-review (enabled; version 1.0.0)
[END SYSTEM CAPABILITY CATALOG]
```

The renderer must:

- Use fixed field order and deterministic serialization for hashing, tests, and audits.
- Bound item count, schema depth, and text length; overflow becomes a summary with retrievable IDs.
- Distinguish `available`, `unavailable`, `approval_required`, and `unsupported`.
- Explain supported alternatives, such as requesting an installed capability for authenticated network access rather than embedding a secret in a Widget.
- Emit the complete `scope_contract` for every category, including the nested `network.request.sources` object, relative file-path rules, Graph operation enums, and size bounds; field names alone are insufficient.
- Emit only stable metadata and availability for a Skill; the relevant-turn Skill loader handles its digest and body when pinning the Run snapshot.

If the first schema-alignment JSON violates this contract, the service gives the same model one bounded correction attempt containing the concrete validation error and its previous response. The correction still passes through the same backend normalizer and authorizer; it never relaxes validation, guesses authority, or expands scope automatically.

## 5. Runtime Contract for a Coding Agent

Before staging, the Workflow creates an immutable contract:

```json
{
  "contract_version": 1,
  "app_id": "daily-planner",
  "catalog_version": 1,
  "schemas": [{"id": "Task", "properties": {"title": "string"}}],
  "capabilities": [
    {"id": "graph.query", "scope": {"entities": ["Task"]}}
  ],
  "grants_digest": "sha256:...",
  "allowed_files": ["controller.js", "manifest.json", "README.md"]
}
```

The Coding Agent writes approved grants verbatim. It cannot add categories, expand scopes, or switch to forbidden APIs. Verification uses the same contract instead of asking a model whether the artifact “looks safe.”

## 6. Update rules and acceptance

- To add a system capability, extend the structured Catalog/ontology and tests first, then update its renderer and documentation.
- Adding an Instruction Skill does not automatically add a system capability or Capability Ontology category; the independent Skill Registry projects its metadata.
- To remove a capability, remove its Catalog item, SDK surface, stale prompt guidance, and compatibility handler together. Do not retain permanent deprecated paths.
- Snapshot tests verify stable Catalog fields and order. Contract tests verify every documented category exists in the ontology.
- Agent prompt tests verify that each role receives only its permitted projection and that runtime-unavailable features are not described as available.

See [Agent Skills](/en/agent/skills.md) for the package format, installation lifecycle, and context boundaries.
