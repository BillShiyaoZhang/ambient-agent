import os
import httpx
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
  // Scoped JavaScript. You are passed 'root' which points to the widget's HTML content div.
  // Use root.querySelector to select elements. Do NOT write global variables.
  // Attach event listeners or draw elements using root.
</js-script>
</ambient-widget>

Always make widgets look visually stunning, glassmorphic, responsive, and functional! Keep user data private and run locally when possible.
"""

async def call_llm_api(provider: str, model: str, prompt: str) -> str:
    """
    Directly contacts Ollama or cloud providers using HTTP clients.
    """
    if provider == "ollama":
        # Ollama local endpoint
        url = os.getenv("OLLAMA_API_URL", "http://localhost:11434/api/generate")
        payload = {
            "model": model,
            "prompt": prompt,
            "system": SYSTEM_PROMPT,
            "stream": False
        }
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, timeout=60.0)
                if response.status_code == 200:
                    return response.json().get("response", "")
                else:
                    return f"Error from Ollama server (status code {response.status_code}): {response.text}"
        except Exception as e:
            return f"Failed to connect to local Ollama server. Make sure Ollama is running. Error: {str(e)}"
            
    else:
        # Default fallback to mock or generic OpenAI compatible API if configured
        api_key = os.getenv("LLM_API_KEY", "")
        api_url = os.getenv("LLM_API_URL", "https://api.openai.com/v1/chat/completions")
        
        if not api_key:
            return f"Mock response: You requested '{prompt}' using provider '{provider}' and model '{model}', but no API key was configured."
            
        # Standard OpenAI compatible completions payload
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.7
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(api_url, json=payload, headers=headers, timeout=60.0)
                if response.status_code == 200:
                    choices = response.json().get("choices", [])
                    if choices:
                        return choices[0].get("message", {}).get("content", "")
                    return "No response content received from API."
                else:
                    return f"API Error (status code {response.status_code}): {response.text}"
        except Exception as e:
            return f"Failed to connect to cloud API provider. Error: {str(e)}"

async def generate_agent_response(user_message: str, provider: str, model: str, session: Session) -> str:
    """
    Coordinates LLM execution, registers prompts and responses in the audit database.
    """
    # 1. Trigger LLM call
    response_text = await call_llm_api(provider, model, user_message)
    
    # 2. Write to Audit Log database table
    audit_log = LLMAuditLog(
        provider=provider,
        model=model,
        prompt=user_message,
        response=response_text
    )
    session.add(audit_log)
    session.commit()
    session.refresh(audit_log)
    
    return response_text
