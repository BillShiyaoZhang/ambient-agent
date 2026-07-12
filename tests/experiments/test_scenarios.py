"""Unit tests for the experiment scenario bank and RouterContext fixtures."""

from backend.agent.intent_plan import IntentKind
from backend.experiments.scenarios import SCENARIOS, get_ambiguous_scenarios, get_scenario
from backend.router_context import RouterContext


def test_scenarios_loaded():
    assert len(SCENARIOS) >= 20


def test_all_scenarios_have_unique_ids():
    ids = [s.id for s in SCENARIOS]
    assert len(ids) == len(set(ids))


def test_ambiguous_subset_is_subset():
    ambig = get_ambiguous_scenarios()
    assert all(s.ambiguous for s in ambig)
    assert {s.id for s in ambig}.issubset({s.id for s in SCENARIOS})
    # At minimum, the 4 plan-designated ambiguous scenarios should be present.
    expected = {"S04", "S05", "S08", "S17"}
    assert expected.issubset({s.id for s in ambig})


def test_get_scenario():
    sc = get_scenario("S01")
    assert sc.user_message == "建一个时钟"
    assert sc.expected_kind == IntentKind.WIDGET_CREATE


def test_context_is_router_context_instance():
    for s in SCENARIOS:
        assert isinstance(s.context, RouterContext), f"{s.id} context is not RouterContext"


def test_chinese_creation_scenarios_target_widget_create():
    chinese_create_ids = ["S01", "S02", "S03"]
    for sid in chinese_create_ids:
        sc = get_scenario(sid)
        assert sc.expected_kind == IntentKind.WIDGET_CREATE, f"{sid} expected widget_create"


def test_existing_widget_modification_scenarios():
    sc = get_scenario("S06")
    assert sc.expected_kind == IntentKind.WIDGET_MODIFY
    assert sc.expected_app_id == "todo-app-efgh"


def test_converse_scenarios():
    for sid in ["S11", "S12", "S19", "S20"]:
        sc = get_scenario(sid)
        assert sc.expected_kind == IntentKind.CONVERSE


def test_graph_query_scenarios():
    for sid in ["S09", "S10", "S18"]:
        sc = get_scenario(sid)
        assert sc.expected_kind == IntentKind.GRAPH_QUERY
        assert sc.expected_app_id is None  # no specific id expected