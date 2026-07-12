# Ambient Agent: Exploratory Contribution Directions

## Status

These notes collect several possible contribution directions that emerged
while reading the current implementation and thinking about Ambient Agent's
product goals. I hope they can provide useful material for discussion, and I
would appreciate corrections where my understanding of the project is
incomplete.

For now, only the Ambient App Manifest is developed into a more concrete Phase
1 proposal. The other directions are kept brief so the immediate discussion
can remain focused.

The order below does not represent project priority.

## Shared perspective

Ambient Agent already combines several properties that are rarely considered
together:

- generated interfaces that can remain available as reusable Apps;
- a local, user-controlled workspace;
- an agent capable of creating and modifying those Apps;
- explicit attention to auditability, permissions, and generated-code safety;
- a test-first approach intended to keep an AI-generated codebase maintainable.

This creates an interesting engineering boundary. The challenge is not only to
generate another interface, but to let generated software become durable
without making it opaque or taking control away from the user.

The directions below explore different parts of that boundary. They share
three constraints:

1. The platform should make important state transitions observable.
2. Automation should not silently replace user intent or consent.
3. New concepts should be introduced only when they solve a concrete problem
   in the existing system.

## Ambient App Manifest

### Opportunity

An Ambient App currently has a durable directory, source files, and basic
platform metadata. Its purpose and relationship to the central graph schemas,
however, are still mostly inferred from its ID, title, conversation history,
or source code.

A small Manifest could turn that implicit meaning into a versioned,
machine-readable contract. In time, the same contract might provide consistent
input to App discovery, routing, permission review, evolution workflows, and
data-flow explanations.

The potential value is not the novelty of a Manifest by itself. Manifests are
a familiar software pattern. The useful part would be giving several Ambient
Agent subsystems one restrained source of descriptive truth, rather than
letting each subsystem independently infer what an App is intended to do.

### Engineering boundary

The Manifest should begin smaller than its possible future uses. The accepted
first-phase direction defines identity, purpose, contract version, App
version, optional intent hints, and references to registered central graph
schemas. Together, `description`, `intents`, and `schema_refs` provide minimal
capability context without introducing a formal capability taxonomy.

It should not simultaneously introduce automatic recommendations, permission
enforcement, router scoring changes, graph mutation behavior, rollback, data
schema migration, or a general plugin system. In particular, a declaration of
purpose, intent, or schema association must never be treated as permission.
The one-time `metadata.json` to `manifest.json` declaration migration is part
of Phase 1 and does not migrate graph or user data.

### Current status

