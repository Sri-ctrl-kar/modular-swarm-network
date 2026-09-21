"""Swarm data models: SharedCorridor, Swarm, SwarmStatus, SwarmSnapshot.

A **swarm is a coordination layer over individual autonomous pods, not a
fictional single vehicle.** Every pod keeps its own id, battery, passengers and
route; the swarm records which stretch of road they cover together. Pod ids
inside a swarm are always stored sorted, so nothing depends on the order they
happened to be added.

Note what M4 does *not* change: pods keep M3's five-member ``PodStatus``
unchanged. Swarm membership lives here, in the swarm layer, which is why
``app/fleet/`` needed no edit at all — see the README for that decision.

SWARM STATE MACHINE

    FORMING ──activate──▶ ACTIVE ──begin_split──▶ SPLITTING ──complete──▶ COMPLETED
        │                                                                     ▲
        └──────────────────────── abandon ────────────────────────────────────┘

Any other transition raises ``SwarmStateError``.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Sequence

from app.errors import ModelValidationError, SwarmStateError
from app.swarm.config import MIN_SWARM_SIZE

DISTANCE_SNAPSHOT_DECIMALS = 4


class SwarmStatus(str, Enum):
    FORMING = "forming"        # members agreed, not yet moving
    ACTIVE = "active"          # traversing the shared corridor together
    SPLITTING = "splitting"    # corridor exhausted, members separating
    COMPLETED = "completed"    # finished; kept for history, never deleted


ALLOWED_SWARM_TRANSITIONS: dict[SwarmStatus, frozenset[SwarmStatus]] = {
    SwarmStatus.FORMING: frozenset({SwarmStatus.ACTIVE, SwarmStatus.COMPLETED}),
    SwarmStatus.ACTIVE: frozenset({SwarmStatus.SPLITTING, SwarmStatus.COMPLETED}),
    SwarmStatus.SPLITTING: frozenset({SwarmStatus.COMPLETED}),
    SwarmStatus.COMPLETED: frozenset(),
}


def _require_text(value: Any, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ModelValidationError(f"{field_name} must be a non-empty string, got {value!r}")
    if value != value.strip():
        raise ModelValidationError(f"{field_name} must not have surrounding whitespace: {value!r}")


def _require_non_negative(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ModelValidationError(f"{field_name} must be a finite number, got {value!r}")
    if value < 0:
        raise ModelValidationError(f"{field_name} must be >= 0, got {value!r}")
    return float(value)


@dataclass(frozen=True)
class SharedCorridor:
    """The stretch the members of a swarm cover together.

    ``edge_ids`` is the common prefix of the members' remaining routes, so the
    corridor always starts at ``origin_node_id`` and ends at
    ``divergence_node_id`` — the node where the members' routes stop agreeing (or
    where the shortest of them ends).
    """

    edge_ids: tuple[str, ...]
    origin_node_id: str
    divergence_node_id: str
    distance_km: float
    travel_time_min: float

    def __post_init__(self) -> None:
        if not self.edge_ids:
            raise ModelValidationError("a shared corridor needs at least one edge")
        for edge_id in self.edge_ids:
            _require_text(edge_id, "corridor edge_id")
        if len(set(self.edge_ids)) != len(self.edge_ids):
            raise ModelValidationError(f"corridor repeats an edge: {self.edge_ids}")
        _require_text(self.origin_node_id, "origin_node_id")
        _require_text(self.divergence_node_id, "divergence_node_id")
        object.__setattr__(self, "distance_km", _require_non_negative(self.distance_km, "distance_km"))
        object.__setattr__(self, "travel_time_min",
                           _require_non_negative(self.travel_time_min, "travel_time_min"))

    @property
    def edge_count(self) -> int:
        return len(self.edge_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_ids": list(self.edge_ids),
            "edge_count": self.edge_count,
            "origin_node_id": self.origin_node_id,
            "divergence_node_id": self.divergence_node_id,
            "distance_km": self.distance_km,
            "travel_time_min": self.travel_time_min,
        }


@dataclass
class Swarm:
    """A platoon of pods coordinating along one shared corridor.

    Mutable by design (a swarm progresses and then splits); its identity
    (``swarm_id``, membership, corridor, leader) is fixed at construction and the
    validation below is what keeps it coherent.
    """

    swarm_id: str
    pod_ids: tuple[str, ...]
    leader_pod_id: str
    corridor: SharedCorridor
    formation_time_min: float
    status: SwarmStatus = SwarmStatus.FORMING
    current_node_id: str = ""
    corridor_edges_completed: int = 0
    shared_distance_km: float = 0.0
    shared_travel_time_min: float = 0.0
    split_time_min: float | None = None
    # Pods that left early because their own trip ended at a corridor node.
    departed_pod_ids: tuple[str, ...] = ()
    #: Set when this swarm's members went on to form another swarm after splitting.
    successor_swarm_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.swarm_id, "swarm_id")
        _require_text(self.leader_pod_id, "leader_pod_id")
        if not isinstance(self.pod_ids, tuple):
            self.pod_ids = tuple(self.pod_ids)
        for pod_id in self.pod_ids:
            _require_text(pod_id, "pod_id")
        if len(set(self.pod_ids)) != len(self.pod_ids):
            raise ModelValidationError(f"swarm {self.swarm_id!r} lists a pod twice: {self.pod_ids}")
        if len(self.pod_ids) < MIN_SWARM_SIZE:
            raise ModelValidationError(
                f"swarm {self.swarm_id!r} needs at least {MIN_SWARM_SIZE} pods, got "
                f"{len(self.pod_ids)}: a single pod is not a swarm"
            )
        # Membership is always sorted, so results never depend on insertion order.
        self.pod_ids = tuple(sorted(self.pod_ids))
        if self.leader_pod_id not in self.pod_ids:
            raise ModelValidationError(
                f"swarm {self.swarm_id!r} leader {self.leader_pod_id!r} is not a member"
            )
        if not isinstance(self.corridor, SharedCorridor):
            raise ModelValidationError(
                f"corridor must be a SharedCorridor, got {type(self.corridor).__name__}"
            )
        self.formation_time_min = _require_non_negative(self.formation_time_min, "formation_time_min")
        self.shared_distance_km = _require_non_negative(self.shared_distance_km, "shared_distance_km")
        self.shared_travel_time_min = _require_non_negative(self.shared_travel_time_min,
                                                           "shared_travel_time_min")
        if isinstance(self.corridor_edges_completed, bool) \
                or not isinstance(self.corridor_edges_completed, int) \
                or self.corridor_edges_completed < 0:
            raise ModelValidationError("corridor_edges_completed must be an int >= 0")
        if self.corridor_edges_completed > self.corridor.edge_count:
            raise ModelValidationError(
                f"swarm {self.swarm_id!r}: completed {self.corridor_edges_completed} of only "
                f"{self.corridor.edge_count} corridor edges"
            )
        self.status = SwarmStatus(self.status)
        if not self.current_node_id:
            self.current_node_id = self.corridor.origin_node_id
        _require_text(self.current_node_id, "current_node_id")
        if self.split_time_min is not None:
            self.split_time_min = _require_non_negative(self.split_time_min, "split_time_min")

    # --- derived ----------------------------------------------------------------
    @property
    def size(self) -> int:
        return len(self.pod_ids)

    @property
    def is_active(self) -> bool:
        return self.status is SwarmStatus.ACTIVE

    @property
    def is_finished(self) -> bool:
        return self.status is SwarmStatus.COMPLETED

    @property
    def corridor_is_complete(self) -> bool:
        return self.corridor_edges_completed >= self.corridor.edge_count

    @property
    def members_still_travelling(self) -> tuple[str, ...]:
        """Members that have not left the formation early."""
        return tuple(p for p in self.pod_ids if p not in set(self.departed_pod_ids))

    def capacity(self, pod_capacities: Sequence[int]) -> int:
        """Total seats across the members.

        A swarm's capacity is the SUM of its pods' capacities, but passengers are
        never moved between pods: the swarm is a coordinated formation, not one
        cabin. This figure is reporting only — it grants no pooling.
        """
        if len(pod_capacities) != self.size:
            raise ModelValidationError(
                f"swarm {self.swarm_id!r} has {self.size} pods but {len(pod_capacities)} capacities"
            )
        return sum(pod_capacities)

    def coordinated_pod_km(self) -> float:
        """Pod-km driven in formation: corridor distance × members.

        Four pods over a 5 km corridor is 20 pod-km and 5 corridor-km. Platooning
        does not turn four pods into one vehicle.
        """
        return self.shared_distance_km * self.size

    # --- state machine ----------------------------------------------------------
    def set_status(self, new_status: SwarmStatus) -> None:
        try:
            new_status = SwarmStatus(new_status)
        except ValueError as exc:
            allowed = ", ".join(s.value for s in SwarmStatus)
            raise SwarmStateError(f"unknown swarm status {new_status!r} (allowed: {allowed})") from exc
        if new_status is self.status:
            raise SwarmStateError(f"swarm {self.swarm_id!r} is already {self.status.value}")
        if new_status not in ALLOWED_SWARM_TRANSITIONS[self.status]:
            allowed = ", ".join(sorted(s.value for s in ALLOWED_SWARM_TRANSITIONS[self.status]))
            raise SwarmStateError(
                f"swarm {self.swarm_id!r}: illegal transition {self.status.value} -> "
                f"{new_status.value} (allowed from {self.status.value}: {allowed or 'nothing'})"
            )
        self.status = new_status

    def activate(self) -> None:
        self.set_status(SwarmStatus.ACTIVE)

    def begin_split(self, time_min: float) -> None:
        self.set_status(SwarmStatus.SPLITTING)
        self.split_time_min = _require_non_negative(time_min, "time_min")

    def complete(self, time_min: float | None = None) -> None:
        self.set_status(SwarmStatus.COMPLETED)
        if time_min is not None and self.split_time_min is None:
            self.split_time_min = _require_non_negative(time_min, "time_min")

    def record_progress(self, *, node_id: str, edges_completed: int,
                        distance_km: float, travel_time_min: float) -> None:
        """Update the swarm's own position along its corridor."""
        _require_text(node_id, "node_id")
        if isinstance(edges_completed, bool) or not isinstance(edges_completed, int) \
                or edges_completed < self.corridor_edges_completed:
            raise ModelValidationError(
                f"swarm {self.swarm_id!r}: corridor progress cannot go backwards "
                f"({self.corridor_edges_completed} -> {edges_completed!r})"
            )
        if edges_completed > self.corridor.edge_count:
            raise ModelValidationError(
                f"swarm {self.swarm_id!r}: {edges_completed} exceeds the corridor's "
                f"{self.corridor.edge_count} edges"
            )
        self.current_node_id = node_id
        self.corridor_edges_completed = edges_completed
        self.shared_distance_km = _require_non_negative(distance_km, "distance_km")
        self.shared_travel_time_min = _require_non_negative(travel_time_min, "travel_time_min")

    def mark_departed(self, pod_id: str) -> None:
        """A member whose own trip ended inside the corridor leaves the formation."""
        if pod_id not in self.pod_ids:
            raise ModelValidationError(f"pod {pod_id!r} is not in swarm {self.swarm_id!r}")
        if pod_id not in self.departed_pod_ids:
            self.departed_pod_ids = tuple(sorted(self.departed_pod_ids + (pod_id,)))

    def add_successor(self, swarm_id: str) -> None:
        _require_text(swarm_id, "swarm_id")
        self.successor_swarm_ids = tuple(sorted(set(self.successor_swarm_ids + (swarm_id,))))

    def duration_min(self, now_min: float) -> float:
        """How long the swarm has existed (up to its split, if it has split)."""
        end = self.split_time_min if self.split_time_min is not None else now_min
        return max(0.0, end - self.formation_time_min)

    def to_dict(self) -> dict[str, Any]:
        return {
            "swarm_id": self.swarm_id,
            "pod_ids": list(self.pod_ids),
            "size": self.size,
            "leader_pod_id": self.leader_pod_id,
            "status": self.status.value,
            "corridor": self.corridor.to_dict(),
            "current_node_id": self.current_node_id,
            "corridor_edges_completed": self.corridor_edges_completed,
            "shared_distance_km": self.shared_distance_km,
            "shared_travel_time_min": self.shared_travel_time_min,
            "coordinated_pod_km": self.coordinated_pod_km(),
            "formation_time_min": self.formation_time_min,
            "split_time_min": self.split_time_min,
            "departed_pod_ids": list(self.departed_pod_ids),
            "successor_swarm_ids": list(self.successor_swarm_ids),
        }


