# Introduction

Ambient Agent is an open-source, self-hostable, multi-device synchronized personal AI assistant framework. Unlike traditional text-based chatbots, Ambient Agent **prioritizes Rich Graphical User Interfaces (Widget GUI)** to showcase AI output while guaranteeing ultimate data privacy.

## 💡 Why Ambient Agent?

In traditional conversational interfaces, interactions with Large Language Models (LLMs) are restricted to plain text bubbles. When dealing with structured, time-sensitive, or highly interactive operations (such as todo lists, calendar events, system dashboards, count-downs), text bubbles become extremely inefficient.

Ambient Agent introduces the **Canvas Workspace**. According to your natural language prompts, the AI dynamically generates and renders mini-applications (Widgets). This graphical user interface converts the LLM into a powerful personal operating assistant.

## 🌟 Core Features

### 1. Dynamic GUI Workspace (Canvas)

- **Dynamic Card Generation**: AI writes customized XML containing HTML, CSS, and JS, compiling them on-the-fly to render interactive cards.
- **Workspace Persistence**: Canvas supports pinning, dragging, minimizing, and closing cards. Layout configurations are persisted to a local SQLite database.

### 2. Isolated Sandboxes & Seamless UX

- **Double Sandbox**: Restricts CSS selectors scope and runs widget scripts in closure containers, avoiding window environment pollution.
- **Seamless Fullscreen**: Swapping between grid layouts and fullscreen view reuses the same React DOM node (Zero Remount), preserving local runtime states.
- **Sandbox Fetch Cache**: Automatically intercepts and caches external GET requests for 5 minutes, removing redundant endpoint calls.

### 3. Privacy & Transparency

- **Local Execution**: Connects to **Ollama** for entirely offline model operations.
- **Audit Log**: Visualizes every request prompt payload and model response details.
