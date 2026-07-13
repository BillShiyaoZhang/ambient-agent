import asyncio
import os
import threading
from typing import Any

import httpx
from sqlmodel import Session

from backend.models import LLMAuditLog


# Thread-local state for retry instrumentation. The OFAT runner uses this to
# detect when a trial was rate-limited and re-run it.
_retry_state = threading.local()


class RetryStats:
    """Tracks retry behaviour for a single LLM call."""

    __slots__ = ("attempts", "exhausted", "retried", "status_codes")

    def __init__(self) -> None:
        self.attempts = 1
        self.retried = False
        self.status_codes: list[int] = []
        self.exhausted = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempts": self.attempts,
            "retried": self.retried,
            "status_codes": list(self.status_codes),
            "exhausted": self.exhausted,
        }


def get_last_retry_stats() -> RetryStats | None:
    """Return the RetryStats recorded for the most recent ``call_llm_api`` call.

    The runner uses this to detect rate-limit retried trials so they can be
    re-run on a fresh API quota window.
    """
    return getattr(_retry_state, "last_stats", None)


def _set_last_retry_stats(stats: RetryStats | None) -> None:
    _retry_state.last_stats = stats

# Default system prompt for the Ambient Agent
SYSTEM_PROMPT = """You are Ambient Agent, an agentic personal coding and productivity assistant.
You can communicate in normal text, but you also have the special ability to spawn dynamic UI widgets on the user's workspace screen when they request something visual (like weather, todo lists, notes, calculators, calendars, system monitoring, charts, etc.).

To spawn a widget, output a block in this exact XML-like format anywhere in your reply:

<ambient-widget id="UNIQUE_WIDGET_ID" title="WIDGET_TITLE_NAME">
<html-content>
  <!-- Raw HTML body using Tailwind/CSS classes -->
</html-content>
<css-styles>
  /* Scoped CSS rules targeting classes inside the widget */
</css-styles>
<js-script>
  // Scoped JavaScript. You are passed 'root' (the widget's HTML content div) and 'ambient' (the client SDK).
  // Use root.querySelector to select elements. Do NOT write global variables.
  // To persist and sync data/state via the Knowledge Graph:
  //   ambient.graph.subscribe(query, callback); // listen for data changes matching a graph query
  //   ambient.graph.mutate(operation);          // write data to the graph (triggers sync across devices)
  // To interact with chat:
  //   ambient.sendMessage("message text"); // sends user message in chat
  // To control window:
  //   ambient.fullscreen(); // requests fullscreen view
  //   ambient.minimize();   // minimizes/restores grid view
</js-script>
</ambient-widget>

Always make widgets look visually stunning, glassmorphic, responsive, and functional! Keep user data private and run locally when possible.
"""

import json


