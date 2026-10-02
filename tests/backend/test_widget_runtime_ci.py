from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_widget_ci_installs_and_checks_the_browser_from_its_locked_playwright() -> None:
    workflow = yaml.safe_load((REPOSITORY_ROOT / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["widget-runtime-ci"]["steps"]
    commands = [step.get("run", "") for step in steps]
    dependency_index = commands.index("npm ci --ignore-scripts")
    test_index = commands.index("npm test")
    browser_steps = [
        (index, command)
        for index, command in enumerate(commands)
        if "playwright-core install --with-deps chromium" in command
    ]

    assert len(browser_steps) == 1, "CI must install the browser paired with locked playwright-core"
    browser_index, install = browser_steps[0]
    assert dependency_index < browser_index < test_index
    assert "npx --no-install" in install, "Browser installation must use the locally locked CLI"
    checks = "\n".join(commands[browser_index:test_index])
    assert 'from "playwright-core"' in checks
    assert "chromium.executablePath()" in checks
    assert 'test -x "$CHROMIUM_EXECUTABLE_PATH"' in checks
    assert '"$CHROMIUM_EXECUTABLE_PATH" --version' in checks
    assert "CHROMIUM_EXECUTABLE_PATH=$CHROMIUM_EXECUTABLE_PATH" in checks
    assert '>> "$GITHUB_ENV"' in checks
    for step in steps[browser_index:test_index]:
        if "chromium" in step.get("run", ""):
            assert step["working-directory"] == "widget-runtime"
            assert step.get("continue-on-error") in (None, False)
