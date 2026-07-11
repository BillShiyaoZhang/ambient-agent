import re

from backend.app_manager import AppManager
from backend.models import ChatMessage
from backend.workspace_storage import WorkspaceStorage


class ContextManager:
    def __init__(self, db_session: WorkspaceStorage, app_manager: AppManager):
        self.db = db_session
        self.app_manager = app_manager

    def _extract_app_ids(self, messages: list[ChatMessage]) -> set[str]:
        """
        Scans messages for <ambient-widget id="some-id" ...> tags and
        /app <app_id> references to identify which apps have been created
        or modified in this session.
        """
        app_ids = set()
        # Find ID patterns matching single or double quotes
        xml_pattern = r"<ambient-widget\s+[^>]*?id=[\"']([^\"']+)[\"']"
        # Match slash command: /app <app_id> (with optional spaces/word boundaries)
        slash_pattern = r"(?:^|\s)/app\s+([a-zA-Z0-9_-]+)"

        for msg in messages:
            # Extract from XML tags
            xml_matches = re.findall(xml_pattern, msg.content)
            for m in xml_matches:
                app_ids.add(m.strip())

            # Extract from /app slash commands
            slash_matches = re.findall(slash_pattern, msg.content)
            for m in slash_matches:
                app_ids.add(m.strip())

        return app_ids

    def _prune_message_content(self, content: str) -> str:
        """
        Replaces the verbose inner XML contents (<html-content>, <css-styles>, <js-script>)
        inside <ambient-widget> blocks with a small placeholder.
        """

        def replace_block(match):
            widget_tag = match.group(0)
            # Find the ID and Title attributes matching single or double quotes
            id_match = re.search(r"id=[\"']([^\"']+)[\"']", widget_tag)
            title_match = re.search(r"title=[\"']([^\"']+)[\"']", widget_tag)

            widget_id = id_match.group(1) if id_match else "unknown"
            widget_title = title_match.group(1) if title_match else "Widget"

            return (
                f'<ambient-widget id="{widget_id}" title="{widget_title}">\n'
                f"  <!-- [HTML, CSS, JS source code omitted from history to save context space] -->\n"
                f"  <!-- The current up-to-date source files for this app are injected in system instructions. -->\n"
                f"</ambient-widget>"
            )

        pattern = (
            r"<ambient-widget\s+[^>]*?id=[\"']([^\"']+)[\"'][^>]*?title=[\"']([^\"']+)[\"'][^>]*?>.*?</ambient-widget>"
        )
        return re.sub(pattern, replace_block, content, flags=re.DOTALL)

    def build_llm_prompt(self, session_id: str) -> list[dict[str, str]]:
        """
        Loads the chat session message history, prunes old code payloads to prevent
        token explosion, identifies active apps, and injects their latest files from disk.
        """
        # 1. Fetch messages for the session sorted by timestamp
        messages = self.db.get_messages(session_id)

        # 2. Extract all apps referenced/created in this session
        app_ids = self._extract_app_ids(messages)

        # 3. Read up-to-date files for active apps from disk
        active_apps_context = []
        for app_id in app_ids:
            app_files = self.app_manager.get_app_files(app_id)
            if app_files:
                active_apps_context.append(
                    f"[Active App: {app_id}]\n"
                    f"Title: {app_files['title']}\n"
                    f"--- index.html (View) ---\n"
                    f"{app_files['html']}\n"
                    f"--- style.css (Style) ---\n"
                    f"{app_files['css']}\n"
                    f"--- controller.js (Controller - using 'ambient' SDK) ---\n"
                    f"{app_files['js']}\n"
                    f"-------------------------"
                )

        # 4. Map DB messages to LLM chat message payload, pruning code blocks
        llm_messages = []
        for msg in messages:
            # Map role
            if msg.role == "user":
                llm_role = "user"
            elif msg.role in ("agent", "code"):
                # Both agent text replies and code edits are agent's output
                llm_role = "assistant"
            elif msg.role == "system":
                llm_role = "system"
            else:
                # Fallback
                llm_role = "user"

            # Prune code payload if it's a code block or contains widget definitions
            pruned_content = self._prune_message_content(msg.content)
            llm_messages.append({"role": llm_role, "content": pruned_content})

        # 5. Inject the active apps code in a system message at the beginning of the context
        if active_apps_context:
            apps_context_str = (
                "Here are the current source code files of the active apps in this conversation.\n"
                "If the user asks to modify an app, make changes directly to these code files and "
                "return the updated code in the same <ambient-widget> structure.\n\n" + "\n\n".join(active_apps_context)
            )
            # Insert active app context right after system prompt, or as a system message at index 0.
            # We will insert it at index 0 so it sets the stage.
            llm_messages.insert(0, {"role": "system", "content": apps_context_str})

        return llm_messages
