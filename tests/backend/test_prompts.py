from backend.agent.prompts.manager import PromptManager


def test_prompt_manager_initialization():
    pm = PromptManager()
    assert pm.prompts_dir.exists()


def test_router_prompt_rendering():
    pm = PromptManager()
    existing_apps = [{"id": "todo-app-1234", "title": "My Todo App"}, {"id": "clock-app-5678", "title": "My Clock App"}]
    prompt = pm.get_prompt("router.md", existing_apps=existing_apps)

    assert "todo-app-1234" in prompt
    assert "My Clock App" in prompt
    assert "is_coding" in prompt


def test_router_prompt_rendering_empty():
    pm = PromptManager()
    prompt = pm.get_prompt("router.md", existing_apps=[])
    assert "(None)" in prompt


def test_agent_system_prompt_inclusion():
    pm = PromptManager()
    prompt = pm.get_prompt("agent_system.md")

    assert "You are Ambient Agent" in prompt


def test_opencode_system_prompt_inclusion():
    pm = PromptManager()
    prompt = pm.get_prompt(
        "coding_agent_system.md", app_id="weather-app", target_dir="/some/path", instruction="make weather blue"
    )

    assert "weather-app" in prompt
    assert "/some/path" in prompt
    assert "make weather blue" in prompt
    assert "must survive Runtime suspension" in prompt
    assert "Never keep user-authored drafts only in React hook state" in prompt
    assert "ambient.lifecycle.onBeforeSuspend(handler)" in prompt
    assert "awaits its `ambient.storage.set(...)`" in prompt
