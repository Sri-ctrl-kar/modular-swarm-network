"""A* search with an admissible, consistent geographic heuristic.

See ``app.routing.costs`` for the admissibility proof.
"""

from __future__ import annotations

import heapq
import logging
import math

from app.errors import NoRouteError
from app.models.edge import Edge
from app.models.route import Route
from app.network.graph import NetworkGraph
from app.routing.costs import (
    TRAVEL_TIME,
    CostModel,
    checked_edge_cost,
    checked_heuristic,
    reconstruct_route,
    validate_endpoints,
)

logger = logging.getLogger(__name__)
ALGORITHM_NAME = "astar"


def astar(graph: NetworkGraph, origin: str, destination: str,
          cost_model: CostModel = TRAVEL_TIME) -> Route:
    validate_endpoints(graph, origin, destination)
    heuristic = cost_model.heuristic_factory(graph, destination)
    g_score: dict[str, float] = {origin: 0.0}
    predecessor: dict[str, Edge] = {}
    closed: set[str] = set()
    heap: list[tuple[float, str]] = [(checked_heuristic(heuristic(origin), origin), origin)]

    while heap:
        _, node = heapq.heappop(heap)
        if node in closed:
            continue
        closed.add(node)
        if node == destination:
            break
        g_node = g_score[node]
        for edge in graph.outgoing_edges(node):
            nxt = edge.destination
            if nxt in closed:
                continue
            candidate = g_node + checked_edge_cost(cost_model, edge)
            if candidate < g_score.get(nxt, math.inf):
                g_score[nxt] = candidate
                predecessor[nxt] = edge
                heapq.heappush(heap, (candidate + checked_heuristic(heuristic(nxt), nxt), nxt))

    if destination not in closed:
        raise NoRouteError(f"no route from {origin!r} to {destination!r}")
    route = reconstruct_route(origin, destination, predecessor, cost_model=cost_model,
                              nodes_visited=len(closed), algorithm=ALGORITHM_NAME)
    logger.info("astar %s->%s cost=%.4f visited=%d", origin, destination, route.total_cost, len(closed))
    return route
