"""Deterministic SYNTHETIC passenger and trip generation (M2).

*** ALL DEMAND DATA IS SYNTHETIC and is not calibrated to real mobility data. ***

The only source of randomness is a local ``random.Random(seed)``. The global
``random`` module is never touched, and the RNG is consumed in a fixed order, so
identical inputs always give byte-identical output — the same guarantee M1's
synthetic city makes.

RNG CONSUMPTION ORDER (fixed; changing it changes every generated scenario)
--------------------------------------------------------------------------
Phase 1, per passenger i in 0..N-1:   home node, then purpose.
Phase 2, per trip:                    time bucket, request time, party size,
                                      then origin and/or destination.

Every weighted draw walks a list that is sorted by key, so the result never
depends on dict iteration order or on the graph's node insertion order.

The model itself (role weights, gravity decay, commuter anchoring, party sizes)
is documented in ``app/demand/config.py``.
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass
from typing import Any, Sequence

from app.demand.config import (
    BASELINE_DEMAND_PROFILE,
    DEFAULT_DEMAND_SEED,
    MINUTES_PER_DAY,
    DemandProfile,
    PlaceRole,
    TimeBucket,
    TripPurpose,
)
from app.demand.models import DemandMatrix, DemandSnapshot, Passenger, TripRequest
from app.errors import DemandGenerationError, DemandProfileError
from app.network.geo import node_distance_km
from app.network.graph import NetworkGraph

logger = logging.getLogger(__name__)

# Request times are reported to this many decimal places. One tenth of a minute
# (6 seconds) is as much precision as a synthetic model can honestly claim.
REQUEST_TIME_DECIMALS = 1


def _weighted_choice(rng: random.Random, weighted: Sequence[tuple[Any, float]], label: str) -> Any:
    """Pick one key. ``weighted`` must already be in a deterministic order."""
    total = math.fsum(weight for _, weight in weighted)
    if not weighted or total <= 0.0:
        raise DemandGenerationError(f"cannot draw {label}: no candidate has a positive weight")
    threshold = rng.random() * total
    running = 0.0
    for key, weight in weighted:
        running += weight
        if threshold < running:
            return key
    return weighted[-1][0]      # only reachable through float rounding


def buckets_within_horizon(profile: DemandProfile,
                           horizon_min: float | None) -> tuple[TimeBucket, ...]:
    """The buckets a run actually uses: windows clipped to ``[0, horizon_min)``
    and ``trip_share`` renormalised over whatever survives.

    Exposed (not private) so callers such as the CLI can report the same windows
    the generator drew from, instead of the profile's unclipped ones.
    """
    if horizon_min is None:
        return profile.buckets
    if isinstance(horizon_min, bool) or not isinstance(horizon_min, (int, float)) \
            or not math.isfinite(horizon_min) or horizon_min <= 0:
        raise DemandGenerationError(f"horizon_min must be a finite number > 0, got {horizon_min!r}")
    limit = min(float(horizon_min), float(MINUTES_PER_DAY))

    clipped = []
    for bucket in profile.buckets:
        windows = tuple((start, min(end, limit)) for start, end in bucket.windows if start < limit)
        if not windows:
            continue
        clipped.append(TimeBucket(name=bucket.name, windows=windows, trip_share=bucket.trip_share,
                                  production=bucket.production, attraction=bucket.attraction))
    if not clipped:
        raise DemandGenerationError(
            f"no time bucket of profile {profile.profile_id!r} starts before minute {limit}"
        )
    share_total = math.fsum(bucket.trip_share for bucket in clipped)
    return tuple(
        TimeBucket(name=b.name, windows=b.windows, trip_share=b.trip_share / share_total,
                   production=b.production, attraction=b.attraction)
        for b in clipped
    )


@dataclass(frozen=True)
class GeneratedDemand:
    """Everything one generator run produced."""

    profile_id: str
    seed: int
    passengers: tuple[Passenger, ...]
    trips: tuple[TripRequest, ...]
    matrix: DemandMatrix

    @property
    def total_demand(self) -> int:
        return self.matrix.total_demand

    def demand_by_time_bucket(self) -> tuple[tuple[str, int], ...]:
        """Passenger volume per time bucket, sorted by bucket name."""
        totals: dict[str, int] = {}
        for trip in self.trips:
            totals[trip.time_bucket] = totals.get(trip.time_bucket, 0) + trip.party_size
        return tuple(sorted(totals.items()))

    def trips_by_time_bucket(self) -> tuple[tuple[str, int], ...]:
        totals: dict[str, int] = {}
        for trip in self.trips:
            totals[trip.time_bucket] = totals.get(trip.time_bucket, 0) + 1
        return tuple(sorted(totals.items()))

    def snapshot(self) -> DemandSnapshot:
        return DemandSnapshot(
            profile_id=self.profile_id,
            seed=self.seed,
            passenger_count=len(self.passengers),
            trip_count=len(self.trips),
            total_demand=self.matrix.total_demand,
            od_cells=tuple((c.origin, c.destination, c.trips, c.passengers) for c in self.matrix.cells),
            bucket_totals=self.demand_by_time_bucket(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "seed": self.seed,
            "passengers": [p.to_dict() for p in self.passengers],
            "trips": [t.to_dict() for t in self.trips],
            "matrix": self.matrix.to_dict(),
        }


class DemandGenerator:
    """Turns a network plus a profile into passengers and trip requests.

    Holds no mutable module-level state: everything lives on the instance, and a
    fresh RNG is created per ``generate()`` call from the seed passed in.
    """

    def __init__(self, graph: NetworkGraph, profile: DemandProfile = BASELINE_DEMAND_PROFILE) -> None:
        if not isinstance(profile, DemandProfile):
            raise DemandProfileError(f"profile must be a DemandProfile, got {type(profile).__name__}")
        if graph.node_count() < 2:
            raise DemandGenerationError(
                f"demand needs at least 2 nodes to form a trip, graph has {graph.node_count()}"
            )
        self._graph = graph
        self._profile = profile
        # Sorted once: every later draw iterates this order, never the graph's.
        self._node_ids: tuple[str, ...] = tuple(sorted(n.node_id for n in graph.nodes()))
        self._roles: dict[str, PlaceRole] = {
            node_id: profile.role_for(node_id, graph.get_node(node_id).node_type)
            for node_id in self._node_ids
        }
        # 22 nodes -> 484 straight-line distances, computed once and reused. Used
        # ONLY for gravity weighting, never as a travel cost.
        self._distance_km: dict[tuple[str, str], float] = {}
        for origin in self._node_ids:
            for destination in self._node_ids:
                self._distance_km[(origin, destination)] = node_distance_km(
                    graph.get_node(origin), graph.get_node(destination)
                )
        self._home_weights = self._weights_from_roles(profile.home_role_weights)

    @property
    def profile(self) -> DemandProfile:
        return self._profile

    @property
    def node_ids(self) -> tuple[str, ...]:
        return self._node_ids

    def role_of(self, node_id: str) -> PlaceRole:
        try:
            return self._roles[node_id]
        except KeyError:
            raise DemandGenerationError(f"unknown node {node_id!r}") from None

    def _weights_from_roles(self, role_weights: dict[PlaceRole, float]) -> tuple[tuple[str, float], ...]:
        return tuple((node_id, role_weights.get(self._roles[node_id], 0.0)) for node_id in self._node_ids)

    def _destination_weights(self, origin: str, bucket: TimeBucket) -> tuple[tuple[str, float], ...]:
        """Attraction, decayed by straight-line distance, with the origin removed
        so that origin != destination holds by construction."""
        exponent = self._profile.gravity_distance_exponent
        weighted = []
        for node_id in self._node_ids:
            if node_id == origin:
                continue
            attraction = bucket.attraction.get(self._roles[node_id], 0.0)
            if attraction <= 0.0:
                continue
            decay = (1.0 + self._distance_km[(origin, node_id)]) ** exponent
            weighted.append((node_id, attraction / decay))
        return tuple(weighted)

    def _origin_weights(self, bucket: TimeBucket, *, exclude: str | None = None) -> tuple[tuple[str, float], ...]:
        return tuple((node_id, bucket.production.get(self._roles[node_id], 0.0))
                     for node_id in self._node_ids if node_id != exclude)

    # --- generation ------------------------------------------------------------
    def generate(self, *, seed: int = DEFAULT_DEMAND_SEED, passenger_count: int = 1000,
                 trips_per_passenger: int = 1, horizon_min: float | None = None,
                 allow_self_pairs: bool = False) -> GeneratedDemand:
        """Generate passengers and their trip requests.

        ``horizon_min`` clips the profile's time windows to ``[0, horizon_min)``
        and renormalises the bucket shares, so a short simulation only sees the
        buckets it actually covers.
        """
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise DemandGenerationError(f"seed must be an int, got {seed!r}")
        if isinstance(passenger_count, bool) or not isinstance(passenger_count, int) or passenger_count < 0:
            raise DemandGenerationError(f"passenger_count must be an int >= 0, got {passenger_count!r}")
        if isinstance(trips_per_passenger, bool) or not isinstance(trips_per_passenger, int) \
                or trips_per_passenger < 1:
            raise DemandGenerationError(f"trips_per_passenger must be an int >= 1, got {trips_per_passenger!r}")

        buckets = buckets_within_horizon(self._profile, horizon_min)
        rng = random.Random(seed)

        passengers = []
        for index in range(passenger_count):
            home = _weighted_choice(rng, self._home_weights, "home node")
            purpose = (TripPurpose.COMMUTE if rng.random() < self._profile.commuter_share
                       else TripPurpose.OTHER)
            passengers.append(Passenger(passenger_id=f"P{index:06d}", home_node_id=home,
                                        purpose=purpose.value))

        bucket_shares = tuple((bucket.name, bucket.trip_share) for bucket in buckets)
        by_name = {bucket.name: bucket for bucket in buckets}

        trips = []
        for passenger in passengers:
            for _ in range(trips_per_passenger):
                bucket = by_name[_weighted_choice(rng, bucket_shares, "time bucket")]
                request_time = self._draw_time(rng, bucket)
                party_size = _weighted_choice(rng, self._profile.party_size_distribution, "party size")
                origin, destination = self._draw_od(rng, passenger, bucket)
                trips.append(TripRequest(
                    trip_id=f"T{len(trips):06d}",
                    passenger_id=passenger.passenger_id,
                    origin_node_id=origin,
                    destination_node_id=destination,
                    request_time_min=round(request_time, REQUEST_TIME_DECIMALS),
                    party_size=party_size,
                    trip_purpose=passenger.purpose,
                    time_bucket=bucket.name,
                ))

        matrix = DemandMatrix.from_trips(trips, self._node_ids, allow_self_pairs=allow_self_pairs)
        logger.info("generated %d passengers / %d trips for profile %s (seed %d)",
                    len(passengers), len(trips), self._profile.profile_id, seed)
        return GeneratedDemand(profile_id=self._profile.profile_id, seed=seed,
                               passengers=tuple(passengers), trips=tuple(trips), matrix=matrix)

    def _draw_time(self, rng: random.Random, bucket: TimeBucket) -> float:
        """Uniform inside the bucket, with multi-window buckets (e.g. a night that
        wraps midnight) weighted by window length."""
        windows = tuple((index, end - start) for index, (start, end) in enumerate(bucket.windows))
        chosen = _weighted_choice(rng, windows, f"window of bucket {bucket.name!r}")
        start, end = bucket.windows[chosen]
        return rng.uniform(start, end)

    def _draw_od(self, rng: random.Random, passenger: Passenger, bucket: TimeBucket) -> tuple[str, str]:
        """Commuters anchor on home during the peaks; everyone else samples both
        ends. The origin is always excluded from the destination candidates."""
        morning = bucket.name == "morning_peak"
        evening = bucket.name == "evening_peak"

        if passenger.is_commuter and morning:
            origin = passenger.home_node_id
            destination = _weighted_choice(rng, self._destination_weights(origin, bucket), "destination")
        elif passenger.is_commuter and evening:
            destination = passenger.home_node_id
            origin = _weighted_choice(rng, self._origin_weights(bucket, exclude=destination), "origin")
        else:
            origin = _weighted_choice(rng, self._origin_weights(bucket), "origin")
            destination = _weighted_choice(rng, self._destination_weights(origin, bucket), "destination")
        return origin, destination


def generate_demand(graph: NetworkGraph, *, seed: int = DEFAULT_DEMAND_SEED,
                    passenger_count: int = 1000, profile: DemandProfile = BASELINE_DEMAND_PROFILE,
                    trips_per_passenger: int = 1, horizon_min: float | None = None,
                    allow_self_pairs: bool = False) -> GeneratedDemand:
    """Convenience wrapper: build a generator and run it once."""
    return DemandGenerator(graph, profile).generate(
        seed=seed, passenger_count=passenger_count, trips_per_passenger=trips_per_passenger,
        horizon_min=horizon_min, allow_self_pairs=allow_self_pairs,
    )
