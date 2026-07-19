import os
import re
from dataclasses import dataclass

from backend.app_manager import AppManager
from backend.models import ChatMessage
from backend.workspace_storage import WorkspaceStorage


@dataclass(frozen=True)
class ContextBudget:
    max_messages: int = 40
    max_total_chars: int = 48_000
    max_artifact_chars: int = 24_000
    max_message_chars: int = 8_000

    @classmethod
    def defaults(cls) -> "ContextBudget":
        return cls(
            max_messages=max(1, int(os.getenv("AGENT_CONTEXT_MAX_MESSAGES", "40"))),
            max_total_chars=max(4_000, int(os.getenv("AGENT_CONTEXT_MAX_CHARS", "48000"))),
            max_artifact_chars=max(0, int(os.getenv("AGENT_CONTEXT_MAX_ARTIFACT_CHARS", "24000"))),
            max_message_chars=max(500, int(os.getenv("AGENT_CONTEXT_MAX_MESSAGE_CHARS", "8000"))),
        )


class ContextManager:
    def __init__(self, db_session: WorkspaceStorage, app_manager: AppManager):
        self.db = db_session
        self.app_manager = app_manager

    def _extract_app_ids(self, messages: list[ChatMessage]) -> list[str]:
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

        return sorted(app_ids)

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

    def build_persistent_summary(
        self,
        session_id: str,
        *,
        budget: ContextBudget | None = None,
        max_summary_chars: int = 8_000,
    ) -> str | None:
        """Build a deterministic extractive recap for messages outside the recent window.

        The durable Run stores both this text and its SHA-256 reference.  It is
        intentionally deterministic: recovery never needs another model call
        merely to recreate context.
        """

        limits = budget or ContextBudget.defaults()
        messages = self.db.get_messages(session_id)
        omitted = messages[: max(0, len(messages) - limits.max_messages)]
        if not omitted or max_summary_chars <= 0:
            return None
        header = f"Extractive recap of {len(omitted)} earlier messages:"
        remaining = max(0, max_summary_chars - len(header) - 1)
        selected: list[str] = []
        used = 0
        for message in reversed(omitted):
            role = "user" if message.role == "user" else "assistant"
            content = self._truncate(self._prune_message_content(message.content), 600, "summary item")
            line = f"- [{role}] {content}"
            if selected and used + len(line) + 1 > remaining:
                continue
            if not selected and len(line) > remaining:
                line = self._truncate(line, remaining, "summary")
            if not line:
                continue
            selected.append(line)
            used += len(line) + 1
        selected.reverse()
        return "\n".join([header, *selected])

    @staticmethod
    def _truncate(value: str, limit: int, label: str) -> str:
        if len(value) <= limit:
            return value
        if limit <= 0:
            return ""
        omitted = len(value) - limit
        suffix = f"\n\n[{label}: {omitted} characters omitted; retrieve the artifact when needed]"
        if len(suffix) >= limit:
            return value[:limit]
        return f"{value[: limit - len(suffix)]}{suffix}"

    def build_llm_prompt(
        self,
        session_id: str,
        *,
        budget: ContextBudget | None = None,
        context_summary: str | None = None,
        artifact_ids: list[str] | None = None,
    ) -> list[dict[str, str]]:
        """
        Loads the chat session message history, prunes old code payloads to prevent
        token explosion, identifies active apps, and injects their latest files from disk.
        """
        limits = budget or ContextBudget.defaults()
        if limits.max_total_chars <= 0:
            raise ValueError("Context budget must be positive")

        # 1. Fetch messages for the session sorted by timestamp.
        messages = self.db.get_messages(session_id)

        # 2. Select artifact references deterministically. Callers may narrow
        # this list for just-in-time retrieval; the fallback remains bounded.
        app_ids = sorted(set(artifact_ids)) if artifact_ids is not None else self._extract_app_ids(messages)

        # 3. Read up-to-date files for active apps from disk
        active_apps_context: list[str] = []
        artifact_chars = 0
        for app_id in app_ids:
            app_files = self.app_manager.get_app_files(app_id)
            if app_files:
                if "layout" in app_files:
                    artifact = (
                        f"[Active App: {app_id}]\n"
                        f"Format: A2UI Layout\n"
                        f"Title: {app_files['title']}\n"
                        f"--- layout.json (A2UI Declarative View) ---\n"
                        f"{app_files['layout']}\n"
                        f"--- controller.js (Controller - using 'ambient' SDK) ---\n"
                        f"{app_files['js']}\n"
                        f"-------------------------"
                    )
                else:
                    artifact = (
                        f"[Active App: {app_id}]\n"
                        f"Format: Legacy HTML\n"
                        f"Title: {app_files['title']}\n"
                        f"--- index.html (View) ---\n"
                        f"{app_files['html']}\n"
                        f"--- style.css (Style) ---\n"
                        f"{app_files['css']}\n"
                        f"--- controller.js (Controller - using 'ambient' SDK) ---\n"
                        f"{app_files['js']}\n"
                        f"-------------------------"
                    )
                remaining = limits.max_artifact_chars - artifact_chars
                if remaining <= 0:
                    break
                artifact = self._truncate(artifact, remaining, f"artifact {app_id}")
                active_apps_context.append(artifact)
                artifact_chars += len(artifact)

        # 4. Map DB messages to LLM chat message payload, pruning code blocks
        recent_messages = messages[-limits.max_messages :]
        omitted_count = max(0, len(messages) - len(recent_messages))
        llm_messages: list[dict[str, str]] = []
        message_chars = 0
        summary_reserve = min(8_000, limits.max_total_chars // 4) if context_summary else 0
        message_budget = max(0, limits.max_total_chars - artifact_chars - summary_reserve - 512)
        selected: list[dict[str, str]] = []
        for msg in reversed(recent_messages):
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
            per_message_limit = min(limits.max_message_chars, max(0, message_budget - message_chars))
            pruned_content = self._truncate(self._prune_message_content(msg.content), per_message_limit, "message")
            if not pruned_content:
                omitted_count += 1
                continue
            if selected and message_chars + len(pruned_content) > message_budget:
                omitted_count += 1
                continue
            selected.append({"role": llm_role, "content": pruned_content})
            message_chars += len(pruned_content)
        llm_messages.extend(reversed(selected))

        if context_summary:
            summary = self._truncate(context_summary, summary_reserve, "summary")
            llm_messages.insert(0, {"role": "system", "content": f"Durable conversation summary:\n{summary}"})
        elif omitted_count:
            llm_messages.insert(
                0,
                {
                    "role": "system",
                    "content": (
                        f"{omitted_count} earlier messages were omitted by the context budget. "
                        "Do not infer missing details; ask for clarification or retrieve a referenced artifact."
                    ),
                },
            )

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
