import re
import os
import json
import uuid
from typing import List, Dict, Any, Tuple, Optional
from sqlmodel import Session

class IntentRouter:
    """
    Decoupled intent routing engine that parses user messages and classifies them 
    into either:
    - Widget coding tasks (app creation, modifications)
    - General conversational questions
    """

    ZH_MAPPINGS = {
        "clock": ["时钟", "秒表", "计时器"],
        "weather": ["天气"],
        "todo": ["待办", "任务"],
        "calculator": ["计算器"],
        "notes": ["笔记", "便签"],
        "calendar": ["日历"],
        "chart": ["图表"],
    }

    CREATION_VERBS = [
        "创建", "制作", "生成", "开发", "写", "设计", "做", 
        "修改", "更新", "增加", "改变", "修复", "优化", "调整", 
        "改下", "完善", "加上", "添加", "重构"
    ]
    
    APP_TYPES = [
        "计算器", "天气", "时钟", "秒表", "计时器", "待办", 
        "任务", "日历", "日程", "笔记", "便签", "图表", 
        "widget", "app", "gui", "应用", "小程序"
    ]

    CREATION_PATTERNS_EN = [
        r"\b(?:create|build|make|generate|write|develop)\s+(?:a\s+)?(?:new\s+)?(?:widget|app|gui|dashboard)\b",
        r"\b(?:modify|update|add|change|fix)\s+(?:the\s+)?(?:widget|app|gui)\b"
    ]

    @classmethod
    async def route(cls, content: str, existing_apps: List[Dict[str, Any]], db_session: Optional[Session] = None) -> Tuple[bool, Optional[str], str]:
        """
        Analyzes message content using an LLM.
        Returns:
            (is_coding, app_id, instruction)
        """
        content_stripped = content.strip()

        # 1. Check for explicit /app command (Fast Fallback - Always overrides)
        app_match = re.match(r"^/app\s+([a-zA-Z0-9_-]+)(?:\s+(.*))?$", content_stripped, re.IGNORECASE)
        if app_match:
            app_id = app_match.group(1).strip()
            instruction = app_match.group(2) or "Refactor or inspect the app."
            return True, app_id, instruction.strip()

        # 2. Call LLM for Intent Routing
        provider_name = os.getenv("LLM_PROVIDER", "ollama")
        model_name = os.getenv("LLM_MODEL", "llama3")
        
        # Import dynamically to avoid circular dependencies
        from backend.agent.providers import get_llm_provider
        provider = get_llm_provider(provider_name, model_name)

        # Format list of existing apps
        if existing_apps:
            apps_list_str = "\n".join([f"- ID: {app['id']}, Title: {app.get('title', '')}" for app in existing_apps])
        else:
            apps_list_str = "(None)"

        system_prompt = f"""You are an intent routing assistant for Ambient Agent.
Your task is to classify whether a user's request is a widget coding task (creating a new app/widget, or modifying/updating/fixing/optimizing an existing app/widget) OR a general conversational question/message.

We have these existing widgets in the workspace:
{apps_list_str}

Please respond in JSON format with three fields:
1. "is_coding": boolean (true if the user is asking to build, update, change, fix, style, create, or modify a widget/app; false if it's general conversation/question/greeting).
2. "app_id": string or null (if is_coding is true, this is the ID of the widget. If it refers to an existing widget, use that widget's exact ID from the list. If it is a new widget, suggest a URL-friendly name in kebab-case like "todo-app", "clock-app", "weather-app", etc., appending a random 4-character hex suffix like "-8f3a").
3. "instruction": string (if is_coding is true, extract the specific modification or creation task instruction; if is_coding is false, return the original message).

Examples of is_coding=true:
- "Make clock-app-1234 look glassmorphic" -> {{"is_coding": true, "app_id": "clock-app-1234", "instruction": "Make clock-app-1234 look glassmorphic"}}
- "帮我改一下待办清单，加上删除按钮" (where existing apps has "todo-app-abcd") -> {{"is_coding": true, "app_id": "todo-app-abcd", "instruction": "加上删除按钮"}}
- "创建天气小程序" -> {{"is_coding": true, "app_id": "weather-app-8f3a", "instruction": "创建天气小程序"}}

Examples of is_coding=false:
- "Hello, who are you?" -> {{"is_coding": false, "app_id": null, "instruction": "Hello, who are you?"}}
- "Tell me a joke" -> {{"is_coding": false, "app_id": null, "instruction": "Tell me a joke"}}

Response MUST be a valid JSON object ONLY.
"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content_stripped}
        ]

        try:
            raw_response = await provider.generate(messages, db_session=db_session)
            
            # Extract JSON block using regex
            json_match = re.search(r"\{.*\}", raw_response, re.DOTALL)
            if not json_match:
                raise ValueError("LLM response did not contain a valid JSON object.")
            
            parsed = json.loads(json_match.group(0))
            is_coding = bool(parsed.get("is_coding", False))
            app_id = parsed.get("app_id")
            instruction = parsed.get("instruction", content_stripped)
            
            # Resolve ambiguity
            if is_coding and app_id:
                base_name = app_id.split("-")[0]
                matching_apps = [app for app in existing_apps if app["id"] == app_id or app["id"].split("-")[0] == base_name]
                
                # If there are multiple matching apps in the workspace, return ambiguity prompt conversational message
                if len(matching_apps) > 1:
                    ids_str = ", ".join([f"`{app['id']}`" for app in matching_apps])
                    msg = f"我发现您有多个同类型应用（{ids_str}），请使用 `/app <Widget ID> <指令>` 明确指定您想修改哪一个。"
                    return False, None, msg

            return is_coding, app_id, instruction

        except Exception as e:
            raise ValueError(f"意图路由大模型解析失败或网络异常。详情: {str(e)}")
