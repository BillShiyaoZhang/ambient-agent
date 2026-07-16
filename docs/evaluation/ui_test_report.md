# Systematic UI Generation Benchmark Report: A2UI vs Direct HTML/CSS

This benchmark evaluates **A2UI** (declarative layout specifications) against **Direct UI** (free-form HTML/CSS/JS code generation) across 6 distinct scenarios covering simple widgets to highly complex stateful widgets.

- **Execution Date**: 2026-07-16
- **Benchmark Run Dir**: `workspace/evaluation_runs/20260716_202721`
- **LLM Judge Model**: `minimax/MiniMax-M3`

---

## Benchmark Metrics Overview

| Scenario | Mode | Static Code Valid? | Aesthetics (1-10) | Functionality (1-10) | Adherence (1-10) |
| --- | --- | --- | --- | --- | --- |
| **Simple CRUD Todo List** | A2UI | ✅ | **6/10** | **8/10** | **9/10** |
| | Direct | ❌ | **9/10** | **3/10** | **6/10** |
| **Data Dashboard (Metrics & Tables)** | A2UI | ✅ | **7/10** | **8/10** | **8/10** |
| | Direct | ❌ | **8/10** | **4/10** | **6/10** |
| **Interactive Stopwatch/Timer** | A2UI | ✅ | **7/10** | **7/10** | **8/10** |
| | Direct | ✅ | **9/10** | **7/10** | **7/10** |
| **Multi-Step Form with Validation (Wizard)** | A2UI | ✅ | **8/10** | **8/10** | **7/10** |
| | Direct | ❌ | **1/10** | **1/10** | **1/10** |
| **Multi-Schema Relationships (Task & Note Links)** | A2UI | ✅ | **6/10** | **8/10** | **9/10** |
| | Direct | ✅ | **8/10** | **8/10** | **6/10** |
| **Dynamic Layout & Filtering (Advanced Canvas)** | A2UI | ❌ | **1/10** | **1/10** | **1/10** |
| | Direct | ✅ | **8/10** | **7/10** | **7/10** |

---

## Scenario Breakdown & Visual Review

### Simple CRUD Todo List

#### 📷 Visual Render Comparison

Use the images below to compare the visual appearance of A2UI vs Direct UI layouts:

```carousel
![A2UI Render](file:///Users/shiyaozhang/.gemini/antigravity/brain/98eb663c-e776-4c1b-b29d-e84b92c289b7/screenshots/todo_a2ui.png)
<!-- slide -->
![Direct UI Render](file:///Users/shiyaozhang/.gemini/antigravity/brain/98eb663c-e776-4c1b-b29d-e84b92c289b7/screenshots/todo_direct.png)
```

#### ⚖️ LLM Judge Scoring & Analysis

**A2UI Feedback**:
> Clean, minimal dark-themed layout with proper spacing and rounded containers. Header, summary, input row, and list are all well-structured. The controller correctly subscribes to Task type, sorts tasks (pending first), updates display list with checkbox unicode characters, formats summary text, and properly handles add and toggle actions. All allowed declarative components (Column, Row, Text, TextField, Button, List) are used appropriately. The toggle uses index-based access via rawList which is slightly brittle under rapid updates but functional. Lacks visual richness compared to richer implementations (no badges, no metrics, no gradient accents).

**Direct UI Feedback**:
> Visually impressive with gradient text title, metric cards with hover states, custom checkboxes, status badges, empty state with icon, and polished transitions. However, the controller fails at runtime due to an undefined `root` variable used throughout the script — none of the querySelector calls will execute, meaning the entire UI is non-functional. The empty style.css file is concerning; all CSS is inlined in the HTML `<style>` tag instead of a separate file, which doesn't fit the Direct HTML/CSS mode's intended structure. Data attributes (data-td, data-td-task-id) are properly used to avoid style/selector bleeding, which is good. Add handler and Enter key binding logic is sound, but the toggle handler queries the graph on every click which is inefficient. Mock test only registered the create_node mutation; the broken initialization likely prevents further functionality.

