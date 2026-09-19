"""Edge cost models, admissible heuristics and shared routing helpers.

WHY THE TIME HEURISTIC IS ADMISSIBLE (and consistent)
-----------------------------------------------------
Let d(u,v) be straight-line (Haversine) distance, V the network speed ceiling,
and k = 0.999 * 60 / V.  ``NetworkGraph.add_edge`` enforces for every edge e=(u,v):
    road_distance(e) >= d(u,v)             and    road_distance(e)/base_time(e) <= V
and the congestion multiplier is always >= 1, so
    current_time(e) >= base_time(e) >= road_distance(e)*60/V >= d(u,v)*60/V > k*d(u,v).
Because d obeys the triangle inequality, h(n) = k*d(n, goal) satisfies
    h(u) <= current_time(u,v) + h(v)            (consistency)
which implies h never overestimates the remaining travel time (admissibility).

The distance heuristic uses the same argument with road_distance >= d.
A *custom* cost function gets the zero heuristic unless a proven one is supplied.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Mapping

from app.config import HEURISTIC_SAFETY_FACTOR
from app.errors import InvalidCostError, RoutingError
from app.models.edge import Edge
from app.models.route import Route
from app.network.geo import node_distance_km
from app.network.graph import NetworkGraph

EdgeCostFn = Callable[[Edge], float]
Heuristic = Callable[[str], float]
HeuristicFactory = Callable[[NetworkGraph, str], Heuristic]


@dataclass(frozen=True)
class CostModel:
    name: str
    edge_cost: EdgeCostFn
    heuristic_factory: HeuristicFactory


def travel_time_cost(edge: Edge) -> float:
    return edge.current_travel_time_min


def distance_cost(edge: Edge) -> float:
    return edge.distance_km


def zero_heuristic_factory(graph: NetworkGraph, goal: str) -> Heuristic:
    return lambda node_id: 0.0


def time_heuristic_factory(graph: NetworkGraph, goal: str) -> Heuristic:
    goal_node = graph.get_node(goal)
    minutes_per_km = HEURISTIC_SAFETY_FACTOR * 60.0 / graph.max_speed_kmh
    return lambda node_id: node_distance_km(graph.get_node(node_id), goal_node) * minutes_per_km


def distance_heuristic_factory(graph: NetworkGraph, goal: str) -> Heuristic:
    goal_node = graph.get_node(goal)
    return lambda node_id: node_distance_km(graph.get_node(node_id), goal_node) * HEURISTIC_SAFETY_FACTOR


TRAVEL_TIME = CostModel("travel_time_min", travel_time_cost, time_heuristic_factory)
DISTANCE = CostModel("distance_km", distance_cost, distance_heuristic_factory)


def custom_cost_model(name: str, edge_cost: EdgeCostFn) -> CostModel:
    """Wrap an arbitrary cost function with the always-safe zero heuristic."""
    return CostModel(name, edge_cost, zero_heuristic_factory)


def checked_edge_cost(model: CostModel, edge: Edge) -> float:
    value = model.edge_cost(edge)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise InvalidCostError(f"cost model {model.name!r} returned invalid cost {value!r} for edge {edge.edge_id!r}")
    return float(value)


def checked_heuristic(value: float, node_id: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise InvalidCostError(f"heuristic returned invalid value {value!r} for node {node_id!r}")
    return float(value)


def validate_endpoints(graph: NetworkGraph, origin: str, destination: str) -> None:
    graph.get_node(origin)       # raises NodeNotFoundError
    graph.get_node(destination)  # raises NodeNotFoundError


def reconstruct_route(
    origin: str,
    destination: str,
    predecessor: Mapping[str, Edge],
    *,
    cost_model: CostModel,
    nodes_visited: int,
    algorithm: str,
) -> Route:
    path_edges: list[Edge] = []
    node = destination
    while node != origin:
        edge = predecessor.get(node)
        if edge is None:  # defensive; algorithms only call this on success
            raise RoutingError(f"broken predecessor chain at {node!r}")
        path_edges.append(edge)
        node = edge.source
    path_edges.reverse()
    return Route(
        origin=origin,
        destination=destination,
        node_ids=(origin, *(e.destination for e in path_edges)),
        edge_ids=tuple(e.edge_id for e in path_edges),
        total_distance_km=math.fsum(e.distance_km for e in path_edges),
        total_travel_time_min=math.fsum(e.current_travel_time_min for e in path_edges),
        total_cost=math.fsum(checked_edge_cost(cost_model, e) for e in path_edges),
        cost_metric=cost_model.name,
        nodes_visited=nodes_visited,
        algorithm=algorithm,
    )
