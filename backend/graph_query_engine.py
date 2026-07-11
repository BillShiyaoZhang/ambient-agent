import json
from typing import Any

from backend.graph_db import GraphDatabase


def execute_graph_query(query: dict, db: GraphDatabase) -> list[dict[str, Any]]:
    target_type = query.get("type")
    properties_filter = query.get("properties", {})

    results = []

    # 1. Fetch matching root nodes
    sql = "SELECT id, type, properties FROM graph_nodes WHERE 1=1"
    params = []
    if target_type:
        sql += " AND type = ?"
        params.append(target_type)

    with db.get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()

        for row in rows:
            node_id = row["id"]
            node_type = row["type"]
            node_props = json.loads(row["properties"])

            # Match properties
            props_match = True
            for k, v in properties_filter.items():
                if node_props.get(k) != v:
                    props_match = False
                    break

            if not props_match:
                continue

            node_res = {
                "id": node_id,
                "type": node_type,
                "properties": node_props,
                "relations": []
            }

            # 2. Process includes if specified
            includes = query.get("include", [])
            for inc in includes:
                relation_type = inc.get("relation")
                target_type_filter = inc.get("target_type")

                # Check outgoing relations: source node (node_id) -> target node
                sql_out = """
                    SELECT e.type AS edge_type, e.properties AS edge_props,
                           n.id AS target_id, n.type AS target_type, n.properties AS target_props
                    FROM graph_edges e
                    JOIN graph_nodes n ON e.to_id = n.id
                    WHERE e.from_id = ? AND e.type = ?
                """
                out_params = [node_id, relation_type]
                if target_type_filter:
                    sql_out += " AND n.type = ?"
                    out_params.append(target_type_filter)

                out_edges = conn.execute(sql_out, out_params).fetchall()
                for edge in out_edges:
                    node_res["relations"].append({
                        "edge_type": edge["edge_type"],
                        "properties": json.loads(edge["edge_props"]),
                        "target": {
                            "id": edge["target_id"],
                            "type": edge["target_type"],
                            "properties": json.loads(edge["target_props"])
                        }
                    })

                # Check incoming relations: target node -> source node (node_id)
                sql_in = """
                    SELECT e.type AS edge_type, e.properties AS edge_props,
                           n.id AS target_id, n.type AS target_type, n.properties AS target_props
                    FROM graph_edges e
                    JOIN graph_nodes n ON e.from_id = n.id
                    WHERE e.to_id = ? AND e.type = ?
                """
                in_params = [node_id, relation_type]
                if target_type_filter:
                    sql_in += " AND n.type = ?"
                    in_params.append(target_type_filter)

                in_edges = conn.execute(sql_in, in_params).fetchall()
                for edge in in_edges:
                    node_res["relations"].append({
                        "edge_type": edge["edge_type"],
                        "properties": json.loads(edge["edge_props"]),
                        "target": {
                            "id": edge["target_id"],
                            "type": edge["target_type"],
                            "properties": json.loads(edge["target_props"])
                        }
                    })

            results.append(node_res)

    return results
