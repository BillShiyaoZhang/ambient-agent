import os
import sys
import json
import asyncio
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Fix python import path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.opencode_service import run_opencode_agent_acp
from backend.agent.providers import get_llm_provider

# Target scenarios definition
SCENARIOS = [
    {
        "id": "todo",
        "name": "Simple CRUD Todo List",
        "instruction": (
            "Create a todo list widget. It should show a list of tasks retrieved from the Graph Database (type Task). "
            "It should have a text field for task title and an 'Add' button. Clicking 'Add' creates a new pending task in the DB. "
            "Each task item should have a checkbox. Checking/unchecking the checkbox updates the task's status to 'completed' or 'pending' in the DB."
        ),
        "mock_data": {
            "Task": [
                {"id": "t1", "type": "Task", "properties": {"title": "Buy fresh groceries", "status": "pending"}},
                {"id": "t2", "type": "Task", "properties": {"title": "Clean kitchen counter", "status": "completed"}},
                {"id": "t3", "type": "Task", "properties": {"title": "Call dentist for checkup", "status": "pending"}},
            ],
            "Note": [],
            "Event": [],
        },
    },
    {
        "id": "notes",
        "name": "Data Dashboard (Metrics & Tables)",
        "instruction": (
            "Create a note dashboard widget. It should show aggregate metrics: total note count, and count of notes with tags. "
            "It should display all notes from the Graph Database (type Note) in a table with columns: Title, Content, Tags. "
            "There should be a 'Clear All' button that deletes all Note nodes from the database."
        ),
        "mock_data": {
            "Note": [
                {
                    "id": "n1",
                    "type": "Note",
                    "properties": {
                        "title": "Quick Recipes",
                        "content": "1. Carbonara Pasta\n2. Margherita Pizza",
                        "tags": "food,cooking",
                    },
                },
                {
                    "id": "n2",
                    "type": "Note",
                    "properties": {
                        "title": "Work Action Items",
                        "content": "Finalize launch plan slide deck",
                        "tags": "work,urgent",
                    },
                },
                {
                    "id": "n3",
                    "type": "Note",
                    "properties": {
                        "title": "Book recommendations",
                        "content": "1. Antigravity Guide\n2. Clean Code",
                        "tags": "reading,personal",
                    },
                },
            ],
            "Task": [],
            "Event": [],
        },
    },
    {
        "id": "stopwatch",
        "name": "Interactive Stopwatch/Timer",
        "instruction": (
            "Create an interactive stopwatch widget. It should display elapsed time in minutes and seconds (e.g. '00:00'). "
            "It should have 'Start', 'Pause', and 'Reset' buttons. There should be a state-bound warning banner at the top "
            "saying 'Timer running!' that only displays when the timer is active. This widget does not need to read or write to the Graph DB."
        ),
        "mock_data": {"Task": [], "Note": [], "Event": []},
    },
    {
        "id": "wizard",
        "name": "Multi-Step Form with Validation (Wizard)",
        "instruction": (
            "Create a multi-step wizard widget for creating calendar events. Step 1: Text fields for Title and Description, with a 'Next' button. "
            "Step 2: Input fields for Event Start Time, End Time, and Location, with 'Back' and 'Next' buttons. "
            "Step 3: Confirmation screen summarizing all inputs, and a 'Create Event' button that saves it to the Graph Database (type Event). "
            "Perform live validation: if Title is empty or End Time is not after Start Time, prevent moving forward and render a red error warning alert on the screen."
        ),
        "mock_data": {"Task": [], "Note": [], "Event": []},
    },
    {
        "id": "relations",
        "name": "Multi-Schema Relationships (Task & Note Links)",
        "instruction": (
            "Create a task and notes association widget. It should list all Tasks from the Graph Database. Clicking a task in the list "
            "queries and displays all Notes associated with it (via the ASSOCIATED_WITH relation edge from Task to Note). "
            "There should be an 'Add Note' input field and button next to the note list that creates a new Note node and registers an "
            "ASSOCIATED_WITH edge from the active Task to the new Note."
        ),
        "mock_data": {
            "Task": [
                {"id": "t1", "type": "Task", "properties": {"title": "Project Alpha Launch", "status": "pending"}},
                {"id": "t2", "type": "Task", "properties": {"title": "Personal Garden", "status": "pending"}},
            ],
            "Note": [
                {
                    "id": "n1",
                    "type": "Note",
                    "properties": {
                        "title": "Alpha Launch Notes",
                        "content": "Schedule launch in Q3 2026",
                        "tags": "work",
                    },
                },
                {
                    "id": "n2",
                    "type": "Note",
                    "properties": {
                        "title": "Garden Seeds",
                        "content": "Buy Tomato and Basil seeds",
                        "tags": "personal",
                    },
                },
            ],
            "Event": [],
            "Task_Note_ASSOCIATED_WITH": [{"from_id": "t1", "to_id": "n1"}, {"from_id": "t2", "to_id": "n2"}],
        },
    },
    {
        "id": "dynamic_accordion",
        "name": "Dynamic Layout & Filtering (Advanced Canvas)",
        "instruction": (
            "Create a dynamic task manager widget. It should show a list of tasks (type Task). It must support: "
            "1) Buttons/tabs to filter by status ('All', 'Pending', 'Completed'); "
            "2) A fuzzy search input field that filters tasks by title in real-time; "
            "3) Collapsible accordion sections for each task that shows/hides its due date and description when clicked; "
            "4) Conditional styling: completed tasks have a green border or background, and overdue tasks (due date before today, which is 2026-07-16) have a red border or background."
        ),
        "mock_data": {
            "Task": [
                {
                    "id": "t1",
                    "type": "Task",
                    "properties": {
                        "title": "Report Submission",
                        "status": "pending",
                        "description": "Compile annual operations report",
                        "due_date": "2026-07-01",
                    },
                },
                {
                    "id": "t2",
                    "type": "Task",
                    "properties": {
                        "title": "Gym workout session",
                        "status": "completed",
                        "description": "Leg day routine",
                        "due_date": "2026-07-20",
                    },
                },
                {
                    "id": "t3",
                    "type": "Task",
                    "properties": {
                        "title": "Read Antigravity docs",
                        "status": "pending",
                        "description": "Review advanced MCP protocols",
                        "due_date": "2026-08-15",
                    },
                },
            ],
            "Note": [],
            "Event": [],
        },
    },
]

