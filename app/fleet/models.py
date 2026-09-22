"""Fleet data models: Pod, PodStatus, TripRecord, FleetSnapshot.

``Pod`` follows M1's ``Edge`` convention: a frozen dataclass whose *identity*
(``pod_id``, ``capacity``) can never change, while its dynamic state moves only
through validated methods that check the state machine first. Nothing here knows
about the network graph or about routing — a Pod validates the *shape* of a node
id (non-empty string), and ``PodFleet`` validates that the node actually exists,
because that is the layer holding the graph.

POD STATE MACHINE (M3 — no swarm states; those belong to M4)

    IDLE ──assign──▶ ASSIGNED ──start_travel──▶ TRAVELING ──arrive──▶ ARRIVED
     │  ▲                │                                               │
     │  └────release─────┘                                               │
     │  ▲                                                                │
     │  └────────────────────────release──────────────────────────────────┘
     │
     └──begin_charging──▶ CHARGING ──finish_charging──▶ IDLE

Any other transition raises ``PodStateError`` rather than silently corrupting
the fleet.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.errors import ModelValidationError, PodStateError
from app.models.route import Route

BATTERY_SNAPSHOT_DECIMALS = 4


class PodStatus(str, Enum):
    IDLE = "idle"              # available, parked at a node
    ASSIGNED = "assigned"      # has a trip and a route, not yet moving
    TRAVELING = "traveling"    # on an edge
    ARRIVED = "arrived"        # reached the trip destination, not yet released
    CHARGING = "charging"      # unavailable while the battery refills


# Allowed transitions. Anything absent here is rejected.
ALLOWED_TRANSITIONS: dict[PodStatus, frozenset[PodStatus]] = {
    PodStatus.IDLE: frozenset({PodStatus.ASSIGNED, PodStatus.CHARGING}),
    PodStatus.ASSIGNED: frozenset({PodStatus.TRAVELING, PodStatus.IDLE}),
    PodStatus.TRAVELING: frozenset({PodStatus.ARRIVED}),
    PodStatus.ARRIVED: frozenset({PodStatus.IDLE}),
    PodStatus.CHARGING: frozenset({PodStatus.IDLE}),
}


#: Prefixes used in ``TripRecord.failure_reason`` so callers can tell the two
#: kinds of failure apart without string guessing.
UNROUTABLE_PREFIX = "unroutable:"
TIMED_OUT_PREFIX = "no pod available within"


class TripKind(str, Enum):
    """What a pod's current assignment is for.

    Added for M5: a pod may be dispatched **empty** to reposition itself. That is a
    fleet-level fact (a pod can drive with nobody aboard), so it lives here, while
    every decision about *when* to reposition stays in ``app/rebalancing/``.

    ``PASSENGER`` is the default everywhere, so existing behaviour is unchanged.
    """

    PASSENGER = "passenger"          # carrying a party; party_size >= 1
    REPOSITIONING = "repositioning"  # empty; party_size == 0


class TripStatus(str, Enum):
    PENDING = "pending"          # requested, waiting for a pod
    ASSIGNED = "assigned"        # a pod holds it, travel not started
    IN_PROGRESS = "in_progress"  # pod is moving
    COMPLETED = "completed"      # pod reached the destination
    FAILED = "failed"            # unserviceable (e.g. no route exists)


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


def _require_percent(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ModelValidationError(f"{field_name} must be a finite number, got {value!r}")
    if not 0.0 <= value <= 100.0:
        raise ModelValidationError(f"{field_name} must be within [0, 100], got {value!r}")
    return float(value)


def _require_non_negative(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ModelValidationError(f"{field_name} must be a finite number, got {value!r}")
    if value < 0:
        raise ModelValidationError(f"{field_name} must be >= 0, got {value!r}")
    return float(value)


@dataclass(frozen=True, eq=False)
class Pod:
    """One autonomous electric passenger pod.

    Units are explicit: ``battery_percent`` is 0-100 % of a full battery,
    distances are km, times are minutes, energy is kWh. The battery figure is a
    simulation approximation (see ``app.fleet.config``), not a physical model.

    Equality is identity, as with ``Edge``; compare ``to_dict()`` for value
    comparison.
    """

    pod_id: str
    capacity: int
    current_node_id: str
    status: PodStatus = PodStatus.IDLE
    battery_percent: float = 100.0
    assigned_trip_id: str | None = None
    occupied_seats: int = 0
    route: Route | None = None
    route_index: int = 0                      # edges of the route already completed
    current_edge_id: str | None = None
    # Time, distance and energy are all sampled from the network when the edge is
    # ENTERED, and stay fixed for that traversal — one consistent rule.
    current_edge_time_min: float | None = None
    current_edge_elapsed_min: float = 0.0
    current_edge_distance_km: float = 0.0
    current_edge_energy_kwh: float = 0.0
    # Lifetime counters (odometer).
    total_distance_km: float = 0.0
    total_travel_time_min: float = 0.0
    total_energy_kwh: float = 0.0
    # Counts PASSENGER arrivals only. Empty repositioning arrivals are counted
    # separately so they can never inflate a passenger-service figure.
    completed_trip_count: int = 0
    completed_repositioning_count: int = 0
    current_trip_kind: TripKind = TripKind.PASSENGER

    def __post_init__(self) -> None:
        _require_text(self.pod_id, "pod_id")
        _require_text(self.current_node_id, "current_node_id")
        _require_int(self.capacity, "capacity", minimum=1)
        _require_int(self.occupied_seats, "occupied_seats", minimum=0)
        _require_int(self.route_index, "route_index", minimum=0)
        _require_int(self.completed_trip_count, "completed_trip_count", minimum=0)
        _require_int(self.completed_repositioning_count, "completed_repositioning_count", minimum=0)
        try:
            object.__setattr__(self, "current_trip_kind", TripKind(self.current_trip_kind))
        except ValueError as exc:
            allowed = ", ".join(k.value for k in TripKind)
            raise ModelValidationError(
                f"current_trip_kind must be one of [{allowed}], got {self.current_trip_kind!r}") from exc
        if self.occupied_seats > self.capacity:
            raise ModelValidationError(
                f"pod {self.pod_id!r}: occupied_seats ({self.occupied_seats}) "
                f"exceeds capacity ({self.capacity})"
            )
        object.__setattr__(self, "battery_percent", _require_percent(self.battery_percent, "battery_percent"))
        for name in ("current_edge_elapsed_min", "current_edge_distance_km",
                     "current_edge_energy_kwh", "total_distance_km",
                     "total_travel_time_min", "total_energy_kwh"):
            object.__setattr__(self, name, _require_non_negative(getattr(self, name), name))
        try:
            object.__setattr__(self, "status", PodStatus(self.status))
        except ValueError as exc:
            allowed = ", ".join(s.value for s in PodStatus)
            raise ModelValidationError(f"status must be one of [{allowed}], got {self.status!r}") from exc
        for name in ("assigned_trip_id", "current_edge_id"):
            value = getattr(self, name)
            if value is not None:
                _require_text(value, name)
        if self.route is not None and not isinstance(self.route, Route):
            raise ModelValidationError(f"route must be a Route or None, got {type(self.route).__name__}")
        if self.route is not None and self.route_index > len(self.route.edge_ids):
            raise ModelValidationError(
                f"pod {self.pod_id!r}: route_index {self.route_index} exceeds the route's "
                f"{len(self.route.edge_ids)} edges"
            )

    # --- derived state ----------------------------------------------------------
    @property
    def available_seats(self) -> int:
        return self.capacity - self.occupied_seats

    @property
    def is_idle(self) -> bool:
        return self.status is PodStatus.IDLE

    @property
    def has_route(self) -> bool:
        return self.route is not None

    @property
    def is_repositioning(self) -> bool:
        """True while this pod is driving empty to reposition itself (M5)."""
        return self.current_trip_kind is TripKind.REPOSITIONING

    @property
    def remaining_edge_ids(self) -> tuple[str, ...]:
        """Edges of the current route not yet completed."""
        if self.route is None:
            return ()
        return self.route.edge_ids[self.route_index:]

    @property
    def route_progress(self) -> float:
        """Completed fraction of the assigned route's edges, 0.0-1.0."""
        if self.route is None or not self.route.edge_ids:
            return 0.0
        return self.route_index / len(self.route.edge_ids)

    def can_accept(self, party_size: int) -> bool:
        _require_int(party_size, "party_size", minimum=1)
        return self.is_idle and party_size <= self.available_seats

    # --- state machine ----------------------------------------------------------
    def _set(self, **fields: Any) -> None:
        for name, value in fields.items():
            object.__setattr__(self, name, value)

    def set_status(self, new_status: PodStatus) -> None:
        """Change status, rejecting any transition not in ``ALLOWED_TRANSITIONS``."""
        try:
            new_status = PodStatus(new_status)
        except ValueError as exc:
            allowed = ", ".join(s.value for s in PodStatus)
            raise PodStateError(f"unknown pod status {new_status!r} (allowed: {allowed})") from exc
        if new_status is self.status:
            raise PodStateError(f"pod {self.pod_id!r} is already {self.status.value}")
        if new_status not in ALLOWED_TRANSITIONS[self.status]:
            allowed = ", ".join(sorted(s.value for s in ALLOWED_TRANSITIONS[self.status]))
            raise PodStateError(
                f"pod {self.pod_id!r}: illegal transition {self.status.value} -> {new_status.value} "
                f"(allowed from {self.status.value}: {allowed or 'nothing'})"
            )
        self._set(status=new_status)

    def assign(self, trip_id: str, route: Route, party_size: int,
               kind: TripKind = TripKind.PASSENGER) -> None:
        """Attach a trip and its route. IDLE -> ASSIGNED.

        ``kind`` defaults to ``PASSENGER``, which requires a party of at least one
        and behaves exactly as before. ``REPOSITIONING`` requires a party of
        **zero**: the pod drives empty, so it can never be mistaken for carrying
        someone, and no seat is ever occupied by a repositioning move.
        """
        _require_text(trip_id, "trip_id")
        try:
            kind = TripKind(kind)
        except ValueError as exc:
            allowed = ", ".join(k.value for k in TripKind)
            raise ModelValidationError(f"kind must be one of [{allowed}], got {kind!r}") from exc
        if kind is TripKind.REPOSITIONING:
            if party_size != 0:
                raise ModelValidationError(
                    f"a repositioning pod travels empty, so party_size must be 0, got {party_size!r}")
        else:
            _require_int(party_size, "party_size", minimum=1)
        if not isinstance(route, Route):
            raise ModelValidationError(f"route must be a Route, got {type(route).__name__}")
        if self.status is not PodStatus.IDLE:
            raise PodStateError(f"pod {self.pod_id!r} must be idle to accept a trip, is {self.status.value}")
        if party_size > self.available_seats:
            raise PodStateError(
                f"pod {self.pod_id!r}: party of {party_size} exceeds {self.available_seats} available seats"
            )
        if route.origin != self.current_node_id:
            raise PodStateError(
                f"pod {self.pod_id!r} is at {self.current_node_id!r} but the route starts at {route.origin!r}"
            )
        self.set_status(PodStatus.ASSIGNED)
        self._set(assigned_trip_id=trip_id, route=route, occupied_seats=party_size,
                  current_trip_kind=kind,
                  route_index=0, current_edge_id=None, current_edge_time_min=None,
                  current_edge_elapsed_min=0.0, current_edge_distance_km=0.0,
                  current_edge_energy_kwh=0.0)

    def start_travel(self, edge_id: str, edge_time_min: float,
                     edge_distance_km: float, edge_energy_kwh: float) -> None:
        """Enter the route's first edge. ASSIGNED -> TRAVELING."""
        if self.status is not PodStatus.ASSIGNED:
            raise PodStateError(f"pod {self.pod_id!r} must be assigned to start travelling, "
                                f"is {self.status.value}")
        self.set_status(PodStatus.TRAVELING)
        self.enter_edge(edge_id, edge_time_min, edge_distance_km, edge_energy_kwh)

    def enter_edge(self, edge_id: str, edge_time_min: float,
                   edge_distance_km: float, edge_energy_kwh: float) -> None:
        """Begin traversing an edge.

        The caller samples duration, distance and energy from the network at this
        moment and they stay fixed for this traversal: the pod is already on the
        road. Congestion changes on edges it has NOT yet entered do affect it,
        because each edge is sampled as the pod enters it.
        """
        _require_text(edge_id, "edge_id")
        edge_time_min = _require_non_negative(edge_time_min, "edge_time_min")
        edge_distance_km = _require_non_negative(edge_distance_km, "edge_distance_km")
        edge_energy_kwh = _require_non_negative(edge_energy_kwh, "edge_energy_kwh")
        if self.status is not PodStatus.TRAVELING:
            raise PodStateError(f"pod {self.pod_id!r} must be travelling to enter an edge, "
                                f"is {self.status.value}")
        expected = self.remaining_edge_ids
        if not expected or expected[0] != edge_id:
            raise PodStateError(
                f"pod {self.pod_id!r}: next route edge is "
                f"{expected[0] if expected else 'none'!r}, got {edge_id!r}"
            )
        self._set(current_edge_id=edge_id, current_edge_time_min=edge_time_min,
                  current_edge_elapsed_min=0.0, current_edge_distance_km=edge_distance_km,
                  current_edge_energy_kwh=edge_energy_kwh)

    def advance_on_edge(self, minutes: float) -> float:
        """Spend time on the current edge. Returns the minutes actually used —
        never more than the edge has left, so a tick cannot overshoot."""
        minutes = _require_non_negative(minutes, "minutes")
        if self.status is not PodStatus.TRAVELING or self.current_edge_time_min is None:
            raise PodStateError(f"pod {self.pod_id!r} is not travelling on an edge")
        remaining = max(0.0, self.current_edge_time_min - self.current_edge_elapsed_min)
        used = min(minutes, remaining)
        self._set(current_edge_elapsed_min=self.current_edge_elapsed_min + used,
                  total_travel_time_min=self.total_travel_time_min + used)
        return used

    @property
    def edge_is_complete(self) -> bool:
        if self.current_edge_time_min is None:
            return False
        return self.current_edge_elapsed_min >= self.current_edge_time_min

    def complete_edge(self, destination_node_id: str) -> tuple[float, float]:
        """Finish the current edge: move to its far node and bill the distance and
        energy that were sampled when the pod entered it. Returns (km, kWh)."""
        _require_text(destination_node_id, "destination_node_id")
        if self.status is not PodStatus.TRAVELING or self.current_edge_id is None:
            raise PodStateError(f"pod {self.pod_id!r} has no edge to complete")
        if not self.edge_is_complete:
            raise PodStateError(
                f"pod {self.pod_id!r}: edge {self.current_edge_id!r} is not finished "
                f"({self.current_edge_elapsed_min:.4f} of {self.current_edge_time_min:.4f} min)"
            )
        distance_km, energy_kwh = self.current_edge_distance_km, self.current_edge_energy_kwh
        self._set(current_node_id=destination_node_id,
                  route_index=self.route_index + 1,
                  current_edge_id=None, current_edge_time_min=None, current_edge_elapsed_min=0.0,
                  current_edge_distance_km=0.0, current_edge_energy_kwh=0.0,
                  total_distance_km=self.total_distance_km + distance_km,
                  total_energy_kwh=self.total_energy_kwh + energy_kwh)
        return distance_km, energy_kwh

    @property
    def route_is_complete(self) -> bool:
        return self.route is not None and self.route_index >= len(self.route.edge_ids)

    def arrive(self) -> None:
        """TRAVELING -> ARRIVED, only once the whole route is behind the pod."""
        if self.status is not PodStatus.TRAVELING:
            raise PodStateError(f"pod {self.pod_id!r} must be travelling to arrive, is {self.status.value}")
        if not self.route_is_complete:
            raise PodStateError(
                f"pod {self.pod_id!r} still has {len(self.remaining_edge_ids)} route edge(s) left"
            )
        self.set_status(PodStatus.ARRIVED)
        if self.current_trip_kind is TripKind.REPOSITIONING:
            self._set(completed_repositioning_count=self.completed_repositioning_count + 1)
        else:
            self._set(completed_trip_count=self.completed_trip_count + 1)

    def release(self) -> str | None:
        """Drop the trip and become IDLE again. Returns the released trip id."""
        if self.status not in (PodStatus.ARRIVED, PodStatus.ASSIGNED):
            raise PodStateError(f"pod {self.pod_id!r} cannot be released from {self.status.value}")
        trip_id = self.assigned_trip_id
        self.set_status(PodStatus.IDLE)
        self._set(assigned_trip_id=None, route=None, route_index=0, occupied_seats=0,
                  current_trip_kind=TripKind.PASSENGER,
                  current_edge_id=None, current_edge_time_min=None, current_edge_elapsed_min=0.0,
                  current_edge_distance_km=0.0, current_edge_energy_kwh=0.0)
        return trip_id

    # --- battery ----------------------------------------------------------------
    def discharge(self, percent: float) -> float:
        """Drain the battery, clamped at 0 so it can never go negative. Returns the
        percentage actually removed (less than asked if the pod hit empty)."""
        percent = _require_non_negative(percent, "percent")
        applied = min(percent, self.battery_percent)
        self._set(battery_percent=self.battery_percent - applied)
        return applied

    def charge(self, percent: float) -> float:
        """Add charge, clamped at 100. Returns the percentage actually added."""
        percent = _require_non_negative(percent, "percent")
        applied = min(percent, 100.0 - self.battery_percent)
        self._set(battery_percent=self.battery_percent + applied)
        return applied

    def begin_charging(self) -> None:
        self.set_status(PodStatus.CHARGING)

    def finish_charging(self) -> None:
        if self.status is not PodStatus.CHARGING:
            raise PodStateError(f"pod {self.pod_id!r} is not charging")
        self.set_status(PodStatus.IDLE)

    # --- serialisation ----------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "pod_id": self.pod_id,
            "capacity": self.capacity,
            "current_node_id": self.current_node_id,
            "status": self.status.value,
            "battery_percent": self.battery_percent,
            "assigned_trip_id": self.assigned_trip_id,
            "occupied_seats": self.occupied_seats,
            "available_seats": self.available_seats,
            "route_edge_ids": None if self.route is None else list(self.route.edge_ids),
            "route_index": self.route_index,
            "route_progress": self.route_progress,
            "current_edge_id": self.current_edge_id,
            "current_edge_time_min": self.current_edge_time_min,
            "current_edge_elapsed_min": self.current_edge_elapsed_min,
            "current_edge_distance_km": self.current_edge_distance_km,
            "current_edge_energy_kwh": self.current_edge_energy_kwh,
            "total_distance_km": self.total_distance_km,
            "total_travel_time_min": self.total_travel_time_min,
            "total_energy_kwh": self.total_energy_kwh,
            "completed_trip_count": self.completed_trip_count,
            "completed_repositioning_count": self.completed_repositioning_count,
            "current_trip_kind": self.current_trip_kind.value,
        }


