import hashlib
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
            # Mutation history table for rollback semantics
            conn.execute("""
                CREATE TABLE IF NOT EXISTS graph_mutation_history (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    forward_actions TEXT NOT NULL,
                    reverse_actions TEXT NOT NULL,
                    snapshot_before TEXT,
                    pinned INTEGER DEFAULT 0,
                    consumed_at TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS graph_effects (
                    idempotency_key TEXT PRIMARY KEY,
                    input_hash TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)

            # 2. Indexes
            conn.execute("CREATE INDEX IF NOT EXISTS idx_nodes_type ON graph_nodes(type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_from ON graph_edges(from_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_to ON graph_edges(to_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_history_session ON graph_mutation_history(session_id)")

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

    # --- Atomic mutation batches -----------------------------------------

    @staticmethod
    def _required_text(action: dict[str, Any], field: str) -> str:
        value = action.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Graph mutation field '{field}' must be a non-empty string")
        return value

    @staticmethod
    def _node_from_conn(conn: sqlite3.Connection, node_id: str) -> dict[str, Any] | None:
        row = conn.execute(
            "SELECT id, type, properties, namespace, created_at FROM graph_nodes WHERE id=?",
            (node_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "type": row["type"],
            "properties": json.loads(row["properties"] or "{}"),
            "namespace": row["namespace"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _edge_from_conn(
        conn: sqlite3.Connection, from_id: str, to_id: str, edge_type: str
    ) -> dict[str, Any] | None:
        row = conn.execute(
            "SELECT from_id, to_id, type, properties, created_at FROM graph_edges "
            "WHERE from_id=? AND to_id=? AND type=?",
            (from_id, to_id, edge_type),
        ).fetchone()
        if row is None:
            return None
        return {
            "from_id": row["from_id"],
            "to_id": row["to_id"],
            "type": row["type"],
            "properties": json.loads(row["properties"] or "{}"),
            "created_at": row["created_at"],
        }

    @staticmethod
    def _validate_properties_in_conn(
        conn: sqlite3.Connection, node_type: str, properties: dict[str, Any]
    ) -> dict[str, Any]:
        row = conn.execute("SELECT properties FROM graph_schemas WHERE id=?", (node_type,)).fetchone()
        if row is None:
            return dict(properties)
        schema_props = json.loads(row["properties"] or "{}")
        validated = dict(properties)
        for key, value in properties.items():
            expected = str(schema_props.get(key, "")).lower()
            if expected == "string" and not isinstance(value, str):
                validated[key] = str(value)
            elif expected == "integer":
                try:
                    validated[key] = int(value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"Property '{key}' on node type '{node_type}' must be an integer") from exc
            elif expected == "number":
                try:
                    validated[key] = float(value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"Property '{key}' on node type '{node_type}' must be a number") from exc
            elif expected == "boolean":
                if isinstance(value, bool):
                    validated[key] = value
                elif str(value).lower() in {"true", "1", "yes"}:
                    validated[key] = True
                elif str(value).lower() in {"false", "0", "no"}:
                    validated[key] = False
                else:
                    raise ValueError(f"Property '{key}' on node type '{node_type}' must be a boolean")
        return validated

    def apply_actions_atomic(
        self,
        actions: list[dict[str, Any]],
        *,
        session_id: str | None = None,
        ticket_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Validate and commit a mutation batch, its undo data, and ticket atomically.

        Internal reverse actions (``replace_node`` and ``restore_node``) are
        accepted only by this method and preserve fields that a merge-style
        update cannot remove.
        """

        if not isinstance(actions, list) or not actions:
            raise ValueError("Graph mutation must contain at least one action")
        if any(not isinstance(action, dict) for action in actions):
            raise ValueError("Every graph mutation action must be an object")

        normalized: list[dict[str, Any]] = []
        reverse_actions: list[dict[str, Any]] = []
        snapshot_before: dict[str, Any] = {"nodes": {}, "edges": {}}
        now = datetime.now(UTC).isoformat()
        ticket_id = ticket_id or (f"tkt-{uuid.uuid4().hex[:12]}" if session_id else None)
        canonical_input = json.dumps(actions, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        input_hash = hashlib.sha256(canonical_input.encode("utf-8")).hexdigest()

        with self.get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if idempotency_key:
                existing_effect = conn.execute(
                    "SELECT input_hash,result_json FROM graph_effects WHERE idempotency_key=?",
                    (idempotency_key,),
                ).fetchone()
                if existing_effect:
                    if existing_effect["input_hash"] != input_hash:
                        raise ValueError("Graph idempotency key was reused with different actions")
                    return json.loads(existing_effect["result_json"])
            for raw_action in actions:
                action = dict(raw_action)
                kind = self._required_text(action, "action")

                if kind == "create_node":
                    node_id = action.get("id") or str(uuid.uuid4())
                    if not isinstance(node_id, str) or not node_id:
                        raise ValueError("create_node.id must be a string")
                    node_type = action.get("type", "Generic")
                    if not isinstance(node_type, str) or not node_type:
                        raise ValueError("create_node.type must be a string")
                    properties = action.get("properties") or {}
                    if not isinstance(properties, dict):
                        raise ValueError("create_node.properties must be an object")
                    old_node = self._node_from_conn(conn, node_id)
                    if old_node:
                        reverse_actions.append(
                            {
                                "action": "replace_node",
                                "id": node_id,
                                "type": old_node["type"],
                                "properties": old_node["properties"],
                            }
                        )
                        snapshot_before["nodes"][node_id] = old_node
                    else:
                        reverse_actions.append({"action": "delete_node", "id": node_id})
                    validated = self._validate_properties_in_conn(conn, node_type, properties)
                    namespace = validated.get("namespace")
                    if not namespace and node_id.startswith("app:"):
                        parts = node_id.split(":")
                        if len(parts) >= 2:
                            namespace = parts[1]
                            validated["namespace"] = namespace
                    conn.execute(
                        """INSERT INTO graph_nodes(id,type,properties,namespace,created_at) VALUES(?,?,?,?,?)
                           ON CONFLICT(id) DO UPDATE SET type=excluded.type,
                           properties=excluded.properties, namespace=excluded.namespace""",
                        (node_id, node_type, json.dumps(validated), namespace, now),
                    )
                    normalized.append(
                        {"action": kind, "id": node_id, "type": node_type, "properties": validated}
                    )

                elif kind in {"update_node_property", "replace_node"}:
                    node_id = self._required_text(action, "id")
                    old_node = self._node_from_conn(conn, node_id)
                    if old_node is None:
                        raise ValueError(f"Node with ID '{node_id}' does not exist")
                    snapshot_before["nodes"].setdefault(node_id, old_node)
                    reverse_actions.append(
                        {
                            "action": "replace_node",
                            "id": node_id,
                            "type": old_node["type"],
                            "properties": old_node["properties"],
                        }
                    )
                    incoming = action.get("properties") or {}
                    if not isinstance(incoming, dict):
                        raise ValueError(f"{kind}.properties must be an object")
                    node_type = action.get("type", old_node["type"])
                    properties = (
                        dict(incoming)
                        if kind == "replace_node"
                        else {**old_node["properties"], **incoming}
                    )
                    validated = self._validate_properties_in_conn(conn, node_type, properties)
                    conn.execute(
                        "UPDATE graph_nodes SET type=?, properties=?, namespace=? WHERE id=?",
                        (node_type, json.dumps(validated), validated.get("namespace"), node_id),
                    )
                    normalized.append(
                        {"action": kind, "id": node_id, "type": node_type, "properties": validated}
                    )

                elif kind == "delete_node":
                    node_id = self._required_text(action, "id")
                    old_node = self._node_from_conn(conn, node_id)
                    if old_node is None:
                        raise ValueError(f"Node with ID '{node_id}' does not exist")
                    edge_rows = conn.execute(
                        "SELECT from_id,to_id,type,properties,created_at FROM graph_edges "
                        "WHERE from_id=? OR to_id=?",
                        (node_id, node_id),
                    ).fetchall()
                    edges = [
                        {
                            "from_id": row["from_id"],
                            "to_id": row["to_id"],
                            "type": row["type"],
                            "properties": json.loads(row["properties"] or "{}"),
                        }
                        for row in edge_rows
                    ]
                    snapshot_before["nodes"][node_id] = old_node
                    for edge in edges:
                        key = f"{edge['from_id']}\x1f{edge['to_id']}\x1f{edge['type']}"
                        snapshot_before["edges"][key] = edge
                    reverse_actions.append(
                        {
                            "action": "restore_node",
                            "id": node_id,
                            "type": old_node["type"],
                            "properties": old_node["properties"],
                            "edges": edges,
                        }
                    )
                    conn.execute("DELETE FROM graph_edges WHERE from_id=? OR to_id=?", (node_id, node_id))
                    conn.execute("DELETE FROM graph_nodes WHERE id=?", (node_id,))
                    normalized.append({"action": kind, "id": node_id})

                elif kind == "restore_node":
                    node_id = self._required_text(action, "id")
                    node_type = self._required_text(action, "type")
                    properties = action.get("properties") or {}
                    edges = action.get("edges") or []
                    if not isinstance(properties, dict) or not isinstance(edges, list):
                        raise ValueError("restore_node payload is malformed")
                    old_node = self._node_from_conn(conn, node_id)
                    if old_node:
                        reverse_actions.append(
                            {
                                "action": "replace_node",
                                "id": node_id,
                                "type": old_node["type"],
                                "properties": old_node["properties"],
                            }
                        )
                    else:
                        reverse_actions.append({"action": "delete_node", "id": node_id})
                    validated = self._validate_properties_in_conn(conn, node_type, properties)
                    conn.execute(
                        """INSERT INTO graph_nodes(id,type,properties,namespace,created_at) VALUES(?,?,?,?,?)
                           ON CONFLICT(id) DO UPDATE SET type=excluded.type,
                           properties=excluded.properties, namespace=excluded.namespace""",
                        (node_id, node_type, json.dumps(validated), validated.get("namespace"), now),
                    )
                    for edge in edges:
                        from_id = self._required_text(edge, "from_id")
                        to_id = self._required_text(edge, "to_id")
                        edge_type = self._required_text(edge, "type")
                        if self._node_from_conn(conn, from_id) is None or self._node_from_conn(conn, to_id) is None:
                            raise ValueError("Cannot restore an edge whose endpoint is missing")
                        conn.execute(
                            """INSERT INTO graph_edges(from_id,to_id,type,properties,created_at)
                               VALUES(?,?,?,?,?) ON CONFLICT(from_id,to_id,type)
                               DO UPDATE SET properties=excluded.properties""",
                            (from_id, to_id, edge_type, json.dumps(edge.get("properties") or {}), now),
                        )
                    normalized.append(action)

                elif kind in {"create_edge", "delete_edge"}:
                    from_id = self._required_text(action, "from_id")
                    to_id = self._required_text(action, "to_id")
                    edge_type = self._required_text(action, "type")
                    old_edge = self._edge_from_conn(conn, from_id, to_id, edge_type)
                    edge_key = f"{from_id}\x1f{to_id}\x1f{edge_type}"
                    if old_edge:
                        snapshot_before["edges"][edge_key] = old_edge
                    if kind == "create_edge":
                        if self._node_from_conn(conn, from_id) is None:
                            raise ValueError(f"Source node '{from_id}' does not exist")
                        if self._node_from_conn(conn, to_id) is None:
                            raise ValueError(f"Target node '{to_id}' does not exist")
                        properties = action.get("properties") or {}
                        if not isinstance(properties, dict):
                            raise ValueError("create_edge.properties must be an object")
                        reverse_actions.append(
                            {
                                "action": "create_edge",
                                "from_id": from_id,
                                "to_id": to_id,
                                "type": edge_type,
                                "properties": old_edge["properties"],
                            }
                            if old_edge
                            else {
                                "action": "delete_edge",
                                "from_id": from_id,
                                "to_id": to_id,
                                "type": edge_type,
                            }
                        )
                        conn.execute(
                            """INSERT INTO graph_edges(from_id,to_id,type,properties,created_at)
                               VALUES(?,?,?,?,?) ON CONFLICT(from_id,to_id,type)
                               DO UPDATE SET properties=excluded.properties""",
                            (from_id, to_id, edge_type, json.dumps(properties), now),
                        )
                        normalized.append(
                            {
                                "action": kind,
                                "from_id": from_id,
                                "to_id": to_id,
                                "type": edge_type,
                                "properties": properties,
                            }
                        )
                    else:
                        if old_edge is None:
                            raise ValueError(f"Edge '{from_id}->{to_id}:{edge_type}' does not exist")
                        reverse_actions.append(
                            {
                                "action": "create_edge",
                                "from_id": from_id,
                                "to_id": to_id,
                                "type": edge_type,
                                "properties": old_edge["properties"],
                            }
                        )
                        conn.execute(
                            "DELETE FROM graph_edges WHERE from_id=? AND to_id=? AND type=?",
                            (from_id, to_id, edge_type),
                        )
                        normalized.append(
                            {"action": kind, "from_id": from_id, "to_id": to_id, "type": edge_type}
                        )
                else:
                    raise ValueError(f"Unsupported graph mutation action: {kind}")

            reverse_actions.reverse()
            if session_id and ticket_id:
                conn.execute(
                    """INSERT INTO graph_mutation_history(
                           id,session_id,forward_actions,reverse_actions,snapshot_before,pinned,created_at
                       ) VALUES(?,?,?,?,?,0,?)""",
                    (
                        ticket_id,
                        session_id,
                        json.dumps(normalized),
                        json.dumps(reverse_actions),
                        json.dumps(snapshot_before),
                        now,
                    ),
                )

            effect_result = {
                "ticket_id": ticket_id,
                "actions": normalized,
                "reverse_actions": reverse_actions,
                "snapshot_before": snapshot_before,
            }
            if idempotency_key:
                conn.execute(
                    "INSERT INTO graph_effects(idempotency_key,input_hash,result_json,created_at) VALUES(?,?,?,?)",
                    (idempotency_key, input_hash, json.dumps(effect_result), now),
                )

        return effect_result

    def preflight_actions(self, actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Validate a batch against one consistent graph snapshot without writing."""

        if not isinstance(actions, list) or not actions:
            raise ValueError("Graph mutation must contain at least one action")
        if any(not isinstance(action, dict) for action in actions):
            raise ValueError("Every graph mutation action must be an object")
        normalized: list[dict[str, Any]] = []
        with self.get_conn() as conn:
            nodes = {row["id"] for row in conn.execute("SELECT id FROM graph_nodes").fetchall()}
            edges = {
                (row["from_id"], row["to_id"], row["type"])
                for row in conn.execute("SELECT from_id,to_id,type FROM graph_edges").fetchall()
            }
            for raw in actions:
                action = dict(raw)
                kind = self._required_text(action, "action")
                if kind == "create_node":
                    node_id = action.get("id") or str(uuid.uuid4())
                    node_type = action.get("type", "Generic")
                    properties = action.get("properties") or {}
                    if not isinstance(node_id, str) or not node_id:
                        raise ValueError("create_node.id must be a string")
                    if not isinstance(node_type, str) or not node_type:
                        raise ValueError("create_node.type must be a string")
                    if not isinstance(properties, dict):
                        raise ValueError("create_node.properties must be an object")
                    validated = self._validate_properties_in_conn(conn, node_type, properties)
                    nodes.add(node_id)
                    normalized.append(
                        {"action": kind, "id": node_id, "type": node_type, "properties": validated}
                    )
                elif kind == "update_node_property":
                    node_id = self._required_text(action, "id")
                    if node_id not in nodes:
                        raise ValueError(f"Node with ID '{node_id}' does not exist")
                    properties = action.get("properties") or {}
                    if not isinstance(properties, dict):
                        raise ValueError("update_node_property.properties must be an object")
                    normalized.append({"action": kind, "id": node_id, "properties": properties})
                elif kind == "delete_node":
                    node_id = self._required_text(action, "id")
                    if node_id not in nodes:
                        raise ValueError(f"Node with ID '{node_id}' does not exist")
                    nodes.remove(node_id)
                    edges = {edge for edge in edges if edge[0] != node_id and edge[1] != node_id}
                    normalized.append({"action": kind, "id": node_id})
                elif kind in {"create_edge", "delete_edge"}:
                    from_id = self._required_text(action, "from_id")
                    to_id = self._required_text(action, "to_id")
                    edge_type = self._required_text(action, "type")
                    edge_key = (from_id, to_id, edge_type)
                    if kind == "create_edge":
                        if from_id not in nodes or to_id not in nodes:
                            raise ValueError("create_edge endpoints must exist after preceding actions")
                        properties = action.get("properties") or {}
                        if not isinstance(properties, dict):
                            raise ValueError("create_edge.properties must be an object")
                        edges.add(edge_key)
                        normalized.append(
                            {
                                "action": kind,
                                "from_id": from_id,
                                "to_id": to_id,
                                "type": edge_type,
                                "properties": properties,
                            }
                        )
                    else:
                        if edge_key not in edges:
                            raise ValueError(f"Edge '{from_id}->{to_id}:{edge_type}' does not exist")
                        edges.remove(edge_key)
                        normalized.append(
                            {"action": kind, "from_id": from_id, "to_id": to_id, "type": edge_type}
                        )
                else:
                    raise ValueError(f"Unsupported graph mutation action: {kind}")
        return normalized

    @staticmethod
    def _normalize_schema_proposal(proposal: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(proposal, dict):
            raise ValueError("Schema proposal must be an object")
        allowed_types = {"string", "integer", "number", "boolean"}
        normalized: dict[str, list[dict[str, Any]]] = {"reused_schemas": [], "new_schemas": []}
        for item in proposal.get("reused_schemas", []) or []:
            if not isinstance(item, dict):
                raise ValueError("reused_schemas entries must be objects")
            schema_id = GraphDatabase._required_text(item, "id")
            extension = item.get("extended_properties") or {}
            if not isinstance(extension, dict) or any(
                not isinstance(key, str) or value not in allowed_types for key, value in extension.items()
            ):
                raise ValueError(f"Schema '{schema_id}' has invalid extended_properties")
            normalized["reused_schemas"].append(
                {**item, "id": schema_id, "extended_properties": dict(extension)}
            )
        for item in proposal.get("new_schemas", []) or []:
            if not isinstance(item, dict):
                raise ValueError("new_schemas entries must be objects")
            schema_id = GraphDatabase._required_text(item, "id")
            properties = item.get("properties") or {}
            if not isinstance(properties, dict) or any(
                not isinstance(key, str) or value not in allowed_types for key, value in properties.items()
            ):
                raise ValueError(f"Schema '{schema_id}' has invalid properties")
            normalized["new_schemas"].append(
                {
                    "id": schema_id,
                    "name": str(item.get("name") or schema_id),
                    "description": str(item.get("description") or ""),
                    "properties": dict(properties),
                }
            )
        return normalized

    def effective_schemas(self, proposal: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Return the schema inventory with an approved, uncommitted proposal overlaid."""

        schemas = {schema["id"]: dict(schema) for schema in self.list_schemas()}
        if proposal is None:
            return list(schemas.values())
        normalized = self._normalize_schema_proposal(proposal)
        for item in normalized["reused_schemas"]:
            schema = schemas.get(item["id"])
            if schema is None:
                raise ValueError(f"Reused schema '{item['id']}' does not exist")
            schema["properties"] = {
                **(schema.get("properties") or {}),
                **item["extended_properties"],
            }
        for item in normalized["new_schemas"]:
            schemas[item["id"]] = {**item, "is_core": False}
        return [schemas[key] for key in sorted(schemas)]

    def apply_schema_proposal_atomic(
        self,
        proposal: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Apply an approved schema proposal in one transaction and return its undo snapshot."""

        normalized = self._normalize_schema_proposal(proposal)
        canonical_input = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        input_hash = hashlib.sha256(canonical_input.encode("utf-8")).hexdigest()
        snapshot: dict[str, Any] = {}
        now = datetime.now(UTC).isoformat()
        with self.get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if idempotency_key:
                existing_effect = conn.execute(
                    "SELECT input_hash,result_json FROM graph_effects WHERE idempotency_key=?",
                    (idempotency_key,),
                ).fetchone()
                if existing_effect:
                    if existing_effect["input_hash"] != input_hash:
                        raise ValueError("Schema idempotency key was reused with a different proposal")
                    return json.loads(existing_effect["result_json"])
            for item in [*normalized["reused_schemas"], *normalized["new_schemas"]]:
                schema_id = item["id"]
                row = conn.execute("SELECT * FROM graph_schemas WHERE id=?", (schema_id,)).fetchone()
                snapshot[schema_id] = dict(row) if row is not None else None
            for item in normalized["reused_schemas"]:
                row = conn.execute("SELECT * FROM graph_schemas WHERE id=?", (item["id"],)).fetchone()
                if row is None:
                    raise ValueError(f"Reused schema '{item['id']}' does not exist")
                properties = {**json.loads(row["properties"] or "{}"), **item["extended_properties"]}
                conn.execute(
                    "UPDATE graph_schemas SET properties=? WHERE id=?",
                    (json.dumps(properties), item["id"]),
                )
            for item in normalized["new_schemas"]:
                conn.execute(
                    """INSERT INTO graph_schemas(id,name,description,properties,is_core,created_at)
                       VALUES(?,?,?,?,0,?) ON CONFLICT(id) DO UPDATE SET
                       name=excluded.name, description=excluded.description,
                       properties=excluded.properties""",
                    (
                        item["id"],
                        item["name"],
                        item["description"],
                        json.dumps(item["properties"]),
                        now,
                    ),
                )
            effect_result = {"proposal": normalized, "snapshot": snapshot}
            if idempotency_key:
                conn.execute(
                    "INSERT INTO graph_effects(idempotency_key,input_hash,result_json,created_at) VALUES(?,?,?,?)",
                    (idempotency_key, input_hash, json.dumps(effect_result), now),
                )
        return effect_result

    def restore_schema_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> None:
        """Restore schemas and invalidate the matching effect ledger atomically."""

        if not isinstance(snapshot, dict):
            raise ValueError("Schema snapshot must be an object")
        with self.get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for schema_id, old in snapshot.items():
                if old is None:
                    conn.execute("DELETE FROM graph_schemas WHERE id=?", (schema_id,))
                    continue
                conn.execute(
                    """INSERT INTO graph_schemas(id,name,description,properties,is_core,created_at)
                       VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                       name=excluded.name, description=excluded.description,
                       properties=excluded.properties, is_core=excluded.is_core,
                       created_at=excluded.created_at""",
                    (
                        old["id"],
                        old["name"],
                        old["description"],
                        old["properties"],
                        old["is_core"],
                        old["created_at"],
                    ),
                )
            if idempotency_key:
                conn.execute("DELETE FROM graph_effects WHERE idempotency_key=?", (idempotency_key,))

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

    # --- Mutation History (for undo of graph_mutation tickets) ---

    def record_mutation_history(
        self,
        ticket_id: str,
        session_id: str,
        forward_actions: list[dict[str, Any]],
        reverse_actions: list[dict[str, Any]],
        snapshot_before: dict[str, Any],
        pinned: bool = False,
    ) -> dict[str, Any]:
        """Persist a mutation ticket so it remains rollback-able."""
        with self.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO graph_mutation_history (
                    id, session_id, forward_actions, reverse_actions,
                    snapshot_before, pinned, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    forward_actions=excluded.forward_actions,
                    reverse_actions=excluded.reverse_actions,
                    snapshot_before=excluded.snapshot_before,
                    pinned=excluded.pinned
                """,
                (
                    ticket_id,
                    session_id,
                    json.dumps(forward_actions or []),
                    json.dumps(reverse_actions or []),
                    json.dumps(snapshot_before or {}),
                    1 if pinned else 0,
                    datetime.now(UTC).isoformat(),
                ),
            )
        return {
            "ticket_id": ticket_id,
            "session_id": session_id,
            "forward_actions": list(forward_actions or []),
            "reverse_actions": list(reverse_actions or []),
            "snapshot_before": dict(snapshot_before or {}),
            "pinned": bool(pinned),
        }

    def pin_mutation_history(self, ticket_id: str) -> bool:
        with self.get_conn() as conn:
            cursor = conn.execute("UPDATE graph_mutation_history SET pinned = 1 WHERE id = ?", (ticket_id,))
            return cursor.rowcount > 0

    def load_mutation_history(self, ticket_id: str) -> dict[str, Any] | None:
        with self.get_conn() as conn:
            row = conn.execute(
                """
                SELECT id, session_id, forward_actions, reverse_actions,
                       snapshot_before, pinned, consumed_at, created_at
                FROM graph_mutation_history WHERE id = ?
                """,
                (ticket_id,),
            ).fetchone()
            if row is None:
                return None
            return {
                "ticket_id": row["id"],
                "session_id": row["session_id"],
                "forward_actions": json.loads(row["forward_actions"]),
                "reverse_actions": json.loads(row["reverse_actions"]),
                "snapshot_before": json.loads(row["snapshot_before"] or "{}"),
                "pinned": bool(row["pinned"]),
                "consumed_at": row["consumed_at"],
                "created_at": row["created_at"],
            }

    def list_mutation_history(self, session_id: str) -> list[dict[str, Any]]:
        with self.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, session_id, forward_actions, reverse_actions,
                       snapshot_before, pinned, consumed_at, created_at
                FROM graph_mutation_history WHERE session_id = ?
                ORDER BY created_at DESC
                """,
                (session_id,),
            ).fetchall()
            return [
                {
                    "ticket_id": row["id"],
                    "session_id": row["session_id"],
                    "forward_actions": json.loads(row["forward_actions"]),
                    "reverse_actions": json.loads(row["reverse_actions"]),
                    "snapshot_before": json.loads(row["snapshot_before"] or "{}"),
                    "pinned": bool(row["pinned"]),
                    "consumed_at": row["consumed_at"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ]


# Module-level convenience: callers construct their own manager per session/db.
# See ``backend.mutation_tickets.MutationTicketManager``.
