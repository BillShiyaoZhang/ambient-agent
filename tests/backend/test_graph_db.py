import os
import json
import pytest
from backend.graph_db import GraphDatabase

def test_graph_db_crud(tmp_path):
    # Set up temp workspace
    workspace_dir = str(tmp_path / "workspace")
    os.makedirs(workspace_dir, exist_ok=True)
    
    db = GraphDatabase(workspace_dir)
    
    # 1. Test Node Creation
    node1 = db.create_node(node_id="task-1", node_type="Task", properties={"title": "Buy groceries", "status": "pending"})
    assert node1["id"] == "task-1"
    assert node1["type"] == "Task"
    assert node1["properties"]["title"] == "Buy groceries"
    
    node2 = db.create_node(node_id="event-1", node_type="CalendarEvent", properties={"summary": "Shopping trip", "time": "2026-07-12"})
    assert node2["id"] == "event-1"
    
    # Verify node retrieval
    retrieved = db.get_node("task-1")
    assert retrieved is not None
    assert retrieved["properties"]["status"] == "pending"
    
    # 2. Test Property Update
    updated = db.update_node_property("task-1", {"status": "completed", "priority": "high"})
    assert updated["properties"]["status"] == "completed"
    assert updated["properties"]["priority"] == "high"
    assert updated["properties"]["title"] == "Buy groceries"  # should preserve other properties
    
    # 3. Test Edge Creation
    edge = db.create_edge(from_id="task-1", to_id="event-1", edge_type="ASSOCIATED_WITH", properties={"weight": 1})
    assert edge["from_id"] == "task-1"
    assert edge["to_id"] == "event-1"
    assert edge["type"] == "ASSOCIATED_WITH"
    assert edge["properties"]["weight"] == 1
    
    # 4. Test Persistence
    db.save()
    
    graph_file = os.path.join(workspace_dir, "graph.json")
    assert os.path.exists(graph_file)
    
    # Load into a new DB instance to verify loading
    db2 = GraphDatabase(workspace_dir)
    assert db2.get_node("task-1") is not None
    assert db2.get_node("task-1")["properties"]["status"] == "completed"
    
    edges = db2.get_edges("task-1")
    assert len(edges) == 1
    assert edges[0]["to_id"] == "event-1"
    
    # 5. Test Cascade Deletion
    # Deleting task-1 should delete the edge connecting it to event-1
    db2.delete_node("task-1")
    assert db2.get_node("task-1") is None
    assert len(db2.get_edges("task-1")) == 0
    assert len(db2.get_edges("event-1")) == 0
    
    # 6. Test direct edge deletion
    # Create another connection
    node3 = db2.create_node(node_id="user-1", node_type="User", properties={"name": "Alice"})
    edge2 = db2.create_edge(from_id="user-1", to_id="event-1", edge_type="ATTENDING")
    assert len(db2.get_edges("user-1")) == 1
    
    db2.delete_edge(from_id="user-1", to_id="event-1", edge_type="ATTENDING")
    assert len(db2.get_edges("user-1")) == 0