@dataclass
class TripRecord:
    """The life of one trip through the fleet. Records are never deleted, so a
    completed simulation keeps its full history.

    Mutable by design (a record is updated as the trip progresses); the immutable
    part is the ``TripRequest`` it holds.
    """

    trip_id: str
    party_size: int
    origin_node_id: str
    destination_node_id: str
    request_time_min: float
    status: TripStatus = TripStatus.PENDING
    pod_id: str | None = None
    assigned_time_min: float | None = None
    completed_time_min: float | None = None
    route_distance_km: float | None = None
    route_travel_time_min: float | None = None      # estimate from the route at assignment
    actual_travel_time_min: float | None = None     # minutes the pod really spent driving
    energy_kwh: float | None = None                 # energy the pod really consumed
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.trip_id, "trip_id")
        _require_text(self.origin_node_id, "origin_node_id")
        _require_text(self.destination_node_id, "destination_node_id")
        _require_int(self.party_size, "party_size", minimum=1)
        _require_non_negative(self.request_time_min, "request_time_min")
        self.status = TripStatus(self.status)

    @property
    def is_resolved(self) -> bool:
        """True once the trip needs no further simulation work."""
        return self.status in (TripStatus.COMPLETED, TripStatus.FAILED)

    @property
    def waiting_time_min(self) -> float | None:
        """Minutes between requesting and being assigned a pod."""
        if self.assigned_time_min is None:
            return None
        return self.assigned_time_min - self.request_time_min

    @property
    def completion_time_min(self) -> float | None:
        """Minutes from request to arrival (wait + travel)."""
        if self.completed_time_min is None:
            return None
        return self.completed_time_min - self.request_time_min

    def to_dict(self) -> dict[str, Any]:
        return {
            "trip_id": self.trip_id,
            "party_size": self.party_size,
            "origin_node_id": self.origin_node_id,
            "destination_node_id": self.destination_node_id,
            "request_time_min": self.request_time_min,
            "status": self.status.value,
            "pod_id": self.pod_id,
            "assigned_time_min": self.assigned_time_min,
            "completed_time_min": self.completed_time_min,
            "waiting_time_min": self.waiting_time_min,
            "completion_time_min": self.completion_time_min,
            "route_distance_km": self.route_distance_km,
            "route_travel_time_min": self.route_travel_time_min,
            "actual_travel_time_min": self.actual_travel_time_min,
            "energy_kwh": self.energy_kwh,
            "failure_reason": self.failure_reason,
        }


