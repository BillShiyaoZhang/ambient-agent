# Introduction

Ambient Agent is an open-source, self-hosted personal AI assistant. It combines conversation, durable background tasks, graph data, and interactive Widgets in one desktop-style workspace.

## What the project solves

A normal chat interface works well for one-off answers but poorly for tasks that must keep running, request confirmation for side effects, or expose data for repeated interaction. Ambient Agent persists each request as a **Run**, classifies it through the Agent, and executes explicit phases. Generated apps and existing capabilities share one App Center and open in the window workspace.

## Current capabilities

- **Durable Agent Runs**: Runs, steps, interactions, and events are stored in `workspace/.ambient/runs.db`. Tasks can be cancelled, retried, and reclaimed after a process restart.
- **App workspace**: Windows can float, maximize, snap, resize, switch, and use layout presets. The backend persists the Canvas V3 configuration.
- **Dynamic Widgets**: A Widget uses Manifest V2 and a `controller.js` file that exports a React component. Create/modify flows approve a schema + capability proposal before staging, verification, and atomic publication.
- **Shared graph data**: Widgets and the Agent read and write user-context facts in the canonical Neo4j knowledge graph through ontology-validated Graph APIs.
- **Model and tool integrations**: Providers, default models, and per-session models are configured in the UI. The Agent can reach external capabilities through the Tool Gateway, MCP, or OpenCode.
- **Audit and confirmation**: LLM requests are written to the workspace audit log. Backend policy, interactions, and persistent effect records govern effectful flows.
- **Least-authority grants**: A Widget receives only Graph, network, file, or installed-capability grants approved during schema alignment, and the backend reauthorizes every operation.

## Boundaries to understand

- `SandboxWidget` is a component name, not a strong sandbox for untrusted JavaScript. Widget controllers execute in the page JavaScript realm and should only load trusted workspace code.
- WebSockets carry chat projections, graph subscriptions, and Run events. The current code has no user identity, device pairing, or conflict-merge protocol, so this documentation does not present it as a complete multi-user or multi-device collaboration system.
- Whether a local model is “offline” depends on the configured provider and tools. Cloud models, MCP, and Widget network requests still create external traffic.

## Recommended reading order

1. [Quick Start](/en/guide/quick-start.md): start the services and configure the first model.
2. [Project Structure](/en/architecture/project-structure.md): learn the directories and module responsibilities.
3. [System and Request Flow](/en/architecture/overview.md): follow input through Runs, data, and UI.
4. [Widget Capability Security](/en/architecture/capability-security.md): understand declaration, approval, generation, and runtime enforcement.
5. Continue with the Agent, Widget, Graph, or integration section relevant to your work.
