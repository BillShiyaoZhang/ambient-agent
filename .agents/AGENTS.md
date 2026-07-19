# Ambient Agent Project Rules

This document outlines workspace-specific rules for coding agents. All agents MUST follow these instructions when working on this project.

## 1. Documentation-Driven Development (DDD) & UML Synchronization

- **Docs-First development**: For any new features, API updates, or database model changes, you MUST first update or write the corresponding documentation/UML designs in the `docs/` folder before making any codebase changes. Development must align to the documented specification.
- **UML Class Diagram Verification**: The class diagrams in `docs/architecture/uml.md` (high-level architecture) and `docs/agent/harness.md` (agent harness architecture) serve as our blueprints. Any modification to database models (`models.py`) or core service classes must be documented in these files.
- **Run Verification Script**: After any changes affecting design/schemas or models, run the verification script to check for compliance:
  ```bash
  uv run python scripts/verify_uml.py
  ```
- **Keep UML Concise (Subset Model)**: Only document core public classes, fields, and main methods in `docs/architecture/uml.md`. Internal helpers or minor private fields can be omitted to avoid noise, but anything documented *must* match the code.
- **Python Project Guidelines**: Always use `uv` for python virtual environment operations when running command line commands.

## 2. Test-Driven Development (TDD) Requirements

All feature work, behavior changes, API changes, schema changes, and bug fixes MUST follow the Red–Green–Refactor cycle. Tests are part of the specification, not a final verification step.

- **Document first**: Update the relevant design/API/UML documentation before changing implementation code. Record observable behavior, edge cases, compatibility requirements, and acceptance criteria.
- **Write the failing test first (Red)**: Add a focused, deterministic test for the smallest behavior slice. Run it and confirm that it fails because the requested behavior is missing or incorrect. Do not skip the red step by writing a test that passes against the old behavior.
- **Implement the minimum (Green)**: Make the smallest production change that satisfies the new test. Keep external services, clocks, randomness, and network calls mocked or injected so tests remain local and repeatable.
- **Refactor under protection**: After the focused test passes, improve structure, naming, and reuse without changing behavior. Re-run the focused tests after each refactor.
- **Test the public contract**: Prefer user-observable behavior, API contracts, pure state/geometry functions, and component interactions over implementation-detail assertions. Every new public event, endpoint, schema field, or SDK method needs both success and failure/compatibility coverage.
- **Frontend tests**: Use Vitest and Testing Library for component behavior, and pure unit tests for reducers, geometry, persistence/migration, theme resolution, and interaction state. Keep browser-only behavior behind injectable boundaries.
- **Backend tests**: Use Pytest with isolated temporary workspaces. Mock LLMs, WebSockets, MCP processes, and other external systems; do not make real network requests from tests.
- **Regression gates**: Run the relevant focused test file after each slice. Before handoff, run the complete frontend suite and build, the complete backend suite, lint/Ruff checks, and `uv run python scripts/verify_uml.py` whenever docs, schemas, or core services changed.
- **No weakened tests**: Do not delete, broaden, skip, or loosen an assertion solely to make a change pass. If the product contract changes, update the documentation and test expectation together and explain the compatibility impact in the change.
- **Determinism and isolation**: Tests must not depend on execution order, production workspace data, local model availability, wall-clock timing, or a developer's environment. Use fixtures, temporary directories, stable IDs, and explicit async synchronization.
- **Completion rule**: A task is incomplete until its new tests pass, existing tests remain green, and the documented acceptance criteria are satisfied.

## 3. App Data Layer & Canonical Ontology Rules

- **Database Backend**: Production/deployment knowledge-graph storage uses Neo4j. SQLite `graph.db` remains only as an explicit isolated-test adapter and one-time migration source. Do NOT use the legacy `graph.json` interface except for backward-compatible test exports/imports.
- **Strict Ontology Enforcement**:
  - Every context record must be classified by exactly one registered entity in the single `ambient-context` ontology. Unknown/abstract entities and unknown properties are rejected; grow the ontology before writing them.
  - Core entities include `Thing`, `Task`, `Event`, `Note`, `Person`, `Organization`, `Project`, `Document`, `Place`, `Message`, and `SoftwareApplication`. Applications reuse or add aligned canonical entities rather than installing disconnected schemas.
  - Only user-context facts belong in the KG. App caches, cursors, credentials, UI state, checkpoints, and raw provider payloads stay under the App workspace directory.
- **Coding Guidelines (Widget JavaScript)**:
  - Widgets must only read/write properties conforming to verified ontology entity names and value types (e.g. String, Integer, Boolean, Number).
  - Do NOT generate or use the deprecated `ambient.model` APIs. Always use `ambient.graph.subscribe` and `ambient.graph.mutate`.
- **Testing Verification**:
  - Run `PYTHONPATH=. uv run pytest` after any updates affecting Graph Database, Query Engine, Schema Alignment, or WebSocket endpoints to verify compliance and prevent regression.
