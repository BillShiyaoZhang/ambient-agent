import re


def parse_widget_from_text(text: str) -> dict[str, str] | None:
    """
    Parses a widget structured as:
    <ambient-widget id="widget-id" title="Widget Title">
      <layout-json>...</layout-json>  (For A2UI)
      <js-script>...</js-script>
    </ambient-widget>
    OR:
    <ambient-widget id="widget-id" title="Widget Title">
      <html-content>...</html-content> (For Legacy)
      <css-styles>...</css-styles>
      <js-script>...</js-script>
    </ambient-widget>

    Returns a dict with keys: id, title, layout (if A2UI), html, css (if legacy), js or None if no match found.
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

    layout_match = re.search(r"<layout-json>(.*?)</layout-json>", content, re.DOTALL)
    if layout_match:
        js_match = re.search(r"<js-script>(.*?)</js-script>", content, re.DOTALL)
        return {
            "id": widget_id,
            "title": title,
            "layout": layout_match.group(1).strip(),
            "js": js_match.group(1).strip() if js_match else "",
        }

    html_match = re.search(r"<html-content>(.*?)</html-content>", content, re.DOTALL)
    css_match = re.search(r"<css-styles>(.*?)</css-styles>", content, re.DOTALL)
    js_match = re.search(r"<js-script>(.*?)</js-script>", content, re.DOTALL)

    return {
        "id": widget_id,
        "title": title,
        "html": html_match.group(1).strip() if html_match else "",
        "css": css_match.group(1).strip() if css_match else "",
        "js": js_match.group(1).strip() if js_match else "",
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
