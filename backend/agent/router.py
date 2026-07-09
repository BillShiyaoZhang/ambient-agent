import re
import uuid
from typing import List, Dict, Any, Tuple, Optional

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
    def route(cls, content: str, existing_apps: List[Dict[str, Any]]) -> Tuple[bool, Optional[str], str]:
        """
        Analyzes message content.
        Returns:
            (is_coding, app_id, instruction)
        """
        content_stripped = content.strip()

        # 1. Check for explicit /app command
        app_match = re.match(r"^/app\s+([a-zA-Z0-9_-]+)(?:\s+(.*))?$", content_stripped, re.IGNORECASE)
        if app_match:
            app_id = app_match.group(1).strip()
            instruction = app_match.group(2) or "Refactor or inspect the app."
            return True, app_id, instruction.strip()

        # 2. Check if user mentions an existing app to modify
        for app_meta in existing_apps:
            app_id_clean = app_meta.get("id", "")
            base_name = app_id_clean.split("-")[0]
            
            # Match directly or by common Chinese terms mapped to standard widget base names
            zh_terms = cls.ZH_MAPPINGS.get(base_name, [])
            if (app_id_clean in content_stripped.lower() or 
                base_name in content_stripped.lower() or 
                any(term in content_stripped for term in zh_terms)):
                return True, app_id_clean, content_stripped

        # 3. Check for keywords indicating a new app creation
        has_en_pattern = any(re.search(pat, content_stripped, re.IGNORECASE) for pat in cls.CREATION_PATTERNS_EN)
        has_zh_pattern = any(v in content_stripped for v in cls.CREATION_VERBS) and any(a in content_stripped for a in cls.APP_TYPES)

        if has_en_pattern or has_zh_pattern:
            guessed_name = "new-app"
            lower_content = content_stripped.lower()
            
            # Map keyword cues to specific templates
            if "calculator" in lower_content or "计算器" in content_stripped:
                guessed_name = "calculator-app"
            elif any(w in lower_content for w in ["stopwatch", "clock", "timer"]) or any(w in content_stripped for w in ["秒表", "时钟", "计时器"]):
                guessed_name = "clock-app"
            elif "todo" in lower_content or "task" in lower_content or any(w in content_stripped for w in ["待办", "任务"]):
                guessed_name = "todo-app"
            elif "notes" in lower_content or any(w in content_stripped for w in ["笔记", "便签"]):
                guessed_name = "notes-app"
            elif "calendar" in lower_content or "日历" in content_stripped:
                guessed_name = "calendar-app"
            elif "chart" in lower_content or "图表" in content_stripped:
                guessed_name = "chart-app"
            elif "weather" in lower_content or "天气" in content_stripped:
                guessed_name = "weather-app"

            suffix = uuid.uuid4().hex[:4]
            app_id = f"{guessed_name}-{suffix}"
            return True, app_id, content_stripped

        # 4. Standard conversational query
        return False, None, content_stripped
