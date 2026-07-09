import inspect
import logging
from typing import Callable, Any, Dict, List, Optional
from sqlmodel import Session

logger = logging.getLogger("agent.tools")

class ToolRegistry:
    """
    Hermes-inspired dynamic tool registration and execution manager.
    Parses function signatures and docstrings to build OpenAPI function schemas.
    """
    def __init__(self):
        self.tools: Dict[str, Callable] = {}
        self.schemas: Dict[str, Dict[str, Any]] = {}

    def register(self, func: Callable) -> Callable:
        name = func.__name__
        self.tools[name] = func
        self.schemas[name] = self._generate_schema(func)
        logger.info(f"Registered tool: {name}")
        return func

    def _generate_schema(self, func: Callable) -> Dict[str, Any]:
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
            elif param.annotation in (list, List, List[str], List[Any]):
                p_type = "array"
            elif param.annotation in (dict, Dict, Dict[str, Any]):
                p_type = "object"
                
            # Extract description from subsequent docstring lines if match parameter name
            param_desc = f"Parameter {param_name}"
            for line in doc_lines[1:]:
                if line.startswith(f":param {param_name}:") or line.startswith(f"{param_name}:"):
                    parts = line.split(":", 2)
                    if len(parts) >= 2:
                        param_desc = parts[-1].strip()
                        break
                        
            properties[param_name] = {
                "type": p_type,
                "description": param_desc
            }
            
            if param.default == inspect.Parameter.empty:
                required.append(param_name)
                
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required
                }
            }
        }
        
    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return list(self.schemas.values())
        
    async def execute(self, name: str, args: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Any:
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
def list_available_apps() -> List[str]:
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
