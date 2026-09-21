"""Milestone 2: deterministic SYNTHETIC passenger demand.

*** ALL DEMAND DATA IS SYNTHETIC. This is a scenario model for simulation and is
    NOT calibrated to real-world city mobility data. ***

This package sits on top of the M1 foundation and answers: where are people
trying to travel, when, and how much demand is there between origins and
destinations? It generates passengers and trip requests, aggregates them into an
origin-destination matrix, and routes them through the existing network.

It consumes the layers below (models, network, routing, simulation) and is
imported by none of them. No vehicles or pods exist here — that is M3.
"""

from __future__ import annotations

from app.demand.config import (
    BASELINE_DEMAND_PROFILE,
    DEMAND_PROFILES,
    DEMAND_PROVENANCE,
    DEFAULT_DEMAND_SEED,
    PEAK_HOUR_DEMAND_PROFILE,
    DemandProfile,
    PlaceRole,
    TimeBucket,
    TripPurpose,
)
from app.demand.generator import (
    DemandGenerator,
    GeneratedDemand,
    buckets_within_horizon,
    generate_demand,
)
from app.demand.metrics import DemandMetrics, compute_metrics, demand_by_node
from app.demand.models import DemandMatrix, DemandSnapshot, ODCell, Passenger, TripRequest
from app.demand.profile_io import (
    BASELINE_DEMAND_PATH,
    DEMAND_SCENARIO_PATHS,
    PEAK_HOUR_DEMAND_PATH,
    dump_demand_profile,
    load_demand_profile,
    resolve_demand_profile,
)
from app.demand.trip_routing import RoutedTrip, free_flow_time_min, route_trip, route_trips

__all__ = [
    "BASELINE_DEMAND_PATH", "BASELINE_DEMAND_PROFILE", "DEFAULT_DEMAND_SEED", "DEMAND_PROFILES",
    "DEMAND_PROVENANCE", "DEMAND_SCENARIO_PATHS", "DemandGenerator", "DemandMatrix", "DemandMetrics",
    "DemandProfile", "DemandSnapshot", "GeneratedDemand", "ODCell", "PEAK_HOUR_DEMAND_PATH",
    "PEAK_HOUR_DEMAND_PROFILE", "Passenger", "PlaceRole", "RoutedTrip", "TimeBucket", "TripPurpose",
    "TripRequest", "buckets_within_horizon", "compute_metrics", "demand_by_node", "dump_demand_profile", "free_flow_time_min",
    "generate_demand", "load_demand_profile", "resolve_demand_profile", "route_trip", "route_trips",
]
