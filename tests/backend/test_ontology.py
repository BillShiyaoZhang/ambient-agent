import json
import sqlite3

import pytest

from backend.graph_db import GraphDatabase, create_graph_database


def test_fresh_database_exposes_one_prebuilt_ontology(tmp_path):
    db = GraphDatabase(str(tmp_path / "workspace"))

    schemas = {schema["id"]: schema for schema in db.list_schemas()}

    assert {
        "Thing",
        "Task",
        "Event",
        "Note",
        "Person",
        "Organization",
        "Project",
        "Document",
        "Place",
        "Message",
        "SoftwareApplication",
    }.issubset(schemas)
    assert {schema["ontology_id"] for schema in schemas.values()} == {"ambient-context"}
    assert schemas["Thing"]["abstract"] is True
    assert schemas["Person"]["ontology_iri"] == "https://schema.org/Person"
    assert schemas["Task"]["subclass_of"] == "Thing"


def test_records_require_one_concrete_registered_entity_and_known_properties(tmp_path):
    db = GraphDatabase(str(tmp_path / "workspace"))

    with pytest.raises(ValueError, match="not registered in ontology"):
        db.create_node("unknown-1", "UnalignedType", {"value": "x"})

    with pytest.raises(ValueError, match="abstract"):
        db.create_node("thing-1", "Thing", {})

    with pytest.raises(ValueError, match="Unknown property 'private_cursor'"):
        db.create_node("task-1", "Task", {"title": "A", "private_cursor": "runtime-only"})

    node = db.create_node("task-1", "Task", {"title": "A", "priority": "high"})
    assert node["type"] == "Task"
    assert db.get_node("task-1")["ontology_entity_id"] == "Task"


def test_ontology_grows_before_new_entity_can_be_instantiated(tmp_path):
    db = GraphDatabase(str(tmp_path / "workspace"))
    proposal = {
        "reused_schemas": [],
        "new_schemas": [
            {
                "id": "Habit",
                "name": "Habit",
                "description": "A recurring user behavior",
                "properties": {"name": "string", "streak": "integer"},
                "subclass_of": "Thing",
                "ontology_iri": "https://example.org/personal/Habit",
                "equivalent_to": ["https://schema.org/Action"],
                "data_scope": "user_context",
            }
        ],
    }

    result = db.apply_schema_proposal_atomic(proposal, idempotency_key="ontology:habit:v1")
    habit = db.get_schema("Habit")

    assert result["proposal"]["new_schemas"][0]["subclass_of"] == "Thing"
    assert habit["ontology_id"] == "ambient-context"
    assert habit["equivalent_to"] == ["https://schema.org/Action"]
    assert db.create_node("habit-1", "Habit", {"name": "Read", "streak": 4})["type"] == "Habit"


def test_ontology_growth_rejects_unaligned_parent_and_runtime_data(tmp_path):
    db = GraphDatabase(str(tmp_path / "workspace"))

    with pytest.raises(ValueError, match="non-context data_scope"):
        db.apply_schema_proposal_atomic(
            {
                "new_schemas": [
                    {
                        "id": "SyncCursor",
                        "properties": {"cursor": "string"},
                        "data_scope": "app_runtime",
                    }
                ]
            }
        )

    with pytest.raises(ValueError, match="parent 'MissingParent'"):
        db.apply_schema_proposal_atomic(
            {
                "new_schemas": [
                    {
                        "id": "OrphanConcept",
                        "properties": {"name": "string"},
                        "subclass_of": "MissingParent",
                    }
                ]
            }
        )


def test_preflight_rejects_unknown_entity_before_any_write(tmp_path):
    db = GraphDatabase(str(tmp_path / "workspace"))

    with pytest.raises(ValueError, match="not registered in ontology"):
        db.preflight_actions(
            [
                {
                    "action": "create_node",
                    "id": "bad-1",
                    "type": "Unregistered",
                    "properties": {"name": "bad"},
                }
            ]
        )

    assert db.get_node("bad-1") is None


def test_graph_database_factory_keeps_sqlite_as_explicit_test_adapter(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAPH_DATABASE_BACKEND", "sqlite")

    db = create_graph_database(str(tmp_path / "workspace"))

    assert isinstance(db, GraphDatabase)


def test_graph_database_factory_selects_neo4j_for_deployment(tmp_path, monkeypatch):
    sentinel = object()
    monkeypatch.setenv("GRAPH_DATABASE_BACKEND", "neo4j")

    import backend.neo4j_graph_db as neo4j_module

    monkeypatch.setattr(
        neo4j_module.Neo4jGraphDatabase,
        "from_env",
        classmethod(lambda cls, workspace_dir=None: sentinel),
    )

    assert create_graph_database(str(tmp_path / "workspace")) is sentinel


def test_ontology_growth_survives_restart_and_cannot_change_existing_property_type(tmp_path):
    workspace = str(tmp_path / "workspace")
    db = GraphDatabase(workspace)
    db.apply_schema_proposal_atomic(
        {"reused_schemas": [{"id": "Task", "extended_properties": {"estimated_minutes": "integer"}}]}
    )

    reopened = GraphDatabase(workspace)

    assert reopened.get_schema("Task")["properties"]["estimated_minutes"] == "integer"
    with pytest.raises(ValueError, match="cannot change property 'estimated_minutes'"):
        reopened.apply_schema_proposal_atomic(
            {"reused_schemas": [{"id": "Task", "extended_properties": {"estimated_minutes": "string"}}]}
        )


def test_existing_sqlite_records_are_aligned_before_the_strict_contract_is_enabled(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    connection = sqlite3.connect(workspace / "graph.db")
    connection.execute(
        """CREATE TABLE graph_nodes (
               id TEXT PRIMARY KEY, type TEXT NOT NULL, properties TEXT,
               namespace TEXT, created_at TEXT NOT NULL
           )"""
    )
    connection.execute(
        """INSERT INTO graph_nodes(id,type,properties,namespace,created_at)
           VALUES(?,?,?,?,?)""",
        (
            "legacy-event",
            "CalendarEvent",
            json.dumps({"summary": "Planning", "time": "2026-07-20"}),
            None,
            "2026-07-20T00:00:00+00:00",
        ),
    )
    connection.commit()
    connection.close()

    db = GraphDatabase(str(workspace))
    event = db.get_node("legacy-event")

    assert event["type"] == "Event"
    assert event["properties"] == {"title": "Planning", "start_time": "2026-07-20"}
    assert db.get_schema("CalendarEvent") is None
