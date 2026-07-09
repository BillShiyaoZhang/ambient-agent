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
