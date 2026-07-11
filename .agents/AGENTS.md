# Ambient Agent Project Rules

This document outlines workspace-specific rules for coding agents. All agents MUST follow these instructions when working on this project.

## 1. Documentation & UML Synchronization

- **UML Class Diagram Verification**: The class diagrams in `backend/UML.md` (high-level architecture) and `backend/agent/harness.md` (agent harness architecture) serve as our blueprints. Any modification to database models (`models.py`) or core service classes must be documented in the corresponding Markdown files.
- **Run Verification Script**: After any code changes affecting the models or service class signatures, run the verification script to check for compliance:
  ```bash
  python scripts/verify_uml.py
  ```
- **Keep UML Concise (Subset Model)**: Only document core public classes, fields, and main methods in `backend/UML.md`. Internal helpers or minor private fields can be omitted to avoid noise, but anything documented *must* match the code.
- **Python Project Guidelines**: Always use `uv` for python virtual environment operations when running command line commands.

## 2. App Data Layer & SQLite Schema Alignment Rules

- **Database Backend**: The system uses SQLite (`graph.db`) inside the workspace for graph storage. Do NOT use the legacy file-based JSON `graph.json` interface except for backward-compatible `save()` backups in test scripts.
- **Strict Schema Enforcement**:
  - All nodes and edges written to the database must conform to registered schema types and structures defined in the `graph_schemas` table (managed in `backend/graph_db.py`).
  - Core schemas are `Task`, `Event`, and `Note`. Applications can register custom schemas.
- **Coding Guidelines (Widget JavaScript)**:
  - Widgets must only read/write database properties conforming to verified schema property names and value types (e.g. String, Integer, Boolean, Number).
  - Do NOT generate or use the deprecated `ambient.model` APIs. Always use `ambient.graph.subscribe` and `ambient.graph.mutate`.
- **Testing Verification**:
  - Run `PYTHONPATH=. uv run pytest` after any updates affecting Graph Database, Query Engine, Schema Alignment, or WebSocket endpoints to verify compliance and prevent regression.

