"""Tests for the structured schema diff (Direction A).

The single most important regression test is that the calendar widget's
controller.js (calendar-app-7b2e) reliably surfaces the four extension fields
it uses beyond the core Event schema: ``category``, ``color``, ``reminder``,
``end_date``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.schema_diff import (
    SchemaExtractor,
    diff_controller_js,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
CALENDAR_JS = REPO_ROOT / "workspace" / "apps" / "calendar-app-7b2e" / "controller.js"


CORE_SCHEMAS = [
    {
        "id": "Task",
        "properties": {
            "title": "string",
            "description": "string",
            "status": "string",
            "due_date": "string",
        },
    },
    {
        "id": "Event",
        "properties": {
            "title": "string",
            "description": "string",
            "start_time": "string",
            "end_time": "string",
            "location": "string",
        },
    },
    {
        "id": "Note",
        "properties": {
            "title": "string",
            "content": "string",
            "tags": "string",
        },
    },
]


def test_clean_widget_passes_diff():
    """A widget that uses only schema-declared props should produce a clean diff."""
    js = """
    ambient.graph.subscribe({ type: "Task" }, (nodes) => { render(nodes); });
    await ambient.graph.mutate([
      { action: "create_node", id: "t-1", type: "Task", properties: { title: "buy milk", status: "pending" } }
    ]);
    """
    diff = diff_controller_js(js, CORE_SCHEMAS)
    assert diff.is_clean, diff.to_markdown()
    assert "PASSED" in diff.to_markdown()


def test_unknown_props_are_detected():
    js = """
    await ambient.graph.mutate([
      { action: "create_node", id: "e-1", type: "Event",
        properties: { title: "foo", category: "work", color: "#ff0000" } }
    ]);
    """
    diff = diff_controller_js(js, CORE_SCHEMAS)
    assert not diff.is_clean
    names = {(u.node_type, u.property_name) for u in diff.unknown_props}
    assert ("Event", "category") in names
    assert ("Event", "color") in names


def test_unknown_type_is_detected():
    js = """
    ambient.graph.subscribe({ type: "MyCustomType" }, (nodes) => {});
    await ambient.graph.mutate([
      { action: "create_node", id: "x-1", type: "MyCustomType", properties: { foo: "bar" } }
    ]);
    """
    diff = diff_controller_js(js, CORE_SCHEMAS)
    assert not diff.is_clean
    type_names = {ut.type_name for ut in diff.unknown_types}
    assert "MyCustomType" in type_names


def test_update_node_action_template_is_picked_up():
    """A widget that builds mutation templates and pushes them to an array
    before passing to ``mutate(...)`` should still be caught."""
    js = """
    const mutations = [];
    if (action === 'create') {
      mutations.push({ action: 'create_node', id: evt.id, type: 'Event',
        properties: { title: evt.title, reminder: evt.reminder } });
    }
    await ambient.graph.mutate(mutations);
    """
    diff = diff_controller_js(js, CORE_SCHEMAS)
    names = {(u.node_type, u.property_name) for u in diff.unknown_props}
    assert ("Event", "reminder") in names


def test_binary_expression_values_do_not_break_parser():
    """``evt.foo || ''`` style expressions should be tolerated."""
    js = """
    await ambient.graph.mutate([
      { action: 'create_node', id: 'e', type: 'Event',
        properties: { title: evt.title, color: evt.color || '#000000' } }
    ]);
    """
    diff = diff_controller_js(js, CORE_SCHEMAS)
    names = {(u.node_type, u.property_name) for u in diff.unknown_props}
    assert ("Event", "color") in names


def test_to_per_field_payload_returns_one_entry_per_unknown_prop():
    js = """
    await ambient.graph.mutate([
      { action: 'create_node', id: 'e', type: 'Event',
        properties: { title: 'x', color: 'red' } }
    ]);
    """
    diff = diff_controller_js(js, CORE_SCHEMAS)
    payload = diff.to_per_field_payload()
    assert len(payload) == 1
    assert payload[0]["node_type"] == "Event"
    assert payload[0]["property_name"] == "color"
    assert payload[0]["action"] == "extend_schema"


@pytest.mark.skipif(not CALENDAR_JS.exists(), reason="calendar widget not on disk")
def test_calendar_widget_real_js_surfaces_four_unknown_props():
    """The headline regression test.

    The real ``controller.js`` of calendar-app-7b2e uses four Event fields
    beyond the core schema: ``category``, ``color``, ``reminder``, ``end_date``.
    The diff must surface all four deterministically.
    """
    js = CALENDAR_JS.read_text(encoding="utf-8")
    diff = diff_controller_js(js, CORE_SCHEMAS)
    names = {(u.node_type, u.property_name) for u in diff.unknown_props}
    expected = {
        ("Event", "category"),
        ("Event", "color"),
        ("Event", "reminder"),
        ("Event", "end_date"),
    }
    missing = expected - names
    assert not missing, f"missing unknown props: {missing}; got: {names}"
    assert not diff.is_clean
    md = diff.to_markdown()
    assert "WARNING" in md
    for _, prop in expected:
        assert prop in md


def test_markdown_passing_includes_checkmark():
    js = """ambient.graph.subscribe({ type: 'Task' }, (n) => {});"""
    diff = diff_controller_js(js, CORE_SCHEMAS)
    md = diff.to_markdown()
    assert "✅" in md and "PASSED" in md


def test_extractor_dedupes_identical_action_templates():
    """If the same action template appears inside mutate() AND as a push target,
    only count it once for occurrences."""
    js = """
    const mutations = [];
    mutations.push({ action: 'create_node', id: 'e', type: 'Event',
      properties: { title: 'x', color: 'red' } });
    await ambient.graph.mutate([
      { action: 'create_node', id: 'e', type: 'Event',
        properties: { title: 'x', color: 'red' } }
    ]);
    """
    actions = SchemaExtractor.extract_actions(js)
    # Should de-duplicate to one entry.
    assert len(actions) == 1
    color_props = (actions[0].get("properties") or {}).get("color")
    assert color_props is not None


def test_extractor_handles_template_literal_strings():
    js = """
    await ambient.graph.mutate([
      { action: 'create_node', id: 'e', type: 'Event',
        properties: { title: `Meeting ${date}` } }
    ]);
    """
    actions = SchemaExtractor.extract_actions(js)
    assert len(actions) == 1
    assert "title" in (actions[0].get("properties") or {})


def test_compute_diff_type_mismatch_when_schema_says_integer_code_says_string_of_digits():
    js = """
    await ambient.graph.mutate([
      { action: 'create_node', id: 'e', type: 'Task',
        properties: { title: 'x', due_date: '2026-01-01' } }
    ]);
    """
    diff = diff_controller_js(js, CORE_SCHEMAS)
    # due_date is in schema as string, code passes string literal - no mismatch.
    mismatches = {(m.node_type, m.property_name) for m in diff.type_mismatches}
    assert ("Task", "due_date") not in mismatches


def test_compute_diff_handles_missing_type():
    """create_node with no ``type`` field should not crash."""
    js = """
    await ambient.graph.mutate([
      { action: 'create_node', id: 'orphan' }
    ]);
    """
    diff = diff_controller_js(js, CORE_SCHEMAS)
    # No way to associate with a schema, so just no unknown_props from this.
    assert diff.is_clean


def test_subscribe_properties_filters_are_inspected():
    """``ambient.graph.subscribe({type, properties: {status: 'pending'}})``
    should flag any filter fields not in the schema."""
    js = """
    ambient.graph.subscribe({ type: 'Task', properties: { status: 'pending', bogus: 'x' } }, (n) => {});
    """
    diff = diff_controller_js(js, CORE_SCHEMAS)
    names = {(u.node_type, u.property_name) for u in diff.unknown_props}
    assert ("Task", "bogus") in names
