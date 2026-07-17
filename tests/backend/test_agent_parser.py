from backend.agent_parser import parse_widget_from_text


def test_parse_unified_widget():
    sample_text = """
    Here is a unified widget utilizing HTM and React:
    <ambient-widget id="unified-counter" title="Counter App">
    <js-script>
      const { useState } = ambient.react;
      export default function Counter() {
        return ambient.html`<div>Counter</div>`;
      }
    </js-script>
    </ambient-widget>
    """

    widget = parse_widget_from_text(sample_text)
    assert widget is not None
    assert widget["id"] == "unified-counter"
    assert widget["title"] == "Counter App"
    assert "ambient.html`<div>Counter</div>`" in widget["js"]


def test_parse_no_widget():
    sample_text = "Hello, how can I help you today?"
    widget = parse_widget_from_text(sample_text)
    assert widget is None
