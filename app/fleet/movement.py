"""Deterministic pod movement along a route.

Movement reads its edge costs from M1's network — ``edge.current_travel_time_min``
and ``edge.congestion_multiplier``, both of which already include the congestion
model. The fleet defines no second congestion model and no second cost model.

WHEN COSTS ARE SAMPLED
----------------------
A pod samples an edge's travel time, distance and energy at the moment it
**enters** that edge, and those values hold for that traversal. So:

* congestion on an edge the pod has not yet reached DOES change its trip — the
  edge is sampled when the pod gets there;
* congestion on the edge the pod is already driving along does NOT retroactively
  change that traversal, because the pod is already on the road.

This is a deliberate, documented modelling choice, not an accident: it keeps a
traversal's cost well-defined without a continuous re-integration of traffic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.fleet.config import DEFAULT_FLEET_CONFIG, FleetConfig
from app.fleet.models import Pod, PodStatus
from app.models.route import Route
from app.network.graph import NetworkGraph

logger = logging.getLogger(__name__)

# Below this many minutes a remaining slice is treated as spent (float guard).
TIME_EPSILON_MIN = 1e-9


@dataclass(frozen=True)
class MovementEvent:
    """Something that happened to a pod during a tick, with the minute it happened."""

    kind: str          # "edge_completed" | "arrived"
    pod_id: str
    time_min: float
    edge_id: str | None = None
    node_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "pod_id": self.pod_id, "time_min": self.time_min,
                "edge_id": self.edge_id, "node_id": self.node_id}


def edge_sample(graph: NetworkGraph, edge_id: str,
                config: FleetConfig = DEFAULT_FLEET_CONFIG) -> tuple[float, float, float]:
    """(travel_time_min, distance_km, energy_kwh) for an edge under current traffic."""
    edge = graph.get_edge(edge_id)
    energy = config.energy_kwh_for(edge.distance_km, edge.congestion_multiplier)
    return edge.current_travel_time_min, edge.distance_km, energy


def estimated_route_energy_kwh(graph: NetworkGraph, route: Route,
                               config: FleetConfig = DEFAULT_FLEET_CONFIG) -> float:
    """Energy the whole route would take under *current* traffic.

    Used at assignment time to check a pod has enough charge. It is an estimate:
    traffic may change before the pod reaches a later edge.
    """
    return sum(edge_sample(graph, edge_id, config)[2] for edge_id in route.edge_ids)


def estimated_route_battery_percent(graph: NetworkGraph, route: Route,
                                    config: FleetConfig = DEFAULT_FLEET_CONFIG) -> float:
    return estimated_route_energy_kwh(graph, route, config) * config.percent_per_kwh


def start_pod_travel(graph: NetworkGraph, pod: Pod,
                     config: FleetConfig = DEFAULT_FLEET_CONFIG) -> None:
    """Move an ASSIGNED pod onto the first edge of its route.

    A zero-edge route cannot happen here: ``TripRequest`` forbids
    origin == destination, so every assigned route has at least one edge.
    """
    remaining = pod.remaining_edge_ids
    if not remaining:
        raise ValueError(f"pod {pod.pod_id!r} has no route edges to start on")
    time_min, distance_km, energy_kwh = edge_sample(graph, remaining[0], config)
    pod.start_travel(remaining[0], time_min, distance_km, energy_kwh)


def advance_pod(graph: NetworkGraph, pod: Pod, minutes: float, now_min: float,
                config: FleetConfig = DEFAULT_FLEET_CONFIG) -> tuple[float, tuple[MovementEvent, ...]]:
    """Advance one travelling pod by up to ``minutes``.

    Several edges may finish inside one tick, so the loop continues while time
    remains. Event times are exact rather than tick-quantised: the minutes
    actually consumed are added to ``now_min``.

    Returns (minutes_used, events).
    """
    if pod.status is not PodStatus.TRAVELING:
        return 0.0, ()

    used_total = 0.0
    remaining = float(minutes)
    events: list[MovementEvent] = []

    while remaining > TIME_EPSILON_MIN and pod.status is PodStatus.TRAVELING:
        used = pod.advance_on_edge(remaining)
        used_total += used
        remaining -= used

        if not pod.edge_is_complete:
            if used <= TIME_EPSILON_MIN:
                break        # nothing consumable this tick; avoid spinning
            continue

        finished_edge_id = pod.current_edge_id
        edge = graph.get_edge(finished_edge_id)
        _, energy_kwh = pod.complete_edge(edge.destination)
        pod.discharge(energy_kwh * config.percent_per_kwh)
        events.append(MovementEvent(kind="edge_completed", pod_id=pod.pod_id,
                                    time_min=now_min + used_total,
                                    edge_id=finished_edge_id, node_id=edge.destination))

        if pod.route_is_complete:
            pod.arrive()
            events.append(MovementEvent(kind="arrived", pod_id=pod.pod_id,
                                        time_min=now_min + used_total, node_id=pod.current_node_id))
            break

        # Sample the NEXT edge now, so congestion applied since departure counts.
        next_edge_id = pod.remaining_edge_ids[0]
        pod.enter_edge(next_edge_id, *edge_sample(graph, next_edge_id, config))

    return used_total, tuple(events)