async def call_llm_api(
    provider: str, model: str, messages: list[dict[str, str]], tools: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """
    Directly contacts Ollama (chat endpoint) or cloud providers using HTTP clients.
    Returns a dict: {"content": str, "tool_calls": Optional[List[Dict[str, Any]]]}
    """
    # Reset per-call retry stats so callers can read them after the call.
    _set_last_retry_stats(None)
    stats = RetryStats()
    _set_last_retry_stats(stats)

    if provider == "ollama":
        # Ollama local chat endpoint
        raw_url = os.getenv("OLLAMA_API_URL", "http://localhost:11434/api/chat")
        # Ensure we use the /api/chat endpoint for messages list
        if "generate" in raw_url:
            url = raw_url.replace("generate", "chat")
        else:
            url = raw_url

        payload = {"model": model, "messages": messages, "stream": False}
        if tools:
            payload["tools"] = tools

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, timeout=300.0)
                if response.status_code == 200:
                    msg_data = response.json().get("message", {})
                    return {
                        "content": msg_data.get("content", "") or "",
                        "tool_calls": msg_data.get("tool_calls", None),
                    }
                else:
                    stats.status_codes.append(response.status_code)
                    return {
                        "content": f"Error from Ollama server (status code {response.status_code}): {response.text}",
                        "tool_calls": None,
                    }
        except Exception as e:
            return {
                "content": f"Failed to connect to local Ollama server. Make sure Ollama is running. Error: {e!s}",
                "tool_calls": None,
            }

    else:
        # Default fallback to mock or generic OpenAI compatible API if configured
        api_key = os.getenv("LLM_API_KEY", "")
        api_url = os.getenv("LLM_API_URL", "https://api.openai.com/v1/chat/completions")

        if not api_key:
            return {
                "content": f"Mock response: You requested using provider '{provider}' and model '{model}', but no API key was configured.",
                "tool_calls": None,
            }

        # Standard OpenAI compatible completions payload
        payload = {"model": model, "messages": messages, "temperature": 0.7}
        if tools:
            payload["tools"] = tools

        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        trust_env = os.getenv("LLM_TRUST_ENV", "true").lower() != "false"

        # Retry policy: 5 attempts with exponential backoff for rate-limit (2062)
        # and longer backoff for usage-cap (2056).
        max_retries = int(os.getenv("LLM_MAX_RETRIES", "5"))
        backoff_base = float(os.getenv("LLM_BACKOFF_BASE", "2.0"))
        usage_cap_backoff = float(os.getenv("LLM_USAGE_CAP_BACKOFF", "30.0"))

        import logging

        logger = logging.getLogger("backend.llm_service")

        for attempt in range(max_retries + 1):
            stats.attempts = attempt + 1
            try:
                async with httpx.AsyncClient(trust_env=trust_env) as client:
                    response = await client.post(api_url, json=payload, headers=headers, timeout=300.0)
                    if response.status_code == 200:
                        res_json = response.json()
                        base_resp = res_json.get("base_resp") or {}
                        status_code = base_resp.get("status_code")
                        status_msg = base_resp.get("status_msg", "")
                        if status_code is not None:
                            stats.status_codes.append(int(status_code))

                        if status_code == 2062:
                            # Soft rate limit — short backoff
                            logger.warning(
                                f"LLM rate-limited (2062), attempt {attempt+1}/{max_retries+1}: {status_msg}"
                            )
                            stats.retried = True
                            if attempt < max_retries:
                                await asyncio.sleep(backoff_base ** attempt)
                                continue
                        elif status_code == 2056:
                            # Hard usage cap — longer backoff, may need to wait
                            logger.warning(
                                f"LLM usage cap (2056), attempt {attempt+1}/{max_retries+1}: {status_msg}"
                            )
                            stats.retried = True
                            if attempt < max_retries:
                                await asyncio.sleep(usage_cap_backoff * (attempt + 1))
                                continue
                            stats.exhausted = True
                            return {
                                "content": f"Usage cap: {status_msg}",
                                "tool_calls": None,
                            }

                        choices = res_json.get("choices", [])
                        if choices:
                            msg_data = choices[0].get("message", {})
                            return {
                                "content": msg_data.get("content", "") or "",
                                "tool_calls": msg_data.get("tool_calls", None),
                            }
                        logger.error(
                            f"LLM API returned 200 but choices list is empty. Raw response: {response.text}"
                        )
                        return {
                            "content": f"No response content received from API. Raw: {response.text}",
                            "tool_calls": None,
                        }
                    else:
                        stats.status_codes.append(response.status_code)
                        return {
                            "content": f"API Error (status code {response.status_code}): {response.text}",
                            "tool_calls": None,
                        }
            except Exception as e:
                import traceback

                traceback.print_exc()
                return {
                    "content": f"Failed to connect to cloud API provider. Error: {type(e).__name__}: {e!s}",
                    "tool_calls": None,
                }
        stats.exhausted = True
        return {"content": "Exhausted retries", "tool_calls": None}


async def generate_agent_response(
    messages: list[dict[str, str]] | None = None,
    provider: str = "ollama",
    model: str = "llama3",
    session: Session = None,
    user_message: str | None = None,
) -> str:
    """
    Coordinates LLM execution, registers prompts and responses in the audit database.
    """
    if messages is None:
        if user_message is not None:
            # Wrap legacy string prompt into structured messages list, including SYSTEM_PROMPT
            messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_message}]
        else:
            messages = []

    # 1. Trigger LLM call
    response_data = await call_llm_api(provider, model, messages)
    if isinstance(response_data, str):
        response_text = response_data
    else:
        response_text = response_data.get("content", "")

    # Extract last user message or serialize full messages for audit log
    prompt_str = ""
    try:
        prompt_str = json.dumps(messages, ensure_ascii=False)
    except Exception:
        prompt_str = str(messages)

    # 2. Write to Audit Log database table
    audit_log = LLMAuditLog(provider=provider, model=model, prompt=prompt_str, response=response_text)
    session.add(audit_log)
    session.commit()
    session.refresh(audit_log)

    return response_text


class LLMService:
    @staticmethod
    async def call_llm_api(
        provider: str, model: str, messages: list[dict[str, str]], tools: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        """
        Wrapper matching UML definition for call_llm_api.
        """
        return await call_llm_api(provider, model, messages, tools)

    @staticmethod
    async def generate_agent_response(
        messages: list[dict[str, str]] | None = None,
        provider: str = "ollama",
        model: str = "llama3",
        session: Session = None,
        user_message: str | None = None,
    ) -> str:
        """
        Wrapper matching UML definition for generate_agent_response.
        """
        return await generate_agent_response(
            messages=messages, provider=provider, model=model, session=session, user_message=user_message
        )
