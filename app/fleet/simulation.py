"""Deterministic discrete-tick fleet simulation.

This is a discrete simulation, not a live control system: nothing sleeps, nothing
polls a wall clock, and a run is a pure function of (network, fleet, trips,
config). There is no RNG here at all — the only seeded randomness in M3 is fleet
initialisation, so a run's outcome is fixed once its inputs are.

TICK ORDER (fixed; part of the behavioural contract)
---------------------------------------------------
Each tick covers the interval [t, t + tick_minutes):

  1. finish charging — pods at or above ``target_charge_percent`` become IDLE
  2. release ARRIVED pods — they become IDLE (their trip is already COMPLETED)
  3. send flat IDLE pods to CHARGING (battery below ``low_battery_percent``)
  4. charge CHARGING pods by ``charge_percent_per_min * tick_minutes``
  5. depart pods that were ASSIGNED on an earlier tick (boarding is done)
  6. advance TRAVELING pods, completing edges and arrivals
  7. assign PENDING trips whose ``request_time_min`` has arrived
  8. give up on PENDING trips that have waited past ``max_trip_wait_min``
  9. advance the clock

Steps 5 and 7 are in that order on purpose: a pod assigned during this tick
departs on the *next* one, so ASSIGNED is a real, observable boarding state
rather than an instant no snapshot could ever catch.

Trips are considered in ``trip_id`` order and pods in ``pod_id`` order, so no
outcome can depend on dict iteration order.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.demand.models import TripRequest
from app.errors import FleetConfigError
from app.fleet.assignment import assign_trip
from app.fleet.config import DEFAULT_FLEET_CONFIG, FleetConfig
from app.fleet.models import (
    TIMED_OUT_PREFIX,
    FleetSnapshot,
    PodStatus,
    TripRecord,
    TripStatus,
)
from app.fleet.movement import MovementEvent, advance_pod, start_pod_travel
from app.fleet.pod_fleet import PodFleet
from app.network.graph import NetworkGraph

logger = logging.getLogger(__name__)

# Safety valve so a misconfigured run cannot loop forever.
DEFAULT_MAX_TICKS = 100_000


@dataclass(frozen=True)
class TickReport:
    """What one tick did."""

    tick_index: int
    start_time_min: float
    end_time_min: float
    assigned_trip_ids: tuple[str, ...] = ()
    failed_trip_ids: tuple[str, ...] = ()
    completed_trip_ids: tuple[str, ...] = ()
    departed_pod_ids: tuple[str, ...] = ()
    released_pod_ids: tuple[str, ...] = ()
    charging_started_pod_ids: tuple[str, ...] = ()
    charging_finished_pod_ids: tuple[str, ...] = ()
    events: tuple[MovementEvent, ...] = ()

    @property
    def did_something(self) -> bool:
        return bool(self.assigned_trip_ids or self.failed_trip_ids or self.completed_trip_ids
                    or self.departed_pod_ids or self.released_pod_ids
                    or self.charging_started_pod_ids or self.charging_finished_pod_ids
                    or self.events)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tick_index": self.tick_index,
            "start_time_min": self.start_time_min,
            "end_time_min": self.end_time_min,
            "assigned_trip_ids": list(self.assigned_trip_ids),
            "failed_trip_ids": list(self.failed_trip_ids),
            "completed_trip_ids": list(self.completed_trip_ids),
            "departed_pod_ids": list(self.departed_pod_ids),
            "released_pod_ids": list(self.released_pod_ids),
            "charging_started_pod_ids": list(self.charging_started_pod_ids),
            "charging_finished_pod_ids": list(self.charging_finished_pod_ids),
            "events": [event.to_dict() for event in self.events],
        }


@dataclass(frozen=True)
class RunReport:
    """Summary of a completed ``run()``."""

    ticks: int
    start_time_min: float
    end_time_min: float
    stopped_because: str
    tick_reports: tuple[TickReport, ...] = field(default=(), repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {"ticks": self.ticks, "start_time_min": self.start_time_min,
                "end_time_min": self.end_time_min, "stopped_because": self.stopped_because}


class FleetSimulation:
    """Drives a PodFleet through a set of TripRequests, tick by tick.

    Trips are M2's ``TripRequest`` objects used as they are: M3 neither
    reimplements them nor changes how demand is generated. Each trip is routed by
    M2's ``route_trip`` at assignment time, so it picks up the traffic in force
    when the pod actually sets out.
    """

    def __init__(self, graph: NetworkGraph, fleet: PodFleet, trips: Iterable[TripRequest],
                 config: FleetConfig = DEFAULT_FLEET_CONFIG, *, start_time_min: float = 0.0,
                 algorithm: str = "astar") -> None:
        if not isinstance(config, FleetConfig):
            raise FleetConfigError(f"config must be a FleetConfig, got {type(config).__name__}")
        if isinstance(start_time_min, bool) or not isinstance(start_time_min, (int, float)) \
                or start_time_min < 0:
            raise FleetConfigError(f"start_time_min must be a number >= 0, got {start_time_min!r}")

        self._graph = graph
        self._fleet = fleet
        self._config = config
        self._algorithm = algorithm
        self._time_min = float(start_time_min)
        self._tick_index = 0

        self._trips: dict[str, TripRequest] = {}
        self._records: dict[str, TripRecord] = {}
        for trip in trips:
            if not isinstance(trip, TripRequest):
                raise FleetConfigError(f"expected TripRequest, got {type(trip).__name__}")
            if trip.trip_id in self._trips:
                raise FleetConfigError(f"duplicate trip id {trip.trip_id!r}")
            self._trips[trip.trip_id] = trip
            self._records[trip.trip_id] = TripRecord(
                trip_id=trip.trip_id, party_size=trip.party_size,
                origin_node_id=trip.origin_node_id, destination_node_id=trip.destination_node_id,
                request_time_min=trip.request_time_min,
            )
        # Fixed, deterministic processing order for assignment.
        self._trip_order: tuple[str, ...] = tuple(sorted(self._trips))
        # Which trip each busy pod is serving, so arrivals can be attributed.
        self._pod_trip: dict[str, str] = {}
        # Odometer readings when a pod departed, so per-trip energy and driving
        # time are measured rather than estimated.
        self._pod_baseline: dict[str, tuple[float, float]] = {}

    # --- read-only state --------------------------------------------------------
    @property
    def graph(self) -> NetworkGraph:
        return self._graph

    @property
    def fleet(self) -> PodFleet:
        return self._fleet

    @property
    def config(self) -> FleetConfig:
        return self._config

    @property
    def time_min(self) -> float:
        return self._time_min

    @property
    def tick_index(self) -> int:
        return self._tick_index

    def trip_request(self, trip_id: str) -> TripRequest:
        try:
            return self._trips[trip_id]
        except KeyError:
            raise FleetConfigError(f"no trip {trip_id!r} in this simulation") from None

    def records(self) -> tuple[TripRecord, ...]:
        """Every trip record, in trip_id order. Records are never deleted."""
        return tuple(self._records[trip_id] for trip_id in self._trip_order)

    def record(self, trip_id: str) -> TripRecord:
        try:
            return self._records[trip_id]
        except KeyError:
            raise FleetConfigError(f"no trip record for {trip_id!r}") from None

    def records_with_status(self, status: TripStatus) -> tuple[TripRecord, ...]:
        status = TripStatus(status)
        return tuple(r for r in self.records() if r.status is status)

    def trip_status_counts(self) -> dict[str, int]:
        """Every status appears, including zeros, so the shape is stable."""
        counts = {status.value: 0 for status in TripStatus}
        for rec in self._records.values():
            counts[rec.status.value] += 1
        return counts

    @property
    def has_pending_work(self) -> bool:
        """True while any trip is unresolved or any pod is still mid-trip."""
        if any(not rec.is_resolved for rec in self._records.values()):
            return True
        return any(pod.status in (PodStatus.ASSIGNED, PodStatus.TRAVELING, PodStatus.ARRIVED)
                   for pod in self._fleet.pods())

    def snapshot(self) -> FleetSnapshot:
        return FleetSnapshot(
            time_min=self._time_min,
            pods=tuple((pod.pod_id, pod.status.value, pod.current_node_id, pod.battery_percent,
                        pod.occupied_seats, pod.completed_trip_count) for pod in self._fleet.pods()),
            trip_status_counts=tuple(sorted(self.trip_status_counts().items())),
        )

    # --- the tick ---------------------------------------------------------------
    def tick(self) -> TickReport:
        """Advance the simulation by exactly one ``tick_minutes`` interval."""
        start = self._time_min
        dt = self._config.tick_minutes
        charging_finished = self._finish_charging()
        released = self._release_arrived()
        charging_started = self._send_flat_pods_to_charge()
        self._charge_pods(dt)
        departed = self._depart_assigned_pods()
        completed, events = self._advance_travelling_pods(dt)
        assigned, failed = self._assign_pending_trips()
        failed = failed + self._expire_waiting_trips()

        self._time_min = start + dt
        report = TickReport(
            tick_index=self._tick_index, start_time_min=start, end_time_min=self._time_min,
            assigned_trip_ids=assigned, failed_trip_ids=failed, completed_trip_ids=completed,
            departed_pod_ids=departed, released_pod_ids=released,
            charging_started_pod_ids=charging_started, charging_finished_pod_ids=charging_finished,
            events=events,
        )
        self._tick_index += 1
        return report

    def run(self, *, max_ticks: int = DEFAULT_MAX_TICKS, until_min: float | None = None) -> RunReport:
        """Tick until the work is done, the clock passes ``until_min``, or
        ``max_ticks`` is reached. Returns why it stopped."""
        if isinstance(max_ticks, bool) or not isinstance(max_ticks, int) or max_ticks < 0:
            raise FleetConfigError(f"max_ticks must be an int >= 0, got {max_ticks!r}")
        if until_min is not None and (isinstance(until_min, bool)
                                      or not isinstance(until_min, (int, float))):
            raise FleetConfigError(f"until_min must be a number or None, got {until_min!r}")

        start = self._time_min
        reports: list[TickReport] = []
        reason = "no pending work"
        while True:
            if not self.has_pending_work:
                reason = "no pending work"
                break
            if len(reports) >= max_ticks:
                reason = "max_ticks reached"
                break
            if until_min is not None and self._time_min >= until_min:
                reason = "until_min reached"
                break
            reports.append(self.tick())

        logger.info("fleet run stopped after %d ticks at minute %.2f (%s)",
                    len(reports), self._time_min, reason)
        return RunReport(ticks=len(reports), start_time_min=start, end_time_min=self._time_min,
                         stopped_because=reason, tick_reports=tuple(reports))

    # --- tick steps -------------------------------------------------------------
    def _finish_charging(self) -> tuple[str, ...]:
        finished = []
        for pod in self._fleet.pods_with_status(PodStatus.CHARGING):
            if pod.battery_percent >= self._config.target_charge_percent:
                pod.finish_charging()
                finished.append(pod.pod_id)
        return tuple(finished)

    def _release_arrived(self) -> tuple[str, ...]:
        released = []
        for pod in self._fleet.pods_with_status(PodStatus.ARRIVED):
            self._fleet.release_pod(pod.pod_id)
            self._pod_trip.pop(pod.pod_id, None)
            released.append(pod.pod_id)
        return tuple(released)

    def _send_flat_pods_to_charge(self) -> tuple[str, ...]:
        started = []
        for pod in self._fleet.idle_pods():
            if pod.battery_percent < self._config.low_battery_percent:
                pod.begin_charging()
                started.append(pod.pod_id)
        return tuple(started)

    def _charge_pods(self, dt: float) -> None:
        added = self._config.charge_percent_per_min * dt
        for pod in self._fleet.pods_with_status(PodStatus.CHARGING):
            pod.charge(added)

    def _depart_assigned_pods(self) -> tuple[str, ...]:
        departed = []
        for pod in self._fleet.pods_with_status(PodStatus.ASSIGNED):
            self._pod_baseline[pod.pod_id] = (pod.total_energy_kwh, pod.total_travel_time_min)
            start_pod_travel(self._graph, pod, self._config)
            trip_id = self._pod_trip.get(pod.pod_id)
            if trip_id is not None:
                self._records[trip_id].status = TripStatus.IN_PROGRESS
            departed.append(pod.pod_id)
        return tuple(departed)

    def _advance_travelling_pods(self, dt: float) -> tuple[tuple[str, ...], tuple[MovementEvent, ...]]:
        completed: list[str] = []
        events: list[MovementEvent] = []
        for pod in self._fleet.pods_with_status(PodStatus.TRAVELING):
            _, pod_events = advance_pod(self._graph, pod, dt, self._time_min, self._config)
            events.extend(pod_events)
            for event in pod_events:
                if event.kind != "arrived":
                    continue
                trip_id = self._pod_trip.get(pod.pod_id)
                if trip_id is None:
                    continue
                rec = self._records[trip_id]
                rec.status = TripStatus.COMPLETED
                rec.completed_time_min = event.time_min
                if pod.route is not None:
                    rec.route_distance_km = pod.route.total_distance_km
                energy_at_start, time_at_start = self._pod_baseline.pop(pod.pod_id, (0.0, 0.0))
                rec.energy_kwh = pod.total_energy_kwh - energy_at_start
                rec.actual_travel_time_min = pod.total_travel_time_min - time_at_start
                completed.append(trip_id)
        return tuple(completed), tuple(events)

    def _assign_pending_trips(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        assigned: list[str] = []
        failed: list[str] = []
        for trip_id in self._trip_order:
            rec = self._records[trip_id]
            if rec.status is not TripStatus.PENDING:
                continue
            if rec.request_time_min > self._time_min:
                continue          # not requested yet
            trip = self._trips[trip_id]
            result = assign_trip(self._fleet, self._graph, trip, self._config, self._algorithm)
            if result.is_unroutable:
                rec.status = TripStatus.FAILED
                rec.failure_reason = result.reason
                failed.append(trip_id)
                continue
            if not result.is_assigned:
                continue          # no pod free right now; retry on a later tick
            pod = self._fleet.get_pod(result.pod_id)
            rec.status = TripStatus.ASSIGNED
            rec.pod_id = result.pod_id
            rec.assigned_time_min = self._time_min
            rec.route_distance_km = result.route.total_distance_km
            rec.route_travel_time_min = result.route.total_travel_time_min
            self._pod_trip[pod.pod_id] = trip_id
            assigned.append(trip_id)
        return tuple(assigned), tuple(failed)

    def _expire_waiting_trips(self) -> tuple[str, ...]:
        """Give up on trips that waited too long. Without this, a trip whose origin
        never sees an idle pod would keep the simulation running forever."""
        limit = self._config.max_trip_wait_min
        expired = []
        for trip_id in self._trip_order:
            rec = self._records[trip_id]
            if rec.status is not TripStatus.PENDING:
                continue
            if self._time_min - rec.request_time_min > limit:
                rec.status = TripStatus.FAILED
                rec.failure_reason = f"{TIMED_OUT_PREFIX} {limit:g} min of the request"
                expired.append(trip_id)
        return tuple(expired)
