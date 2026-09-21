"""Deterministic pod-to-trip assignment.

Kept separate from both routing and movement: this module decides *which* pod
takes a trip, asks M2's ``route_trip`` for the path (no routing logic is
reimplemented here), and hands the result to the pod.

POLICY (deliberately simple — global optimisation is out of scope for M3)
------------------------------------------------------------------------
A pod is eligible for a trip when all of these hold:

1. it is IDLE (not assigned, travelling, arrived or charging);
2. it is standing at the trip's origin node;
3. its available seats cover the party size;
4. its battery covers the route's estimated energy plus the configured reserve.

Among eligible pods the **lowest pod_id** wins. That makes assignment fully
reproducible and independent of dict ordering.

Condition 2 means M3 does no repositioning: a pod does not drive empty to a
pickup, so trips with no co-located pod are reported as unassigned rather than
quietly served. Repositioning and fleet rebalancing are fleet-optimisation
concerns deferred with the rest of M4.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.demand.models import TripRequest
from app.demand.trip_routing import route_trip
from app.errors import AssignmentError
from app.fleet.config import DEFAULT_FLEET_CONFIG, FleetConfig
from app.fleet.models import Pod
from app.fleet.movement import estimated_route_battery_percent
from app.fleet.pod_fleet import PodFleet
from app.models.route import Route
from app.network.graph import NetworkGraph

logger = logging.getLogger(__name__)

# Why an assignment attempt did not produce a pod.
NO_POD_AT_ORIGIN = "no idle pod at the trip origin"
NO_CAPACITY = "no idle pod at the origin has enough seats"
NO_BATTERY = "no eligible pod has enough battery for the route"


@dataclass(frozen=True)
class AssignmentResult:
    """What one assignment attempt produced."""

    trip_id: str
    pod_id: str | None = None
    route: Route | None = None
    reason: str | None = None

    @property
    def is_assigned(self) -> bool:
        return self.pod_id is not None

    @property
    def is_unroutable(self) -> bool:
        """No route exists at all — the trip can never be served, not just now."""
        return self.route is None and self.pod_id is None and self.reason is not None \
            and self.reason.startswith("unroutable:")


def candidate_pods(fleet: PodFleet, trip: TripRequest) -> tuple[Pod, ...]:
    """Idle pods standing at the trip's origin, in pod_id order."""
    return tuple(pod for pod in fleet.pods()
                 if pod.is_idle and pod.current_node_id == trip.origin_node_id)


def eligible_pods(fleet: PodFleet, graph: NetworkGraph, trip: TripRequest, route: Route,
                  config: FleetConfig = DEFAULT_FLEET_CONFIG) -> tuple[Pod, ...]:
    """Candidates that also satisfy capacity and battery, in pod_id order."""
    needed = estimated_route_battery_percent(graph, route, config) \
        + config.assignment_battery_reserve_percent
    return tuple(pod for pod in candidate_pods(fleet, trip)
                 if pod.can_accept(trip.party_size) and pod.battery_percent >= needed)


def select_pod(pods: tuple[Pod, ...]) -> Pod | None:
    """The deterministic choice: lowest pod_id. ``pods`` is already sorted."""
    return pods[0] if pods else None


def assign_trip(fleet: PodFleet, graph: NetworkGraph, trip: TripRequest,
                config: FleetConfig = DEFAULT_FLEET_CONFIG,
                algorithm: str = "astar") -> AssignmentResult:
    """Try to give ``trip`` a pod. Never raises for ordinary "no pod free" cases —
    those are reported in ``reason`` so the simulation can retry on a later tick.
    """
    if not isinstance(trip, TripRequest):
        raise AssignmentError(f"expected a TripRequest, got {type(trip).__name__}")

    routed = route_trip(graph, trip, algorithm)
    if not routed.is_routed:
        return AssignmentResult(trip_id=trip.trip_id,
                                reason=f"unroutable: {routed.unroutable_reason}")
    route = routed.route

    at_origin = candidate_pods(fleet, trip)
    if not at_origin:
        return AssignmentResult(trip_id=trip.trip_id, route=route, reason=NO_POD_AT_ORIGIN)
    with_seats = tuple(pod for pod in at_origin if pod.can_accept(trip.party_size))
    if not with_seats:
        return AssignmentResult(trip_id=trip.trip_id, route=route, reason=NO_CAPACITY)

    pod = select_pod(eligible_pods(fleet, graph, trip, route, config))
    if pod is None:
        return AssignmentResult(trip_id=trip.trip_id, route=route, reason=NO_BATTERY)

    fleet.assign_trip(pod.pod_id, trip.trip_id, route, trip.party_size)
    logger.debug("trip %s -> pod %s", trip.trip_id, pod.pod_id)
    return AssignmentResult(trip_id=trip.trip_id, pod_id=pod.pod_id, route=route)