ARTIFACTS_DIR = Path("/Users/shiyaozhang/.gemini/antigravity/brain/98eb663c-e776-4c1b-b29d-e84b92c289b7")
RUN_TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
RUN_DIR = Path("workspace/evaluation_runs") / RUN_TIMESTAMP
APPS_DIR = RUN_DIR / "apps"
WORKSPACE_DIR = RUN_DIR / "workspace"
CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# Make target dirs
APPS_DIR.mkdir(parents=True, exist_ok=True)
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

# Copy templates and check files
PREVIEW_TEMPLATE_PATH = Path("scripts/evaluation/preview_template.html")
RUNTIME_VALIDATOR_PATH = Path("scripts/evaluation/validate_runtime.js")

# Set isolation env variables for opencode
os.environ["WORKSPACE_DIR"] = str(WORKSPACE_DIR.absolute())
os.environ["APPS_DIR"] = str(APPS_DIR.absolute())
os.environ["BENCHMARK_MODE"] = "true"

results = []


async def run_scenario(scenario):
    sc_id = scenario["id"]
    name = scenario["name"]
    instruction = scenario["instruction"]
    mock_data = scenario["mock_data"]

    print(
        f"\n========================================\nRunning Scenario: {name} ({sc_id})\n========================================"
    )

    modes = ["a2ui", "direct"]
    scenario_res = {"scenario_id": sc_id, "name": name, "modes": {}}

    for mode in modes:
        app_id = f"{sc_id}_{mode}"
        mode_dir = APPS_DIR / app_id
        mode_dir.mkdir(parents=True, exist_ok=True)

        # Write trigger file to enforce layout type selection in backend/opencode_service.py
        if mode == "a2ui":
            (mode_dir / "layout.json").write_text("[]", encoding="utf-8")
        else:
            (mode_dir / "index.html").write_text("<div></div>", encoding="utf-8")

        print(f"\n[{sc_id}] Generating code in mode '{mode.upper()}'...")
        start_time = datetime.now()

        # Run developer agent
        logs = ""
        try:
            logs = await run_opencode_agent_acp(app_id, instruction, lambda x: None)
            success = True
        except Exception as e:
            logs = f"Exception: {e}"
            success = False

        elapsed = (datetime.now() - start_time).total_seconds()
        print(f"[{sc_id}] Generation completed in {elapsed:.1f} seconds. Success: {success}")

        # Verify files existence
        files_valid = False
        layout_json = "null"
        controller_js = ""
        index_html = ""
        style_css = ""

        if mode == "a2ui":
            has_layout = (mode_dir / "layout.json").exists()
            has_controller = (mode_dir / "controller.js").exists()
            files_valid = has_layout and has_controller
            if has_layout:
                layout_json = (mode_dir / "layout.json").read_text(encoding="utf-8")
            if has_controller:
                controller_js = (mode_dir / "controller.js").read_text(encoding="utf-8")
        else:
            has_html = (mode_dir / "index.html").exists()
            has_css = (mode_dir / "style.css").exists()
            has_controller = (mode_dir / "controller.js").exists()
            files_valid = has_html and has_css and has_controller
            if has_html:
                index_html = (mode_dir / "index.html").read_text(encoding="utf-8")
            if has_css:
                style_css = (mode_dir / "style.css").read_text(encoding="utf-8")
            if has_controller:
                controller_js = (mode_dir / "controller.js").read_text(encoding="utf-8")

        # Run JSDOM runtime validator
        runtime_success = False
        runtime_err = None
        registered_events = []
        mutations = []
        subscriptions = []

        if files_valid:
            try:
                cmd = ["node", str(RUNTIME_VALIDATOR_PATH.absolute()), mode, str(mode_dir.absolute())]
                proc_env = os.environ.copy()
                proc_env["NODE_PATH"] = "frontend/node_modules"
                run_res = subprocess.run(cmd, env=proc_env, capture_output=True, text=True, timeout=15.0)

                if run_res.returncode == 0:
                    try:
                        val_res = json.loads(run_res.stdout)
                        runtime_success = val_res.get("success", False)
                        runtime_err = val_res.get("error", None)
                        registered_events = val_res.get("registeredEvents", [])
                        mutations = val_res.get("mutations", [])
                        subscriptions = val_res.get("subscriptions", [])
                    except Exception as je:
                        runtime_err = f"Failed to parse validator stdout JSON: {je}. Raw output:\n{run_res.stdout}"
                else:
                    runtime_err = f"Validator script crashed (code {run_res.returncode}): {run_res.stderr}"
            except Exception as re:
                runtime_err = f"Failed to run validator subprocess: {re}"
        else:
            runtime_err = "Skipped (missing required generation files)"

        # Create HTML Preview for screenshot
        screenshot_success = False
        preview_html_path = mode_dir / "preview.html"
        screenshot_path = mode_dir / "screenshot.png"

        if files_valid:
            try:
                template = PREVIEW_TEMPLATE_PATH.read_text(encoding="utf-8")
                rendered_preview = (
                    template.replace("{{ TITLE }}", f"{name} ({mode.upper()})")
                    .replace("{{ MODE }}", mode)
                    .replace("{{ MODE_LABEL }}", "A2UI Declarative Mode" if mode == "a2ui" else "Direct HTML/CSS Mode")
                    .replace("{{ SCENARIO }}", name)
                    .replace("{{ COMPONENT_CSS }}", style_css)
                    .replace("{{ COMPONENT_HTML }}", index_html)
                    .replace("{{ LAYOUT_JSON }}", layout_json)
                    .replace("{{ CONTROLLER_JS }}", controller_js)
                    .replace("{{ SCENARIO_MOCK_DATA }}", json.dumps(mock_data))
                )
                preview_html_path.write_text(rendered_preview, encoding="utf-8")

                # Spawn Google Chrome headless screenshot
                chrome_cmd = [
                    CHROME_PATH,
                    "--headless",
                    "--disable-gpu",
                    f"--screenshot={screenshot_path.absolute()}",
                    "--window-size=600,480",
                    f"file://{preview_html_path.absolute()}",
                ]
                # Run with 15s timeout
                subprocess.run(chrome_cmd, capture_output=True, timeout=15.0)
                if screenshot_path.exists():
                    screenshot_success = True
                    # Copy screenshot to artifacts folder for frontend viewing
                    dest_screenshot = ARTIFACTS_DIR / "screenshots" / f"{sc_id}_{mode}.png"
                    dest_screenshot.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy(screenshot_path, dest_screenshot)
            except Exception as se:
                print(f"[{sc_id}] Screenshot failed: {se}")

        scenario_res["modes"][mode] = {
            "success": success,
            "elapsed": elapsed,
            "files_valid": files_valid,
            "runtime_success": runtime_success,
            "runtime_error": runtime_err,
            "screenshot_success": screenshot_success,
            "registered_events": registered_events,
            "mutations": mutations,
            "subscriptions": subscriptions,
            "code_details": {
                "layout_json": layout_json,
                "controller_js": controller_js,
                "index_html": index_html,
                "style_css": style_css,
            },
        }

    results.append(scenario_res)


