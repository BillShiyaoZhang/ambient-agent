import json
import os
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
        """Return App IDs from structured artifact-reference messages."""

        app_ids: set[str] = set()
        for msg in messages:
            reference = self._artifact_reference(msg.content)
            if reference is not None:
                app_ids.add(reference["app_id"])

        return sorted(app_ids)

    @staticmethod
    def _artifact_reference(content: str) -> dict[str, str] | None:
        try:
            payload = json.loads(content)
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("artifact") != "app":
            return None
        app_id = payload.get("app_id")
        if not isinstance(app_id, str) or not app_id.strip():
            return None
        return {
            "artifact": "app",
            "app_id": app_id.strip(),
            **(
                {"manifest_revision": str(payload["manifest_revision"])}
                if payload.get("manifest_revision") is not None
                else {}
            ),
            **({"grants_digest": str(payload["grants_digest"])} if payload.get("grants_digest") else {}),
        }

    def _compact_message_content(self, content: str) -> str:
        reference = self._artifact_reference(content)
        return json.dumps(reference, ensure_ascii=False, sort_keys=True) if reference is not None else content

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
            content = self._truncate(self._compact_message_content(message.content), 600, "summary item")
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
                artifact = (
                    f"[Active App: {app_id}]\n"
                    f"Format: Manifest V2 React/HTM\n"
                    f"Title: {app_files['title']}\n"
                    f"Manifest revision: {app_files.get('manifest_revision', '')}\n"
                    f"Capability digest: {app_files.get('grants_digest', '')}\n"
                    f"Capabilities: {app_files.get('capabilities', [])}\n"
                    f"--- controller.js ---\n"
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

            # Structured artifact references remain compact and deterministic.
            per_message_limit = min(limits.max_message_chars, max(0, message_budget - message_chars))
            pruned_content = self._truncate(self._compact_message_content(msg.content), per_message_limit, "message")
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
                "Here are read-only snapshots of active App artifacts referenced by this conversation.\n"
                "Use them to understand current state. Any App change must go through the isolated Widget coding, "
                "verification, and atomic-promotion workflow; never return executable App source in conversation.\n\n"
                + "\n\n".join(active_apps_context)
            )
            # Insert active app context right after system prompt, or as a system message at index 0.
            # We will insert it at index 0 so it sets the stage.
            llm_messages.insert(0, {"role": "system", "content": apps_context_str})

        return llm_messages
