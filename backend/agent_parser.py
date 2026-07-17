import re
from typing import Any


def parse_widget_from_text(text: str) -> dict[str, str] | None:
    """
    Parses a widget structured as:
    <ambient-widget id="widget-id" title="Widget Title">
      <js-script>...</js-script>
    </ambient-widget>

    Returns a dict with keys: id, title, js, html, css or None if no match found.
    """
    # Robust matching of ambient-widget tag and its attributes in any order
    tag_match = re.search(r"(<ambient-widget\s+[^>]*?>)(.*?)</ambient-widget>", text, re.DOTALL)
    if not tag_match:
        return None

    tag_open, content = tag_match.groups()
    id_match = re.search(r'id="([^"]+)"', tag_open)
    title_match = re.search(r'title="([^"]+)"', tag_open)
    if not id_match or not title_match:
        return None

    widget_id = id_match.group(1).strip()
    title = title_match.group(1).strip()

    js_match = re.search(r"<js-script>(.*?)</js-script>", content, re.DOTALL)
    return {
        "id": widget_id,
        "title": title,
        "js": js_match.group(1).strip() if js_match else "",
        "html": "",
        "css": "",
    }


class AgentParser:
    @staticmethod
    def parse_widgets(text: str) -> list[dict[str, str]]:
        """
        Parses all widgets from text. Currently parses a single widget
        and returns it inside a list, matching UML specification.
        """
        widget = parse_widget_from_text(text)
        return [widget] if widget else []


def serialize_widget_to_text(widget: dict[str, Any]) -> str:
    """
    Serializes a widget dictionary back into its XML representation.
    """
    widget_id = widget.get("id", "")
    title = widget.get("title", "")
    js = widget.get("js", "")

    return f'<ambient-widget id="{widget_id}" title="{title}">\n<js-script>\n{js}\n</js-script>\n</ambient-widget>'
