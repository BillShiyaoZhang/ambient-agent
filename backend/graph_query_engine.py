from typing import Dict, Any, List
from backend.graph_db import GraphDatabase

def execute_graph_query(query: dict, db: GraphDatabase) -> List[Dict[str, Any]]:
    results = []
    
    # 1. Fetch matching root nodes
    target_type = query.get("type")
    properties_filter = query.get("properties", {})
    
    for node_id, node in db.nodes.items():
        if target_type and node.get("type") != target_type:
            continue
            
        # Match properties
        props_match = True
        node_props = node.get("properties", {})
        for k, v in properties_filter.items():
            if node_props.get(k) != v:
                props_match = False
                break
                
        if not props_match:
            continue
            
        # Clone node data to avoid modifying database reference directly
        node_res = {
            "id": node["id"],
            "type": node["type"],
            "properties": dict(node.get("properties", {})),
            "relations": []
        }
        
        # 2. Process includes if specified
        includes = query.get("include", [])
        for inc in includes:
            relation_type = inc.get("relation")
            target_type_filter = inc.get("target_type")
            
            # Find all edges connecting from or to this root node
            for edge in db.edges:
                if edge["type"] == relation_type:
                    # Check outgoing relation
                    if edge["from_id"] == node_id:
                        to_node = db.get_node(edge["to_id"])
                        if to_node and (not target_type_filter or to_node["type"] == target_type_filter):
                            node_res["relations"].append({
                                "edge_type": edge["type"],
                                "properties": dict(edge.get("properties", {})),
                                "target": {
                                    "id": to_node["id"],
                                    "type": to_node["type"],
                                    "properties": dict(to_node.get("properties", {}))
                                }
                            })
                    # Check incoming relation (for bidirectionality support)
                    elif edge["to_id"] == node_id:
                        from_node = db.get_node(edge["from_id"])
                        if from_node and (not target_type_filter or from_node["type"] == target_type_filter):
                            node_res["relations"].append({
                                "edge_type": edge["type"],
                                "properties": dict(edge.get("properties", {})),
                                "target": {
                                    "id": from_node["id"],
                                    "type": from_node["type"],
                                    "properties": dict(from_node.get("properties", {}))
                                }
                            })
                            
        results.append(node_res)
        
    return results