**Comparison Summary**:
> A2UI succeeds better overall for this Simple CRUD Todo List task. While the Direct HTML/CSS mode delivers a significantly more polished and visually rich UI, it fails at runtime due to the undefined `root` reference, making the entire application non-functional. The A2UI implementation, though more minimal in aesthetics, correctly executes, properly subscribes to the Task type, uses declarative bindings and actions appropriately, and implements all CRUD operations cleanly. For a functional deliverable, A2UI's adherence to the declarative paradigm and working controller outweighs the Direct mode's visual superiority but execution failure.


---

### Data Dashboard (Metrics & Tables)

#### 📷 Visual Render Comparison

Use the images below to compare the visual appearance of A2UI vs Direct UI layouts:

```carousel
![A2UI Render](file:///Users/shiyaozhang/.gemini/antigravity/brain/98eb663c-e776-4c1b-b29d-e84b92c289b7/screenshots/notes_a2ui.png)
<!-- slide -->
![Direct UI Render](file:///Users/shiyaozhang/.gemini/antigravity/brain/98eb663c-e776-4c1b-b29d-e84b92c289b7/screenshots/notes_direct.png)
```

#### ⚖️ LLM Judge Scoring & Analysis

**A2UI Feedback**:
> Solid implementation using allowed components (Column, Row, Text, Card, Table, Button) with proper bindings. Dark theme is consistent with good color contrast. Metric cards and table section are well-styled. The runtime executes successfully and the subscription/mutation flow is correctly implemented. The header subtitle 'Manage your notes' feels slightly redundant, and the title section is a bit sparse compared to the direct version, but the overall structure is clean and functional.

**Direct UI Feedback**:
> Visually polished with gradient text on the title, hover effects on metric cards, tag chips with proper styling, sticky table header, and a nice empty state with an icon. Good class prefixing (nd-) avoids scope bleeding. However, the runtime JS execution failed which is a critical issue. The style.css file is empty while all CSS is embedded in the HTML's <style> tag, violating the file separation expectation. There is also a potential XSS concern using innerHTML with unescaped note content. Despite the visual polish, the broken runtime significantly undermines the implementation.

**Comparison Summary**:
> A2UI succeeded better overall because it delivered a working runtime with proper component usage, correct state bindings, and functional subscriptions/mutations. The Direct mode had more visually refined aesthetics (gradient text, tag chips, hover effects), but the runtime execution failure is a critical flaw that renders the dashboard non-functional. The empty style.css file in the Direct mode also fails to meet the expected file structure. For a data dashboard, functional correctness outweighs minor visual polish, giving A2UI the edge in this comparison.


---

### Interactive Stopwatch/Timer

#### 📷 Visual Render Comparison

Use the images below to compare the visual appearance of A2UI vs Direct UI layouts:

```carousel
![A2UI Render](file:///Users/shiyaozhang/.gemini/antigravity/brain/98eb663c-e776-4c1b-b29d-e84b92c289b7/screenshots/stopwatch_a2ui.png)
<!-- slide -->
![Direct UI Render](file:///Users/shiyaozhang/.gemini/antigravity/brain/98eb663c-e776-4c1b-b29d-e84b92c289b7/screenshots/stopwatch_direct.png)
```

#### ⚖️ LLM Judge Scoring & Analysis

**A2UI Feedback**:
> Clean, functional design with proper state management using bindings for display and warning visibility. The dark theme with monospace time display is appropriate. The setInterval approach with 50ms updates but second-level display is well-implemented. The warning banner's display binding with 'none'/'block' values is a clever declarative approach. However, the visual design is relatively basic compared to modern standards, and the subscription registration shows 'Note' which suggests state subscriptions may not be properly tracked by the runtime. Layout structure is sensible with proper spacing and alignment.

