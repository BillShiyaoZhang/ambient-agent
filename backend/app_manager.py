import os
import json
import shutil
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

class AppManager:
    def __init__(self):
        self.apps_dir = os.getenv("APPS_DIR", os.path.join("backend", "apps"))

    def _get_app_path(self, app_id: str) -> str:
        return os.path.join(self.apps_dir, app_id)

    def create_or_update_app(self, app_id: str, title: str, html: str, css: str, js: str) -> None:
        app_path = self._get_app_path(app_id)
        os.makedirs(app_path, exist_ok=True)

        # Write metadata.json
        meta_path = os.path.join(app_path, "metadata.json")
        created_at = datetime.now(timezone.utc).isoformat()
        
        # Preserve created_at if already exists
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    existing_meta = json.load(f)
                    created_at = existing_meta.get("created_at", created_at)
            except Exception:
                pass

        meta_data = {
            "id": app_id,
            "title": title,
            "created_at": created_at,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta_data, f, indent=2, ensure_ascii=False)

        # Write index.html (View)
        with open(os.path.join(app_path, "index.html"), "w", encoding="utf-8") as f:
            f.write(html)

        # Write style.css (Style)
        with open(os.path.join(app_path, "style.css"), "w", encoding="utf-8") as f:
            f.write(css)

        # Write controller.js (Controller)
        with open(os.path.join(app_path, "controller.js"), "w", encoding="utf-8") as f:
            f.write(js)

        # Write empty data.json (Model) if not already exists
        data_path = os.path.join(app_path, "data.json")
        if not os.path.exists(data_path):
            with open(data_path, "w", encoding="utf-8") as f:
                f.write("{}")

    def _ensure_metadata(self, app_id: str, app_path: str, meta_path: str) -> Optional[Dict[str, Any]]:
        import re
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        
        # If metadata.json does not exist, verify that index.html exists
        html_path = os.path.join(app_path, "index.html")
        if not os.path.exists(html_path):
            return None

        # Extract title from index.html if possible, fallback to clean app_id
        title = app_id.replace("-", " ").title()
        try:
            with open(html_path, "r", encoding="utf-8") as f:
                html_content = f.read()
            title_match = re.search(r"<title>(.*?)</title>", html_content, re.IGNORECASE)
            if title_match:
                title = title_match.group(1).strip()
        except Exception:
            pass

        meta_data = {
            "id": app_id,
            "title": title,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        try:
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta_data, f, indent=2, ensure_ascii=False)
            return meta_data
        except Exception:
            return None

    def get_app_files(self, app_id: str) -> Optional[Dict[str, str]]:
        app_path = self._get_app_path(app_id)
        meta_path = os.path.join(app_path, "metadata.json")
        meta = self._ensure_metadata(app_id, app_path, meta_path)
        if not meta:
            return None

        try:
            html = ""
            html_path = os.path.join(app_path, "index.html")
            if os.path.exists(html_path):
                with open(html_path, "r", encoding="utf-8") as f:
                    html = f.read()

            css = ""
            css_path = os.path.join(app_path, "style.css")
            if os.path.exists(css_path):
                with open(css_path, "r", encoding="utf-8") as f:
                    css = f.read()

            js = ""
            js_path = os.path.join(app_path, "controller.js")
            if os.path.exists(js_path):
                with open(js_path, "r", encoding="utf-8") as f:
                    js = f.read()

            return {
                "id": app_id,
                "title": meta.get("title", app_id),
                "html": html,
                "css": css,
                "js": js
            }
        except Exception:
            return None

    def list_apps(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.apps_dir):
            return []

        apps_list = []
        try:
            for item in os.listdir(self.apps_dir):
                item_path = os.path.join(self.apps_dir, item)
                if os.path.isdir(item_path):
                    meta_path = os.path.join(item_path, "metadata.json")
                    meta = self._ensure_metadata(item, item_path, meta_path)
                    if meta:
                        apps_list.append(meta)
        except Exception:
            pass
        return apps_list

    def delete_app(self, app_id: str) -> bool:
        app_path = self._get_app_path(app_id)
        if not os.path.exists(app_path):
            return False
        try:
            shutil.rmtree(app_path)
            return True
        except Exception:
            return False

    def get_app_data(self, app_id: str) -> Dict[str, Any]:
        app_path = self._get_app_path(app_id)
        data_path = os.path.join(app_path, "data.json")
        if not os.path.exists(data_path):
            return {}
        try:
            with open(data_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def save_app_data(self, app_id: str, data: Dict[str, Any]) -> None:
        app_path = self._get_app_path(app_id)
        # Ensure directory exists if they save data
        os.makedirs(app_path, exist_ok=True)
        data_path = os.path.join(app_path, "data.json")
        try:
            with open(data_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
