import re


def parse_widget_from_text(text: str) -> dict[str, str] | None:
    """
    Parses a widget structured as:
    <ambient-widget id="widget-id" title="Widget Title">
      <html-content>...</html-content>
      <css-styles>...</css-styles>
      <js-script>...</js-script>
    </ambient-widget>
    
    Returns a dict with keys: id, title, html, css, js or None if no match found.
    """
    pattern = r"<ambient-widget\s+id=\"([^\"]+)\"\s+title=\"([^\"]+)\">(.*?)</ambient-widget>"
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        return None

    widget_id, title, content = match.groups()

    html_match = re.search(r"<html-content>(.*?)</html-content>", content, re.DOTALL)
    css_match = re.search(r"<css-styles>(.*?)</css-styles>", content, re.DOTALL)
    js_match = re.search(r"<js-script>(.*?)</js-script>", content, re.DOTALL)

    return {
        "id": widget_id.strip(),
        "title": title.strip(),
        "html": html_match.group(1).strip() if html_match else "",
        "css": css_match.group(1).strip() if css_match else "",
        "js": js_match.group(1).strip() if js_match else ""
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

