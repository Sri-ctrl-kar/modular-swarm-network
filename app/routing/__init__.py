"""Routing algorithms sharing one Route result type."""

from __future__ import annotations

from typing import Callable

from app.models.route import Route
from app.network.graph import NetworkGraph
from app.routing.astar import astar
from app.routing.costs import DISTANCE, TRAVEL_TIME, CostModel, custom_cost_model
from app.routing.dijkstra import dijkstra

ALGORITHMS: dict[str, Callable[..., Route]] = {"astar": astar, "dijkstra": dijkstra}
ALGORITHM_LABELS = {"astar": "A*", "dijkstra": "Dijkstra"}


def find_route(graph: NetworkGraph, origin: str, destination: str,
               algorithm: str = "astar", cost_model: CostModel = TRAVEL_TIME) -> Route:
    try:
        func = ALGORITHMS[algorithm]
    except KeyError:
        raise ValueError(f"unknown algorithm {algorithm!r}; choose from {sorted(ALGORITHMS)}") from None
    return func(graph, origin, destination, cost_model)


__all__ = ["ALGORITHMS", "ALGORITHM_LABELS", "CostModel", "DISTANCE", "TRAVEL_TIME",
           "astar", "custom_cost_model", "dijkstra", "find_route"]
