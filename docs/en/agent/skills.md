# Agent Skills: Installation, Activation, and Security Boundaries

Ambient Agent supports the `SKILL.md` format from the [Agent Skills specification](https://agentskills.io/specification). A Skill is instruction and workflow knowledge loaded on demand; it is not executable code, a model Tool, a Capability grant, or an App. The Catalog can aggregate the bundled Market, administrator-configured local Markets, and GitHub sources pinned to immutable Git commits and expected SHA-256 values. Making a source discoverable does not trust its packages: external packages install disabled and quarantined, then require a digest-bound `agent.context.inject` decision. Discovery, download, installation, and update never expand Agent, Widget, or Runtime authority.

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
- the `agent-skill:ambient-agent:*` namespace is reserved for loader-derived bundled packages; an external Market cannot claim it;
- `market.json.ontology_refs` may reference only canonical entity IDs already registered in the current `ambient-context`;
- the Skill directory, `SKILL.md`, and `market.json` must be real directories or regular files; symlinks, invalid UTF-8, duplicate or unknown fields, and content beyond size limits are rejected;
- `SKILL.md` is not evaluated as Jinja or any other template; its frontmatter and body are data;
- installation runs no hooks, downloads no dependencies, and registers no Tools. The GitHub Provider may read only one administrator-pinned, expected-SHA-256 `SKILL.md`; package instructions cannot trigger another network read.

`allowed-tools` does not pre-approve a call. If a Tool is absent from the current turn, the current scope is insufficient, or its effect requires an interaction, the Skill declaration does not change the denial.

## 3. Market, installation state, and content snapshots

Skill Market and App Center are separate views:

- `GET /api/skill-market` lists installable packages, versions, summaries, source toggle state, trust class, and digests; `PATCH /api/skill-market/sources/{source_id}` changes a workspace source preference using the current Skill-registry revision as its CAS condition;
- `POST /api/skills/install` installs the current Market version by `market_id` and also performs an explicit update; `PATCH /api/skills/{catalog_id}` changes the enabled state of a trusted or already-authorized installation, `PATCH /api/skills/{catalog_id}/authorization` grants, changes, or revokes external context injection, and `DELETE /api/skills/{catalog_id}` uninstalls;
- `GET /api/app-store` remains the launcher and layout for installed Apps, Skills, and executable capabilities; it is not a remote Market index.

The default Catalog always contains the bundled Market. `SKILL_MARKET_DIR` remains a compatible configuration that is added to the Catalog instead of replacing the bundled source. An administrator may also point `SKILL_CATALOG_CONFIG` at a versioned JSON configuration declaring one or more pinned GitHub Providers. Configuration makes packages discoverable, not trusted or authorized. The install API still accepts no arbitrary URL, Git repository, or archive. Installation proceeds as follows:

1. parse and validate `SKILL.md`, file boundaries, and public metadata;
2. compute a stable digest over the exact `SKILL.md` and `market.json` content;
3. write an immutable, content-addressed local snapshot;
4. atomically write the installation, trust, authorization, and enabled state to workspace SQLite;
5. expose the Skill through the installed catalog and Agent metadata projection only after the complete transaction commits.

The authorization state machine is deliberately asymmetric:

- a bundled, loader-verified Skill is `trusted + implicit` and preserves the existing enable/disable behavior;
- an external Skill is first installed as `quarantined + none + disabled`;
- approving `explicit_only` allows only an explicit `/skill <name>` selection; approving `implicit` additionally permits deterministic relevance matching;
- approval verifies the immutable package again, records the exact authorized digest, and enables it in the same SQLite transaction;
- revocation sets `quarantined + none + disabled` without needing to read a possibly damaged package;
- an external package update always revokes the old decision and atomically quarantines the new bytes. A version label is never an authorization identity.

The immutable principal is `catalog_id@skill_digest`. The grant audit digest is SHA-256 over canonical JSON containing exactly:

```json
{
  "activation_policy": "explicit_only | implicit",
  "capability": "agent.context.inject",
  "catalog_id": "agent-skill:<namespace>:<name>",
  "skill_digest": "sha256:<package-digest>"
}
```

This digest is an audit/CAS identity, not a signature and not a Widget grant. Skill authorization mutations require the current Skill-registry revision; other mutation APIs accept it for compatibility, and App Center always sends it. Concurrent authorization, update, enable, revoke, and uninstall operations therefore fail with a conflict instead of silently overwriting one another.

This MVP consent channel assumes a single-user workspace: a trusted Host shows the review confirmation and submits the digest/revision-bound decision. It does not cryptographically attest a human actor and is not a pending Durable Run interaction. A multi-user or remotely administered Market must add authenticated actor identity, append-only decision audit, and a one-time interaction challenge before treating this as delegated consent.

Installation state and content have separate responsibilities:

- `workspace/.ambient/skills.db` is the control-plane source of truth for `catalog_id`, version, digest, source, enabled state, authorization state/policy/digest, and installation time; the Market API compares that record with the current Market to derive `not_installed | installed | update_available | market_older | integrity_conflict`, and it never writes an uninstalled item to the database;
- `workspace/.ambient/skills/packages/<sha256>/` contains only the exact validated `SKILL.md` and `market.json` for that installation;
- installing the same ID, version, and digest is idempotent; only a higher SemVer precedence reports `update_available` and permits an explicit update; an older version reports `market_older` and the normal install API rejects the downgrade; equal precedence (including build-metadata-only changes) with a different digest reports `integrity_conflict` and cannot overwrite the installation;
- update creates the new snapshot before atomically switching the installation record; failure leaves the old version usable;
- uninstall removes only the installation record. The content-addressed package remains as an immutable cache so request-path cleanup cannot race a concurrent reinstall. Future GC must use locking or leases, a grace period, and mark-and-sweep. An already-started Run uses its own pinned body copy and does not depend on the installation directory remaining present.

Installed snapshots continue to work when the Market is temporarily unavailable. A missing, corrupt, or digest-mismatched snapshot makes the Skill unavailable rather than falling back to the newest content with the same name.

### 3.1 Multi-source Catalog and supply-chain boundary

`SkillCatalog` is a discovery aggregator, not a new execution runtime. Each Provider only lists normalized candidate snapshots and returns its own health:

| Provider | Default | Installable content | Trust semantics |
| --- | --- | --- | --- |
| `bundled` | Required and release-loaded | `SKILL.md + market.json` | Only this loader may produce `bundled/verified` |
| `local` | Optional through `SKILL_MARKET_DIR` | Two regular files | Always `local/unverified` |
| `github` | Optional through `SKILL_CATALOG_CONFIG` | One expected-SHA-256 `SKILL.md` at a pinned commit | Always `local/unverified`; a commit/hash proves reproducibility, not publisher trust |
| Future registry/search | Disabled by default | Discovery metadata only, or conversion into the immutable snapshot above | Rank, downloads, platform review, and upstream scans are advisory signals only |

The minimum Provider contract is a stable `source_id`, `kind`, `required`, `list_entries()`, and bounded errors. The Catalog owns deterministic ordering, cross-source `market_id/catalog_id` conflict detection, and failure isolation. A required-source failure fails the Catalog request. An optional failure appears as `unavailable` in `GET /api/skill-market.sources` while other sources remain discoverable and installed snapshots remain usable. The same ID from two healthy sources fails the whole merge; Provider order never silently wins.

Every configured source has a workspace-level `enabled` preference that defaults to `true`, including bundled, local, and GitHub sources. App Center always shows every configured source and lets the user toggle each one:

- a newly configured source with no stored preference is enabled automatically, so additive Providers do not disappear because of migration defaults;
- a disabled source is not asked for `list_entries()`, so it performs no network or local-Market read. The API returns the source with `status = "disabled"` and `enabled = false`, but returns none of its candidate entries;
- disabling affects discovery and new install/update requests only. Existing content-addressed snapshots, approvals, and active Runs do not change;
- installing a `market_id` from a disabled source fails even when the install API is called directly;
- preferences live in a control-plane table in `.ambient/skills.db` and share the Skill-registry revision with installation and authorization mutations, preventing silent toggle/install overwrites;
- a source toggle is not trust, authorization, a network grant, or the installed Skill enabled bit. Re-enabling only restores discovery; an external Skill still installs quarantined and requires digest-bound approval.

GitHub configuration version is `1`, and both Provider and entry data are administrator-controlled. Every entry declares:

```json
{
  "repository": "owner/repository",
  "commit": "40-character-lowercase-git-sha",
  "path": "skills/example",
  "sha256": "sha256:<SKILL.md-sha256>",
  "files": ["SKILL.md"],
  "namespace": "github-owner-repository",
  "title": "Example",
  "provider": "Publisher",
  "tags": ["example"],
  "triggers": ["example workflow"],
  "ontology_refs": []
}
```

The Host constructs only `raw.githubusercontent.com/<repo>/<commit>/<path>/SKILL.md`, bounds the response, timeout, and redirects, verifies the exact SHA-256, then writes a content-addressed Catalog cache in the workspace. `files` must equal `["SKILL.md"]`, so an upstream Skill with `scripts/`, `references/`, `assets/`, hooks, dependencies, or executable entry points is incompatible with the current profile and cannot be installed. A cache hit is re-hashed. A network failure may use only an already verified cache for the same expected hash—never a branch, tag, HEAD, or different commit.

Catalog source identity and installation trust are shown separately:

- `catalog_source` records Provider ID/kind, `source_uri`, `source_revision`, `upstream_hash`, and `update_strategy`;
- `provenance.digest` is Ambient's package digest over the exact `SKILL.md` and synthesized `market.json`;
- a Git commit and upstream file hash are supply-chain pins; they never set `provenance.verified` to true or bypass quarantine approval;
- local Markets retain SemVer comparison. A GitHub installation `version` equals its commit rather than inventing SemVer, and uses `content_hash`: changed bytes under the same commit are an integrity conflict; a different commit/hash is an explicit `update_available`, and updating always revokes the old approval;
- API `version = 1` remains unchanged. `sources`, `catalog_source`, and `package_compatibility` are additive fields.

A mature market is integrated through a new Provider adapter, never by embedding its permission model, install command, or executor into Ambient. `skills.sh` is a suitable future search and audit-signal source, but each result must still resolve to a pinned commit/hash standalone snapshot. A result without an immutable revision or complete file inventory is display-only and cannot install. Entries requiring scripts, MCP, network, files, or UI must become a Capability, Plugin, or Widget and pass that runtime's sandbox and grant channel; they cannot widen `agent.context.inject`.

The repository ships a **disabled-by-default** Anthropic official-source
configuration at
[`backend/catalogs/anthropic.json`](https://github.com/BillShiyaoZhang/ambient-agent/blob/main/backend/catalogs/anthropic.json).
Host development can set
`SKILL_CATALOG_CONFIG=backend/catalogs/anthropic.json`; Docker can set
`SKILL_CATALOG_CONFIG=/app/backend/catalogs/anthropic.json`. It pins an exact
`anthropics/skills` commit and includes only `doc-coauthoring`, whose directory
contains exactly one `SKILL.md` at that revision. The other top-level Skills
carry scripts, references, or assets and therefore do not enter the current
profile. This curated list is an auditable compatibility list, not Ambient
authorization of Anthropic content; first installation is still quarantined.

## 4. Progressive disclosure and Run snapshots

Ambient follows Agent Skills progressive disclosure, with different trust channels:

1. **Bundled global metadata**: project bounded metadata only for loader-verified bundled Skills to the Router. External metadata is untrusted natural language and never enters `SystemCapabilityCatalog`, even after the user authorizes its body.
2. **Deterministic selection**: `SkillManager` selects enabled Skills without an LLM. An `explicit_only` external Skill requires `/skill <name>`; an `implicit` Skill may match bounded triggers, identity metadata, or description terms.
3. **Body for a relevant turn**: load the complete bounded `SKILL.md` only after selection. Selection does not interpret `allowed-tools` and cannot create a capability.

After selection, the Durable Run pins:

```text
catalog_id + version + digest + bounded instruction content
+ trust class + activation policy + authorized digest + principal/grant digest
```

If the Run enters `converse`, subsequent model calls use only that snapshot; the MVP does not inject Skill bodies into Widget planning, Graph mutation, or other effect workflows. Updating, disabling, or uninstalling the Skill during the Run does not drift the recovered converse prompt. Actual Tools, Capabilities, and permissions still read current policy and fail closed on every execution. LLM audit records the digest of every read Skill without copying unbounded bodies into events or the KG.

Within a Run, the full body exists only in the private checkpoint. Public Run lists, details, and the replayable WebSocket remove `instructions` and `allowed_tools`, returning only audit metadata such as `catalog_id`, version, and digest. This preserves deterministic recovery without widening the Skill body into public Run API data.

Bundled and external bodies use different prompt channels:

- a bundled body remains trusted procedural system guidance for backward compatibility and retains the existing bounded read-only Converse tools;
- an external body is wrapped as clearly marked untrusted data in a separate user-role message and is never concatenated into the system prompt;
- selecting any external body deterministically forces the Run to `Converse` before LLM routing. The external text therefore never reaches the Router and cannot steer the Run into Widget generation, Graph mutation, a Capability action, or another effect workflow;
- that Converse call receives no model Tools, no tool context, no workspace read scope, no prior chat history, no durable summary, and no active App/Graph artifacts. It receives only the bounded current user request, core policy, and the external untrusted-data envelope.
- its visible reply is persisted with `display_only` context policy and exact Skill provenance. Later Routers, ordinary prompts, artifact discovery, and durable summaries exclude that reply, so third-party influence cannot escape through assistant-message history on the next turn.

This is a **semantic containment boundary**, not an operating-system sandbox. There is no third-party process in this context-only package format: the installer accepts only `SKILL.md` and `market.json`, and never executes scripts. If a future package needs executable code, it must be represented as an existing executable Capability or verified Widget and pass that runtime's process isolation, exact grants, approval, Durable Run, and audit path; `agent.context.inject` can never authorize execution.

Text such as “ignore previous rules,” “assume permission,” or “use an unavailable Tool” cannot change the real tool schema, Capability Catalog, or authorizer. A Run pins the exact bytes and grant audit identity when selection succeeds, but before every not-yet-started external model call it first verifies package integrity, then rereads the live installation, digest, enabled state, policy, and authorization immediately next to `provider.generate`; that final read is the authorization-admission linearization point. A revocation, update, uninstall, or policy change committed before admission therefore blocks a pending, resumed, or retried injection. A change after admission is an in-flight cancellation case: it cannot cancel a request already sent to the provider or make a model forget a completed response.

## 5. Tools, Capabilities, and permissions

Activating a Skill grants only bounded context injection, not an effect approval. An action must pass each real boundary:

```text
Skill guidance
  -> supplied Tool schema or installed Capability action
  -> input validation
  -> Tool/adapter policy and permission interaction
  -> Durable Run effect, recovery, and audit
```

Guidance in a Skill can rely only on stable Tool names actually supplied in the current turn or exact installed `catalog_id + action_id` pairs. Its body cannot declare a raw MCP command, arbitrary `app_id + tool_name`, remote Agent URL, secret, or recovery guarantee. Installing or authorizing a Skill neither installs dependencies nor approves Tool use, data access, MCP spawn, network, file, Graph mutation, or irreversible effects. In the external semantic sandbox, no such Tools are supplied at all. Outside that sandbox, when a referenced capability is absent, the call fails through the existing `unsupported` or permission-interaction semantics.

## 6. App Center and UI

The MVP draws a strict App Center distinction between Instruction Skills and executable items:

- an installed Skill uses `launch_mode = "details"`; its details view exposes source, digest, ontology references, authorization principal/grant digest, and uninstall, but no background Run or Generate UI button;
- an external Skill shows an explicit quarantine warning instead of a normal enable toggle. The user can approve explicit-only activation, approve implicit matching, change policy, or revoke; each confirmation states that `agent.context.inject` grants no Tool, data, network, or file access;
- a trusted bundled Skill retains the existing enable/disable control;
- a Skill takes effect through a relevant Agent turn and remains useful without its own UI;
- a workflow that needs a structured action or UI reuses an existing executable Capability or generated App; headless Capability actions, Durable UI-generation Runs, minimal `capability.invoke` grants, staging, verification, and atomic promotion retain their existing semantics;
- this version creates no Skill-to-UI binding and never generates or publishes an App merely because a Skill is installed.

A Market package cannot carry a live Controller that bypasses Widget verification. See [Widgets and App Center](/en/architecture/apps.md) for the complete App rules.

## 7. Ontology and knowledge graph

`market.json.ontology_refs` are hints that reference existing canonical schemas only:

- installing, enabling, updating, or uninstalling a Skill never grows the `ambient-context` ontology;
- an unknown entity reference does not create a schema automatically; the reference is invalid or the Skill becomes unavailable;
- `ontology_refs` do not grant `graph.query` or `graph.mutate`;
- the Skill manifest, body, digest, source, installation state, authorization decision, and configuration never enter the KG.

Authorization is control-plane security state in `.ambient/skills.db`, not domain knowledge and not a new Capability Ontology node. `ontology_refs` remain validated references to existing canonical entities only.

User-context facts actually produced while using a Skill, such as a `Task`, `Event`, or `Note`, must still reuse a canonical entity and pass the existing schema-alignment, Graph-preflight, user-interaction, and atomic-mutation path before entering the KG. The existence of the installed Skill is system installation state, not a user-context fact.

## 8. Threat model and failure semantics

| Risk or failure | Required semantics |
| --- | --- |
| Malicious body attempts prompt injection | Load on demand only; core policy wins; execution boundaries still fail closed |
| External metadata attacks the Router before body selection | Never project external Skills into `SystemCapabilityCatalog`; select them deterministically in `SkillManager` |
| External body tries to route into an effect workflow | Force bounded Converse before Router invocation; provide no Tools, history, summary, workspace scope, or artifacts |
| Skill-influenced reply becomes trusted history on a later turn | Persist it for display/audit as `display_only`, but exclude it from Router, prompt, artifact, and summary reuse |
| Extra files, symlink, oversized content, or invalid UTF-8 | Installation fails without exposing partial state |
| Installation is treated as approval or `allowed-tools` is forged | External installation is disabled and quarantined; authorization grants context injection only |
| Stale approval races a package update | Require the exact package digest and registry revision; mismatch conflicts, and update revokes the old decision |
| A failed Run is retried after revocation or update | Revalidate the live exact grant and package immediately before model use; never rely on the self-contained snapshot hash alone |
| External package claims bundled identity | Loader-derived trust plus reserved namespace and exact source/catalog validation fail closed |
| User revokes a damaged external package | Revocation does not load package bytes and atomically disables/quarantines the record |
| A higher Market version appears | Report `update_available`; switch the validated snapshot only after an explicit update |
| Content digest changes under the same version | The listing may flag the mismatch, but install rejects it as an integrity conflict and never overwrites the installed snapshot |
| Market version is older than the installation | Report `market_older` and reject the normal update; this version has no implicit or silent downgrade path |
| Context token denial of service | Project metadata globally; bound bodies, Skill count, and total characters |
| Update or uninstall races with an active Run | The Run uses its pinned bounded body and never rereads the installation directory |
| Snapshot is missing or has the wrong digest | Skill becomes unavailable; implicit selection skips it, explicit selection reports an integrity error, and no other same-name version is substituted |
| A checkpoint selection marker contradicts its snapshot | Resume fails with `invalid_skill_snapshot`; it never silently becomes an empty selection or re-resolves current installation state |
| Body names a missing Tool or unapproved adapter | Existing interaction/failure semantics apply; never pretend success |
| Skill is mistaken for an executable or UI item | App Center opens details only; execution and UI generation remain existing Capability/App behavior |
| An optional Catalog source is unavailable | Installed Skills continue to work; that source is `unavailable` and other sources still return |
| The user disables a Catalog source | Do not call the Provider, return no candidates, and reject installs from it; installed snapshots keep working |
| The same ID appears in multiple sources | Fail the Catalog merge until the administrator removes the ambiguity |
| GitHub commit/hash mismatch or redirect | Reject the candidate; only a verified cache for the same expected hash may be used |
| Upstream package includes scripts/references/assets | Mark incompatible and forbid installation; executable behavior uses Capability/Plugin/Widget |

Install, update, enable/disable, and uninstall are serialized and committed atomically in SQLite transactions; idempotent installation and stable catalog IDs avoid duplicate records. Registry or snapshot corruption is reported as an error and must not be treated as an empty catalog or substituted with the latest same-name content.

## 9. Compatibility

- Manifest V2, Capability Ontology, Tool Gateway, Capability actions, and the Durable Run protocol remain unchanged;
- existing `CapabilityManifest.kind = "skill"` items retain their executable-capability meaning and `skill:` IDs; Instruction Skills use `agent-skill:` IDs, and the two are never silently reinterpreted;
- an old workspace without Skill installation tables or snapshots means “no Skills installed”; migration is additive and does not alter Apps, Capabilities, layout, or the KG. Existing bundled records acquire loader-derived `trusted + implicit`; every legacy external/local record is disabled and quarantined so a historical `enabled` bit cannot become an authorization grant;
- an old workspace without the source-preference table treats every current and future source as enabled; only an explicit stored `enabled = false` disables one;
- a new Run explicitly starts with `skill_selection_state = pending`, which becomes `pinned | pinned_none` after routing; only a legacy checkpoint missing both marker and snapshot resumes as `pinned_none`, so it cannot gain a subsequently installed Skill; an unknown marker, missing pinned snapshot, or any contradictory combination fails closed;
- existing App Center items and layout fields remain readable; an Instruction Skill is a backward-compatible `details` launch mode and does not change Capability action/UI behavior;
- the Capability Ontology and Widget grant schema remain unchanged. `agent.context.inject` is a Skill control-plane decision and deliberately does not create a second executable-grant vocabulary;
- Skill manifest/version, installation-registry revision, and content digest use independent version domains and never reuse or spuriously increment App Manifest, Capability Ontology, or Runtime Contract versions.

## 10. MVP non-goals

The following are outside this version:

- installation from a request-supplied arbitrary URL, mutable Git branch/tag, or community archive;
- automatic GitHub repository enumeration or direct execution of a third-party market install command;
- treating `skills.sh`, a community registry, popularity, publisher badges, or upstream malware scans as an Ambient trust grant;
- packaging or reading the standard's optional `references/`, `assets/`, or `scripts/` directories;
- executing a Skill-provided script, install hook, shell command, or dynamic Python/JavaScript Tool;
- automatic installation, authentication, or approval of Skill dependencies;
- proactive background Skill execution, scheduled tasks, or effects outside a user Run;
- direct UI generation for an Instruction Skill or a Skill-to-UI lifecycle binding;
- Market payments, ratings, reviews, publisher self-service uploads, and automatic updates;
- modeling installed Skills as a second ontology or writing them into the KG;
- placing unbounded bodies from multiple Skills into every prompt or composing Skills automatically without stable snapshots.