@dataclass(frozen=True)
class SwarmSnapshot:
    """Immutable, fingerprintable swarm state at one instant.

    Same spirit as M1's ``NetworkSnapshot``, M2's ``DemandSnapshot`` and M3's
    ``FleetSnapshot``. Distances are rounded so the fingerprint carries no
    meaningless float noise.
    """

    time_min: float
    swarms: tuple[tuple[str, str, str, str, float, int], ...]
    status_counts: tuple[tuple[str, int], ...]
    formation_count: int = 0
    split_count: int = 0

    def __post_init__(self) -> None:
        _require_non_negative(self.time_min, "time_min")
        for name in ("formation_count", "split_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ModelValidationError(f"{name} must be an int >= 0, got {value!r}")
        seen: set[str] = set()
        rounded = []
        for swarm_id, status, members, node_id, distance, edges in self.swarms:
            if swarm_id in seen:
                raise ModelValidationError(f"duplicate swarm id in snapshot: {swarm_id!r}")
            seen.add(swarm_id)
            _require_non_negative(distance, f"distance for {swarm_id!r}")
            rounded.append((swarm_id, status, members, node_id,
                            round(float(distance), DISTANCE_SNAPSHOT_DECIMALS), edges))
        object.__setattr__(self, "swarms", tuple(sorted(rounded)))
        counts: set[str] = set()
        for name, total in self.status_counts:
            if name in counts:
                raise ModelValidationError(f"duplicate swarm status in snapshot: {name!r}")
            counts.add(name)
        object.__setattr__(self, "status_counts", tuple(sorted(self.status_counts)))

    def as_dict(self) -> dict[str, Any]:
        return {
            "time_min": self.time_min,
            "swarms": [list(row) for row in self.swarms],
            "status_counts": dict(self.status_counts),
            "formation_count": self.formation_count,
            "split_count": self.split_count,
        }

    def fingerprint(self) -> str:
        """Stable SHA-256 of the canonical JSON form (for reproducibility checks)."""
        canonical = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
