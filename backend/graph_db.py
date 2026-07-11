import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any


class GraphDatabase:
    def __init__(self, workspace_dir: str | None = None):
        if not workspace_dir:
            workspace_dir = os.getenv("WORKSPACE_DIR", "workspace")
        self.workspace_dir = workspace_dir
        self.db_path = os.path.join(self.workspace_dir, "graph.db")
        self.legacy_filepath = os.path.join(self.workspace_dir, "graph.json")

        # Ensure workspace dir exists
        os.makedirs(self.workspace_dir, exist_ok=True)
        self.load()

    @contextmanager
    def get_conn(self):
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=OFF;")  # Defer strict foreign keys to allow dynamic operations
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def load(self) -> None:
        """
        Initializes SQLite tables, sets up default schemas,
        and performs legacy migration from graph.json if it exists.
        """
        with self.get_conn() as conn:
            # 1. Create tables
            conn.execute("""
                CREATE TABLE IF NOT EXISTS graph_schemas (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    properties TEXT, -- JSON string: { "field": "type" }
                    is_core INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS graph_nodes (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    properties TEXT, -- JSON string
                    namespace TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS graph_edges (
                    from_id TEXT NOT NULL,
                    to_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    properties TEXT, -- JSON string
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (from_id, to_id, type)
                )
            """)

            # 2. Indexes
            conn.execute("CREATE INDEX IF NOT EXISTS idx_nodes_type ON graph_nodes(type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_from ON graph_edges(from_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_to ON graph_edges(to_id)")

        # 3. Seed core schemas
        self._seed_core_schemas()

        # 4. Migrate legacy graph.json if present
        self._migrate_legacy_data()

    def _seed_core_schemas(self) -> None:
        core_schemas = {
            "Task": {
                "name": "Task",
                "description": "Represents a todo item or actionable task",
                "properties": {"title": "string", "description": "string", "status": "string", "due_date": "string"},
            },
            "Event": {
                "name": "Event",
                "description": "Represents a calendar entry or scheduled block of time",
                "properties": {
                    "title": "string",
                    "description": "string",
                    "start_time": "string",
                    "end_time": "string",
                    "location": "string",
                },
            },
            "Note": {
                "name": "Note",
                "description": "Represents a freeform text note or document chunk",
                "properties": {"title": "string", "content": "string", "tags": "string"},
            },
        }
        for schema_id, info in core_schemas.items():
            self.register_schema(
                schema_id=schema_id,
                name=info["name"],
                description=info["description"],
                properties=info["properties"],
                is_core=True,
            )

    def _migrate_legacy_data(self) -> None:
        if not os.path.exists(self.legacy_filepath):
            return

        try:
            print("[GraphDB] Migrating legacy graph.json to graph.db...")
            with open(self.legacy_filepath, encoding="utf-8") as f:
                data = json.load(f)

            legacy_nodes = data.get("nodes", {})
            legacy_edges = data.get("edges", [])

            with self.get_conn() as conn:
                # Migrate nodes
                for node_id, node in legacy_nodes.items():
                    conn.execute(
                        "INSERT OR IGNORE INTO graph_nodes (id, type, properties, namespace, created_at) VALUES (?, ?, ?, ?, ?)",
                        (
                            node_id,
                            node.get("type", "Generic"),
                            json.dumps(node.get("properties", {})),
                            node.get("properties", {}).get("namespace"),
                            datetime.now(UTC).isoformat(),
                        ),
                    )
                # Migrate edges
                for edge in legacy_edges:
                    conn.execute(
                        "INSERT OR IGNORE INTO graph_edges (from_id, to_id, type, properties, created_at) VALUES (?, ?, ?, ?, ?)",
                        (
                            edge.get("from_id"),
                            edge.get("to_id"),
                            edge.get("type"),
                            json.dumps(edge.get("properties", {})),
                            datetime.now(UTC).isoformat(),
                        ),
                    )

            # Backup old file to avoid re-migration
            backup_path = self.legacy_filepath + ".backup"
            if os.path.exists(backup_path):
                os.remove(backup_path)
            os.rename(self.legacy_filepath, backup_path)
            print("[GraphDB] Migration completed successfully.")
        except Exception as e:
            print(f"[GraphDB] Error migrating legacy JSON graph: {e}")

    # --- Schema APIs ---

    def register_schema(
        self, schema_id: str, name: str, description: str, properties: dict[str, str], is_core: bool = False
    ) -> dict[str, Any]:
        with self.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO graph_schemas (id, name, description, properties, is_core, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    description=excluded.description,
                    properties=excluded.properties
                """,
                (
                    schema_id,
                    name,
                    description,
                    json.dumps(properties),
                    1 if is_core else 0,
                    datetime.now(UTC).isoformat(),
                ),
            )
        return {"id": schema_id, "name": name, "description": description, "properties": properties, "is_core": is_core}

    def get_schema(self, schema_id: str) -> dict[str, Any] | None:
        with self.get_conn() as conn:
            row = conn.execute(
                "SELECT id, name, description, properties, is_core FROM graph_schemas WHERE id = ?", (schema_id,)
            ).fetchone()
            if row:
                return {
                    "id": row["id"],
                    "name": row["name"],
                    "description": row["description"],
                    "properties": json.loads(row["properties"]),
                    "is_core": bool(row["is_core"]),
                }
        return None

    def list_schemas(self) -> list[dict[str, Any]]:
        with self.get_conn() as conn:
            rows = conn.execute("SELECT id, name, description, properties, is_core FROM graph_schemas").fetchall()
            return [
                {
                    "id": row["id"],
                    "name": row["name"],
                    "description": row["description"],
                    "properties": json.loads(row["properties"]),
                    "is_core": bool(row["is_core"]),
                }
                for row in rows
            ]

    # --- Node/Edge Property Validation ---

    def validate_properties(self, node_type: str, properties: dict[str, Any]) -> dict[str, Any]:
        schema = self.get_schema(node_type)
        if not schema:
            return properties

        schema_props = schema.get("properties", {})
        validated_props = dict(properties)

        for k, v in properties.items():
            if k in schema_props:
                expected_type = schema_props[k].lower()
                if expected_type == "string" and not isinstance(v, str):
                    validated_props[k] = str(v)
                elif expected_type == "integer":
                    try:
                        validated_props[k] = int(v)
                    except (ValueError, TypeError):
                        raise ValueError(f"Property '{k}' on node type '{node_type}' must be an integer, got: {v}")
                elif expected_type == "number":
                    try:
                        validated_props[k] = float(v)
                    except (ValueError, TypeError):
                        raise ValueError(f"Property '{k}' on node type '{node_type}' must be a number, got: {v}")
                elif expected_type == "boolean":
                    if isinstance(v, bool):
                        validated_props[k] = v
                    elif str(v).lower() in ["true", "1", "yes"]:
                        validated_props[k] = True
                    elif str(v).lower() in ["false", "0", "no"]:
                        validated_props[k] = False
                    else:
                        raise ValueError(f"Property '{k}' on node type '{node_type}' must be a boolean, got: {v}")
        return validated_props

    # --- Node APIs ---

    def create_node(
        self, node_id: str | None = None, node_type: str = "Generic", properties: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not node_id:
            node_id = str(uuid.uuid4())

        raw_properties = properties or {}

        # 1. Namespace Extraction & Verification
        namespace = raw_properties.get("namespace")
        if not namespace and node_id.startswith("app:"):
            # Auto-namespace based on app id
            parts = node_id.split(":")
            if len(parts) >= 2:
                namespace = parts[1]
                raw_properties["namespace"] = namespace

        # 2. Schema Validation
        validated_properties = self.validate_properties(node_type, raw_properties)

        with self.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO graph_nodes (id, type, properties, namespace, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    type=excluded.type,
                    properties=excluded.properties,
                    namespace=excluded.namespace
                """,
                (node_id, node_type, json.dumps(validated_properties), namespace, datetime.now(UTC).isoformat()),
            )

        return {"id": node_id, "type": node_type, "properties": validated_properties}

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        with self.get_conn() as conn:
            row = conn.execute("SELECT id, type, properties FROM graph_nodes WHERE id = ?", (node_id,)).fetchone()
            if row:
                return {"id": row["id"], "type": row["type"], "properties": json.loads(row["properties"])}
        return None

    def update_node_property(self, node_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        node = self.get_node(node_id)
        if not node:
            raise ValueError(f"Node with ID '{node_id}' does not exist.")

        updated_props = dict(node["properties"])
        updated_props.update(properties)

        validated_props = self.validate_properties(node["type"], updated_props)

        namespace = validated_props.get("namespace")

        with self.get_conn() as conn:
            conn.execute(
                "UPDATE graph_nodes SET properties = ?, namespace = ? WHERE id = ?",
                (json.dumps(validated_props), namespace, node_id),
            )

        return {"id": node_id, "type": node["type"], "properties": validated_props}

    def delete_node(self, node_id: str) -> bool:
        with self.get_conn() as conn:
            # 1. Cascade delete edges
            conn.execute("DELETE FROM graph_edges WHERE from_id = ? OR to_id = ?", (node_id, node_id))
            # 2. Delete node
            cursor = conn.execute("DELETE FROM graph_nodes WHERE id = ?", (node_id,))
            return cursor.rowcount > 0

    # --- Edge APIs ---

    def create_edge(
        self, from_id: str, to_id: str, edge_type: str, properties: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        # Validate that both nodes exist
        if not self.get_node(from_id):
            raise ValueError(f"Source node '{from_id}' does not exist.")
        if not self.get_node(to_id):
            raise ValueError(f"Target node '{to_id}' does not exist.")

        edge_properties = properties or {}

        with self.get_conn() as conn:
            # Upsert edge
            conn.execute(
                """
                INSERT INTO graph_edges (from_id, to_id, type, properties, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(from_id, to_id, type) DO UPDATE SET
                    properties=excluded.properties
                """,
                (from_id, to_id, edge_type, json.dumps(edge_properties), datetime.now(UTC).isoformat()),
            )

        return {"from_id": from_id, "to_id": to_id, "type": edge_type, "properties": edge_properties}

    def get_edges(self, node_id: str) -> list[dict[str, Any]]:
        with self.get_conn() as conn:
            rows = conn.execute(
                "SELECT from_id, to_id, type, properties FROM graph_edges WHERE from_id = ? OR to_id = ?",
                (node_id, node_id),
            ).fetchall()
            return [
                {
                    "from_id": row["from_id"],
                    "to_id": row["to_id"],
                    "type": row["type"],
                    "properties": json.loads(row["properties"]),
                }
                for row in rows
            ]

    def delete_edge(self, from_id: str, to_id: str, edge_type: str) -> bool:
        with self.get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM graph_edges WHERE from_id = ? AND to_id = ? AND type = ?", (from_id, to_id, edge_type)
            )
            return cursor.rowcount > 0

    # Backwards compatibility mappings for tests or direct node/edge lookups
    @property
    def nodes(self) -> dict[str, dict[str, Any]]:
        """
        Emulates the old dict nodes mapping.
        Note: Reading all nodes in-memory can be slow for huge databases,
        but required for backwards compatibility in existing query codes.
        """
        with self.get_conn() as conn:
            rows = conn.execute("SELECT id, type, properties FROM graph_nodes").fetchall()
            return {
                row["id"]: {"id": row["id"], "type": row["type"], "properties": json.loads(row["properties"])}
                for row in rows
            }

    @property
    def edges(self) -> list[dict[str, Any]]:
        """
        Emulates the old list edges mapping.
        """
        with self.get_conn() as conn:
            rows = conn.execute("SELECT from_id, to_id, type, properties FROM graph_edges").fetchall()
            return [
                {
                    "from_id": row["from_id"],
                    "to_id": row["to_id"],
                    "type": row["type"],
                    "properties": json.loads(row["properties"]),
                }
                for row in rows
            ]

    def save(self) -> None:
        """
        Exports the current nodes and edges to graph.json for backward compatibility and test assertions.
        """
        try:
            with open(self.legacy_filepath, "w", encoding="utf-8") as f:
                json.dump({"nodes": self.nodes, "edges": self.edges}, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[GraphDB] Error exporting to json: {e}")
