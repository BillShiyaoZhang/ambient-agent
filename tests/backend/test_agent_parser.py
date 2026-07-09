import pytest
from backend.agent_parser import parse_widget_from_text

def test_parse_valid_widget():
    sample_text = """
    Here is the weather widget you requested:
    <ambient-widget id="weather-card" title="Local Weather">
    <html-content>
      <div class="weather">24°C</div>
    </html-content>
    <css-styles>
      .weather { color: sun; }
    </css-styles>
    <js-script>
      console.log("hello");
    </js-script>
    </ambient-widget>
    Hope you like it!
    """
    
    widget = parse_widget_from_text(sample_text)
    assert widget is not None
    assert widget["id"] == "weather-card"
    assert widget["title"] == "Local Weather"
    assert "24°C" in widget["html"]
    assert ".weather {" in widget["css"]
    assert 'console.log("hello");' in widget["js"]

def test_parse_no_widget():
    sample_text = "Hello, how can I help you today?"
    widget = parse_widget_from_text(sample_text)
    assert widget is None

def test_parse_partial_widget():
    # Test widget missing js-script
    sample_text = """
    <ambient-widget id="partial" title="No JS">
    <html-content><h1>Hi</h1></html-content>
    <css-styles>h1 { color: red; }</css-styles>
    </ambient-widget>
    """
    widget = parse_widget_from_text(sample_text)
    assert widget is not None
    assert widget["id"] == "partial"
    assert widget["title"] == "No JS"
    assert widget["html"] == "<h1>Hi</h1>"
    assert widget["css"] == "h1 { color: red; }"
    assert widget["js"] == ""
