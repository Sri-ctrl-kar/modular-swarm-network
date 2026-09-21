"""Deterministic demand metrics.

Averages are rounded to a deliberately modest number of decimals: this is a
synthetic model, and reporting a party size to ten decimal places would be fake
precision. Every collection is sorted, so two identical runs produce identical
metrics objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.demand.generator import GeneratedDemand
from app.demand.trip_routing import RoutedTrip

PARTY_SIZE_DECIMALS = 3
DISTANCE_DECIMALS = 3
TIME_DECIMALS = 2


def _mean(values: Sequence[float], decimals: int) -> float | None:
    """Rounded mean, or None for an empty input (rather than a misleading 0.0)."""
    if not values:
        return None
    return round(sum(values) / len(values), decimals)


@dataclass(frozen=True)
class DemandMetrics:
    """Aggregate view of one demand set, optionally including routing results."""

    profile_id: str
    seed: int
    total_passengers: int
    total_trip_requests: int
    total_passenger_volume: int
    unique_origins: int
    unique_destinations: int
    od_pairs_used: int
    average_party_size: float | None
    top_od_pairs: tuple[tuple[str, str, int], ...]
    demand_by_time_bucket: tuple[tuple[str, int], ...]
    trips_by_time_bucket: tuple[tuple[str, int], ...]
    demand_by_node: tuple[tuple[str, int, int], ...]
    # Routing metrics: present only when routed trips were supplied.
    routed_trips: int | None = None
    unroutable_trips: int | None = None
    average_route_distance_km: float | None = None
    average_route_travel_time_min: float | None = None
    average_free_flow_travel_time_min: float | None = None

    @property
    def peak_bucket(self) -> str | None:
        """Busiest time bucket by passenger volume; ties break on name."""
        if not self.demand_by_time_bucket:
            return None
        return min(self.demand_by_time_bucket, key=lambda item: (-item[1], item[0]))[0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "seed": self.seed,
            "total_passengers": self.total_passengers,
            "total_trip_requests": self.total_trip_requests,
            "total_passenger_volume": self.total_passenger_volume,
            "unique_origins": self.unique_origins,
            "unique_destinations": self.unique_destinations,
            "od_pairs_used": self.od_pairs_used,
            "average_party_size": self.average_party_size,
            "peak_bucket": self.peak_bucket,
            "top_od_pairs": [list(pair) for pair in self.top_od_pairs],
            "demand_by_time_bucket": dict(self.demand_by_time_bucket),
            "trips_by_time_bucket": dict(self.trips_by_time_bucket),
            "demand_by_node": [list(row) for row in self.demand_by_node],
            "routed_trips": self.routed_trips,
            "unroutable_trips": self.unroutable_trips,
            "average_route_distance_km": self.average_route_distance_km,
            "average_route_travel_time_min": self.average_route_travel_time_min,
            "average_free_flow_travel_time_min": self.average_free_flow_travel_time_min,
        }


def demand_by_node(demand: GeneratedDemand) -> tuple[tuple[str, int, int], ...]:
    """(node_id, passengers produced, passengers attracted) for every node that
    appears in the demand, sorted by node id."""
    produced = dict(demand.matrix.demand_by_origin())
    attracted = dict(demand.matrix.demand_by_destination())
    node_ids = sorted(set(produced) | set(attracted))
    return tuple((node_id, produced.get(node_id, 0), attracted.get(node_id, 0)) for node_id in node_ids)


def compute_metrics(demand: GeneratedDemand, routed: Sequence[RoutedTrip] | None = None,
                    *, top_n: int = 5) -> DemandMetrics:
    """Summarise a demand set; pass ``routed`` to include routing metrics."""
    matrix = demand.matrix
    party_sizes = [trip.party_size for trip in demand.trips]

    routed_count = unroutable_count = None
    avg_distance = avg_time = avg_free_flow = None
    if routed is not None:
        successful = [item for item in routed if item.is_routed]
        routed_count = len(successful)
        unroutable_count = len(routed) - routed_count
        avg_distance = _mean([item.route.total_distance_km for item in successful], DISTANCE_DECIMALS)
        avg_time = _mean([item.route.total_travel_time_min for item in successful], TIME_DECIMALS)
        avg_free_flow = _mean([item.free_flow_travel_time_min for item in successful], TIME_DECIMALS)

    return DemandMetrics(
        profile_id=demand.profile_id,
        seed=demand.seed,
        total_passengers=len(demand.passengers),
        total_trip_requests=len(demand.trips),
        total_passenger_volume=matrix.total_demand,
        unique_origins=len(matrix.origins()),
        unique_destinations=len(matrix.destinations()),
        od_pairs_used=matrix.pair_count,
        average_party_size=_mean(party_sizes, PARTY_SIZE_DECIMALS),
        top_od_pairs=tuple((c.origin, c.destination, c.passengers) for c in matrix.top_pairs(top_n)),
        demand_by_time_bucket=demand.demand_by_time_bucket(),
        trips_by_time_bucket=demand.trips_by_time_bucket(),
        demand_by_node=demand_by_node(demand),
        routed_trips=routed_count,
        unroutable_trips=unroutable_count,
        average_route_distance_km=avg_distance,
        average_route_travel_time_min=avg_time,
        average_free_flow_travel_time_min=avg_free_flow,
    )
