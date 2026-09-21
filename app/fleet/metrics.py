"""Deterministic fleet metrics.

Every figure is rounded to a modest number of decimals: this is a synthetic
simulation with an approximated battery model, so reporting energy to ten decimal
places would be fake precision. An average over zero samples is ``None`` rather
than a misleading ``0.0``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.fleet.models import TIMED_OUT_PREFIX, UNROUTABLE_PREFIX, PodStatus, TripRecord, TripStatus
from app.fleet.pod_fleet import PodFleet

DISTANCE_DECIMALS = 3
TIME_DECIMALS = 2
ENERGY_DECIMALS = 3
RATIO_DECIMALS = 4


def _mean(values: Sequence[float], decimals: int) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), decimals)


@dataclass(frozen=True)
class FleetMetrics:
    """Aggregate view of a fleet simulation at one instant."""

    simulated_minutes: float
    # Pods
    total_pods: int
    idle_pods: int
    assigned_pods: int
    traveling_pods: int
    arrived_pods: int
    charging_pods: int
    total_capacity: int
    # Trips
    total_trips: int
    completed_trips: int
    pending_trips: int
    in_progress_trips: int
    assigned_trips: int
    failed_trips: int
    unroutable_trips: int
    timed_out_trips: int
    unassigned_trips: int
    # Averages and totals
    average_pod_utilization: float | None
    average_passenger_occupancy: float | None
    average_occupancy_rate: float | None
    total_distance_km: float
    total_travel_time_min: float
    total_energy_kwh: float
    average_energy_per_km: float | None
    average_trip_completion_time_min: float | None
    average_trip_wait_time_min: float | None
    total_passengers_carried: int

    @property
    def completion_rate(self) -> float | None:
        """Share of trips that were served, or None when there were no trips."""
        if self.total_trips == 0:
            return None
        return round(self.completed_trips / self.total_trips, RATIO_DECIMALS)

    def to_dict(self) -> dict[str, Any]:
        data = {field: getattr(self, field) for field in self.__dataclass_fields__}
        data["completion_rate"] = self.completion_rate
        return data


def compute_fleet_metrics(fleet: PodFleet, records: Sequence[TripRecord],
                          simulated_minutes: float) -> FleetMetrics:
    """Summarise a fleet and its trip records.

    ``average_pod_utilization`` is the share of the simulated window each pod
    spent actually driving, averaged over pods — not the share of pods that are
    currently busy.
    """
    pods = fleet.pods()
    counts = fleet.count_by_status()

    completed = [r for r in records if r.status is TripStatus.COMPLETED]
    failed = [r for r in records if r.status is TripStatus.FAILED]
    pending = [r for r in records if r.status is TripStatus.PENDING]
    unroutable = [r for r in failed if (r.failure_reason or "").startswith(UNROUTABLE_PREFIX)]
    timed_out = [r for r in failed if (r.failure_reason or "").startswith(TIMED_OUT_PREFIX)]

    total_distance = sum(pod.total_distance_km for pod in pods)
    total_time = sum(pod.total_travel_time_min for pod in pods)
    total_energy = sum(pod.total_energy_kwh for pod in pods)

    utilizations = ([pod.total_travel_time_min / simulated_minutes for pod in pods]
                    if simulated_minutes > 0 else [])
    occupancies = [float(r.party_size) for r in completed]
    capacity_rates = ([r.party_size / fleet.get_pod(r.pod_id).capacity
                       for r in completed if r.pod_id and fleet.has_pod(r.pod_id)])
    completion_times = [r.completion_time_min for r in completed if r.completion_time_min is not None]
    wait_times = [r.waiting_time_min for r in records if r.waiting_time_min is not None]

    return FleetMetrics(
        simulated_minutes=round(float(simulated_minutes), TIME_DECIMALS),
        total_pods=len(pods),
        idle_pods=counts[PodStatus.IDLE.value],
        assigned_pods=counts[PodStatus.ASSIGNED.value],
        traveling_pods=counts[PodStatus.TRAVELING.value],
        arrived_pods=counts[PodStatus.ARRIVED.value],
        charging_pods=counts[PodStatus.CHARGING.value],
        total_capacity=fleet.total_capacity(),
        total_trips=len(records),
        completed_trips=len(completed),
        pending_trips=len(pending),
        in_progress_trips=sum(1 for r in records if r.status is TripStatus.IN_PROGRESS),
        assigned_trips=sum(1 for r in records if r.status is TripStatus.ASSIGNED),
        failed_trips=len(failed),
        unroutable_trips=len(unroutable),
        timed_out_trips=len(timed_out),
        # Trips that never got a pod: still waiting, or given up on.
        unassigned_trips=len(pending) + len(timed_out),
        average_pod_utilization=_mean(utilizations, RATIO_DECIMALS),
        average_passenger_occupancy=_mean(occupancies, RATIO_DECIMALS),
        average_occupancy_rate=_mean(capacity_rates, RATIO_DECIMALS),
        total_distance_km=round(total_distance, DISTANCE_DECIMALS),
        total_travel_time_min=round(total_time, TIME_DECIMALS),
        total_energy_kwh=round(total_energy, ENERGY_DECIMALS),
        average_energy_per_km=(round(total_energy / total_distance, ENERGY_DECIMALS)
                               if total_distance > 0 else None),
        average_trip_completion_time_min=_mean(completion_times, TIME_DECIMALS),
        average_trip_wait_time_min=_mean(wait_times, TIME_DECIMALS),
        total_passengers_carried=sum(r.party_size for r in completed),
    )
