import pytest

from backend.app_manager import AppManager
from backend.context_manager import ContextBudget, ContextManager
from backend.models import ChatMessage, ChatSession
from backend.workspace_storage import WorkspaceStorage


@pytest.fixture(name="db_session")
def db_session_fixture(tmp_path):
    workspace_dir = str(tmp_path / "workspace")
    storage = WorkspaceStorage(workspace_dir)
    yield storage


@pytest.fixture
def temp_apps_dir(tmp_path, monkeypatch, db_session):
    # AppManager resolves self.apps_dir using WORKSPACE_DIR by default
    monkeypatch.setenv("WORKSPACE_DIR", db_session.workspace_dir)
    return db_session.apps_dir


def test_session_message_relations(db_session):
    # Create session
    chat_sess = ChatSession(id="session-1", title="Test Chat")
    db_session.add(chat_sess)
    db_session.commit()
    db_session.refresh(chat_sess)

    assert chat_sess.id == "session-1"
    assert chat_sess.title == "Test Chat"

    # Add messages with different roles
    msg1 = ChatMessage(session_id="session-1", role="user", content="Hello")
    msg2 = ChatMessage(session_id="session-1", role="agent", content="Hi")
    msg3 = ChatMessage(session_id="session-1", role="code", content="<ambient-widget id='w1'>...</ambient-widget>")

    db_session.add(msg1)
    db_session.add(msg2)
    db_session.add(msg3)
    db_session.commit()

    # Read back messages
    messages = db_session.get_messages("session-1")

    assert len(messages) == 3
    assert messages[0].role == "user"
    assert messages[1].role == "agent"
    assert messages[2].role == "code"


def test_context_manager_pruning_and_injection(db_session, temp_apps_dir):
    # 1. Create app files on disk using AppManager
    app_manager = AppManager()
    app_manager.create_or_update_app(
        app_id="weather-widget",
        title="Weather Widget",
        js="console.log('weather');",
    )

    # 2. Setup Session and Messages
    chat_sess = ChatSession(id="session-2", title="Weather Test")
    db_session.add(chat_sess)
    db_session.commit()

    # Chat messages history
    msg_user1 = ChatMessage(session_id="session-2", role="user", content="Show weather")
    # This message has role "code" containing the widget definition
    msg_code = ChatMessage(
        session_id="session-2",
        role="code",
        content="<ambient-widget id='weather-widget' title='Weather Widget'>...</ambient-widget>",
    )
    msg_agent1 = ChatMessage(session_id="session-2", role="agent", content="I spawned the widget for you.")
    msg_user2 = ChatMessage(session_id="session-2", role="user", content="Make the text green")

    db_session.add(msg_user1)
    db_session.add(msg_code)
    db_session.add(msg_agent1)
    db_session.add(msg_user2)
    db_session.commit()

    # 3. Build context
    context_manager = ContextManager(db_session=db_session, app_manager=app_manager)
    prompt_messages = context_manager.build_llm_prompt("session-2")

    # We expect prompt_messages to be a list of dicts suitable for LLM APIs (role, content)
    # The list should contain:
    # - User message 1
    # - The Code message but PRUNED (the large widget definition stripped/reduced)
    # - Agent message 1
    # - User message 2
    # - Injected System/System block containing active app code: weather-widget index.html, style.css, controller.js

    # Let's inspect the messages
    assert len(prompt_messages) > 0

    # Find the message corresponding to role "code"
    assistant_msgs = [m for m in prompt_messages if m["role"] == "assistant"]
    assert len(assistant_msgs) >= 1

    # Let's check that the active code injected into system messages exists
    system_msgs = [m for m in prompt_messages if m["role"] == "system"]
    assert len(system_msgs) >= 1

    # The system message should inject the app details
    system_content = "".join([m["content"] for m in system_msgs])
    assert "[Active App: weather-widget]" in system_content
    assert "console.log('weather');" in system_content


def test_context_manager_builds_stable_summary_for_omitted_messages(db_session, temp_apps_dir):
    db_session.add(ChatSession(id="session-summary", title="Summary Test"))
    for index in range(6):
        db_session.add(
            ChatMessage(
                session_id="session-summary",
                role="user" if index % 2 == 0 else "agent",
                content=f"historical message {index}",
            )
        )
    db_session.commit()

    manager = ContextManager(db_session=db_session, app_manager=AppManager())
    limits = ContextBudget(max_messages=3, max_total_chars=4_000, max_artifact_chars=0)

    first = manager.build_persistent_summary("session-summary", budget=limits)
    second = manager.build_persistent_summary("session-summary", budget=limits)

    assert first == second
    assert first is not None
    assert "3 earlier messages" in first
    assert "historical message 0" in first
    assert "historical message 2" in first
    assert "historical message 3" not in first