@dataclass(frozen=True)
class FleetSnapshot:
    """Immutable, fingerprintable state of the fleet at one instant.

    Same spirit as M1's ``NetworkSnapshot`` and M2's ``DemandSnapshot``: a
    canonical dict plus a stable SHA-256. Battery percentages are rounded so the
    fingerprint does not carry meaningless float noise.
    """

    time_min: float
    pods: tuple[tuple[str, str, str, float, int, int], ...]
    trip_status_counts: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        _require_non_negative(self.time_min, "time_min")
        seen: set[str] = set()
        rounded = []
        for pod_id, status, node_id, battery, occupied, completed in self.pods:
            if pod_id in seen:
                raise ModelValidationError(f"duplicate pod id in snapshot: {pod_id!r}")
            seen.add(pod_id)
            _require_percent(battery, f"battery for {pod_id!r}")
            _require_int(occupied, f"occupied seats for {pod_id!r}", minimum=0)
            _require_int(completed, f"completed trips for {pod_id!r}", minimum=0)
            rounded.append((pod_id, status, node_id,
                            round(float(battery), BATTERY_SNAPSHOT_DECIMALS), occupied, completed))
        object.__setattr__(self, "pods", tuple(sorted(rounded)))
        counts: set[str] = set()
        for name, total in self.trip_status_counts:
            if name in counts:
                raise ModelValidationError(f"duplicate trip status in snapshot: {name!r}")
            counts.add(name)
            _require_int(total, f"count for {name!r}", minimum=0)
        object.__setattr__(self, "trip_status_counts", tuple(sorted(self.trip_status_counts)))

    def as_dict(self) -> dict[str, Any]:
        return {
            "time_min": self.time_min,
            "pods": [list(pod) for pod in self.pods],
            "trip_status_counts": dict(self.trip_status_counts),
        }

    def fingerprint(self) -> str:
        """Stable SHA-256 of the canonical JSON form (for reproducibility checks)."""
        canonical = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
