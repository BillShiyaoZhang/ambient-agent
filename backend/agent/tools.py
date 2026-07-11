import inspect
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("agent.tools")


class ToolRegistry:
    """
    Hermes-inspired dynamic tool registration and execution manager.
    Parses function signatures and docstrings to build OpenAPI function schemas.
    """

    def __init__(self):
        self.tools: dict[str, Callable] = {}
        self.schemas: dict[str, dict[str, Any]] = {}

    def register(self, func: Callable) -> Callable:
        name = func.__name__
        self.tools[name] = func
        self.schemas[name] = self._generate_schema(func)
        logger.info(f"Registered tool: {name}")
        return func

    def _generate_schema(self, func: Callable) -> dict[str, Any]:
        name = func.__name__
        sig = inspect.signature(func)
        doc = func.__doc__ or ""

        doc_lines = [line.strip() for line in doc.split("\n") if line.strip()]
        description = doc_lines[0] if doc_lines else f"Execute tool function {func.__name__}"

        properties = {}
        required = []

        for param_name, param in sig.parameters.items():
            # Filter out context parameters that should be injected by the harness
            if param_name in ("self", "db_session", "session_id"):
                continue

            p_type = "string"
            if param.annotation == int:
                p_type = "integer"
            elif param.annotation == float:
                p_type = "number"
            elif param.annotation == bool:
                p_type = "boolean"
            elif param.annotation in (list, list, list[str], list[Any]):
                p_type = "array"
            elif param.annotation in (dict, dict, dict[str, Any]):
                p_type = "object"

            # Extract description from subsequent docstring lines if match parameter name
            param_desc = f"Parameter {param_name}"
            for line in doc_lines[1:]:
                if line.startswith(f":param {param_name}:") or line.startswith(f"{param_name}:"):
                    parts = line.split(":", 2)
                    if len(parts) >= 2:
                        param_desc = parts[-1].strip()
                        break

            properties[param_name] = {"type": p_type, "description": param_desc}

            if param.default == inspect.Parameter.empty:
                required.append(param_name)

        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {"type": "object", "properties": properties, "required": required},
            },
        }

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return list(self.schemas.values())

    async def execute(self, name: str, args: dict[str, Any], context: dict[str, Any] | None = None) -> Any:
        if name not in self.tools:
            raise ValueError(f"Tool '{name}' is not registered.")

        func = self.tools[name]
        sig = inspect.signature(func)
        merged_args = {}

        # Bind expected arguments
        for param_name in sig.parameters:
            if param_name in args:
                merged_args[param_name] = args[param_name]
            elif context and param_name in context:
                merged_args[param_name] = context[param_name]

        if inspect.iscoroutinefunction(func):
            return await func(**merged_args)
        else:
            return func(**merged_args)


# Instantiate a global registry for standard agent tools
registry = ToolRegistry()


# Example Tool definitions
@registry.register
def list_available_apps() -> list[str]:
    """
    Returns a list of IDs of all ambient widget applications currently configured on disk.
    """
    from backend.app_manager import AppManager

    mgr = AppManager()
    return [app["id"] for app in mgr.list_apps()]


@registry.register
def delete_widget_app(app_id: str) -> bool:
    """
    Deletes a widget application configuration from disk.
    :param app_id: The ID of the widget application to delete.
    """
    from backend.app_manager import AppManager

    mgr = AppManager()
    return mgr.delete_app(app_id)


@registry.register
def query_graph(query_json: str) -> str:
    """
    Query the global Knowledge Graph database using a declarative query.
    :param query_json: The declarative graph query in JSON string format.
    """
    import json

    from backend.graph_query_engine import execute_graph_query
    from backend.main import graph_db

    try:
        query = json.loads(query_json)
        res = execute_graph_query(query, graph_db)
        return json.dumps(res, ensure_ascii=False)
    except Exception as e:
        return f"Error executing query: {e!s}"


@registry.register
async def mutate_graph(actions_json: str) -> str:
    """
    Perform a batch of mutation actions on the global Knowledge Graph.
    :param actions_json: The list of actions in JSON string format. Actions can be create_node, update_node_property, delete_node, create_edge, delete_edge.
    """
    import json

    from backend.graph_subscription import subscription_manager
    from backend.main import graph_db

    try:
        actions = json.loads(actions_json)
        for action in actions:
            act_type = action.get("action")
            if act_type == "create_node":
                graph_db.create_node(
                    node_id=action.get("id"),
                    node_type=action.get("type", "Generic"),
                    properties=action.get("properties"),
                )
            elif act_type == "update_node_property":
                graph_db.update_node_property(node_id=action.get("id"), properties=action.get("properties"))
            elif act_type == "delete_node":
                graph_db.delete_node(node_id=action.get("id"))
            elif act_type == "create_edge":
                graph_db.create_edge(
                    from_id=action.get("from_id"),
                    to_id=action.get("to_id"),
                    edge_type=action.get("type"),
                    properties=action.get("properties"),
                )
            elif act_type == "delete_edge":
                graph_db.delete_edge(
                    from_id=action.get("from_id"), to_id=action.get("to_id"), edge_type=action.get("type")
                )

        # Broadcast updates
        async def send_ws(ws, payload):
            try:
                await ws.send_json(payload)
            except Exception:
                pass

        await subscription_manager.broadcast_updates(graph_db, send_ws)
        return "success"
    except Exception as e:
        return f"Error: {e!s}"
