# Agent Skills: Installation, Activation, and Security Boundaries

Ambient Agent supports the `SKILL.md` format from the [Agent Skills specification](https://agentskills.io/specification). A Skill is instruction and workflow knowledge loaded on demand; it is not executable code, a model Tool, a Capability grant, or an App. The MVP installs Skills only from bundled or administrator-configured local trusted Markets. Discovery, installation, and update never expand Agent, Widget, or Runtime authority.

## 1. Domain boundaries

| Object | Purpose | Execution and authorization |
| --- | --- | --- |
| Instruction Skill | Tells an Agent when and how to perform a class of tasks | Affects only the context of a relevant turn; has no effect by itself |
| Model Tool | A trusted local function callable by an Agent | Must already be registered with the Tool Gateway and still enforces effect, scope, approval, timeout, and idempotency |
| Executable Capability | A structured action exposed by MCP or a remote Agent | Executes through a Capability Manifest, input/result schemas, a Durable Run, and adapter permission |
| App UI | An interactive interface running in the Widget Runtime | May use only the exact grants approved in its Manifest V2 |

These objects can be composed but cannot impersonate one another. A Skill may explain how to use an existing Tool or reference an installed Capability action. It cannot create a Tool, change a ToolSpec, declare a new adapter, or turn installation into runtime permission. A Skill without a UI remains an Instruction Skill; it never becomes a system-level Tool merely because it is headless.

Instruction Skills use the independent `agent-skill:<namespace>:<name>` catalog ID space. Existing `CapabilityManifest.kind = "skill"` entries retain `skill:<provider>:<id>`. Structural separation prevents two independent registries from acquiring the same ID through concurrent check-then-write operations.

The design conclusion is that an installable, versioned, on-demand instruction layer is sound because it reuses the existing Agent and Durable Run. Treating the same text manifest as an executable plugin, permission declaration, or UI package is unsound because that would bypass existing Capability, approval, and Widget security boundaries. The MVP therefore implements context-only Skills.

## 2. `SKILL.md` contract

A standard Skill contains at least one `SKILL.md`:

```text
skill-name/
├── SKILL.md
└── market.json      # Ambient Market descriptor; not part of the Agent Skills standard
```

`SKILL.md` contains YAML frontmatter followed by a Markdown body. The MVP follows the standard fields:

- `name` matches the parent directory, uses lowercase letters, digits, and hyphens, and is at most 64 characters;
- `description` explains both what the Skill does and when to use it, and is at most 1,024 characters;
- `license`, `compatibility`, and string-valued `metadata` are optional public metadata;
- `allowed-tools` is an experimental compatibility declaration that describes the expected environment only.

The Agent Skills standard also permits optional directories such as `references/`, `assets/`, and `scripts/`. To keep the first installer's attack surface and snapshot contract small, an Ambient MVP Market entry accepts exactly the two regular files shown above; any additional file or directory rejects installation. This version therefore supports the standard metadata, body, and on-demand body loading, but does not read optional resources and never executes scripts.

Ambient applies additional package constraints:

- `SKILL.md` accepts only the Agent Skills standard fields above; Ambient extensions belong in the adjacent `market.json`;
- `market.json` declares `market_id`, `catalog_id`, title, provider, version, tags/icon/accent, `ontology_refs`, triggers, `surfaces`, and provenance; the MVP accepts only `agent_context` in `surfaces`;
- content digest, validation result, and timestamps in the installation record are produced by the Installer and cannot be trusted when self-reported by a package; only the bundled release Market is marked `bundled/verified` by the loader, while every `SKILL_MARKET_DIR` entry is `local/unverified` even if its `market.json` claims otherwise;
- `market.json.ontology_refs` may reference only canonical entity IDs already registered in the current `ambient-context`;
- the Skill directory, `SKILL.md`, and `market.json` must be real directories or regular files; symlinks, invalid UTF-8, duplicate or unknown fields, and content beyond size limits are rejected;
- `SKILL.md` is not evaluated as Jinja or any other template; its frontmatter and body are data;
- installation runs no hooks, downloads no dependencies, reads no remote content, and registers no Tools.

`allowed-tools` does not pre-approve a call. If a Tool is absent from the current turn, the current scope is insufficient, or its effect requires an interaction, the Skill declaration does not change the denial.

## 3. Market, installation state, and content snapshots

Skill Market and App Center are separate views:

- `GET /api/skill-market` lists installable packages, versions, summaries, sources, and digests from trusted sources;
- `POST /api/skills/install` installs the current Market version by `market_id` and also performs an explicit update; `PATCH /api/skills/{catalog_id}` changes `enabled` only, and `DELETE /api/skills/{catalog_id}` uninstalls;
- `GET /api/app-store` remains the launcher and layout for installed Apps, Skills, and executable capabilities; it is not a remote Market index.

The MVP uses the bundled Market by default. An administrator may point `SKILL_MARKET_DIR` at a trusted local Market root. It does not accept arbitrary URLs, Git repositories, or unverified archives. Installation proceeds as follows:

1. parse and validate `SKILL.md`, file boundaries, and public metadata;
2. compute a stable digest over the exact `SKILL.md` and `market.json` content;
3. write an immutable, content-addressed local snapshot;
4. atomically write the installation record and enabled state to workspace SQLite;
5. expose the Skill through the installed catalog and Agent metadata projection only after the complete transaction commits.

Installation state and content have separate responsibilities:

- `workspace/.ambient/skills.db` is the source of truth for `catalog_id`, version, digest, source, enabled state, and installation time; the Market API compares that record with the current Market to derive `not_installed | installed | update_available | market_older | integrity_conflict`, and it never writes an uninstalled item to the database;
- `workspace/.ambient/skills/packages/<sha256>/` contains only the exact validated `SKILL.md` and `market.json` for that installation;
- installing the same ID, version, and digest is idempotent; only a higher SemVer precedence reports `update_available` and permits an explicit update; an older version reports `market_older` and the normal install API rejects the downgrade; equal precedence (including build-metadata-only changes) with a different digest reports `integrity_conflict` and cannot overwrite the installation;
- update creates the new snapshot before atomically switching the installation record; failure leaves the old version usable;
- uninstall removes only the installation record. The content-addressed package remains as an immutable cache so request-path cleanup cannot race a concurrent reinstall. Future GC must use locking or leases, a grace period, and mark-and-sweep. An already-started Run uses its own pinned body copy and does not depend on the installation directory remaining present.

Installed snapshots continue to work when the Market is temporarily unavailable. A missing, corrupt, or digest-mismatched snapshot makes the Skill unavailable rather than falling back to the newest content with the same name.

## 4. Progressive disclosure and Run snapshots

Ambient follows Agent Skills progressive disclosure:

1. **Global metadata**: project only the stable ID, name, description, version, enabled state, and availability of installed Skills to the Router; only enabled and available items are selectable, and neither the digest nor every body enters every prompt.
2. **Body for a relevant turn**: load the complete bounded `SKILL.md` only after explicit selection or after the Skill is determined to be relevant to the current request.

After selection, the Durable Run pins:

```text
catalog_id + version + digest + bounded instruction content
```

If the Run enters `converse`, subsequent model calls use only that snapshot; the MVP does not inject Skill bodies into Widget planning, Graph mutation, or other effect workflows. Updating, disabling, or uninstalling the Skill during the Run does not drift the recovered converse prompt. Actual Tools, Capabilities, and permissions still read current policy and fail closed on every execution. LLM audit records the digest of every read Skill without copying unbounded bodies into events or the KG.

Within a Run, the full body exists only in the private checkpoint. Public Run lists, details, and the replayable WebSocket remove `instructions` and `allowed_tools`, returning only audit metadata such as `catalog_id`, version, and digest. This preserves deterministic recovery without widening the Skill body into public Run API data.

The Skill body is injected as bounded guidance below Ambient's core system policy. Text such as “ignore previous rules,” “assume permission,” or “use an unavailable Tool” cannot change the real tool schema, Capability Catalog, or authorizer.

## 5. Tools, Capabilities, and permissions

Activating a Skill is a read-only context operation, not effect approval. An action must pass each real boundary:

```text
Skill guidance
  -> supplied Tool schema or installed Capability action
  -> input validation
  -> Tool/adapter policy and permission interaction
  -> Durable Run effect, recovery, and audit
```

Guidance in a Skill can rely only on stable Tool names actually supplied in the current turn or exact installed `catalog_id + action_id` pairs. Its body cannot declare a raw MCP command, arbitrary `app_id + tool_name`, remote Agent URL, secret, or recovery guarantee. Installing a Skill neither installs dependencies nor approves MCP spawn, network, file, Graph mutation, or irreversible effects. When a referenced capability is absent, the call fails through the existing `unsupported` or permission-interaction semantics.

## 6. App Center and UI

The MVP draws a strict App Center distinction between Instruction Skills and executable items:

- an installed Skill uses `launch_mode = "details"`; its details view exposes source, digest, ontology references, enable/disable, and uninstall, but no background Run or Generate UI button;
- a Skill takes effect through a relevant Agent turn and remains useful without its own UI;
- a workflow that needs a structured action or UI reuses an existing executable Capability or generated App; headless Capability actions, Durable UI-generation Runs, minimal `capability.invoke` grants, staging, verification, and atomic promotion retain their existing semantics;
- this version creates no Skill-to-UI binding and never generates or publishes an App merely because a Skill is installed.

A Market package cannot carry a live Controller that bypasses Widget verification. See [Widgets and App Center](/en/architecture/apps.md) for the complete App rules.

## 7. Ontology and knowledge graph

`market.json.ontology_refs` are hints that reference existing canonical schemas only:

- installing, enabling, updating, or uninstalling a Skill never grows the `ambient-context` ontology;
- an unknown entity reference does not create a schema automatically; the reference is invalid or the Skill becomes unavailable;
- `ontology_refs` do not grant `graph.query` or `graph.mutate`;
- the Skill manifest, body, digest, source, installation state, permissions, and configuration never enter the KG.

User-context facts actually produced while using a Skill, such as a `Task`, `Event`, or `Note`, must still reuse a canonical entity and pass the existing schema-alignment, Graph-preflight, user-interaction, and atomic-mutation path before entering the KG. The existence of the installed Skill is system installation state, not a user-context fact.

## 8. Threat model and failure semantics

| Risk or failure | Required semantics |
| --- | --- |
| Malicious body attempts prompt injection | Load on demand only; core policy wins; execution boundaries still fail closed |
| Extra files, symlink, oversized content, or invalid UTF-8 | Installation fails without exposing partial state |
| Installation is treated as approval or `allowed-tools` is forged | Successful installation still grants no Tool, Capability, or adapter permission |
| A higher Market version appears | Report `update_available`; switch the validated snapshot only after an explicit update |
| Content digest changes under the same version | The listing may flag the mismatch, but install rejects it as an integrity conflict and never overwrites the installed snapshot |
| Market version is older than the installation | Report `market_older` and reject the normal update; this version has no implicit or silent downgrade path |
| Context token denial of service | Project metadata globally; bound bodies, Skill count, and total characters |
| Update or uninstall races with an active Run | The Run uses its pinned bounded body and never rereads the installation directory |
| Snapshot is missing or has the wrong digest | Skill becomes unavailable; implicit selection skips it, explicit selection reports an integrity error, and no other same-name version is substituted |
| A checkpoint selection marker contradicts its snapshot | Resume fails with `invalid_skill_snapshot`; it never silently becomes an empty selection or re-resolves current installation state |
| Body names a missing Tool or unapproved adapter | Existing interaction/failure semantics apply; never pretend success |
| Skill is mistaken for an executable or UI item | App Center opens details only; execution and UI generation remain existing Capability/App behavior |
| Market is unavailable | Installed Skills continue to work; the Market listing returns an explicit error |

Install, update, enable/disable, and uninstall are serialized and committed atomically in SQLite transactions; idempotent installation and stable catalog IDs avoid duplicate records. Registry or snapshot corruption is reported as an error and must not be treated as an empty catalog or substituted with the latest same-name content.

## 9. Compatibility

- Manifest V2, Capability Ontology, Tool Gateway, Capability actions, and the Durable Run protocol remain unchanged;
- existing `CapabilityManifest.kind = "skill"` items retain their executable-capability meaning and `skill:` IDs; Instruction Skills use `agent-skill:` IDs, and the two are never silently reinterpreted;
- an old workspace without Skill installation tables or snapshots means “no Skills installed”; migration is idempotent and does not alter Apps, Capabilities, layout, or the KG;
- a new Run explicitly starts with `skill_selection_state = pending`, which becomes `pinned | pinned_none` after routing; only a legacy checkpoint missing both marker and snapshot resumes as `pinned_none`, so it cannot gain a subsequently installed Skill; an unknown marker, missing pinned snapshot, or any contradictory combination fails closed;
- existing App Center items and layout fields remain readable; an Instruction Skill is a backward-compatible `details` launch mode and does not change Capability action/UI behavior;
- Skill manifest/version, installation-registry revision, and content digest use independent version domains and never reuse or spuriously increment App Manifest, Capability Ontology, or Runtime Contract versions.

## 10. MVP non-goals

The following are outside this version:

- installation from an arbitrary network URL, Git repository, or community archive;
- packaging or reading the standard's optional `references/`, `assets/`, or `scripts/` directories;
- executing a Skill-provided script, install hook, shell command, or dynamic Python/JavaScript Tool;
- automatic installation, authentication, or approval of Skill dependencies;
- proactive background Skill execution, scheduled tasks, or effects outside a user Run;
- direct UI generation for an Instruction Skill or a Skill-to-UI lifecycle binding;
- Market payments, ratings, reviews, publisher self-service uploads, and automatic updates;
- modeling installed Skills as a second ontology or writing them into the KG;
- placing unbounded bodies from multiple Skills into every prompt or composing Skills automatically without stable snapshots.