async def judge_llm(scenario_data):
    # Retrieve configured provider
    provider_name = os.getenv("EVALUATION_LLM_PROVIDER", "openai")
    model_name = os.getenv("EVALUATION_LLM_MODEL", "gpt-4o")
    provider = get_llm_provider(provider_name, model_name)

    sc_id = scenario_data["scenario_id"]
    name = scenario_data["name"]

    a2ui_mode = scenario_data["modes"]["a2ui"]
    direct_mode = scenario_data["modes"]["direct"]

    judge_prompt = f"""You are an expert Frontend Architect and Code Judge evaluating two generated implementations of the user request: "{name}".

Here are the details for the two modes:

### 1. A2UI Declarative Mode:
- Files Generated: {{"layout.json", "controller.js"}}
- Layout structure (`layout.json`):
```json
{a2ui_mode["code_details"]["layout_json"]}
```
- Controller logic (`controller.js`):
```javascript
{a2ui_mode["code_details"]["controller_js"]}
```
- Runtime JS execution success: {a2ui_mode["runtime_success"]} (Error if any: {a2ui_mode["runtime_error"]})
- Subscriptions registered: {a2ui_mode["subscriptions"]}
- Mutations invoked during events mock test: {a2ui_mode["mutations"]}

### 2. Direct HTML/CSS Mode:
- Files Generated: {{"index.html", "style.css", "controller.js"}}
- HTML (`index.html`):
```html
{direct_mode["code_details"]["index_html"]}
```
- CSS (`style.css`):
```css
{direct_mode["code_details"]["style_css"]}
```
- Controller logic (`controller.js`):
```javascript
{direct_mode["code_details"]["controller_js"]}
```
- Runtime JS execution success: {direct_mode["runtime_success"]} (Error if any: {direct_mode["runtime_error"]})
- Subscriptions registered: {direct_mode["subscriptions"]}
- Mutations invoked during events mock test: {direct_mode["mutations"]}

Evaluate and compare these two implementations based on:
1. Spacing, alignment, completeness, responsiveness, aesthetic details.
2. Correct subscription queries, graph schema mapping, dynamic updates, and mutations on event clicks.
3. Does A2UI use ONLY allowed components? Does Direct UI avoid scoping bleeding?

Provide your scoring in JSON format. Output ONLY the JSON object, do not wrap in markdown blocks. Format:
{{
  "a2ui_aesthetics": 1-10 integer,
  "a2ui_functionality": 1-10 integer,
  "a2ui_adherence": 1-10 integer,
  "a2ui_feedback": "Short feedback paragraph",
  "direct_aesthetics": 1-10 integer,
  "direct_functionality": 1-10 integer,
  "direct_adherence": 1-10 integer,
  "direct_feedback": "Short feedback paragraph",
  "comparison_summary": "Short paragraph outlining which approach succeeded better for this task and why"
}}
"""
    try:
        messages = [{"role": "user", "content": judge_prompt}]
        response_str = await provider.generate(messages)
        # Strip any formatting if the model outputs markdown block
        clean_res = response_str.strip()
        if clean_res.startswith("```"):
            lines = clean_res.split("\n")
            if lines[0].startswith("```json") or lines[0].startswith("```"):
                clean_res = "\n".join(lines[1:-1]).strip()
        return json.loads(clean_res)
    except Exception as e:
        print(f"[{sc_id}] LLM judge failed: {e}")
        return {
            "a2ui_aesthetics": 5,
            "a2ui_functionality": 5,
            "a2ui_adherence": 5,
            "a2ui_feedback": f"Judge call failed: {e}",
            "direct_aesthetics": 5,
            "direct_functionality": 5,
            "direct_adherence": 5,
            "direct_feedback": f"Judge call failed: {e}",
            "comparison_summary": "Could not execute model judge scoring.",
        }


