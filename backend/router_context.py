from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from backend.app_manager import AppManager
from backend.graph_db import GraphDatabase


@dataclass
class GraphSnapshot:
    """Lightweight summary of current graph state for routing context."""

    type_counts: dict[str, int] = field(default_factory=dict)
    recent_nodes_by_type: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    schema_manifest: list[dict[str, Any]] = field(default_factory=list)
    node_count: int = 0
    edge_count: int = 0

    @classmethod
    def from_db(cls, db: GraphDatabase, recent_per_type: int = 5) -> "GraphSnapshot":
        """Build a snapshot from a GraphDatabase instance.

        ``recent_per_type`` caps how many nodes of each type we expose per type, to
        keep LLM prompt size bounded.
        """
        snap = cls()
        with db.get_conn() as conn:
            # Type counts
            count_rows = conn.execute(
                "SELECT type, COUNT(*) AS c FROM graph_nodes GROUP BY type"
            ).fetchall()
            snap.type_counts = {r["type"]: r["c"] for r in count_rows}

            # Total node + edge count for context
            tot = conn.execute("SELECT COUNT(*) AS c FROM graph_nodes").fetchone()
            snap.node_count = tot["c"] if tot else 0
            tot_e = conn.execute("SELECT COUNT(*) AS c FROM graph_edges").fetchone()
            snap.edge_count = tot_e["c"] if tot_e else 0

            # Recent nodes per type, ordered by created_at DESC
            recent_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
            rows = conn.execute(
                "SELECT id, type, properties, created_at FROM graph_nodes ORDER BY created_at DESC"
            ).fetchall()
            for row in rows:
                if len(recent_by_type[row["type"]]) >= recent_per_type:
                    continue
                import json

                recent_by_type[row["type"]].append(
                    {
                        "id": row["id"],
                        "type": row["type"],
                        "properties": json.loads(row["properties"]),
                        "created_at": row["created_at"],
                    }
                )
            snap.recent_nodes_by_type = dict(recent_by_type)

        snap.schema_manifest = db.list_schemas()
        return snap


@dataclass
class RouterContext:
    """Context assembled and given to the router before intent classification."""

    app_manifests: list[dict[str, Any]] = field(default_factory=list)
    graph_snapshot: GraphSnapshot = field(default_factory=GraphSnapshot)
    session_recent: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def build(
        cls,
        app_manager: AppManager,
        graph_db: GraphDatabase,
        session_messages: list[dict[str, Any]] | None = None,
        recent_messages_count: int = 5,
        recent_nodes_per_type: int = 5,
    ) -> "RouterContext":
        """Construct a RouterContext from live workspace state."""
        apps = app_manager.list_apps() or []
        snap = GraphSnapshot.from_db(graph_db, recent_per_type=recent_nodes_per_type)
        msgs = session_messages or []
        # Keep last N messages (already ordered chronologically by caller)
        recent = list(msgs[-recent_messages_count:]) if recent_messages_count > 0 else []
        return cls(app_manifests=apps, graph_snapshot=snap, session_recent=recent)

    def render_for_prompt(
        self,
        sections: list[str] | None = None,
        include_widget_keyword_hint: bool = False,
    ) -> str:
        """Render context into a human-readable block suitable for LLM prompts.

        Parameters
        ----------
        sections
            List of section names to include. ``None`` = include all (legacy).
            Available: ``widgets``, ``graph_counts``, ``recent_nodes``,
            ``schemas``, ``history``.
        include_widget_keyword_hint
            If True, append a hint that widget Titles may semantically match
            user keywords (e.g. 'My Clock' ↔ '时钟'). Used by C4 lenient variant.
        """
        if sections is None:
            sections = ["widgets", "graph_counts", "recent_nodes", "schemas", "history"]

        lines: list[str] = []

        if "widgets" in sections:
            lines.append("## Existing Widgets")
            if self.app_manifests:
                for app in self.app_manifests:
                    title = app.get("title", app.get("id"))
                    lines.append(f"- ID: `{app.get('id', '?')}`, Title: {title}")
                    if app.get("description"):
                        lines.append(f"  Description: {app['description']}")
                    if app.get("intents"):
                        lines.append(f"  Intents: {', '.join(app['intents'])}")
                    if app.get("schema_refs"):
                        lines.append(f"  Schema Refs: {', '.join(app['schema_refs'])}")
                if include_widget_keyword_hint:
                    lines.append(
                        "\n_Note: widget Titles may semantically match user keywords "
                        "(e.g. 'My Clock' ↔ '时钟', 'My Todo List' ↔ '待办')._"
                    )
            else:
                lines.append("- (None)")

        if "graph_counts" in sections:
            lines.append("")
            lines.append("## Graph State Summary")
            if self.graph_snapshot.type_counts:
                counts_str = ", ".join(
                    f"{k}={v}" for k, v in sorted(self.graph_snapshot.type_counts.items())
                )
                lines.append(f"- Node counts: {counts_str}")
            else:
                lines.append("- (no nodes yet)")
            lines.append(
                f"- Total: {self.graph_snapshot.node_count} nodes, "
                f"{self.graph_snapshot.edge_count} edges"
            )

        if "recent_nodes" in sections:
            lines.append("")
            lines.append("## Recent Nodes (by type)")
            if self.graph_snapshot.recent_nodes_by_type:
                for t, nodes in sorted(self.graph_snapshot.recent_nodes_by_type.items()):
                    lines.append(f"- Type `{t}`:")
                    for n in nodes:
                        lines.append(f"  - id=`{n['id']}`, properties={n['properties']}")
            else:
                lines.append("- (none)")

        if "schemas" in sections:
            lines.append("")
            lines.append("## Registered Schemas")
            if self.graph_snapshot.schema_manifest:
                for s in self.graph_snapshot.schema_manifest:
                    props_str = ", ".join(
                        f"{k}:{v}" for k, v in (s.get("properties") or {}).items()
                    )
                    lines.append(f"- `{s['id']}` — {s.get('description', '')} [{props_str}]")
            else:
                lines.append("- (none)")

        if "history" in sections and self.session_recent:
            lines.append("")
            lines.append("## Recent Conversation")
            for m in self.session_recent:
                role = m.get("role", "?")
                content = (m.get("content") or "")[:200]
                lines.append(f"- [{role}] {content}")

        return "\n".join(lines)
