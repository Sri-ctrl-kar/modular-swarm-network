"""Dispatching and tracking repositioning moves.

A repositioning pod drives **empty**, using M3's movement machinery unchanged:
``pod.assign(..., kind=TripKind.REPOSITIONING)`` puts it in ASSIGNED with **zero**
occupied seats, and from there M3's own departure, ``advance_pod`` and release run
exactly as for a passenger trip. **There is no second movement model, no second
cost model and no second congestion model** — the route is an M1 route and the
energy is M3's battery model.

PASSENGER_TRIP VERSUS REPOSITIONING_TRIP
----------------------------------------
Kept apart at every level, so an empty move can never flatter a passenger figure:

* the pod records it under ``completed_repositioning_count``, never
  ``completed_trip_count``;
* no ``TripRecord`` is created, so M3's trip metrics never see it;
* occupied seats stay 0, so occupancy is untouched;
* its kilometres are reported here as **deadhead**, and never netted off anything.

The kilometres, minutes and kWh are real: a repositioning pod adds distance and
energy to the fleet totals. That is the cost of rebalancing and it is reported, not
hidden.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.errors import RepositioningError
from app.fleet.models import Pod, PodStatus, TripKind
from app.fleet.pod_fleet import PodFleet
from app.rebalancing.planner import RepositionPlanItem, RepositionReason

logger = logging.getLogger(__name__)

REPOSITION_ID_TEMPLATE = "RP{index:05d}"
DECIMALS = 4


def reposition_id_for(index: int) -> str:
    """Stable, sortable id for the ``index``-th dispatched move (1-based)."""
    if isinstance(index, bool) or not isinstance(index, int) or index < 1:
        raise RepositioningError(f"reposition index must be an int >= 1, got {index!r}")
    return REPOSITION_ID_TEMPLATE.format(index=index)


class RepositionStatus(str, Enum):
    DISPATCHED = "dispatched"      # pod holds the move, not yet driving
    IN_PROGRESS = "in_progress"    # pod is driving empty
    COMPLETED = "completed"        # pod reached the target node
    FAILED = "failed"              # could not be completed


@dataclass
class RepositionAssignment:
    """One dispatched repositioning move, from decision to arrival.

    Records are never deleted, so a finished run keeps the full rebalancing history
    alongside its costs.
    """

    reposition_id: str
    pod_id: str
    origin_node_id: str
    target_node_id: str
    reason: RepositionReason
    priority: int
    priority_score: float
    estimated_distance_km: float
    estimated_travel_time_min: float
    estimated_energy_kwh: float
    dispatch_time_min: float
    target_deficit_at_dispatch: float
    status: RepositionStatus = RepositionStatus.DISPATCHED
    arrival_time_min: float | None = None
    actual_distance_km: float | None = None
    actual_travel_time_min: float | None = None
    actual_energy_kwh: float | None = None
    failure_reason: str | None = None

    @property
    def is_resolved(self) -> bool:
        return self.status in (RepositionStatus.COMPLETED, RepositionStatus.FAILED)

    @property
    def duration_min(self) -> float | None:
        if self.arrival_time_min is None:
            return None
        return round(self.arrival_time_min - self.dispatch_time_min, DECIMALS)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reposition_id": self.reposition_id,
            "pod_id": self.pod_id,
            "origin_node_id": self.origin_node_id,
            "target_node_id": self.target_node_id,
            "reason": self.reason.value,
            "priority": self.priority,
            "priority_score": self.priority_score,
            "estimated_distance_km": self.estimated_distance_km,
            "estimated_travel_time_min": self.estimated_travel_time_min,
            "estimated_energy_kwh": self.estimated_energy_kwh,
            "dispatch_time_min": self.dispatch_time_min,
            "target_deficit_at_dispatch": self.target_deficit_at_dispatch,
            "status": self.status.value,
            "arrival_time_min": self.arrival_time_min,
            "duration_min": self.duration_min,
            "actual_distance_km": self.actual_distance_km,
            "actual_travel_time_min": self.actual_travel_time_min,
            "actual_energy_kwh": self.actual_energy_kwh,
            "failure_reason": self.failure_reason,
        }


def dispatch(fleet: PodFleet, item: RepositionPlanItem, reposition_id: str,
             now_min: float) -> RepositionAssignment:
    """Give a pod an empty repositioning assignment.

    Raises ``RepositioningError`` rather than silently skipping if the pod is no
    longer idle: a caller that races the fleet should hear about it.
    """
    pod = fleet.get_pod(item.pod_id)
    if pod.status is not PodStatus.IDLE:
        raise RepositioningError(
            f"pod {pod.pod_id!r} is {pod.status.value}, not idle, so it cannot be repositioned"
        )
    if pod.current_node_id != item.origin_node_id:
        raise RepositioningError(
            f"pod {pod.pod_id!r} is at {pod.current_node_id!r}, not {item.origin_node_id!r}"
        )
    # party_size=0 — the pod travels empty, and M3 enforces that for this kind.
    pod.assign(reposition_id, item.route, 0, TripKind.REPOSITIONING)
    logger.debug("dispatched %s: %s %s -> %s (%s)", reposition_id, pod.pod_id,
                 item.origin_node_id, item.target_node_id, item.reason.value)
    return RepositionAssignment(
        reposition_id=reposition_id, pod_id=pod.pod_id,
        origin_node_id=item.origin_node_id, target_node_id=item.target_node_id,
        reason=item.reason, priority=item.request.priority,
        priority_score=item.priority_score,
        estimated_distance_km=item.estimated_distance_km,
        estimated_travel_time_min=item.estimated_travel_time_min,
        estimated_energy_kwh=item.estimated_energy_kwh,
        dispatch_time_min=float(now_min),
        target_deficit_at_dispatch=item.target_deficit,
    )


def complete(assignment: RepositionAssignment, pod: Pod, arrival_time_min: float,
             distance_km: float, travel_time_min: float, energy_kwh: float) -> None:
    """Mark a move finished, recording what it actually cost."""
    if assignment.is_resolved:
        raise RepositioningError(
            f"{assignment.reposition_id} is already {assignment.status.value}")
    if pod.current_node_id != assignment.target_node_id:
        raise RepositioningError(
            f"{assignment.reposition_id}: pod {pod.pod_id!r} is at {pod.current_node_id!r}, "
            f"not the target {assignment.target_node_id!r}"
        )
    assignment.status = RepositionStatus.COMPLETED
    assignment.arrival_time_min = float(arrival_time_min)
    assignment.actual_distance_km = round(distance_km, DECIMALS)
    assignment.actual_travel_time_min = round(travel_time_min, DECIMALS)
    assignment.actual_energy_kwh = round(energy_kwh, DECIMALS)


def fail(assignment: RepositionAssignment, reason: str) -> None:
    """Mark a move failed. Records are kept, never deleted."""
    if assignment.status is RepositionStatus.COMPLETED:
        raise RepositioningError(
            f"{assignment.reposition_id} already completed and cannot be failed")
    assignment.status = RepositionStatus.FAILED
    assignment.failure_reason = reason
