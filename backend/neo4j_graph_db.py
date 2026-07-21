from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.graph_db import GraphDatabase
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


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class Neo4jGraphDatabase(GraphDatabase):
    """Neo4j implementation of the canonical ontology and context-graph contract."""

    def __init__(self, driver: Any, *, database: str = "neo4j", workspace_dir: str | None = None):
        self.driver = driver
        self.database = database
        self.workspace_dir = workspace_dir or os.getenv("WORKSPACE_DIR", "workspace")
        self.legacy_filepath = os.path.join(self.workspace_dir, "graph.json")
        self.db_path = os.path.join(self.workspace_dir, "graph.db")
        self.load()

    @classmethod
    def from_env(cls, workspace_dir: str | None = None) -> Neo4jGraphDatabase:
        try:
            from neo4j import GraphDatabase as Neo4jDriver
        except ImportError as exc:  # pragma: no cover - dependency packaging failure
            raise RuntimeError("The neo4j Python driver is required for GRAPH_DATABASE_BACKEND=neo4j") from exc

        uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        username = os.getenv("NEO4J_USERNAME", "neo4j")
        password = os.getenv("NEO4J_PASSWORD")
        if not password:
            raise RuntimeError("NEO4J_PASSWORD is required for the Neo4j graph backend")
        driver = Neo4jDriver.driver(uri, auth=(username, password))
        try:
            driver.verify_connectivity()
            instance = cls(
                driver,
                database=os.getenv("NEO4J_DATABASE", "neo4j"),
                workspace_dir=workspace_dir,
            )
            if os.getenv("GRAPH_MIGRATE_SQLITE", "0").lower() in {"1", "true", "yes"}:
                instance.migrate_from_sqlite(instance.db_path)
            return instance
        except Exception:
            driver.close()
            raise

    def close(self) -> None:
        self.driver.close()

    def _read(self, callback: Callable[..., Any], *args: Any) -> Any:
        with self.driver.session(database=self.database) as session:
            return session.execute_read(callback, *args)

    def _write(self, callback: Callable[..., Any], *args: Any) -> Any:
        with self.driver.session(database=self.database) as session:
            return session.execute_write(callback, *args)

    def load(self) -> None:
        constraints = (
            "CREATE CONSTRAINT ambient_ontology_id IF NOT EXISTS FOR (n:Ontology) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT ambient_entity_id IF NOT EXISTS FOR (n:OntologyEntity) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT ambient_record_id IF NOT EXISTS FOR (n:ContextRecord) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT ambient_effect_key IF NOT EXISTS FOR (n:GraphEffect) REQUIRE n.idempotency_key IS UNIQUE",
            "CREATE CONSTRAINT ambient_history_id IF NOT EXISTS FOR (n:GraphMutationHistory) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT ambient_migration_id IF NOT EXISTS FOR (n:GraphMigration) REQUIRE n.id IS UNIQUE",
        )
        now = datetime.now(UTC).isoformat()
        entities = [entity.as_schema() for entity in PREBUILT_ONTOLOGY]

        for statement in constraints:
            self._write(lambda tx, query: tx.run(query).consume(), statement)

        def initialize(tx: Any) -> None:
            tx.run(
                """
                MERGE (o:Ontology {id: $ontology_id})
                ON CREATE SET o.created_at = $now
                SET o.version = $version
                """,
                ontology_id=ONTOLOGY_ID,
                version=ONTOLOGY_VERSION,
                now=now,
            ).consume()
            tx.run(
                """
                MATCH (o:Ontology {id: $ontology_id})
                UNWIND $entities AS item
                MERGE (e:OntologyEntity {id: item.id})
                ON CREATE SET e.created_at = $now
                SET e.name = item.name,
                    e.description = item.description,
                    e.properties_json = item.properties_json,
                    e.is_core = item.is_core,
                    e.ontology_id = $ontology_id,
                    e.ontology_iri = item.ontology_iri,
                    e.source = item.source,
                    e.equivalent_to = item.equivalent_to,
                    e.subclass_of = item.subclass_of,
                    e.abstract = item.abstract,
                    e.data_scope = item.data_scope
                MERGE (e)-[:IN_ONTOLOGY]->(o)
                """,
                ontology_id=ONTOLOGY_ID,
                now=now,
                entities=[
                    {
                        **entity,
                        "properties_json": _json(entity["properties"]),
                    }
                    for entity in entities
                ],
            ).consume()
            tx.run(
                """
                MATCH (child:OntologyEntity {ontology_id: $ontology_id})
                WHERE child.subclass_of IS NOT NULL
                MATCH (parent:OntologyEntity {id: child.subclass_of, ontology_id: $ontology_id})
                MERGE (child)-[:SUBCLASS_OF]->(parent)
                """,
                ontology_id=ONTOLOGY_ID,
            ).consume()

        self._write(initialize)

    @staticmethod
    def _schema_from_record(record: Any | None) -> dict[str, Any] | None:
        if record is None:
            return None
        return {
            "id": record["id"],
            "name": record["name"],
            "description": record["description"] or "",
            "properties": json.loads(record["properties_json"] or "{}"),
            "is_core": bool(record["is_core"]),
            "ontology_id": record["ontology_id"],
            "ontology_iri": record["ontology_iri"],
            "source": record["source"],
            "equivalent_to": list(record["equivalent_to"] or []),
            "subclass_of": record["subclass_of"],
            "abstract": bool(record["abstract"]),
            "data_scope": record["data_scope"] or USER_CONTEXT_SCOPE,
        }

    @staticmethod
    def _schema_return() -> str:
        return """e.id AS id, e.name AS name, e.description AS description,
                  e.properties_json AS properties_json, e.is_core AS is_core,
                  e.ontology_id AS ontology_id, e.ontology_iri AS ontology_iri,
                  e.source AS source, e.equivalent_to AS equivalent_to,
                  e.subclass_of AS subclass_of, e.abstract AS abstract,
                  e.data_scope AS data_scope"""

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
        schema_id = self._required_text({"id": schema_id}, "id")
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
        if subclass_of is not None and self.get_schema(subclass_of) is None:
            raise ValueError(f"Ontology entity '{schema_id}' references missing parent '{subclass_of}'")
        ontology_iri = ontology_iri or f"urn:ambient:ontology:{schema_id}"
        schema = {
            "id": schema_id,
            "name": name,
            "description": description,
            "properties": properties,
            "is_core": bool(is_core),
            "ontology_id": ONTOLOGY_ID,
            "ontology_iri": ontology_iri,
            "source": source,
            "equivalent_to": correspondences,
            "subclass_of": subclass_of,
            "abstract": bool(abstract),
            "data_scope": data_scope,
        }
        now = datetime.now(UTC).isoformat()

        def upsert(tx: Any) -> None:
            tx.run(
                """
                MATCH (o:Ontology {id: $ontology_id})
                MERGE (e:OntologyEntity {id: $entity.id})
                ON CREATE SET e.created_at = $now
                SET e.name = $entity.name,
                    e.description = $entity.description,
                    e.properties_json = $properties_json,
                    e.is_core = $entity.is_core,
                    e.ontology_id = $ontology_id,
                    e.ontology_iri = $entity.ontology_iri,
                    e.source = $entity.source,
                    e.equivalent_to = $entity.equivalent_to,
                    e.subclass_of = $entity.subclass_of,
                    e.abstract = $entity.abstract,
                    e.data_scope = $entity.data_scope
                MERGE (e)-[:IN_ONTOLOGY]->(o)
                WITH e
                OPTIONAL MATCH (e)-[old:SUBCLASS_OF]->(:OntologyEntity)
                DELETE old
                WITH DISTINCT e
                OPTIONAL MATCH (parent:OntologyEntity {id: $entity.subclass_of, ontology_id: $ontology_id})
                FOREACH (_ IN CASE WHEN parent IS NULL THEN [] ELSE [1] END |
                    MERGE (e)-[:SUBCLASS_OF]->(parent))
                """,
                ontology_id=ONTOLOGY_ID,
                entity=schema,
                properties_json=_json(properties),
                now=now,
            ).consume()

        self._write(upsert)
        return schema

    def get_schema(self, schema_id: str) -> dict[str, Any] | None:
        return_clause = self._schema_return()

        def fetch(tx: Any) -> dict[str, Any] | None:
            record = tx.run(
                f"""MATCH (e:OntologyEntity {{id: $id, ontology_id: $ontology_id}})
                    RETURN {return_clause}""",
                id=schema_id,
                ontology_id=ONTOLOGY_ID,
            ).single()
            return self._schema_from_record(record)

        return self._read(fetch)

    def list_schemas(self) -> list[dict[str, Any]]:
        return_clause = self._schema_return()

        def fetch(tx: Any) -> list[dict[str, Any]]:
            result = tx.run(
                f"""MATCH (e:OntologyEntity {{ontology_id: $ontology_id}})
                    RETURN {return_clause} ORDER BY id""",
                ontology_id=ONTOLOGY_ID,
            )
            return [self._schema_from_record(record) for record in result]

        return self._read(fetch)

    def validate_properties(self, node_type: str, properties: dict[str, Any]) -> dict[str, Any]:
        schema = self.get_schema(node_type)
        if schema is None:
            raise ValueError(f"Entity '{node_type}' is not registered in ontology '{ONTOLOGY_ID}'")
        return coerce_entity_properties(schema, properties)

    @staticmethod
    def _node_from_record(record: Any | None) -> dict[str, Any] | None:
        if record is None:
            return None
        entity_ids = list(record["entity_ids"] or [])
        if len(entity_ids) != 1:
            raise RuntimeError(f"Context record '{record['id']}' must have exactly one ontology entity")
        return {
            "id": record["id"],
            "type": entity_ids[0],
            "ontology_entity_id": entity_ids[0],
            "properties": json.loads(record["properties_json"] or "{}"),
        }

    @staticmethod
    def _tx_get_node(tx: Any, node_id: str) -> dict[str, Any] | None:
        record = tx.run(
            """
            MATCH (n:ContextRecord {id: $id})
            OPTIONAL MATCH (n)-[:INSTANCE_OF]->(e:OntologyEntity {ontology_id: $ontology_id})
            RETURN n.id AS id, n.properties_json AS properties_json,
                   collect(e.id) AS entity_ids, n.namespace AS namespace,
                   n.created_at AS created_at
            """,
            id=node_id,
            ontology_id=ONTOLOGY_ID,
        ).single()
        node = Neo4jGraphDatabase._node_from_record(record)
        if node is not None:
            node["namespace"] = record["namespace"]
            node["created_at"] = record["created_at"]
        return node

    @staticmethod
    def _tx_get_schema(tx: Any, entity_id: str) -> dict[str, Any] | None:
        record = tx.run(
            """
            MATCH (e:OntologyEntity {id: $id, ontology_id: $ontology_id})
            RETURN e.id AS id, e.properties_json AS properties_json,
                   e.ontology_id AS ontology_id, e.abstract AS abstract,
                   e.data_scope AS data_scope
            """,
            id=entity_id,
            ontology_id=ONTOLOGY_ID,
        ).single()
        if record is None:
            return None
        return {
            "id": record["id"],
            "properties": json.loads(record["properties_json"] or "{}"),
            "ontology_id": record["ontology_id"],
            "abstract": bool(record["abstract"]),
            "data_scope": record["data_scope"],
        }

    @staticmethod
    def _tx_upsert_node(
        tx: Any,
        *,
        node_id: str,
        node_type: str,
        properties: dict[str, Any],
        now: str,
    ) -> None:
        record = tx.run(
            """
            MATCH (e:OntologyEntity {id: $entity_id, ontology_id: $ontology_id})
            MERGE (n:ContextRecord {id: $id})
            ON CREATE SET n.created_at = $now
            SET n.entity_id = $entity_id,
                n.properties_json = $properties_json,
                n.namespace = $namespace,
                n.updated_at = $now
            WITH n, e
            OPTIONAL MATCH (n)-[old:INSTANCE_OF]->(:OntologyEntity)
            DELETE old
            WITH DISTINCT n, e
            MERGE (n)-[:INSTANCE_OF]->(e)
            RETURN n.id AS id
            """,
            id=node_id,
            entity_id=node_type,
            ontology_id=ONTOLOGY_ID,
            properties_json=_json(properties),
            namespace=properties.get("namespace", ""),
            now=now,
        ).single()
        if record is None:
            raise ValueError(f"Entity '{node_type}' is not registered in ontology '{ONTOLOGY_ID}'")

    def create_node(
        self, node_id: str | None = None, node_type: str = "Generic", properties: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        node_id = node_id or str(uuid.uuid4())
        raw_properties = dict(properties or {})
        if "namespace" not in raw_properties and node_id.startswith("app:"):
            parts = node_id.split(":")
            if len(parts) >= 2:
                raw_properties["namespace"] = parts[1]
        validated = self.validate_properties(node_type, raw_properties)
        now = datetime.now(UTC).isoformat()
        self._write(
            lambda tx: self._tx_upsert_node(
                tx,
                node_id=node_id,
                node_type=node_type,
                properties=validated,
                now=now,
            )
        )
        return {"id": node_id, "type": node_type, "ontology_entity_id": node_type, "properties": validated}

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        node = self._read(lambda tx: self._tx_get_node(tx, node_id))
        if node is not None:
            node.pop("namespace", None)
            node.pop("created_at", None)
        return node

    def routing_snapshot(self, recent_per_type: int = 5) -> dict[str, Any]:
        """Return the same bounded Router context contract as the SQLite adapter."""

        def fetch(tx: Any) -> dict[str, Any]:
            type_counts = {
                record["type"]: record["count"]
                for record in tx.run(
                    """
                    MATCH (n:ContextRecord)-[:INSTANCE_OF]->
                          (e:OntologyEntity {ontology_id: $ontology_id})
                    RETURN e.id AS type, count(n) AS count
                    ORDER BY type
                    """,
                    ontology_id=ONTOLOGY_ID,
                )
            }
            node_record = tx.run("MATCH (n:ContextRecord) RETURN count(n) AS count").single()
            edge_record = tx.run(
                "MATCH (:ContextRecord)-[r:GRAPH_EDGE]->(:ContextRecord) RETURN count(r) AS count"
            ).single()
            recent_by_type: dict[str, list[dict[str, Any]]] = {}
            records = tx.run(
                """
                MATCH (n:ContextRecord)-[:INSTANCE_OF]->
                      (e:OntologyEntity {ontology_id: $ontology_id})
                RETURN n.id AS id, e.id AS type, n.properties_json AS properties_json,
                       n.created_at AS created_at
                ORDER BY n.created_at DESC, n.id ASC
                """,
                ontology_id=ONTOLOGY_ID,
            )
            for record in records:
                items = recent_by_type.setdefault(record["type"], [])
                if len(items) >= recent_per_type:
                    continue
                items.append(
                    {
                        "id": record["id"],
                        "type": record["type"],
                        "properties": json.loads(record["properties_json"] or "{}"),
                        "created_at": record["created_at"],
                    }
                )
            return {
                "type_counts": type_counts,
                "recent_nodes_by_type": recent_by_type,
                "node_count": node_record["count"] if node_record else 0,
                "edge_count": edge_record["count"] if edge_record else 0,
            }

        snapshot = self._read(fetch)
        snapshot["schema_manifest"] = self.list_schemas()
        return snapshot

    def update_node_property(self, node_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        node = self.get_node(node_id)
        if node is None:
            raise ValueError(f"Node with ID '{node_id}' does not exist.")
        validated = self.validate_properties(node["type"], {**node["properties"], **properties})
        now = datetime.now(UTC).isoformat()
        self._write(
            lambda tx: self._tx_upsert_node(
                tx,
                node_id=node_id,
                node_type=node["type"],
                properties=validated,
                now=now,
            )
        )
        return {"id": node_id, "type": node["type"], "ontology_entity_id": node["type"], "properties": validated}

    def delete_node(self, node_id: str) -> bool:
        def delete(tx: Any) -> bool:
            record = tx.run(
                """MATCH (n:ContextRecord {id: $id})
                   WITH n, count(n) AS found
                   DETACH DELETE n
                   RETURN found""",
                id=node_id,
            ).single()
            return bool(record and record["found"])

        return self._write(delete)

    @staticmethod
    def _edge_from_record(record: Any | None) -> dict[str, Any] | None:
        if record is None:
            return None
        return {
            "from_id": record["from_id"],
            "to_id": record["to_id"],
            "type": record["type"],
            "properties": json.loads(record["properties_json"] or "{}"),
        }

    @staticmethod
    def _tx_get_edge(tx: Any, from_id: str, to_id: str, edge_type: str) -> dict[str, Any] | None:
        record = tx.run(
            """
            MATCH (a:ContextRecord {id: $from_id})-[r:GRAPH_EDGE {edge_type: $edge_type}]->
                  (b:ContextRecord {id: $to_id})
            RETURN a.id AS from_id, b.id AS to_id, r.edge_type AS type,
                   r.properties_json AS properties_json, r.created_at AS created_at
            """,
            from_id=from_id,
            to_id=to_id,
            edge_type=edge_type,
        ).single()
        edge = Neo4jGraphDatabase._edge_from_record(record)
        if edge is not None:
            edge["created_at"] = record["created_at"]
        return edge

    @staticmethod
    def _tx_upsert_edge(
        tx: Any,
        *,
        from_id: str,
        to_id: str,
        edge_type: str,
        properties: dict[str, Any],
        now: str,
    ) -> None:
        record = tx.run(
            """
            MATCH (a:ContextRecord {id: $from_id}), (b:ContextRecord {id: $to_id})
            MERGE (a)-[r:GRAPH_EDGE {edge_type: $edge_type}]->(b)
            ON CREATE SET r.created_at = $now
            SET r.properties_json = $properties_json, r.updated_at = $now
            RETURN r.edge_type AS type
            """,
            from_id=from_id,
            to_id=to_id,
            edge_type=edge_type,
            properties_json=_json(properties),
            now=now,
        ).single()
        if record is None:
            raise ValueError("Cannot create an edge whose endpoint is missing")

    def create_edge(
        self, from_id: str, to_id: str, edge_type: str, properties: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if self.get_node(from_id) is None:
            raise ValueError(f"Source node '{from_id}' does not exist.")
        if self.get_node(to_id) is None:
            raise ValueError(f"Target node '{to_id}' does not exist.")
        edge_properties = dict(properties or {})
        now = datetime.now(UTC).isoformat()
        self._write(
            lambda tx: self._tx_upsert_edge(
                tx,
                from_id=from_id,
                to_id=to_id,
                edge_type=edge_type,
                properties=edge_properties,
                now=now,
            )
        )
        return {"from_id": from_id, "to_id": to_id, "type": edge_type, "properties": edge_properties}

    def get_edges(self, node_id: str) -> list[dict[str, Any]]:
        def fetch(tx: Any) -> list[dict[str, Any]]:
            result = tx.run(
                """
                MATCH (:ContextRecord {id: $id})-[r:GRAPH_EDGE]-(:ContextRecord)
                RETURN startNode(r).id AS from_id, endNode(r).id AS to_id,
                       r.edge_type AS type, r.properties_json AS properties_json
                ORDER BY from_id, to_id, type
                """,
                id=node_id,
            )
            return [self._edge_from_record(record) for record in result]

        return self._read(fetch)

    def delete_edge(self, from_id: str, to_id: str, edge_type: str) -> bool:
        def delete(tx: Any) -> bool:
            record = tx.run(
                """
                MATCH (a:ContextRecord {id: $from_id})-[r:GRAPH_EDGE {edge_type: $edge_type}]->
                      (b:ContextRecord {id: $to_id})
                WITH collect(r) AS relationships
                FOREACH (rel IN relationships | DELETE rel)
                RETURN size(relationships) AS removed
                """,
                from_id=from_id,
                to_id=to_id,
                edge_type=edge_type,
            ).single()
            return bool(record and record["removed"])

        return self._write(delete)

    @staticmethod
    def _tx_incident_edges(tx: Any, node_id: str) -> list[dict[str, Any]]:
        result = tx.run(
            """
            MATCH (:ContextRecord {id: $id})-[r:GRAPH_EDGE]-(:ContextRecord)
            RETURN startNode(r).id AS from_id, endNode(r).id AS to_id,
                   r.edge_type AS type, r.properties_json AS properties_json,
                   r.created_at AS created_at
            """,
            id=node_id,
        )
        edges: list[dict[str, Any]] = []
        for record in result:
            edge = Neo4jGraphDatabase._edge_from_record(record)
            edge["created_at"] = record["created_at"]
            edges.append(edge)
        return edges

    @staticmethod
    def _tx_delete_node(tx: Any, node_id: str) -> None:
        tx.run("MATCH (n:ContextRecord {id: $id}) DETACH DELETE n", id=node_id).consume()

    @staticmethod
    def _tx_delete_edge(tx: Any, from_id: str, to_id: str, edge_type: str) -> None:
        tx.run(
            """
            MATCH (a:ContextRecord {id: $from_id})-[r:GRAPH_EDGE {edge_type: $edge_type}]->
                  (b:ContextRecord {id: $to_id})
            DELETE r
            """,
            from_id=from_id,
            to_id=to_id,
            edge_type=edge_type,
        ).consume()

    def apply_actions_atomic(
        self,
        actions: list[dict[str, Any]],
        *,
        session_id: str | None = None,
        ticket_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(actions, list) or not actions:
            raise ValueError("Graph mutation must contain at least one action")
        if any(not isinstance(action, dict) for action in actions):
            raise ValueError("Every graph mutation action must be an object")

        prepared_actions = [dict(action) for action in actions]
        for action in prepared_actions:
            if action.get("action") == "create_node" and not action.get("id"):
                action["id"] = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        ticket_id = ticket_id or (f"tkt-{uuid.uuid4().hex[:12]}" if session_id else None)
        canonical_input = _json(actions)
        input_hash = hashlib.sha256(canonical_input.encode("utf-8")).hexdigest()

        def commit(tx: Any) -> dict[str, Any]:
            if idempotency_key:
                existing = tx.run(
                    """
                    MATCH (effect:GraphEffect {idempotency_key: $key})
                    RETURN effect.input_hash AS input_hash, effect.result_json AS result_json
                    """,
                    key=idempotency_key,
                ).single()
                if existing is not None:
                    if existing["input_hash"] != input_hash:
                        raise ValueError("Graph idempotency key was reused with different actions")
                    return json.loads(existing["result_json"])

            normalized: list[dict[str, Any]] = []
            reverse_actions: list[dict[str, Any]] = []
            snapshot_before: dict[str, Any] = {"nodes": {}, "edges": {}}

            for raw_action in prepared_actions:
                action = dict(raw_action)
                kind = self._required_text(action, "action")

                if kind == "create_node":
                    node_id = self._required_text(action, "id")
                    node_type = action.get("type", "Generic")
                    if not isinstance(node_type, str) or not node_type:
                        raise ValueError("create_node.type must be a string")
                    properties = action.get("properties") or {}
                    if not isinstance(properties, dict):
                        raise ValueError("create_node.properties must be an object")
                    if "namespace" not in properties and node_id.startswith("app:"):
                        parts = node_id.split(":")
                        if len(parts) >= 2:
                            properties = {**properties, "namespace": parts[1]}
                    schema = self._tx_get_schema(tx, node_type)
                    if schema is None:
                        raise ValueError(f"Entity '{node_type}' is not registered in ontology '{ONTOLOGY_ID}'")
                    validated = coerce_entity_properties(schema, properties)
                    old_node = self._tx_get_node(tx, node_id)
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
                    self._tx_upsert_node(
                        tx,
                        node_id=node_id,
                        node_type=node_type,
                        properties=validated,
                        now=now,
                    )
                    normalized.append({"action": kind, "id": node_id, "type": node_type, "properties": validated})

                elif kind in {"update_node_property", "replace_node"}:
                    node_id = self._required_text(action, "id")
                    old_node = self._tx_get_node(tx, node_id)
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
                    schema = self._tx_get_schema(tx, node_type)
                    if schema is None:
                        raise ValueError(f"Entity '{node_type}' is not registered in ontology '{ONTOLOGY_ID}'")
                    validated = coerce_entity_properties(schema, properties)
                    self._tx_upsert_node(
                        tx,
                        node_id=node_id,
                        node_type=node_type,
                        properties=validated,
                        now=now,
                    )
                    normalized.append({"action": kind, "id": node_id, "type": node_type, "properties": validated})

                elif kind == "delete_node":
                    node_id = self._required_text(action, "id")
                    old_node = self._tx_get_node(tx, node_id)
                    if old_node is None:
                        raise ValueError(f"Node with ID '{node_id}' does not exist")
                    edges = self._tx_incident_edges(tx, node_id)
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
                    self._tx_delete_node(tx, node_id)
                    normalized.append({"action": kind, "id": node_id})

                elif kind == "restore_node":
                    node_id = self._required_text(action, "id")
                    node_type = self._required_text(action, "type")
                    properties = action.get("properties") or {}
                    edges = action.get("edges") or []
                    if not isinstance(properties, dict) or not isinstance(edges, list):
                        raise ValueError("restore_node payload is malformed")
                    old_node = self._tx_get_node(tx, node_id)
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
                    schema = self._tx_get_schema(tx, node_type)
                    if schema is None:
                        raise ValueError(f"Entity '{node_type}' is not registered in ontology '{ONTOLOGY_ID}'")
                    validated = coerce_entity_properties(schema, properties)
                    self._tx_upsert_node(
                        tx,
                        node_id=node_id,
                        node_type=node_type,
                        properties=validated,
                        now=now,
                    )
                    for edge in edges:
                        from_id = self._required_text(edge, "from_id")
                        to_id = self._required_text(edge, "to_id")
                        edge_type = self._required_text(edge, "type")
                        if self._tx_get_node(tx, from_id) is None or self._tx_get_node(tx, to_id) is None:
                            raise ValueError("Cannot restore an edge whose endpoint is missing")
                        self._tx_upsert_edge(
                            tx,
                            from_id=from_id,
                            to_id=to_id,
                            edge_type=edge_type,
                            properties=edge.get("properties") or {},
                            now=now,
                        )
                    normalized.append(action)

                elif kind in {"create_edge", "delete_edge"}:
                    from_id = self._required_text(action, "from_id")
                    to_id = self._required_text(action, "to_id")
                    edge_type = self._required_text(action, "type")
                    old_edge = self._tx_get_edge(tx, from_id, to_id, edge_type)
                    edge_key = f"{from_id}\x1f{to_id}\x1f{edge_type}"
                    if old_edge:
                        snapshot_before["edges"][edge_key] = old_edge
                    if kind == "create_edge":
                        if self._tx_get_node(tx, from_id) is None:
                            raise ValueError(f"Source node '{from_id}' does not exist")
                        if self._tx_get_node(tx, to_id) is None:
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
                        self._tx_upsert_edge(
                            tx,
                            from_id=from_id,
                            to_id=to_id,
                            edge_type=edge_type,
                            properties=properties,
                            now=now,
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
                        self._tx_delete_edge(tx, from_id, to_id, edge_type)
                        normalized.append({"action": kind, "from_id": from_id, "to_id": to_id, "type": edge_type})
                else:
                    raise ValueError(f"Unsupported graph mutation action: {kind}")

            reverse_actions.reverse()
            if session_id and ticket_id:
                tx.run(
                    """
                    CREATE (history:GraphMutationHistory {
                        id: $id, session_id: $session_id,
                        forward_actions_json: $forward_actions_json,
                        reverse_actions_json: $reverse_actions_json,
                        snapshot_before_json: $snapshot_before_json,
                        pinned: false, created_at: $now
                    })
                    """,
                    id=ticket_id,
                    session_id=session_id,
                    forward_actions_json=_json(normalized),
                    reverse_actions_json=_json(reverse_actions),
                    snapshot_before_json=_json(snapshot_before),
                    now=now,
                ).consume()
            result = {
                "ticket_id": ticket_id,
                "actions": normalized,
                "reverse_actions": reverse_actions,
                "snapshot_before": snapshot_before,
            }
            if idempotency_key:
                tx.run(
                    """
                    CREATE (effect:GraphEffect {
                        idempotency_key: $key, input_hash: $input_hash,
                        result_json: $result_json, created_at: $now
                    })
                    """,
                    key=idempotency_key,
                    input_hash=input_hash,
                    result_json=_json(result),
                    now=now,
                ).consume()
            return result

        return self._write(commit)

    def preflight_actions(self, actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not isinstance(actions, list) or not actions:
            raise ValueError("Graph mutation must contain at least one action")
        if any(not isinstance(action, dict) for action in actions):
            raise ValueError("Every graph mutation action must be an object")
        nodes = {
            node_id: {"type": node["type"], "properties": dict(node["properties"])}
            for node_id, node in self.nodes.items()
        }
        edges = {(edge["from_id"], edge["to_id"], edge["type"]) for edge in self.edges}
        normalized: list[dict[str, Any]] = []
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
                validated = self.validate_properties(node_type, properties)
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
                validated = self.validate_properties(node["type"], {**node["properties"], **properties})
                nodes[node_id] = {"type": node["type"], "properties": validated}
                normalized.append(
                    {"action": kind, "id": node_id, "properties": {key: validated[key] for key in properties}}
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

    def effective_schemas(self, proposal: dict[str, Any] | None = None) -> list[dict[str, Any]]:
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
                item["id"], schema["properties"], item["extended_properties"]
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
        normalized = self._normalize_schema_proposal(proposal)
        self._validate_proposal_parents(normalized)
        input_hash = hashlib.sha256(_json(normalized).encode("utf-8")).hexdigest()
        now = datetime.now(UTC).isoformat()

        def commit(tx: Any) -> dict[str, Any]:
            if idempotency_key:
                existing = tx.run(
                    """
                    MATCH (effect:GraphEffect {idempotency_key: $key})
                    RETURN effect.input_hash AS input_hash, effect.result_json AS result_json
                    """,
                    key=idempotency_key,
                ).single()
                if existing:
                    if existing["input_hash"] != input_hash:
                        raise ValueError("Schema idempotency key was reused with a different proposal")
                    return json.loads(existing["result_json"])

            for item in normalized["new_schemas"]:
                if self._tx_full_schema(tx, item["id"]) is not None:
                    raise ValueError(
                        f"Ontology entity '{item['id']}' already exists; extend the canonical entity instead"
                    )

            snapshot: dict[str, Any] = {}
            for item in [*normalized["reused_schemas"], *normalized["new_schemas"]]:
                schema = self._tx_full_schema(tx, item["id"])
                snapshot[item["id"]] = schema

            for item in normalized["reused_schemas"]:
                schema = self._tx_full_schema(tx, item["id"])
                if schema is None:
                    raise ValueError(f"Reused schema '{item['id']}' does not exist")
                properties = self._merge_entity_properties(
                    item["id"], schema["properties"], item["extended_properties"]
                )
                tx.run(
                    """
                    MATCH (e:OntologyEntity {id: $id, ontology_id: $ontology_id})
                    SET e.properties_json = $properties_json
                    """,
                    id=item["id"],
                    ontology_id=ONTOLOGY_ID,
                    properties_json=_json(properties),
                ).consume()

            for item in normalized["new_schemas"]:
                tx.run(
                    """
                    MATCH (o:Ontology {id: $ontology_id})
                    MERGE (e:OntologyEntity {id: $entity.id})
                    ON CREATE SET e.created_at = $now
                    SET e.name = $entity.name,
                        e.description = $entity.description,
                        e.properties_json = $properties_json,
                        e.is_core = false,
                        e.ontology_id = $ontology_id,
                        e.ontology_iri = $entity.ontology_iri,
                        e.source = $entity.source,
                        e.equivalent_to = $entity.equivalent_to,
                        e.subclass_of = $entity.subclass_of,
                        e.abstract = $entity.abstract,
                        e.data_scope = $entity.data_scope
                    MERGE (e)-[:IN_ONTOLOGY]->(o)
                    WITH e
                    OPTIONAL MATCH (e)-[old:SUBCLASS_OF]->(:OntologyEntity)
                    DELETE old
                    WITH DISTINCT e
                    OPTIONAL MATCH (parent:OntologyEntity {id: $entity.subclass_of, ontology_id: $ontology_id})
                    FOREACH (_ IN CASE WHEN parent IS NULL THEN [] ELSE [1] END |
                        MERGE (e)-[:SUBCLASS_OF]->(parent))
                    """,
                    ontology_id=ONTOLOGY_ID,
                    entity=item,
                    properties_json=_json(item["properties"]),
                    now=now,
                ).consume()

            result = {"proposal": normalized, "snapshot": snapshot}
            if idempotency_key:
                tx.run(
                    """
                    CREATE (effect:GraphEffect {
                        idempotency_key: $key, input_hash: $input_hash,
                        result_json: $result_json, created_at: $now
                    })
                    """,
                    key=idempotency_key,
                    input_hash=input_hash,
                    result_json=_json(result),
                    now=now,
                ).consume()
            return result

        return self._write(commit)

    @classmethod
    def _tx_full_schema(cls, tx: Any, schema_id: str) -> dict[str, Any] | None:
        record = tx.run(
            f"""
            MATCH (e:OntologyEntity {{id: $id, ontology_id: $ontology_id}})
            RETURN {cls._schema_return()}
            """,
            id=schema_id,
            ontology_id=ONTOLOGY_ID,
        ).single()
        return cls._schema_from_record(record)

    def restore_schema_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> None:
        if not isinstance(snapshot, dict):
            raise ValueError("Schema snapshot must be an object")

        def restore(tx: Any) -> None:
            for schema_id, old in snapshot.items():
                if old is None:
                    in_use = tx.run(
                        """
                        MATCH (:ContextRecord)-[:INSTANCE_OF]->(e:OntologyEntity {id: $id})
                        RETURN count(*) AS count
                        """,
                        id=schema_id,
                    ).single()
                    if in_use and in_use["count"]:
                        raise ValueError(f"Cannot remove ontology entity '{schema_id}' while records use it")
                    tx.run(
                        "MATCH (e:OntologyEntity {id: $id, ontology_id: $ontology_id}) DETACH DELETE e",
                        id=schema_id,
                        ontology_id=ONTOLOGY_ID,
                    ).consume()
                    continue
                tx.run(
                    """
                    MATCH (o:Ontology {id: $ontology_id})
                    MERGE (e:OntologyEntity {id: $entity.id})
                    SET e.name = $entity.name,
                        e.description = $entity.description,
                        e.properties_json = $properties_json,
                        e.is_core = $entity.is_core,
                        e.ontology_id = $ontology_id,
                        e.ontology_iri = $entity.ontology_iri,
                        e.source = $entity.source,
                        e.equivalent_to = $entity.equivalent_to,
                        e.subclass_of = $entity.subclass_of,
                        e.abstract = $entity.abstract,
                        e.data_scope = $entity.data_scope
                    MERGE (e)-[:IN_ONTOLOGY]->(o)
                    WITH e
                    OPTIONAL MATCH (e)-[old_parent:SUBCLASS_OF]->(:OntologyEntity)
                    DELETE old_parent
                    WITH DISTINCT e
                    OPTIONAL MATCH (parent:OntologyEntity {id: $entity.subclass_of, ontology_id: $ontology_id})
                    FOREACH (_ IN CASE WHEN parent IS NULL THEN [] ELSE [1] END |
                        MERGE (e)-[:SUBCLASS_OF]->(parent))
                    """,
                    ontology_id=ONTOLOGY_ID,
                    entity=old,
                    properties_json=_json(old["properties"]),
                ).consume()
            if idempotency_key:
                tx.run(
                    "MATCH (effect:GraphEffect {idempotency_key: $key}) DELETE effect",
                    key=idempotency_key,
                ).consume()

        self._write(restore)

    @property
    def nodes(self) -> dict[str, dict[str, Any]]:
        def fetch(tx: Any) -> dict[str, dict[str, Any]]:
            result = tx.run(
                """
                MATCH (n:ContextRecord)
                OPTIONAL MATCH (n)-[:INSTANCE_OF]->(e:OntologyEntity {ontology_id: $ontology_id})
                WITH n, collect(e.id) AS entity_ids
                RETURN n.id AS id, n.properties_json AS properties_json, entity_ids
                ORDER BY id
                """,
                ontology_id=ONTOLOGY_ID,
            )
            nodes: dict[str, dict[str, Any]] = {}
            for record in result:
                node = self._node_from_record(record)
                nodes[node["id"]] = node
            return nodes

        return self._read(fetch)

    @property
    def edges(self) -> list[dict[str, Any]]:
        def fetch(tx: Any) -> list[dict[str, Any]]:
            result = tx.run(
                """
                MATCH (a:ContextRecord)-[r:GRAPH_EDGE]->(b:ContextRecord)
                RETURN a.id AS from_id, b.id AS to_id, r.edge_type AS type,
                       r.properties_json AS properties_json
                ORDER BY from_id, to_id, type
                """
            )
            return [self._edge_from_record(record) for record in result]

        return self._read(fetch)

    def save(self) -> None:
        Path(self.workspace_dir).mkdir(parents=True, exist_ok=True)
        with open(self.legacy_filepath, "w", encoding="utf-8") as file:
            json.dump({"nodes": self.nodes, "edges": self.edges}, file, indent=2, ensure_ascii=False)

    def record_mutation_history(
        self,
        ticket_id: str,
        session_id: str,
        forward_actions: list[dict[str, Any]],
        reverse_actions: list[dict[str, Any]],
        snapshot_before: dict[str, Any],
        pinned: bool = False,
    ) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()

        def upsert(tx: Any) -> None:
            tx.run(
                """
                MERGE (history:GraphMutationHistory {id: $id})
                ON CREATE SET history.created_at = $now
                SET history.session_id = $session_id,
                    history.forward_actions_json = $forward_actions_json,
                    history.reverse_actions_json = $reverse_actions_json,
                    history.snapshot_before_json = $snapshot_before_json,
                    history.pinned = $pinned
                """,
                id=ticket_id,
                session_id=session_id,
                forward_actions_json=_json(forward_actions or []),
                reverse_actions_json=_json(reverse_actions or []),
                snapshot_before_json=_json(snapshot_before or {}),
                pinned=bool(pinned),
                now=now,
            ).consume()

        self._write(upsert)
        return {
            "ticket_id": ticket_id,
            "session_id": session_id,
            "forward_actions": list(forward_actions or []),
            "reverse_actions": list(reverse_actions or []),
            "snapshot_before": dict(snapshot_before or {}),
            "pinned": bool(pinned),
        }

    def pin_mutation_history(self, ticket_id: str) -> bool:
        def pin(tx: Any) -> bool:
            record = tx.run(
                """
                MATCH (history:GraphMutationHistory {id: $id})
                SET history.pinned = true
                RETURN count(history) AS count
                """,
                id=ticket_id,
            ).single()
            return bool(record and record["count"])

        return self._write(pin)

    @staticmethod
    def _history_from_record(record: Any | None) -> dict[str, Any] | None:
        if record is None:
            return None
        return {
            "ticket_id": record["id"],
            "session_id": record["session_id"],
            "forward_actions": json.loads(record["forward_actions_json"] or "[]"),
            "reverse_actions": json.loads(record["reverse_actions_json"] or "[]"),
            "snapshot_before": json.loads(record["snapshot_before_json"] or "{}"),
            "pinned": bool(record["pinned"]),
            "consumed_at": record["consumed_at"],
            "created_at": record["created_at"],
        }

    @staticmethod
    def _history_return() -> str:
        return """history.id AS id, history.session_id AS session_id,
                  history.forward_actions_json AS forward_actions_json,
                  history.reverse_actions_json AS reverse_actions_json,
                  history.snapshot_before_json AS snapshot_before_json,
                  history.pinned AS pinned, history.consumed_at AS consumed_at,
                  history.created_at AS created_at"""

    def load_mutation_history(self, ticket_id: str) -> dict[str, Any] | None:
        return_clause = self._history_return()

        def fetch(tx: Any) -> dict[str, Any] | None:
            record = tx.run(
                f"""MATCH (history:GraphMutationHistory {{id: $id}})
                    RETURN {return_clause}""",
                id=ticket_id,
            ).single()
            return self._history_from_record(record)

        return self._read(fetch)

    def list_mutation_history(self, session_id: str) -> list[dict[str, Any]]:
        return_clause = self._history_return()

        def fetch(tx: Any) -> list[dict[str, Any]]:
            result = tx.run(
                f"""MATCH (history:GraphMutationHistory {{session_id: $session_id}})
                    RETURN {return_clause} ORDER BY created_at DESC""",
                session_id=session_id,
            )
            return [self._history_from_record(record) for record in result]

        return self._read(fetch)

    @staticmethod
    def _infer_property_type(value: Any) -> str:
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int):
            return "integer"
        if isinstance(value, float):
            return "number"
        return "string"

    def migrate_from_sqlite(self, sqlite_path: str | os.PathLike[str]) -> dict[str, int | bool]:
        """Idempotently import the legacy SQLite graph without deleting its source."""

        path = Path(sqlite_path)
        if not path.exists():
            return {"already_migrated": False, "schemas": 0, "nodes": 0, "edges": 0}
        resolved = str(path.resolve())
        marker_id = f"sqlite:{hashlib.sha256(resolved.encode('utf-8')).hexdigest()}"

        def marker_exists(tx: Any) -> bool:
            record = tx.run(
                "MATCH (migration:GraphMigration {id: $id}) RETURN count(migration) AS count",
                id=marker_id,
            ).single()
            return bool(record and record["count"])

        if self._read(marker_exists):
            return {"already_migrated": True, "schemas": 0, "nodes": 0, "edges": 0}

        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        try:
            tables = {
                row["name"]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            schema_rows = (
                connection.execute("SELECT * FROM graph_schemas").fetchall() if "graph_schemas" in tables else []
            )
            node_rows = connection.execute("SELECT * FROM graph_nodes").fetchall() if "graph_nodes" in tables else []
            edge_rows = connection.execute("SELECT * FROM graph_edges").fetchall() if "graph_edges" in tables else []

            definitions: dict[str, dict[str, Any]] = {}
            for row in schema_rows:
                raw = dict(row)
                original_id = str(raw["id"])
                schema_id, property_aliases = LEGACY_ENTITY_ALIASES.get(original_id, (original_id, {}))
                properties = json.loads(raw.get("properties") or "{}")
                for old_key, new_key in property_aliases.items():
                    if old_key in properties:
                        properties.setdefault(new_key, properties[old_key])
                        properties.pop(old_key, None)
                definition = definitions.setdefault(
                    schema_id,
                    {
                        "id": schema_id,
                        "name": raw.get("name") or schema_id,
                        "description": raw.get("description") or "Imported from the SQLite knowledge graph",
                        "properties": {},
                        "is_core": bool(raw.get("is_core")),
                        "ontology_iri": raw.get("ontology_iri") or f"urn:ambient:legacy:{schema_id}",
                        "source": raw.get("source") or "legacy-sqlite",
                        "equivalent_to": json.loads(raw.get("equivalent_to") or "[]"),
                        "subclass_of": raw.get("subclass_of") or "Thing",
                        "abstract": bool(raw.get("abstract")),
                    },
                )
                definition["properties"].update(properties)

            decoded_nodes: list[dict[str, Any]] = []
            for row in node_rows:
                raw = dict(row)
                original_type = str(raw.get("type") or "LegacyRecord")
                entity_id, property_aliases = LEGACY_ENTITY_ALIASES.get(original_type, (original_type, {}))
                properties = json.loads(raw.get("properties") or "{}")
                for old_key, new_key in property_aliases.items():
                    if old_key in properties:
                        properties.setdefault(new_key, properties[old_key])
                        properties.pop(old_key, None)
                decoded_nodes.append({"id": str(raw["id"]), "type": entity_id, "properties": properties})
                definition = definitions.setdefault(
                    entity_id,
                    {
                        "id": entity_id,
                        "name": entity_id,
                        "description": "Entity inferred during SQLite knowledge-graph migration",
                        "properties": {},
                        "is_core": False,
                        "ontology_iri": f"urn:ambient:legacy:{entity_id}",
                        "source": "legacy-sqlite",
                        "equivalent_to": [],
                        "subclass_of": "Thing",
                        "abstract": False,
                    },
                )
                for key, value in properties.items():
                    if key == "namespace":
                        continue
                    definition["properties"].setdefault(key, self._infer_property_type(value))

            current = {schema["id"]: schema for schema in self.list_schemas()}
            for schema_id, definition in definitions.items():
                existing = current.get(schema_id)
                if existing:
                    definition = {
                        **existing,
                        "properties": {**existing["properties"], **definition["properties"]},
                    }
                parent = definition.get("subclass_of")
                if schema_id != "Thing" and not parent:
                    parent = "Thing"
                self.register_schema(
                    schema_id=schema_id,
                    name=definition["name"],
                    description=definition["description"],
                    properties=definition["properties"],
                    is_core=definition.get("is_core", False),
                    ontology_iri=definition.get("ontology_iri"),
                    source=definition.get("source") or "legacy-sqlite",
                    equivalent_to=definition.get("equivalent_to") or [],
                    subclass_of=parent,
                    abstract=definition.get("abstract", False),
                )

            for node in decoded_nodes:
                self.create_node(node["id"], node["type"], node["properties"])
            for row in edge_rows:
                raw = dict(row)
                self.create_edge(
                    str(raw["from_id"]),
                    str(raw["to_id"]),
                    str(raw["type"]),
                    json.loads(raw.get("properties") or "{}"),
                )

            now = datetime.now(UTC).isoformat()

            def write_marker(tx: Any) -> None:
                tx.run(
                    """
                    CREATE (migration:GraphMigration {
                        id: $id, source: $source, completed_at: $now,
                        schema_count: $schema_count, node_count: $node_count,
                        edge_count: $edge_count
                    })
                    """,
                    id=marker_id,
                    source=resolved,
                    now=now,
                    schema_count=len(definitions),
                    node_count=len(decoded_nodes),
                    edge_count=len(edge_rows),
                ).consume()

            self._write(write_marker)
            return {
                "already_migrated": False,
                "schemas": len(definitions),
                "nodes": len(decoded_nodes),
                "edges": len(edge_rows),
            }
        finally:
            connection.close()