**Direct UI Feedback**:
> Beautiful, modern design with sophisticated use of gradients, gradient text effects for the display, smooth transitions on the banner, and polished button states with hover/active animations. The tabular-nums for the time display is a nice touch. However, the banner visibility logic (`elapsed > 0 || intervalId !== null`) keeps the banner visible after pausing, which differs from a strict 'running only' semantic. The controller relies on a global `root` reference that isn't passed as a parameter, and the CSS uses a global `*` reset which could cause scope bleeding in embedded contexts. The IIFE properly scopes JavaScript variables.

**Comparison Summary**:
> The Direct HTML/CSS mode wins on aesthetics with its modern gradient design, smooth animations, and polished visual details that feel more contemporary. The A2UI mode wins on adherence to its declarative paradigm, using bindings and events properly without global dependencies. Both implementations are functionally sound with similar timer logic, but they have minor issues: A2UI's subscription tracking appears weak, and Direct mode's banner persistence after pause is a behavioral inconsistency. For a visual showcase like a stopwatch where aesthetics matter, the Direct mode delivers a more impressive user experience, while A2UI provides better architectural separation of concerns.


---

### Multi-Step Form with Validation (Wizard)

#### 📷 Visual Render Comparison

Use the images below to compare the visual appearance of A2UI vs Direct UI layouts:

```carousel
![A2UI Render](file:///Users/shiyaozhang/.gemini/antigravity/brain/98eb663c-e776-4c1b-b29d-e84b92c289b7/screenshots/wizard_a2ui.png)
<!-- slide -->
![Direct UI Render](file:///Users/shiyaozhang/.gemini/antigravity/brain/98eb663c-e776-4c1b-b29d-e84b92c289b7/screenshots/wizard_direct.png)
```

#### ⚖️ LLM Judge Scoring & Analysis

**A2UI Feedback**:
> The A2UI implementation delivers a polished, well-structured three-step wizard with a cohesive dark theme. Spacing, typography hierarchy, and color semantics (blue primary, green success, red error) are well thought out. The summary card on step 3 is a nice touch for confirmation. Validation logic is robust—title required on step 1, start/end time required with valid date parsing and chronological ordering on step 2. Event creation via ambient.graph.mutate with a proper Event node and ISO timestamp is correctly implemented. Minor concerns: the hiddenStyle/stepStyle approach for showing/hiding steps is a workaround rather than a conditional rendering pattern, and both error rows (error-1 and error-2) bind to the same /wizard/error state, which is fine but could lead to confusion. Overall a solid, complete implementation.

**Direct UI Feedback**:
> The Direct HTML/CSS mode produced essentially nothing—an empty <div></div> in index.html, an empty style.css, and an empty controller.js. There is no visual output, no styling, no interactivity, no validation, and no event handling. The runtime JS execution also failed. This is a complete failure to deliver the requested multi-step form with validation.

**Comparison Summary**:
> The A2UI Declarative Mode clearly succeeded while the Direct HTML/CSS Mode completely failed. A2UI produced a fully functional, aesthetically pleasing three-step wizard with proper validation, step navigation, summary confirmation, and graph mutation for event creation—all using only allowed declarative components. The Direct mode generated empty placeholder files with no implementation whatsoever, making it a non-functional deliverable. For a multi-step form with validation task, the A2UI approach is dramatically superior in this comparison.


---

### Multi-Schema Relationships (Task & Note Links)

#### 📷 Visual Render Comparison

Use the images below to compare the visual appearance of A2UI vs Direct UI layouts:

```carousel
![A2UI Render](file:///Users/shiyaozhang/.gemini/antigravity/brain/98eb663c-e776-4c1b-b29d-e84b92c289b7/screenshots/relations_a2ui.png)
<!-- slide -->
![Direct UI Render](file:///Users/shiyaozhang/.gemini/antigravity/brain/98eb663c-e776-4c1b-b29d-e84b92c289b7/screenshots/relations_direct.png)
```

