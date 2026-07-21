import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from backend.ontology import (
    ONTOLOGY_ID,
    ONTOLOGY_VERSION,
    PREBUILT_ONTOLOGY,
    USER_CONTEXT_SCOPE,
    LEGACY_ENTITY_ALIASES,
    coerce_entity_properties,
    validate_correspondences,
    validate_property_definition,
)


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
        Initializes the SQLite compatibility adapter and canonical ontology,
        and performs legacy migration from graph.json if it exists.
        """
        with self.get_conn() as conn:
            # 1. Create tables
            conn.execute("""
                CREATE TABLE IF NOT EXISTS graph_ontologies (
                    id TEXT PRIMARY KEY,
                    version TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS graph_schemas (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    properties TEXT, -- JSON string: { "field": "type" }
                    is_core INTEGER DEFAULT 0,
                    ontology_id TEXT NOT NULL DEFAULT 'ambient-context',
                    ontology_iri TEXT,
                    source TEXT,
                    equivalent_to TEXT NOT NULL DEFAULT '[]',
                    subclass_of TEXT,
                    abstract INTEGER DEFAULT 0,
                    data_scope TEXT NOT NULL DEFAULT 'user_context',
                    created_at TEXT NOT NULL
                )
            """)
            existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(graph_schemas)").fetchall()}
            ontology_columns = {
                "ontology_id": "TEXT NOT NULL DEFAULT 'ambient-context'",
                "ontology_iri": "TEXT",
                "source": "TEXT",
                "equivalent_to": "TEXT NOT NULL DEFAULT '[]'",
                "subclass_of": "TEXT",
                "abstract": "INTEGER DEFAULT 0",
                "data_scope": "TEXT NOT NULL DEFAULT 'user_context'",
            }
            for column, definition in ontology_columns.items():
                if column not in existing_columns:
                    conn.execute(f"ALTER TABLE graph_schemas ADD COLUMN {column} {definition}")
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
            conn.execute(
                "INSERT OR IGNORE INTO graph_ontologies(id,version,created_at) VALUES(?,?,?)",
                (ONTOLOGY_ID, ONTOLOGY_VERSION, datetime.now(UTC).isoformat()),
            )

        # 3. Seed core schemas
        self._seed_core_schemas()

        # 4. Align records written by the former permissive SQLite contract
        self._align_existing_sqlite_records()

        # 5. Migrate legacy graph.json if present
        self._migrate_legacy_data()

    def _seed_core_schemas(self) -> None:
        for entity in PREBUILT_ONTOLOGY:
            self.register_schema(
                schema_id=entity.id,
                name=entity.name,
                description=entity.description,
                properties=entity.properties,
                is_core=entity.is_core,
                ontology_iri=entity.ontology_iri,
                source=entity.source,
                equivalent_to=list(entity.equivalent_to),
                subclass_of=entity.subclass_of,
                abstract=entity.abstract,
                data_scope=entity.data_scope,
            )

    def _align_existing_sqlite_records(self) -> None:
        definitions: dict[str, dict[str, str]] = {}
        updates: list[tuple[str, str, str]] = []
        with self.get_conn() as conn:
            rows = conn.execute("SELECT id,type,properties FROM graph_nodes").fetchall()
            for row in rows:
                original_type = str(row["type"] or "LegacyRecord")
                node_type, property_aliases = LEGACY_ENTITY_ALIASES.get(original_type, (original_type, {}))
                properties = json.loads(row["properties"] or "{}")
                for old_key, new_key in property_aliases.items():
                    if old_key in properties:
                        properties.setdefault(new_key, properties[old_key])
                        properties.pop(old_key, None)

                existing = self.get_schema(node_type)
                if existing is not None and existing.get("abstract"):
                    node_type = f"Legacy{node_type}Record"
                inferred = definitions.setdefault(node_type, {})
                for key, value in properties.items():
                    if key != "namespace":
                        inferred.setdefault(key, self._infer_property_type(value))
                if node_type != original_type or properties != json.loads(row["properties"] or "{}"):
                    updates.append((node_type, json.dumps(properties), row["id"]))

            for node_type, properties_json, node_id in updates:
                conn.execute(
                    "UPDATE graph_nodes SET type=?, properties=? WHERE id=?",
                    (node_type, properties_json, node_id),
                )

        for node_type, inferred in definitions.items():
            existing = self.get_schema(node_type)
            if existing is None:
                self.register_schema(
                    node_type,
                    node_type,
                    "Entity inferred while aligning the legacy SQLite knowledge graph",
                    inferred,
                    source="legacy-sqlite",
                    ontology_iri=f"urn:ambient:legacy:{node_type}",
                )
                continue
            if set(inferred) - set(existing["properties"]):
                self.register_schema(
                    node_type,
                    existing["name"],
                    existing["description"],
                    {**existing["properties"], **inferred},
                    existing["is_core"],
                    ontology_iri=existing["ontology_iri"],
                    source=existing["source"],
                    equivalent_to=existing["equivalent_to"],
                    subclass_of=existing["subclass_of"],
                    abstract=existing["abstract"],
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
            definitions: dict[str, dict[str, str]] = {}
            for node in legacy_nodes.values():
                node_type = str(node.get("type") or "LegacyRecord")
                properties = node.get("properties") or {}
                inferred = definitions.setdefault(node_type, {})
                for key, value in properties.items():
                    if key != "namespace":
                        inferred.setdefault(key, self._infer_property_type(value))

            for node_type, inferred in definitions.items():
                existing = self.get_schema(node_type)
                if existing is None:
                    self.register_schema(
                        node_type,
                        node_type,
                        "Entity inferred during graph.json migration",
                        inferred,
                        source="legacy-graph-json",
                        ontology_iri=f"urn:ambient:legacy:{node_type}",
                    )
                elif set(inferred) - set(existing["properties"]):
                    self.register_schema(
                        node_type,
                        existing["name"],
                        existing["description"],
                        {**existing["properties"], **inferred},
                        existing["is_core"],
                        ontology_iri=existing["ontology_iri"],
                        source=existing["source"],
                        equivalent_to=existing["equivalent_to"],
                        subclass_of=existing["subclass_of"],
                        abstract=existing["abstract"],
                    )

            for node_id, node in legacy_nodes.items():
                self.create_node(
                    node_id=str(node_id),
                    node_type=str(node.get("type") or "LegacyRecord"),
                    properties=node.get("properties") or {},
                )
            for edge in legacy_edges:
                self.create_edge(
                    from_id=str(edge.get("from_id") or ""),
                    to_id=str(edge.get("to_id") or ""),
                    edge_type=str(edge.get("type") or ""),
                    properties=edge.get("properties") or {},
                )

            # Backup old file to avoid re-migration
            backup_path = self.legacy_filepath + ".backup"
            if os.path.exists(backup_path):
                os.remove(backup_path)
            os.rename(self.legacy_filepath, backup_path)
            print("[GraphDB] Migration completed successfully.")
        except Exception as e:
            print(f"[GraphDB] Error migrating legacy JSON graph: {e}")

    @staticmethod
    def _infer_property_type(value: Any) -> str:
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int):
            return "integer"
        if isinstance(value, float):
            return "number"
        return "string"

    # --- Schema APIs ---

    def register_schema(
        self,
        schema_id: str,
        name: str,
        description: str,
        properties: dict[str, str],
        is_core: bool = False,
        *,
        ontology_iri: str | None = None,
        source: str = "ambient-context",
        equivalent_to: list[str] | tuple[str, ...] | None = None,
        subclass_of: str | None = "Thing",
        abstract: bool = False,
        data_scope: str = USER_CONTEXT_SCOPE,
    ) -> dict[str, Any]:
        if not isinstance(schema_id, str) or not schema_id.strip():
            raise ValueError("Ontology entity id must be a non-empty string")
        schema_id = schema_id.strip()
        properties = validate_property_definition(properties, entity_id=schema_id)
        existing = self.get_schema(schema_id)
        if existing is not None:
            properties = self._merge_entity_properties(schema_id, existing["properties"], properties)
            is_core = is_core or existing["is_core"]
        correspondences = validate_correspondences(equivalent_to, entity_id=schema_id)
        if data_scope != USER_CONTEXT_SCOPE:
            raise ValueError(f"Ontology entity '{schema_id}' has non-context data_scope '{data_scope}'")
        if subclass_of == schema_id:
            raise ValueError(f"Ontology entity '{schema_id}' cannot be its own parent")
        if subclass_of is not None and subclass_of != "Thing" and self.get_schema(subclass_of) is None:
            raise ValueError(f"Ontology entity '{schema_id}' references missing parent '{subclass_of}'")
        ontology_iri = ontology_iri or f"urn:ambient:ontology:{schema_id}"
        with self.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO graph_schemas (
                    id, name, description, properties, is_core, ontology_id,
                    ontology_iri, source, equivalent_to, subclass_of, abstract,
                    data_scope, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    description=excluded.description,
                    properties=excluded.properties,
                    ontology_id=excluded.ontology_id,
                    ontology_iri=excluded.ontology_iri,
                    source=excluded.source,
                    equivalent_to=excluded.equivalent_to,
                    subclass_of=excluded.subclass_of,
                    abstract=excluded.abstract,
                    data_scope=excluded.data_scope
                """,
                (
                    schema_id,
                    name,
                    description,
                    json.dumps(properties),
                    1 if is_core else 0,
                    ONTOLOGY_ID,
                    ontology_iri,
                    source,
                    json.dumps(correspondences),
                    subclass_of,
                    1 if abstract else 0,
                    data_scope,
                    datetime.now(UTC).isoformat(),
                ),
            )
        return {
            "id": schema_id,
            "name": name,
            "description": description,
            "properties": properties,
            "is_core": is_core,
            "ontology_id": ONTOLOGY_ID,
            "ontology_iri": ontology_iri,
            "source": source,
            "equivalent_to": correspondences,
            "subclass_of": subclass_of,
            "abstract": abstract,
            "data_scope": data_scope,
        }

    def get_schema(self, schema_id: str) -> dict[str, Any] | None:
        with self.get_conn() as conn:
            row = conn.execute(
                """SELECT id, name, description, properties, is_core, ontology_id,
                          ontology_iri, source, equivalent_to, subclass_of, abstract, data_scope
                   FROM graph_schemas WHERE id = ?""",
                (schema_id,),
            ).fetchone()
            if row:
                return {
                    "id": row["id"],
                    "name": row["name"],
                    "description": row["description"],
                    "properties": json.loads(row["properties"]),
                    "is_core": bool(row["is_core"]),
                    "ontology_id": row["ontology_id"],
                    "ontology_iri": row["ontology_iri"],
                    "source": row["source"],
                    "equivalent_to": json.loads(row["equivalent_to"] or "[]"),
                    "subclass_of": row["subclass_of"],
                    "abstract": bool(row["abstract"]),
                    "data_scope": row["data_scope"],
                }
        return None

    def list_schemas(self) -> list[dict[str, Any]]:
        with self.get_conn() as conn:
            rows = conn.execute(
                """SELECT id, name, description, properties, is_core, ontology_id,
                          ontology_iri, source, equivalent_to, subclass_of, abstract, data_scope
                   FROM graph_schemas ORDER BY id"""
            ).fetchall()
            return [
                {
                    "id": row["id"],
                    "name": row["name"],
                    "description": row["description"],
                    "properties": json.loads(row["properties"]),
                    "is_core": bool(row["is_core"]),
                    "ontology_id": row["ontology_id"],
                    "ontology_iri": row["ontology_iri"],
                    "source": row["source"],
                    "equivalent_to": json.loads(row["equivalent_to"] or "[]"),
                    "subclass_of": row["subclass_of"],
                    "abstract": bool(row["abstract"]),
                    "data_scope": row["data_scope"],
                }
                for row in rows
            ]

    def routing_snapshot(self, recent_per_type: int = 5) -> dict[str, Any]:
        """Return bounded graph context without exposing adapter internals."""
        recent_by_type: dict[str, list[dict[str, Any]]] = {}
        with self.get_conn() as conn:
            count_rows = conn.execute("SELECT type, COUNT(*) AS c FROM graph_nodes GROUP BY type").fetchall()
            type_counts = {row["type"]: row["c"] for row in count_rows}
            node_row = conn.execute("SELECT COUNT(*) AS c FROM graph_nodes").fetchone()
            edge_row = conn.execute("SELECT COUNT(*) AS c FROM graph_edges").fetchone()
            rows = conn.execute(
                "SELECT id, type, properties, created_at FROM graph_nodes ORDER BY created_at DESC, id ASC"
            ).fetchall()
            for row in rows:
                items = recent_by_type.setdefault(row["type"], [])
                if len(items) >= recent_per_type:
                    continue
                items.append(
                    {
                        "id": row["id"],
                        "type": row["type"],
                        "properties": json.loads(row["properties"] or "{}"),
                        "created_at": row["created_at"],
                    }
                )
        return {
            "type_counts": type_counts,
            "recent_nodes_by_type": recent_by_type,
            "schema_manifest": self.list_schemas(),
            "node_count": node_row["c"] if node_row else 0,
            "edge_count": edge_row["c"] if edge_row else 0,
        }

    # --- Node/Edge Property Validation ---

    def validate_properties(self, node_type: str, properties: dict[str, Any]) -> dict[str, Any]:
        schema = self.get_schema(node_type)
        if schema is None:
            raise ValueError(f"Entity '{node_type}' is not registered in ontology '{ONTOLOGY_ID}'")
        return coerce_entity_properties(schema, properties)

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
                return {
                    "id": row["id"],
                    "type": row["type"],
                    "ontology_entity_id": row["type"],
                    "properties": json.loads(row["properties"]),
                }
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
    def _edge_from_conn(conn: sqlite3.Connection, from_id: str, to_id: str, edge_type: str) -> dict[str, Any] | None:
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
        row = conn.execute(
            """SELECT id, properties, ontology_id, abstract, data_scope
               FROM graph_schemas WHERE id=?""",
            (node_type,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Entity '{node_type}' is not registered in ontology '{ONTOLOGY_ID}'")
        return coerce_entity_properties(
            {
                "id": row["id"],
                "properties": json.loads(row["properties"] or "{}"),
                "ontology_id": row["ontology_id"],
                "abstract": bool(row["abstract"]),
                "data_scope": row["data_scope"],
            },
            properties,
        )

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
                    normalized.append({"action": kind, "id": node_id, "type": node_type, "properties": validated})

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
                    properties = dict(incoming) if kind == "replace_node" else {**old_node["properties"], **incoming}
                    validated = self._validate_properties_in_conn(conn, node_type, properties)
                    conn.execute(
                        "UPDATE graph_nodes SET type=?, properties=?, namespace=? WHERE id=?",
                        (node_type, json.dumps(validated), validated.get("namespace"), node_id),
                    )
                    normalized.append({"action": kind, "id": node_id, "type": node_type, "properties": validated})

                elif kind == "delete_node":
                    node_id = self._required_text(action, "id")
                    old_node = self._node_from_conn(conn, node_id)
                    if old_node is None:
                        raise ValueError(f"Node with ID '{node_id}' does not exist")
                    edge_rows = conn.execute(
                        "SELECT from_id,to_id,type,properties,created_at FROM graph_edges WHERE from_id=? OR to_id=?",
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
                        normalized.append({"action": kind, "from_id": from_id, "to_id": to_id, "type": edge_type})
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
            nodes = {
                row["id"]: {"type": row["type"], "properties": json.loads(row["properties"] or "{}")}
                for row in conn.execute("SELECT id,type,properties FROM graph_nodes").fetchall()
            }
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
                    nodes[node_id] = {"type": node_type, "properties": validated}
                    normalized.append({"action": kind, "id": node_id, "type": node_type, "properties": validated})
                elif kind == "update_node_property":
                    node_id = self._required_text(action, "id")
                    node = nodes.get(node_id)
                    if node is None:
                        raise ValueError(f"Node with ID '{node_id}' does not exist")
                    properties = action.get("properties") or {}
                    if not isinstance(properties, dict):
                        raise ValueError("update_node_property.properties must be an object")
                    merged = {**node["properties"], **properties}
                    validated = self._validate_properties_in_conn(conn, node["type"], merged)
                    nodes[node_id] = {"type": node["type"], "properties": validated}
                    normalized.append(
                        {
                            "action": kind,
                            "id": node_id,
                            "properties": {key: validated[key] for key in properties},
                        }
                    )
                elif kind == "delete_node":
                    node_id = self._required_text(action, "id")
                    if node_id not in nodes:
                        raise ValueError(f"Node with ID '{node_id}' does not exist")
                    nodes.pop(node_id)
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
                        normalized.append({"action": kind, "from_id": from_id, "to_id": to_id, "type": edge_type})
                else:
                    raise ValueError(f"Unsupported graph mutation action: {kind}")
        return normalized

    @staticmethod
    def _normalize_schema_proposal(proposal: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(proposal, dict):
            raise ValueError("Schema proposal must be an object")
        normalized: dict[str, list[dict[str, Any]]] = {"reused_schemas": [], "new_schemas": []}
        for item in proposal.get("reused_schemas", []) or []:
            if not isinstance(item, dict):
                raise ValueError("reused_schemas entries must be objects")
            schema_id = GraphDatabase._required_text(item, "id")
            extension = validate_property_definition(item.get("extended_properties") or {}, entity_id=schema_id)
            data_scope = item.get("data_scope", USER_CONTEXT_SCOPE)
            if data_scope != USER_CONTEXT_SCOPE:
                raise ValueError(f"Ontology entity '{schema_id}' has non-context data_scope '{data_scope}'")
            normalized["reused_schemas"].append(
                {**item, "id": schema_id, "extended_properties": dict(extension), "data_scope": data_scope}
            )
        for item in proposal.get("new_schemas", []) or []:
            if not isinstance(item, dict):
                raise ValueError("new_schemas entries must be objects")
            schema_id = GraphDatabase._required_text(item, "id")
            properties = validate_property_definition(item.get("properties") or {}, entity_id=schema_id)
            data_scope = item.get("data_scope", USER_CONTEXT_SCOPE)
            if data_scope != USER_CONTEXT_SCOPE:
                raise ValueError(f"Ontology entity '{schema_id}' has non-context data_scope '{data_scope}'")
            subclass_of = item.get("subclass_of", "Thing")
            if subclass_of is not None and (not isinstance(subclass_of, str) or not subclass_of.strip()):
                raise ValueError(f"Ontology entity '{schema_id}' has invalid subclass_of")
            subclass_of = subclass_of.strip() if isinstance(subclass_of, str) else None
            if subclass_of == schema_id:
                raise ValueError(f"Ontology entity '{schema_id}' cannot be its own parent")
            ontology_iri = item.get("ontology_iri") or f"urn:ambient:ontology:{schema_id}"
            if not isinstance(ontology_iri, str) or not ontology_iri.strip():
                raise ValueError(f"Ontology entity '{schema_id}' has invalid ontology_iri")
            equivalent_to = validate_correspondences(item.get("equivalent_to"), entity_id=schema_id)
            normalized["new_schemas"].append(
                {
                    "id": schema_id,
                    "name": str(item.get("name") or schema_id),
                    "description": str(item.get("description") or ""),
                    "properties": dict(properties),
                    "ontology_id": ONTOLOGY_ID,
                    "ontology_iri": ontology_iri.strip(),
                    "source": str(item.get("source") or "ambient-context"),
                    "equivalent_to": equivalent_to,
                    "subclass_of": subclass_of,
                    "abstract": bool(item.get("abstract", False)),
                    "data_scope": data_scope,
                }
            )
        return normalized

    @staticmethod
    def _merge_entity_properties(
        entity_id: str,
        current: dict[str, str],
        extension: dict[str, str],
    ) -> dict[str, str]:
        for key, value in extension.items():
            if key in current and current[key] != value:
                raise ValueError(
                    f"Ontology entity '{entity_id}' cannot change property '{key}' from '{current[key]}' to '{value}'"
                )
        return {**current, **extension}

    def _validate_proposal_parents(self, normalized: dict[str, list[dict[str, Any]]]) -> None:
        available = {schema["id"] for schema in self.list_schemas()}
        available.update(item["id"] for item in normalized["new_schemas"])
        for item in normalized["new_schemas"]:
            parent = item.get("subclass_of")
            if parent is not None and parent not in available:
                raise ValueError(f"Ontology entity '{item['id']}' references missing parent '{parent}'")

    def effective_schemas(self, proposal: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Return the schema inventory with an approved, uncommitted proposal overlaid."""

        schemas = {schema["id"]: dict(schema) for schema in self.list_schemas()}
        if proposal is None:
            return list(schemas.values())
        normalized = self._normalize_schema_proposal(proposal)
        self._validate_proposal_parents(normalized)
        for item in normalized["reused_schemas"]:
            schema = schemas.get(item["id"])
            if schema is None:
                raise ValueError(f"Reused schema '{item['id']}' does not exist")
            schema["properties"] = self._merge_entity_properties(
                item["id"], schema.get("properties") or {}, item["extended_properties"]
            )
        for item in normalized["new_schemas"]:
            if item["id"] in schemas:
                raise ValueError(f"Ontology entity '{item['id']}' already exists; extend the canonical entity instead")
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
        self._validate_proposal_parents(normalized)
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
            for item in normalized["new_schemas"]:
                existing_schema = conn.execute("SELECT id FROM graph_schemas WHERE id=?", (item["id"],)).fetchone()
                if existing_schema is not None:
                    raise ValueError(
                        f"Ontology entity '{item['id']}' already exists; extend the canonical entity instead"
                    )
            for item in [*normalized["reused_schemas"], *normalized["new_schemas"]]:
                schema_id = item["id"]
                row = conn.execute("SELECT * FROM graph_schemas WHERE id=?", (schema_id,)).fetchone()
                snapshot[schema_id] = dict(row) if row is not None else None
            for item in normalized["reused_schemas"]:
                row = conn.execute("SELECT * FROM graph_schemas WHERE id=?", (item["id"],)).fetchone()
                if row is None:
                    raise ValueError(f"Reused schema '{item['id']}' does not exist")
                properties = self._merge_entity_properties(
                    item["id"], json.loads(row["properties"] or "{}"), item["extended_properties"]
                )
                conn.execute(
                    "UPDATE graph_schemas SET properties=? WHERE id=?",
                    (json.dumps(properties), item["id"]),
                )
            for item in normalized["new_schemas"]:
                conn.execute(
                    """INSERT INTO graph_schemas(
                           id,name,description,properties,is_core,ontology_id,ontology_iri,
                           source,equivalent_to,subclass_of,abstract,data_scope,created_at
                       ) VALUES(?,?,?,?,0,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                       name=excluded.name, description=excluded.description,
                       properties=excluded.properties, ontology_id=excluded.ontology_id,
                       ontology_iri=excluded.ontology_iri, source=excluded.source,
                       equivalent_to=excluded.equivalent_to, subclass_of=excluded.subclass_of,
                       abstract=excluded.abstract, data_scope=excluded.data_scope""",
                    (
                        item["id"],
                        item["name"],
                        item["description"],
                        json.dumps(item["properties"]),
                        ONTOLOGY_ID,
                        item["ontology_iri"],
                        item["source"],
                        json.dumps(item["equivalent_to"]),
                        item["subclass_of"],
                        1 if item["abstract"] else 0,
                        item["data_scope"],
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
                    """INSERT INTO graph_schemas(
                           id,name,description,properties,is_core,ontology_id,ontology_iri,
                           source,equivalent_to,subclass_of,abstract,data_scope,created_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                       name=excluded.name, description=excluded.description,
                       properties=excluded.properties, is_core=excluded.is_core,
                       ontology_id=excluded.ontology_id, ontology_iri=excluded.ontology_iri,
                       source=excluded.source, equivalent_to=excluded.equivalent_to,
                       subclass_of=excluded.subclass_of, abstract=excluded.abstract,
                       data_scope=excluded.data_scope, created_at=excluded.created_at""",
                    (
                        old["id"],
                        old["name"],
                        old["description"],
                        old["properties"],
                        old["is_core"],
                        old.get("ontology_id", ONTOLOGY_ID),
                        old.get("ontology_iri"),
                        old.get("source"),
                        old.get("equivalent_to", "[]"),
                        old.get("subclass_of"),
                        old.get("abstract", 0),
                        old.get("data_scope", USER_CONTEXT_SCOPE),
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
                row["id"]: {
                    "id": row["id"],
                    "type": row["type"],
                    "ontology_entity_id": row["type"],
                    "properties": json.loads(row["properties"]),
                }
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


def create_graph_database(workspace_dir: str | None = None) -> GraphDatabase:
    """Build the configured graph adapter without importing Neo4j for SQLite-only tests."""

    configured = os.getenv("GRAPH_DATABASE_BACKEND")
    backend = (configured or ("neo4j" if os.getenv("NEO4J_URI") else "sqlite")).strip().lower()
    if backend == "sqlite":
        return GraphDatabase(workspace_dir)
    if backend == "neo4j":
        from backend.neo4j_graph_db import Neo4jGraphDatabase

        return Neo4jGraphDatabase.from_env(workspace_dir)
    raise ValueError("GRAPH_DATABASE_BACKEND must be either 'neo4j' or 'sqlite'")
