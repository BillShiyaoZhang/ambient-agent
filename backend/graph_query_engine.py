from typing import Any

from backend.graph_db import GraphDatabase


def execute_graph_query(query: dict, db: GraphDatabase) -> list[dict[str, Any]]:
    target_type = query.get("type")
    properties_filter = query.get("properties", {})

    results = []

    # Fetch through the graph adapter rather than assuming a SQLite connection.
    rows = db.list_nodes(str(target_type) if target_type else None)
    for row in rows:
        node_id = row["id"]
        node_type = row["type"]
        node_props = row.get("properties") or {}

        if any(node_props.get(key) != value for key, value in properties_filter.items()):
            continue

        node_res = {"id": node_id, "type": node_type, "properties": node_props, "relations": []}
        includes = query.get("include", [])
        if includes:
            incident_edges = db.get_edges(node_id)
            for inc in includes:
                relation_type = inc.get("relation")
                target_type_filter = inc.get("target_type")
                for edge in incident_edges:
                    if edge.get("type") != relation_type:
                        continue
                    if edge.get("from_id") == node_id:
                        target_id = edge.get("to_id")
                    elif edge.get("to_id") == node_id:
                        target_id = edge.get("from_id")
                    else:
                        continue
                    target = db.get_node(str(target_id))
                    if target is None or (target_type_filter and target.get("type") != target_type_filter):
                        continue
                    node_res["relations"].append(
                        {
                            "edge_type": edge["type"],
                            "properties": edge.get("properties") or {},
                            "target": {
                                "id": target["id"],
                                "type": target["type"],
                                "properties": target.get("properties") or {},
                            },
                        }
                    )

        results.append(node_res)

    return results