async def main():
    print(f"Starting Systematic UI Benchmark. Results saved in: {RUN_DIR}")

    # Use semaphore to limit active generations to 2 concurrently to avoid rate limiting
    sem = asyncio.Semaphore(2)

    async def run_with_sem(sc):
        async with sem:
            await run_scenario(sc)

    await asyncio.gather(*(run_with_sem(sc) for sc in SCENARIOS))

    print("\nRunning LLM-as-a-judge scoring on all scenarios...")
    for sc_res in results:
        sc_id = sc_res["scenario_id"]
        judge_scores = await judge_llm(sc_res)
        sc_res["judge"] = judge_scores

    # Write final Markdown report
    report_path = Path("docs/evaluation/ui_test_report.md")
    report_path.parent.mkdir(parents=True, exist_ok=True)

    report_content = f"""# Systematic UI Generation Benchmark Report: A2UI vs Direct HTML/CSS

This benchmark evaluates **A2UI** (declarative layout specifications) against **Direct UI** (free-form HTML/CSS/JS code generation) across 6 distinct scenarios covering simple widgets to highly complex stateful widgets.

- **Execution Date**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
- **Benchmark Run Dir**: `{RUN_DIR}`
- **LLM Judge Model**: `{os.getenv("EVALUATION_LLM_PROVIDER", "openai")}/{os.getenv("EVALUATION_LLM_MODEL", "gpt-4o")}`

---

## Benchmark Metrics Overview

| Scenario | Mode | Generation Time | Static Code Valid? | JSDOM Run Success? | Chrome Screenshot? | Aesthetics (1-10) | Functionality (1-10) | Adherence (1-10) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
"""

    for r in results:
        a2 = r["modes"]["a2ui"]
        di = r["modes"]["direct"]
        j = r.get("judge", {})

        report_content += f"| **{r['name']}** | A2UI | {a2['elapsed']:.1f}s | {'✅' if a2['files_valid'] else '❌'} | {'✅' if a2['runtime_success'] else '❌'} | {'✅' if a2['screenshot_success'] else '❌'} | **{j.get('a2ui_aesthetics', 5)}/10** | **{j.get('a2ui_functionality', 5)}/10** | **{j.get('a2ui_adherence', 5)}/10** |\n"
        report_content += f"| | Direct | {di['elapsed']:.1f}s | {'✅' if di['files_valid'] else '❌'} | {'✅' if di['runtime_success'] else '❌'} | {'✅' if di['screenshot_success'] else '❌'} | **{j.get('direct_aesthetics', 5)}/10** | **{j.get('direct_functionality', 5)}/10** | **{j.get('direct_adherence', 5)}/10** |\n"

    report_content += "\n---\n\n## Scenario Breakdown & Visual Review\n\n"

    for r in results:
        sc_id = r["scenario_id"]
        a2 = r["modes"]["a2ui"]
        di = r["modes"]["direct"]
        j = r.get("judge", {})

        report_content += f"### {r['name']}\n\n"
        report_content += "#### 📷 Visual Render Comparison\n\n"
        report_content += "Use the images below to compare the visual appearance of A2UI vs Direct UI layouts:\n\n"

        a2_img_path = ARTIFACTS_DIR / "screenshots" / f"{sc_id}_a2ui.png"
        di_img_path = ARTIFACTS_DIR / "screenshots" / f"{sc_id}_direct.png"

        report_content += "```carousel\n"
        if a2_img_path.exists():
            report_content += f"![A2UI Render]({a2_img_path.as_uri()})\n"
        else:
            report_content += "No A2UI screenshot captured.\n"
        report_content += "<!-- slide -->\n"
        if di_img_path.exists():
            report_content += f"![Direct UI Render]({di_img_path.as_uri()})\n"
        else:
            report_content += "No Direct UI screenshot captured.\n"
        report_content += "```\n\n"

        report_content += "#### ⚖️ LLM Judge Scoring & Analysis\n\n"
        report_content += f"**A2UI Feedback**:\n> {j.get('a2ui_feedback', 'No feedback available.')}\n\n"
        report_content += f"**Direct UI Feedback**:\n> {j.get('direct_feedback', 'No feedback available.')}\n\n"
        report_content += f"**Comparison Summary**:\n> {j.get('comparison_summary', 'No summary available.')}\n\n"
        report_content += "\n---\n\n"

    report_path.write_text(report_content, encoding="utf-8")
    print(f"\nFinal report compiled successfully at: {report_path}")

    # Also copy report to artifacts dir for user visibility
    artifact_report_path = ARTIFACTS_DIR / "ui_test_report.md"
    shutil.copy(report_path, artifact_report_path)
    print(f"Artifact report copied to: {artifact_report_path}")


if __name__ == "__main__":
    asyncio.run(main())
