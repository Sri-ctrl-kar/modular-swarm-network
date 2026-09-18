"""Dijkstra's algorithm (binary heap + adjacency lists, O((V+E) log V))."""

from __future__ import annotations

import heapq
import logging
import math

from app.errors import NoRouteError
from app.models.edge import Edge
from app.models.route import Route
from app.network.graph import NetworkGraph
from app.routing.costs import TRAVEL_TIME, CostModel, checked_edge_cost, reconstruct_route, validate_endpoints

logger = logging.getLogger(__name__)
ALGORITHM_NAME = "dijkstra"


def dijkstra(graph: NetworkGraph, origin: str, destination: str,
             cost_model: CostModel = TRAVEL_TIME) -> Route:
    """Return the minimum-cost route. Heap ties break on node id (deterministic)."""
    validate_endpoints(graph, origin, destination)
    best: dict[str, float] = {origin: 0.0}
    predecessor: dict[str, Edge] = {}
    settled: set[str] = set()
    heap: list[tuple[float, str]] = [(0.0, origin)]

    while heap:
        cost, node = heapq.heappop(heap)
        if node in settled:
            continue
        settled.add(node)
        if node == destination:
            break
        for edge in graph.outgoing_edges(node):
            nxt = edge.destination
            if nxt in settled:
                continue
            candidate = cost + checked_edge_cost(cost_model, edge)
            if candidate < best.get(nxt, math.inf):
                best[nxt] = candidate
                predecessor[nxt] = edge
                heapq.heappush(heap, (candidate, nxt))

    if destination not in settled:
        raise NoRouteError(f"no route from {origin!r} to {destination!r}")
    route = reconstruct_route(origin, destination, predecessor, cost_model=cost_model,
                              nodes_visited=len(settled), algorithm=ALGORITHM_NAME)
    logger.info("dijkstra %s->%s cost=%.4f visited=%d", origin, destination, route.total_cost, len(settled))
    return route
