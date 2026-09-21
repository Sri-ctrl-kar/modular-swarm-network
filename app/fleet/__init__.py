"""Milestone 3: deterministic SYNTHETIC autonomous electric pod fleet.

*** ALL FLEET DATA IS SYNTHETIC. The pods, capacities, energy model and charging
    behaviour are invented for prototyping; the battery model is an explicit
    simulation approximation, not physics. ***

This package is the physical fleet layer on top of M2's demand:

    demand generator -> TripRequests -> router -> pod assignment
    -> pod movement -> trip completion

**M3 models pods independently. Swarm/platoon formation is intentionally
deferred to M4.** There is no grouping, no platooning and no magnetic linking
here, and no pod status describes one.

It consumes the layers below (models, network, routing, simulation, demand) and
is imported by none of them.
"""

from __future__ import annotations

from app.fleet.assignment import (
    NO_BATTERY,
    NO_CAPACITY,
    NO_POD_AT_ORIGIN,
    AssignmentResult,
    assign_trip,
    candidate_pods,
    eligible_pods,
    select_pod,
)
from app.fleet.config import (
    DEFAULT_FLEET_CONFIG,
    DEFAULT_FLEET_SEED,
    DEPOT_ROLE_WEIGHTS,
    FLEET_PROVENANCE,
    FleetConfig,
)
from app.fleet.generator import depot_weights, generate_fleet, pod_id_for
from app.fleet.metrics import FleetMetrics, compute_fleet_metrics
from app.fleet.models import (
    ALLOWED_TRANSITIONS,
    TIMED_OUT_PREFIX,
    UNROUTABLE_PREFIX,
    FleetSnapshot,
    Pod,
    PodStatus,
    TripRecord,
    TripStatus,
)
from app.fleet.movement import (
    MovementEvent,
    advance_pod,
    edge_sample,
    estimated_route_battery_percent,
    estimated_route_energy_kwh,
    start_pod_travel,
)
from app.fleet.pod_fleet import PodFleet
from app.fleet.simulation import FleetSimulation, RunReport, TickReport

__all__ = [
    "ALLOWED_TRANSITIONS", "AssignmentResult", "DEFAULT_FLEET_CONFIG", "DEFAULT_FLEET_SEED",
    "DEPOT_ROLE_WEIGHTS", "FLEET_PROVENANCE", "FleetConfig", "FleetMetrics", "FleetSimulation",
    "FleetSnapshot", "MovementEvent", "NO_BATTERY", "NO_CAPACITY", "NO_POD_AT_ORIGIN", "Pod",
    "PodFleet", "PodStatus", "RunReport", "TIMED_OUT_PREFIX", "TickReport", "TripRecord",
    "TripStatus", "UNROUTABLE_PREFIX", "advance_pod", "assign_trip", "candidate_pods",
    "compute_fleet_metrics", "depot_weights", "edge_sample", "eligible_pods",
    "estimated_route_battery_percent", "estimated_route_energy_kwh", "generate_fleet",
    "pod_id_for", "select_pod", "start_pod_travel",
]