#### ⚖️ LLM Judge Scoring & Analysis

**A2UI Feedback**:
> Clean two-panel layout with proper dark theme and good spacing. The layout correctly uses allowed components (Column, Row, Text, List, TextField, Button) with proper data bindings. The controller subscribes only to Task and fetches Notes via ASSOCIATED_WITH edges, then creates both node and edge on add-note. Drawbacks: no status indicators, no metric counters, no empty-state visuals, and the List components render plain text without per-item styling (no dots, no hover/active state visual, no createdAt display). Functionally correct but visually basic.

**Direct UI Feedback**:
> Visually rich: gradient title, metric cards, status dots, hover/active states, and proper empty-state placeholders. The controller correctly subscribes to Task, queries ASSOCIATED_WITH edges, and mutates Note + edge together. However, the style.css file is essentially dead code — it defines rw- prefixed classes that don't appear anywhere in the HTML, which uses rd- prefixed classes styled by an inline <style> block inside index.html. This violates the intended separation of concerns and makes the CSS file wasted. Inline <style> within HTML is a mild scoping concern even with prefixed class names.

**Comparison Summary**:
> The Direct HTML/CSS mode delivers a noticeably more polished, information-dense UI (metrics, status dots, active selection, empty states) and matches the A2UI in graph correctness. However, it loses points on adherence because the dedicated style.css contains an entire unused design system (rw- classes) while the actual styling lives in an inline <style> block in the HTML. The A2UI mode is leaner and fully conforms to the allowed components, but feels visually minimal. Overall the Direct approach produces a better user experience at the cost of a sloppy file organization; the A2UI approach is more disciplined but plain. They tie on functionality, with Direct winning aesthetics and A2UI winning adherence.


---

### Dynamic Layout & Filtering (Advanced Canvas)

#### 📷 Visual Render Comparison

Use the images below to compare the visual appearance of A2UI vs Direct UI layouts:

```carousel
![A2UI Render](file:///Users/shiyaozhang/.gemini/antigravity/brain/98eb663c-e776-4c1b-b29d-e84b92c289b7/screenshots/dynamic_accordion_a2ui.png)
<!-- slide -->
![Direct UI Render](file:///Users/shiyaozhang/.gemini/antigravity/brain/98eb663c-e776-4c1b-b29d-e84b92c289b7/screenshots/dynamic_accordion_direct.png)
```

#### ⚖️ LLM Judge Scoring & Analysis

**A2UI Feedback**:
> Complete failure. The layout.json is an empty array, controller.js has no logic, and runtime execution failed. While it registered a 'Task' subscription, no actual layout, rendering, or interaction logic was produced. This implementation is effectively non-functional and provides no value.

**Direct UI Feedback**:
> Strong implementation with a polished GitHub-inspired dark theme. Includes proper spacing, hover effects, smooth expand/collapse animations, status indicators (dot + badge), overdue highlighting, and a clean empty state. Functionally solid with fuzzy search, tab filtering, debounced input, and proper Task subscription via ambient.graph. However, no mutations are invoked on event interactions (expand toggle, filter clicks) - the test reported 'none', which is a gap since meaningful UI events should typically trigger graph mutations. Scoping is correctly handled via IIFE with explicit root/ambient/fetch parameters, avoiding variable bleeding.

**Comparison Summary**:
> The Direct HTML/CSS implementation is overwhelmingly superior. A2UI mode produced essentially nothing - an empty layout array and empty controller with a failed runtime. Direct mode delivered a complete, well-designed, functional task manager with subscription handling, fuzzy search, filtering, expand/collapse interactions, and overdue detection, all wrapped in proper scoping. The only weakness in Direct mode is the absence of mutation invocations on user interactions, but this is a minor shortcoming compared to A2UI's total failure to render anything. Direct mode succeeded entirely; A2UI mode failed entirely.


---

