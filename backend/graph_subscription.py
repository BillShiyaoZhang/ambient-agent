import json
import logging
from typing import Any

from backend.graph_db import GraphDatabase
from backend.graph_query_engine import execute_graph_query

logger = logging.getLogger("graph_subscription")

class SubscriptionManager:
    def __init__(self):
        # Maps websocket -> {subscription_id: query}
        self.active_subscriptions: dict[Any, dict[str, dict]] = {}
        # Maps (websocket, subscription_id) -> last_seen_results_json
        self.last_results: dict[tuple[Any, str], str] = {}

    def register(self, websocket: Any, subscription_id: str, query: dict, db: GraphDatabase) -> dict:
        if websocket not in self.active_subscriptions:
            self.active_subscriptions[websocket] = {}
        self.active_subscriptions[websocket][subscription_id] = query

        # Execute immediately and return initial data to seed
        res = execute_graph_query(query, db)
        res_json = json.dumps(res, sort_keys=True)
        self.last_results[(websocket, subscription_id)] = res_json
        return res

    def unregister(self, websocket: Any, subscription_id: str):
        if websocket in self.active_subscriptions:
            self.active_subscriptions[websocket].pop(subscription_id, None)
        self.last_results.pop((websocket, subscription_id), None)

    def unregister_all(self, websocket: Any):
        subs = self.active_subscriptions.pop(websocket, None)
        if subs:
            for sub_id in subs.keys():
                self.last_results.pop((websocket, sub_id), None)

    async def broadcast_updates(self, db: GraphDatabase, send_json_fn: Any, mutated_types: set[str] | None = None):
        # Evaluate all subscriptions and push updates if the output changed
        for websocket, subs in list(self.active_subscriptions.items()):
            for sub_id, query in list(subs.items()):
                # Optimization check: skip executing query if the mutation doesn't affect its types
                if mutated_types is not None:
                    query_type = query.get("type")
                    includes = query.get("include", [])
                    include_types = {inc.get("target_type") for inc in includes if inc.get("target_type")}

                    # If query matches a specific type and that type isn't in mutated_types,
                    # and none of the included types are in mutated_types, we can skip
                    if query_type and query_type not in mutated_types and not (include_types & mutated_types):
                        continue

                try:
                    res = execute_graph_query(query, db)
                    res_json = json.dumps(res, sort_keys=True)
                    last_json = self.last_results.get((websocket, sub_id))

                    if res_json != last_json:
                        self.last_results[(websocket, sub_id)] = res_json
                        # Call the coroutine function to send update
                        await send_json_fn(websocket, {
                            "type": "graph_query_update",
                            "subscription_id": sub_id,
                            "data": res
                        })
                except Exception as e:
                    logger.error(f"Error executing graph query broadcast for {sub_id}: {e}")

# Instantiate global SubscriptionManager
subscription_manager = SubscriptionManager()
