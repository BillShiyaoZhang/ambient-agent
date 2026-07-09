import os
import httpx
from typing import Optional
from sqlmodel import Session
from backend.models import LLMAuditLog

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
  // To persist and sync data/state:
  //   const data = await ambient.model.get(); // returns dict, initially {}
  //   await ambient.model.set(newData);       // saves to backend data.json and syncs
  //   ambient.model.onChange(data => { ... }); // triggers on data updates (e.g. from other devices)
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
from typing import List, Dict

async def call_llm_api(provider: str, model: str, messages: List[Dict[str, str]]) -> str:
    """
    Directly contacts Ollama (chat endpoint) or cloud providers using HTTP clients.
    """
    if provider == "ollama":
        # Ollama local chat endpoint
        raw_url = os.getenv("OLLAMA_API_URL", "http://localhost:11434/api/chat")
        # Ensure we use the /api/chat endpoint for messages list
        if "generate" in raw_url:
            url = raw_url.replace("generate", "chat")
        else:
            url = raw_url
            
        payload = {
            "model": model,
            "messages": messages,
            "stream": False
        }
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, timeout=300.0)
                if response.status_code == 200:
                    return response.json().get("message", {}).get("content", "")
                else:
                    return f"Error from Ollama server (status code {response.status_code}): {response.text}"
        except Exception as e:
            return f"Failed to connect to local Ollama server. Make sure Ollama is running. Error: {str(e)}"
            
    else:
        # Default fallback to mock or generic OpenAI compatible API if configured
        api_key = os.getenv("LLM_API_KEY", "")
        api_url = os.getenv("LLM_API_URL", "https://api.openai.com/v1/chat/completions")
        
        if not api_key:
            prompt_summary = messages[-1]["content"] if messages else ""
            return f"Mock response: You requested using provider '{provider}' and model '{model}', but no API key was configured."
            
        # Standard OpenAI compatible completions payload
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.7
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        trust_env = os.getenv("LLM_TRUST_ENV", "true").lower() != "false"
        try:
            async with httpx.AsyncClient(trust_env=trust_env) as client:
                response = await client.post(api_url, json=payload, headers=headers, timeout=300.0)
                if response.status_code == 200:
                    choices = response.json().get("choices", [])
                    if choices:
                        return choices[0].get("message", {}).get("content", "")
                    return "No response content received from API."
                else:
                    return f"API Error (status code {response.status_code}): {response.text}"
        except Exception as e:
            import traceback
            traceback.print_exc()
            return f"Failed to connect to cloud API provider. Error: {type(e).__name__}: {str(e)}"

async def generate_agent_response(
    messages: Optional[List[Dict[str, str]]] = None,
    provider: str = "ollama",
    model: str = "llama3",
    session: Session = None,
    user_message: Optional[str] = None
) -> str:
    """
    Coordinates LLM execution, registers prompts and responses in the audit database.
    """
    if messages is None:
        if user_message is not None:
            # Wrap legacy string prompt into structured messages list, including SYSTEM_PROMPT
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message}
            ]
        else:
            messages = []

    # 1. Trigger LLM call
    response_text = await call_llm_api(provider, model, messages)
    
    # Extract last user message or serialize full messages for audit log
    prompt_str = ""
    try:
        prompt_str = json.dumps(messages, ensure_ascii=False)
    except Exception:
        prompt_str = str(messages)

    # 2. Write to Audit Log database table
    audit_log = LLMAuditLog(
        provider=provider,
        model=model,
        prompt=prompt_str,
        response=response_text
    )
    session.add(audit_log)
    session.commit()
    session.refresh(audit_log)
    
    return response_text

class LLMService:
    @staticmethod
    async def call_llm_api(provider: str, model: str, messages: List[Dict[str, str]]) -> str:
        """
        Wrapper matching UML definition for call_llm_api.
        """
        return await call_llm_api(provider, model, messages)

    @staticmethod
    async def generate_agent_response(
        messages: Optional[List[Dict[str, str]]] = None,
        provider: str = "ollama",
        model: str = "llama3",
        session: Session = None,
        user_message: Optional[str] = None
    ) -> str:
        """
        Wrapper matching UML definition for generate_agent_response.
        """
        return await generate_agent_response(
            messages=messages,
            provider=provider,
            model=model,
            session=session,
            user_message=user_message
        )

