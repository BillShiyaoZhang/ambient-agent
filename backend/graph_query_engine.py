from typing import Any

from backend.graph_db import GraphDatabase

MAX_GRAPH_QUERY_RESULTS = 500
MAX_GRAPH_QUERY_INCLUDES = 16
MAX_GRAPH_QUERY_RELATIONS_PER_RESULT = 200


def execute_graph_query(query: dict, db: GraphDatabase) -> list[dict[str, Any]]:
    if not isinstance(query, dict):
        raise ValueError("Graph query must be a JSON object")
    target_type = query.get("type")
    properties_filter = query.get("properties", {})
    includes = query.get("include", [])
    limit = query.get("limit", MAX_GRAPH_QUERY_RESULTS)
    if target_type is not None and (not isinstance(target_type, str) or not target_type):
        raise ValueError("Graph query type must be a non-empty string")
    if not isinstance(properties_filter, dict) or len(properties_filter) > 64:
        raise ValueError("Graph query properties must be an object with at most 64 fields")
    if not isinstance(includes, list) or len(includes) > MAX_GRAPH_QUERY_INCLUDES:
        raise ValueError(f"Graph query include must contain at most {MAX_GRAPH_QUERY_INCLUDES} entries")
    if any(
        not isinstance(include, dict)
        or not isinstance(include.get("relation"), str)
        or not include.get("relation")
        or (
            include.get("target_type") is not None
            and (not isinstance(include.get("target_type"), str) or not include.get("target_type"))
        )
        for include in includes
    ):
        raise ValueError("Graph query include entries must declare relation and optional target_type strings")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_GRAPH_QUERY_RESULTS:
        raise ValueError(f"Graph query limit must be between 1 and {MAX_GRAPH_QUERY_RESULTS}")

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
                    if len(node_res["relations"]) >= MAX_GRAPH_QUERY_RELATIONS_PER_RESULT:
                        break
                if len(node_res["relations"]) >= MAX_GRAPH_QUERY_RELATIONS_PER_RESULT:
                    break

        results.append(node_res)
        if len(results) >= limit:
            break

    return results
