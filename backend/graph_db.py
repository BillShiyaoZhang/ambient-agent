import os
import json
import uuid
from typing import Dict, Any, List, Optional
import threading

# Global thread lock for safety
graph_lock = threading.Lock()

class GraphDatabase:
    def __init__(self, workspace_dir: Optional[str] = None):
        if not workspace_dir:
            workspace_dir = os.getenv("WORKSPACE_DIR", "workspace")
        self.workspace_dir = workspace_dir
        self.filepath = os.path.join(self.workspace_dir, "graph.json")
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.edges: List[Dict[str, Any]] = []
        self.load()

    def load(self) -> None:
        with graph_lock:
            if os.path.exists(self.filepath):
                try:
                    with open(self.filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        self.nodes = data.get("nodes", {})
                        self.edges = data.get("edges", [])
                except Exception as e:
                    print(f"[GraphDB] Error loading graph database: {e}")
                    self.nodes = {}
                    self.edges = []
            else:
                self.nodes = {}
                self.edges = []

    def save(self) -> None:
        with graph_lock:
            os.makedirs(self.workspace_dir, exist_ok=True)
            try:
                with open(self.filepath, "w", encoding="utf-8") as f:
                    json.dump({
                        "nodes": self.nodes,
                        "edges": self.edges
                    }, f, indent=2, ensure_ascii=False)
            except Exception as e:
                print(f"[GraphDB] Error saving graph database: {e}")

    def create_node(self, node_id: Optional[str] = None, node_type: str = "Generic", properties: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not node_id:
            node_id = str(uuid.uuid4())
        
        node = {
            "id": node_id,
            "type": node_type,
            "properties": properties or {}
        }
        
        self.nodes[node_id] = node
        self.save()
        return node

    def get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        return self.nodes.get(node_id)

    def update_node_property(self, node_id: str, properties: Dict[str, Any]) -> Dict[str, Any]:
        node = self.nodes.get(node_id)
        if not node:
            raise ValueError(f"Node with ID '{node_id}' does not exist.")
        
        node["properties"].update(properties)
        self.save()
        return node

    def delete_node(self, node_id: str) -> bool:
        if node_id in self.nodes:
            # 1. Cascade delete edges
            self.edges = [
                edge for edge in self.edges 
                if edge["from_id"] != node_id and edge["to_id"] != node_id
            ]
            # 2. Delete node
            del self.nodes[node_id]
            self.save()
            return True
        return False

    def create_edge(self, from_id: str, to_id: str, edge_type: str, properties: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if from_id not in self.nodes:
            raise ValueError(f"Source node '{from_id}' does not exist.")
        if to_id not in self.nodes:
            raise ValueError(f"Target node '{to_id}' does not exist.")
            
        edge = {
            "from_id": from_id,
            "to_id": to_id,
            "type": edge_type,
            "properties": properties or {}
        }
        
        # Avoid duplicate exact edges
        exists = False
        for e in self.edges:
            if e["from_id"] == from_id and e["to_id"] == to_id and e["type"] == edge_type:
                e["properties"].update(properties or {})
                edge = e
                exists = True
                break
                
        if not exists:
            self.edges.append(edge)
            
        self.save()
        return edge

    def get_edges(self, node_id: str) -> List[Dict[str, Any]]:
        # Returns all edges connected to node_id (incoming or outgoing)
        return [
            edge for edge in self.edges 
            if edge["from_id"] == node_id or edge["to_id"] == node_id
        ]

    def delete_edge(self, from_id: str, to_id: str, edge_type: str) -> bool:
        original_len = len(self.edges)
        self.edges = [
            edge for edge in self.edges
            if not (edge["from_id"] == from_id and edge["to_id"] == to_id and edge["type"] == edge_type)
        ]
        if len(self.edges) < original_len:
            self.save()
            return True
        return False