Selected for a focused implementation after alignment in
[Issue #2](https://github.com/BillShiyaoZhang/ambient-agent/issues/2):

- [Manifest design directions](manifest-design-directions.md)
- [Manifest Phase 1 design](manifest-phase-1.md)

The implementation remains limited to the accepted Manifest contract,
validation, one-time legacy repair or migration, `AppManager` integration, API
compatibility, and tests.

## Reliable App evolution

### Opportunity

Ambient Apps are not disposable model responses. Once an App is pinned, used
over time, or connected to user data, modifying it resembles maintaining a
small piece of personal software.

That suggests a longer-term evolution path in which an agent does not need to
edit the active copy directly. It could prepare a candidate version, allow the
platform to validate it, show the user a concise change summary or preview,
and activate it only after the relevant checks have passed. Retaining the
previous working version could make recovery understandable rather than
dependent on reconstructing files after a failure.

This could turn generated-code modification from a best-effort operation into
an observable lifecycle:

```text
prepare -> validate -> review -> activate -> recover if necessary
```

The value would come less from storing more copies of files and more from
making the transition between working versions explicit.

### Engineering boundary

File history alone is not a complete reliability model. A meaningful design
would need to distinguish:

- source-code rollback from user-data rollback;
- validation failure from runtime failure;
- App versioning from Manifest schema versioning;
- a candidate that exists on disk from a version that is active;
- safe source changes from data-schema changes that require migration.

It may be reasonable for early prototypes to rely on simpler safeguards. A
full lifecycle should be introduced only if the project wants Ambient Apps to
be treated as durable user assets and is prepared to define these transitions.

### Current status

Exploratory and deferred. This is not part of Manifest Phase 1.

## Conversation-to-Skill

### Opportunity

Some useful personal tools begin as repeated conversations rather than an
explicit request to build an App. A user may repeatedly ask the agent to
organize a morning plan, compare a recurring set of options, or summarize the
same kind of information.

If the system can recognize a stable repeated goal—not merely repeated
wording—it could suggest an existing App or offer to create a reusable one.
This would connect conversation with Ambient Agent's distinguishing ability to
turn an interaction into a persistent interface.

The important product behavior may be the recommendation boundary:

```text
observe a repeated need
  -> check for an existing suitable App
  -> explain a possible reusable tool
  -> let the user decide
```

Handled carefully, this could help the system become more useful over time
without requiring the user to think like a developer or manually identify
every automation opportunity.

### Engineering boundary

The system should recommend rather than silently create or modify an App.
Repeated wording is not sufficient evidence of a durable need, and a useful
design would need:

- a definition of repeated semantic intent;
- conservative thresholds and recommendation frequency limits;
- a check for existing Apps before proposing a new one;
- an explanation of what would be created and what data it would use;
- explicit user confirmation;
- a global way to disable or dismiss this behavior.

An initial design might operate on local conversation summaries rather than a
new global behavioral profile. The feature should not become a pretext for
collecting more personal history than its recommendation requires.

### Current status

Exploratory and deferred. It requires a separate consent, recommendation, and
evaluation design. Manifest intent hints could eventually support it, but do
not implement it.

## Personal UI DNA

### Opportunity

Generated interfaces create the possibility that Apps can reflect stable user
preferences: information density, layout style, interaction patterns, or
accessibility needs. If these preferences were explicit and user-controlled,
they might help independently generated Apps feel coherent rather than
starting from generic defaults each time.

The potentially useful concept is not hidden personalization. It is a small,
inspectable preference model that the user can choose to apply when generating
or revising an App.

Such a model could make personalization portable across generated interfaces
while still allowing each App to retain an appropriate design for its own
purpose.

### Engineering boundary

This direction would be harmful if it inferred a permanent preference from a
single action or made personalization impossible to inspect.

A responsible design would likely need to be:

- disabled by default, or introduced through explicit opt-in;
- visible, editable, pausable, and deletable;
- divided into specific preferences rather than one opaque profile;
- confirmed after a stable pattern is observed rather than silently inferred;
- optional for each App generation;
- subordinate to accessibility, task requirements, and project defaults.

For example, "prefers compact task lists" is more accountable than a broad
claim that the system has learned the user's aesthetic identity.

### Current status

Exploratory and deferred. This appears less foundational than App reliability
and the Manifest contract, unless user-controlled personalization is already a
near-term product priority.

## Privacy Data Map

### Opportunity

Ambient Agent already treats auditability as a product concern. Raw LLM audit
logs are valuable for technical inspection, but a user may still need a more
direct answer to questions such as:

- Which information left the device?
- Which model or external service received it?
- Which App wrote local data?
- Which permission allowed an operation?
- Did an App's observed behavior match its declared purpose?

A Privacy Data Map could present these events as understandable data flows
across conversations, models, Apps, local storage, and external services.

Its strongest contribution would not be a visualization alone. It could
provide a shared provenance vocabulary for events that are currently observed
at different layers. That could make "privacy first" something a user can
inspect in normal operation, not only a property documented by developers.

### Engineering boundary

Privacy observability must not create an additional store of sensitive
content. The default event model should prefer metadata and categories—for
example, that location text was sent to a named model—rather than duplicating
the complete location text in another log.

A serious design would need to define:

- the event producers and a stable event schema;
- which data is recorded as content, classification, count, or reference;
- retention and deletion behavior;
- redaction rules;
- the difference between developer diagnostics and user explanations;
- how declared behavior could be compared with observed behavior;
- how to avoid implying completeness when some flows cannot be observed.

The Manifest might eventually describe intended behavior, while the data map
reports observed behavior. A mismatch could then be visible, but that requires
both sides to have precise semantics.

### Current status

Exploratory and deferred. It requires a separate event, retention, and privacy
model before UI design.

## Considered alternative: Ambient UI DSL

### Why it was considered

A structured UI DSL could represent common interfaces as validated data rather
than unrestricted source code. For Apps such as task lists, tables, forms,
settings panels, and standard dashboards, this could make several platform
concerns easier to handle:

- predictable rendering across desktop and mobile layouts;
- accessibility defaults;
- structural validation before activation;
- clearer permission and data-binding boundaries;
- safer transformations by an agent;
- more stable automated tests.

The strongest reason to consider a DSL would not be to make every App look the
same. It would be to make common interface behavior easier for the platform to
understand, verify, and adapt.

### Why a strict DSL is not recommended

Ambient Agent's ability to generate complete HTML, CSS, and JavaScript Apps is
also one of its important capabilities. Requiring every App to use a fixed DSL
would limit the system to components and interactions anticipated by the
platform.

That could make unconventional interfaces difficult or impossible, including:

- games and simulations;
- specialized animations;
- novel interaction patterns;
- advanced visualizations;
- interfaces using browser capabilities not represented by the DSL.

A sufficiently extensible DSL might eventually reproduce much of the
complexity of HTML, CSS, and JavaScript while still requiring a separate
parser, renderer, compatibility model, and migration path. At that point, the
project would maintain two web-platform abstractions without necessarily
gaining a clear safety boundary.

For that reason, replacing generated code with a mandatory DSL does not appear
to fit the project's current direction.

### Possible hybrid model

A more compatible approach could be an explicit two-mode model:

```text
Structured mode
  -> common Apps described through a constrained DSL

Code mode
  -> complex Apps implemented with HTML, CSS, and JavaScript
```

In a hybrid model:

- the user could see which mode an App uses;
- common Apps could receive stronger validation and responsive behavior;
- the DSL could allow controlled choices for layout, color, and supported
  extensions;
- complex Apps would retain the freedom of the existing code path;
- the platform could apply mode-specific review and permission policies.

This avoids forcing every App into the least expressive mode, but it also
introduces two App execution and maintenance paths.

### Engineering boundary

A complete hybrid design would need to define:

- how an agent chooses between structured and code mode;
- whether an App can migrate between modes;
- how the App Store and runtime expose the selected mode;
- how data binding and permissions differ between modes;
- how DSL versions remain backward compatible;
- whether structured Apps can embed custom code;
- where extensions stop before the DSL loses its safety properties;
- how testing, preview, and App evolution work in both paths.

Allowing arbitrary JavaScript extensions inside the DSL would weaken its
validation boundary. Prohibiting extensions entirely would preserve more
control but reduce expressiveness. That tradeoff should be resolved
deliberately rather than hidden behind an "extensible DSL" label.

### Current assessment

A strict DSL is not recommended because it would constrain the open-ended App
generation that distinguishes Ambient Agent.

A hybrid DSL may have real value for common, highly structured Apps, but its
complexity is significantly higher than introducing a small Manifest contract.
It does not appear suitable as a first contribution.

This alternative may be worth revisiting if concrete problems in mobile
adaptation, accessibility, validation, or permission control prove difficult
to solve cleanly through the existing code-based App model.

## How the directions relate

These ideas can form a coherent long-term sequence, but they do not need to be
implemented as one system:

```text
Manifest
  -> describes what an App is intended to be

Reliable App evolution
  -> controls how an App changes

Conversation-to-Skill
  -> proposes when a reusable App may be helpful

Personal UI DNA
  -> optionally supplies explicit user preferences

Privacy Data Map
  -> explains what the system actually did with data
```

The relationship is useful for checking interfaces between future features,
not for justifying a large combined implementation. Each direction should
still earn its own design, tests, and user value.

For example:

- A Manifest may provide discovery hints, but it does not decide when to make
  a recommendation.
- A Manifest may declare intended capabilities, but it does not grant them.
- A version number may identify an App revision, but it does not provide safe
  activation or rollback.
- A privacy view may compare declaration with observation, but it cannot make
  an imprecise declaration trustworthy.
- A preference model may influence generation, but it should not override the
  user's decision for a specific App.

## Suggested contribution sequence

If the maintainer considers any of these directions useful, a conservative
sequence could be:

1. Record the accepted Manifest contract and one-time migration boundary.
2. Implement only that accepted boundary with tests.
3. Observe whether routing, App maintenance, or user transparency has the next
   clearest problem in the working system.
4. Open a separate design discussion for that problem.

This sequence is only a contribution proposal. It is intended to keep changes
reviewable and to let evidence from the project guide later architecture.

## Maintainer alignment

Issue #2 established the Phase 1 direction: use a standalone `manifest.json`,
remove `metadata.json` from the final normal runtime paths, expose compact App
capability and schema context through normalized App records for future
routing, and keep the graph database as the single data center. The other
contribution directions in this document remain exploratory and require
separate discussions before implementation.
