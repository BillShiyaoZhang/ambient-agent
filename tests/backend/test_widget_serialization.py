from backend.agent_parser import serialize_widget_to_text


def test_serialize_unified_widget():
    widget = {
        "id": "my-unified-app",
        "title": "Unified App",
        "html": "",
        "css": "",
        "js": "export default function App() { return ambient.html`<div>Unified</div>`; }",
    }
    xml = serialize_widget_to_text(widget)
    assert '<ambient-widget id="my-unified-app" title="Unified App">' in xml
    assert "<html-content>" not in xml
    assert "<css-styles>" not in xml
    assert "<js-script>\nexport default function App() { return ambient.html`<div>Unified</div>`; }\n</js-script>" in xml
