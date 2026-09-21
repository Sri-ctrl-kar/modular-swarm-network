"""Demand data models: Passenger, TripRequest, DemandMatrix, DemandSnapshot.

All four are frozen dataclasses validated on construction, following the same
conventions as M1's ``Node`` / ``Edge`` / ``Route``: typed errors from
``app.errors``, ``to_dict()`` for serialisation, and no mutable global state.

Nothing here knows about the network graph. ``DemandMatrix`` is told which node
ids are legal so it can reject unknown ones without importing the network layer.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from app.errors import ModelValidationError, NodeNotFoundError


def _require_text(value: Any, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ModelValidationError(f"{field_name} must be a non-empty string, got {value!r}")
    if value != value.strip():
        raise ModelValidationError(f"{field_name} must not have surrounding whitespace: {value!r}")


def _require_int(value: Any, field_name: str, *, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ModelValidationError(f"{field_name} must be an integer, got {value!r}")
    if value < minimum:
        raise ModelValidationError(f"{field_name} must be >= {minimum}, got {value!r}")


def _require_time(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ModelValidationError(f"{field_name} must be a finite number, got {value!r}")
    if value < 0:
        raise ModelValidationError(f"{field_name} must be >= 0, got {value!r}")
    return float(value)


@dataclass(frozen=True)
class Passenger:
    """One synthetic traveller.

    ``home_node_id`` is where this passenger's commute starts; commuters anchor
    their peak-hour trips on it (see ``app.demand.config``). ``passenger_id`` is
    derived from the passenger's index, so it is stable for a given seed+count.
    """

    passenger_id: str
    home_node_id: str
    purpose: str

    def __post_init__(self) -> None:
        _require_text(self.passenger_id, "passenger_id")
        _require_text(self.home_node_id, "home_node_id")
        _require_text(self.purpose, "purpose")

    @property
    def is_commuter(self) -> bool:
        return self.purpose == "commute"

    def to_dict(self) -> dict[str, Any]:
        return {"passenger_id": self.passenger_id, "home_node_id": self.home_node_id,
                "purpose": self.purpose}


@dataclass(frozen=True)
class TripRequest:
    """A wish to travel: who, from where to where, when, and how many people.

    This is demand only — no vehicle is implied or assigned (that is M3).
    """

    trip_id: str
    passenger_id: str
    origin_node_id: str
    destination_node_id: str
    request_time_min: float
    party_size: int
    trip_purpose: str
    time_bucket: str

    def __post_init__(self) -> None:
        for name in ("trip_id", "passenger_id", "origin_node_id", "destination_node_id",
                     "trip_purpose", "time_bucket"):
            _require_text(getattr(self, name), name)
        if self.origin_node_id == self.destination_node_id:
            raise ModelValidationError(
                f"trip {self.trip_id!r}: origin and destination must differ "
                f"(both {self.origin_node_id!r})"
            )
        _require_int(self.party_size, "party_size", minimum=1)
        object.__setattr__(self, "request_time_min", _require_time(self.request_time_min, "request_time_min"))

    @property
    def od_pair(self) -> tuple[str, str]:
        return (self.origin_node_id, self.destination_node_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trip_id": self.trip_id,
            "passenger_id": self.passenger_id,
            "origin_node_id": self.origin_node_id,
            "destination_node_id": self.destination_node_id,
            "request_time_min": self.request_time_min,
            "party_size": self.party_size,
            "trip_purpose": self.trip_purpose,
            "time_bucket": self.time_bucket,
        }


@dataclass(frozen=True)
class ODCell:
    """Aggregated demand for one ordered (origin, destination) pair."""

    origin: str
    destination: str
    trips: int
    passengers: int

    def __post_init__(self) -> None:
        _require_text(self.origin, "origin")
        _require_text(self.destination, "destination")
        _require_int(self.trips, "trips", minimum=1)
        _require_int(self.passengers, "passengers", minimum=1)
        if self.passengers < self.trips:
            raise ModelValidationError(
                f"cell {self.origin}->{self.destination}: passengers ({self.passengers}) "
                f"cannot be fewer than trips ({self.trips}); every trip carries >= 1 passenger"
            )

    def to_dict(self) -> dict[str, Any]:
        return {"origin": self.origin, "destination": self.destination,
                "trips": self.trips, "passengers": self.passengers}


@dataclass(frozen=True)
class DemandMatrix:
    """Origin-destination demand aggregated from trip requests.

    ``cells`` is always sorted by (origin, destination), so two matrices built
    from the same trips in any order compare and fingerprint identically. Only
    non-empty pairs are stored — the matrix is sparse, the "0" cells of a
    conceptual dense table simply have no entry.
    """

    node_ids: tuple[str, ...]
    cells: tuple[ODCell, ...]
    allow_self_pairs: bool = False

    def __post_init__(self) -> None:
        if not self.node_ids:
            raise ModelValidationError("a demand matrix needs at least one node id")
        for node_id in self.node_ids:
            _require_text(node_id, "node_id")
        if len(set(self.node_ids)) != len(self.node_ids):
            raise ModelValidationError("duplicate node id in demand matrix node_ids")
        object.__setattr__(self, "node_ids", tuple(sorted(self.node_ids)))
        if not isinstance(self.allow_self_pairs, bool):
            raise ModelValidationError("allow_self_pairs must be a bool")

        known = set(self.node_ids)
        seen: set[tuple[str, str]] = set()
        for cell in self.cells:
            if not isinstance(cell, ODCell):
                raise ModelValidationError(f"demand matrix cells must be ODCell, got {type(cell).__name__}")
            for role, node_id in (("origin", cell.origin), ("destination", cell.destination)):
                if node_id not in known:
                    raise NodeNotFoundError(f"demand matrix {role} {node_id!r} is not a known node")
            if cell.origin == cell.destination and not self.allow_self_pairs:
                raise ModelValidationError(
                    f"self-pair {cell.origin!r} is not allowed; pass allow_self_pairs=True to keep it"
                )
            if (cell.origin, cell.destination) in seen:
                raise ModelValidationError(f"duplicate OD cell {cell.origin}->{cell.destination}")
            seen.add((cell.origin, cell.destination))
        object.__setattr__(self, "cells", tuple(sorted(self.cells, key=lambda c: (c.origin, c.destination))))
        object.__setattr__(self, "_index", {(c.origin, c.destination): c for c in self.cells})

    # --- construction ----------------------------------------------------------
    @classmethod
    def from_trips(cls, trips: Iterable[TripRequest], node_ids: Sequence[str],
                   *, allow_self_pairs: bool = False) -> "DemandMatrix":
        """Aggregate trip requests, summing party sizes into passenger volume."""
        totals: dict[tuple[str, str], list[int]] = {}
        for trip in trips:
            if not isinstance(trip, TripRequest):
                raise ModelValidationError(f"expected TripRequest, got {type(trip).__name__}")
            bucket = totals.setdefault(trip.od_pair, [0, 0])
            bucket[0] += 1
            bucket[1] += trip.party_size
        cells = tuple(ODCell(origin, destination, count, passengers)
                      for (origin, destination), (count, passengers) in totals.items())
        return cls(node_ids=tuple(node_ids), cells=cells, allow_self_pairs=allow_self_pairs)

    # --- queries ---------------------------------------------------------------
    def _check_node(self, node_id: str, label: str) -> None:
        if node_id not in set(self.node_ids):
            raise NodeNotFoundError(f"unknown {label} node {node_id!r}")

    def cell(self, origin: str, destination: str) -> ODCell | None:
        self._check_node(origin, "origin")
        self._check_node(destination, "destination")
        return self._index.get((origin, destination))

    def demand_for(self, origin: str, destination: str) -> int:
        """Passenger volume for one OD pair (0 when no trips were requested)."""
        cell = self.cell(origin, destination)
        return cell.passengers if cell else 0

    def trips_for(self, origin: str, destination: str) -> int:
        cell = self.cell(origin, destination)
        return cell.trips if cell else 0

    @property
    def total_demand(self) -> int:
        """Total passenger volume (party sizes summed)."""
        return sum(cell.passengers for cell in self.cells)

    @property
    def total_trips(self) -> int:
        return sum(cell.trips for cell in self.cells)

    @property
    def pair_count(self) -> int:
        return len(self.cells)

    def origins(self) -> tuple[str, ...]:
        return tuple(sorted({cell.origin for cell in self.cells}))

    def destinations(self) -> tuple[str, ...]:
        return tuple(sorted({cell.destination for cell in self.cells}))

    def top_pairs(self, limit: int = 5) -> tuple[ODCell, ...]:
        """Busiest pairs first. Ties break on origin then destination, so the
        order is deterministic rather than dependent on insertion order."""
        _require_int(limit, "limit", minimum=0)
        ranked = sorted(self.cells, key=lambda c: (-c.passengers, -c.trips, c.origin, c.destination))
        return tuple(ranked[:limit])

    def demand_by_origin(self) -> tuple[tuple[str, int], ...]:
        totals: dict[str, int] = {}
        for cell in self.cells:
            totals[cell.origin] = totals.get(cell.origin, 0) + cell.passengers
        return tuple(sorted(totals.items()))

    def demand_by_destination(self) -> tuple[tuple[str, int], ...]:
        totals: dict[str, int] = {}
        for cell in self.cells:
            totals[cell.destination] = totals.get(cell.destination, 0) + cell.passengers
        return tuple(sorted(totals.items()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_ids": list(self.node_ids),
            "allow_self_pairs": self.allow_self_pairs,
            "total_trips": self.total_trips,
            "total_demand": self.total_demand,
            "cells": [cell.to_dict() for cell in self.cells],
        }


@dataclass(frozen=True)
class DemandSnapshot:
    """Immutable, fingerprintable summary of one generated demand set.

    Same spirit as M1's ``NetworkSnapshot``: a canonical dict plus a stable
    SHA-256, so two runs can be compared with one string.
    """

    profile_id: str
    seed: int
    passenger_count: int
    trip_count: int
    total_demand: int
    od_cells: tuple[tuple[str, str, int, int], ...]
    bucket_totals: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        _require_text(self.profile_id, "profile_id")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ModelValidationError(f"seed must be an int, got {self.seed!r}")
        for name in ("passenger_count", "trip_count", "total_demand"):
            _require_int(getattr(self, name), name, minimum=0)
        seen_pairs: set[tuple[str, str]] = set()
        for origin, destination, trips, passengers in self.od_cells:
            if (origin, destination) in seen_pairs:
                raise ModelValidationError(f"duplicate OD pair in snapshot: {origin}->{destination}")
            seen_pairs.add((origin, destination))
            _require_int(trips, f"trips for {origin}->{destination}", minimum=0)
            _require_int(passengers, f"passengers for {origin}->{destination}", minimum=0)
        seen_buckets: set[str] = set()
        for name, total in self.bucket_totals:
            if name in seen_buckets:
                raise ModelValidationError(f"duplicate time bucket in snapshot: {name!r}")
            seen_buckets.add(name)
            _require_int(total, f"bucket total for {name!r}", minimum=0)
        object.__setattr__(self, "od_cells", tuple(sorted(self.od_cells)))
        object.__setattr__(self, "bucket_totals", tuple(sorted(self.bucket_totals)))

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "seed": self.seed,
            "passenger_count": self.passenger_count,
            "trip_count": self.trip_count,
            "total_demand": self.total_demand,
            "od_cells": [list(cell) for cell in self.od_cells],
            "bucket_totals": dict(self.bucket_totals),
        }

    def fingerprint(self) -> str:
        """Stable SHA-256 of the canonical JSON form (for reproducibility checks)."""
        canonical = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
