"""Deterministic swarm simulation: M3's fleet plus formation, platooning and splitting.

``SwarmSimulation`` extends ``FleetSimulation`` rather than replacing it, so
**M3 needed no edit at all**: charging, assignment, trip records, the dispatch
timeout and the battery model are inherited unchanged. Two steps are overridden:

* **departure** — assigned pods may wait up to ``max_formation_delay_min`` for
  compatible partners, and those that find partners depart together as a swarm;
* **movement** — the members of an ACTIVE swarm are advanced as a unit by
  ``advance_swarm``, while everyone else moves exactly as in M3.

``enable_swarms=False`` restores M3's behaviour precisely (immediate departure,
independent movement), which is what makes the baseline comparison in
``app/swarm/metrics.py`` a controlled one: same network, demand, fleet, seed and
horizon, one switch.

TICK ORDER — M3's order, with splitting inserted before departure
-----------------------------------------------------------------
  1. finish charging          5. split swarms whose corridor is finished  (M4)
  2. release arrived pods     6. form swarms, then depart                 (M4)
  3. flat pods to charging     7. advance swarms as units, then loners     (M4)
  4. charge charging pods     8. assign pending trips
                              9. expire long-waiting trips
                             10. advance the clock

Steps 5–7 sit exactly where M3's departure and movement steps were, so the rest
of the order is untouched. There is **no RNG anywhere in this module**; swarm
formation is a sorted scan against fixed thresholds.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.demand.models import TripRequest
from app.errors import SwarmConfigError, SwarmNotFoundError
from app.fleet.config import DEFAULT_FLEET_CONFIG, FleetConfig
from app.fleet.models import PodStatus, TripStatus
from app.fleet.movement import MovementEvent, advance_pod, start_pod_travel
from app.fleet.pod_fleet import PodFleet
from app.fleet.simulation import FleetSimulation, TickReport
from app.network.graph import NetworkGraph
from app.swarm.config import DEFAULT_SWARM_CONFIG, SwarmConfig
from app.swarm.formation import build_swarms, plan_formation, swarm_id_for
from app.swarm.models import Swarm, SwarmSnapshot, SwarmStatus
from app.swarm.movement import advance_swarm

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SwarmTickReport:
    """M3's tick report plus what the swarm layer did during the same tick."""

    base: TickReport
    formed_swarm_ids: tuple[str, ...] = ()
    split_swarm_ids: tuple[str, ...] = ()
    held_pod_ids: tuple[str, ...] = ()          # waiting at a node for partners
    platooned_pod_ids: tuple[str, ...] = ()     # moved as part of a formation

    @property
    def tick_index(self) -> int:
        return self.base.tick_index

    @property
    def start_time_min(self) -> float:
        return self.base.start_time_min

    @property
    def end_time_min(self) -> float:
        return self.base.end_time_min

    # The M3 tick fields are forwarded, so callers can treat a SwarmTickReport as a
    # TickReport that happens to know about swarms too.
    @property
    def assigned_trip_ids(self) -> tuple[str, ...]:
        return self.base.assigned_trip_ids

    @property
    def failed_trip_ids(self) -> tuple[str, ...]:
        return self.base.failed_trip_ids

    @property
    def completed_trip_ids(self) -> tuple[str, ...]:
        return self.base.completed_trip_ids

    @property
    def departed_pod_ids(self) -> tuple[str, ...]:
        return self.base.departed_pod_ids

    @property
    def released_pod_ids(self) -> tuple[str, ...]:
        return self.base.released_pod_ids

    @property
    def charging_started_pod_ids(self) -> tuple[str, ...]:
        return self.base.charging_started_pod_ids

    @property
    def charging_finished_pod_ids(self) -> tuple[str, ...]:
        return self.base.charging_finished_pod_ids

    @property
    def events(self):
        return self.base.events

    @property
    def did_something(self) -> bool:
        return self.base.did_something or bool(
            self.formed_swarm_ids or self.split_swarm_ids or self.held_pod_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.base.to_dict(),
            "formed_swarm_ids": list(self.formed_swarm_ids),
            "split_swarm_ids": list(self.split_swarm_ids),
            "held_pod_ids": list(self.held_pod_ids),
            "platooned_pod_ids": list(self.platooned_pod_ids),
        }


@dataclass
class _TickAccumulator:
    """Per-tick swarm events, collected by the overridden steps."""

    formed: list[str] = field(default_factory=list)
    split: list[str] = field(default_factory=list)
    held: list[str] = field(default_factory=list)
    platooned: list[str] = field(default_factory=list)


class SwarmSimulation(FleetSimulation):
    def __init__(self, graph: NetworkGraph, fleet: PodFleet, trips: Iterable[TripRequest],
                 config: FleetConfig = DEFAULT_FLEET_CONFIG, *,
                 swarm_config: SwarmConfig = DEFAULT_SWARM_CONFIG, enable_swarms: bool = True,
                 start_time_min: float = 0.0, algorithm: str = "astar") -> None:
        if not isinstance(swarm_config, SwarmConfig):
            raise SwarmConfigError(
                f"swarm_config must be a SwarmConfig, got {type(swarm_config).__name__}"
            )
        if not isinstance(enable_swarms, bool):
            raise SwarmConfigError(f"enable_swarms must be a bool, got {enable_swarms!r}")
        super().__init__(graph, fleet, trips, config,
                         start_time_min=start_time_min, algorithm=algorithm)
        self._swarm_config = swarm_config
        self._enable_swarms = enable_swarms
        self._swarms: dict[str, Swarm] = {}
        self._pod_swarm: dict[str, str] = {}
        self._swarm_counter = 0
        self._formation_count = 0
        self._split_count = 0
        self._formation_attempts = 0
        self._accumulator = _TickAccumulator()

    # --- read-only state --------------------------------------------------------
    @property
    def swarm_config(self) -> SwarmConfig:
        return self._swarm_config

    @property
    def swarms_enabled(self) -> bool:
        return self._enable_swarms

    @property
    def formation_count(self) -> int:
        return self._formation_count

    @property
    def split_count(self) -> int:
        return self._split_count

    @property
    def formation_attempts(self) -> int:
        """Ticks on which at least one pod was a formation candidate."""
        return self._formation_attempts

    def swarms(self) -> tuple[Swarm, ...]:
        """Every swarm ever formed, in swarm_id order. Never deleted."""
        return tuple(self._swarms[swarm_id] for swarm_id in sorted(self._swarms))

    def swarm(self, swarm_id: str) -> Swarm:
        try:
            return self._swarms[swarm_id]
        except KeyError:
            raise SwarmNotFoundError(f"no swarm with id {swarm_id!r}") from None

    def active_swarms(self) -> tuple[Swarm, ...]:
        return tuple(s for s in self.swarms() if s.status is SwarmStatus.ACTIVE)

    def swarm_of_pod(self, pod_id: str) -> Swarm | None:
        swarm_id = self._pod_swarm.get(pod_id)
        return None if swarm_id is None else self._swarms[swarm_id]

    def swarm_status_counts(self) -> dict[str, int]:
        counts = {status.value: 0 for status in SwarmStatus}
        for swarm in self._swarms.values():
            counts[swarm.status.value] += 1
        return counts

    def pods_in_swarms(self) -> tuple[str, ...]:
        """Pods currently coordinating in an active swarm, in pod_id order."""
        return tuple(sorted(pod_id for pod_id, swarm_id in self._pod_swarm.items()
                            if self._swarms[swarm_id].status is SwarmStatus.ACTIVE))

    def swarm_snapshot(self) -> SwarmSnapshot:
        return SwarmSnapshot(
            time_min=self.time_min,
            swarms=tuple((s.swarm_id, s.status.value, "|".join(s.pod_ids), s.current_node_id,
                          s.shared_distance_km, s.corridor_edges_completed)
                         for s in self.swarms()),
            status_counts=tuple(sorted(self.swarm_status_counts().items())),
            formation_count=self._formation_count,
            split_count=self._split_count,
        )

    # --- the tick ---------------------------------------------------------------
    def tick(self) -> SwarmTickReport:
        """One tick of M3's loop, with swarm splitting, formation and platoon
        movement in place of plain departure and movement."""
        self._accumulator = _TickAccumulator()
        base = super().tick()
        return SwarmTickReport(
            base=base,
            formed_swarm_ids=tuple(self._accumulator.formed),
            split_swarm_ids=tuple(self._accumulator.split),
            held_pod_ids=tuple(sorted(self._accumulator.held)),
            platooned_pod_ids=tuple(sorted(self._accumulator.platooned)),
        )

    # --- overridden step: departure (splitting + formation live here) ------------
    def _depart_assigned_pods(self) -> tuple[str, ...]:
        if not self._enable_swarms:
            return super()._depart_assigned_pods()

        # Step 5 — swarms that finished their corridor separate before anyone departs.
        self._split_finished_swarms()

        assigned = self._fleet.pods_with_status(PodStatus.ASSIGNED)
        if not assigned:
            return ()
        self._formation_attempts += 1

        # Step 6 — plan formations among the pods waiting to set out.
        plan = plan_formation(self._graph, assigned, self._swarm_config)
        new_swarms = build_swarms(plan, self.time_min, self._next_swarm_id)

        departed: list[str] = []
        for swarm in new_swarms:
            self._register_swarm(swarm)
            for pod_id in swarm.pod_ids:
                self._depart_one(self._fleet.get_pod(pod_id))
                departed.append(pod_id)
            swarm.activate()
            self._accumulator.formed.append(swarm.swarm_id)
            logger.debug("formed %s: %s over %d edges (%.2f km)", swarm.swarm_id,
                         ", ".join(swarm.pod_ids), swarm.corridor.edge_count,
                         swarm.corridor.distance_km)

        # Pods nobody could platoon with wait for partners, up to the delay budget.
        grouped = set(plan.pods_in_groups)
        for pod in assigned:
            if pod.pod_id in grouped:
                continue
            if self._waited_long_enough(pod.pod_id):
                self._depart_one(pod)
                departed.append(pod.pod_id)
            else:
                self._accumulator.held.append(pod.pod_id)
        return tuple(departed)

    def _waited_long_enough(self, pod_id: str) -> bool:
        """True once a pod has held at its origin for the full formation delay."""
        trip_id = self._pod_trip.get(pod_id)
        if trip_id is None:
            return True
        assigned_at = self._records[trip_id].assigned_time_min
        if assigned_at is None:
            return True
        return (self.time_min - assigned_at) >= self._swarm_config.max_formation_delay_min

    def _depart_one(self, pod) -> None:
        """M3's departure bookkeeping for a single pod (odometer baseline, first edge)."""
        self._pod_baseline[pod.pod_id] = (pod.total_energy_kwh, pod.total_travel_time_min)
        start_pod_travel(self._graph, pod, self._config)
        trip_id = self._pod_trip.get(pod.pod_id)
        if trip_id is not None:
            self._records[trip_id].status = TripStatus.IN_PROGRESS

    # --- overridden step: movement ----------------------------------------------
    def _advance_travelling_pods(self, dt: float) -> tuple[tuple[str, ...], tuple[MovementEvent, ...]]:
        if not self._enable_swarms:
            return super()._advance_travelling_pods(dt)

        completed: list[str] = []
        events: list[MovementEvent] = []
        pods_by_id = {pod.pod_id: pod for pod in self._fleet.pods()}

        # Step 7a — active swarms move as units, in swarm_id order.
        moved_with_swarm: set[str] = set()
        for swarm in self.active_swarms():
            members = [pods_by_id[p] for p in swarm.members_still_travelling if p in pods_by_id]
            travelling = [p for p in members if p.status is PodStatus.TRAVELING]
            if not travelling:
                continue
            result = advance_swarm(self._graph, swarm, pods_by_id, dt, self.time_min, self._config)
            events.extend(result.events)
            moved_with_swarm.update(p.pod_id for p in travelling)
            self._accumulator.platooned.extend(p.pod_id for p in travelling)
            for event in result.events:
                if event.kind == "arrived":
                    trip_id = self._finish_trip(event.pod_id, event)
                    if trip_id is not None:
                        completed.append(trip_id)

        # Step 7b — everyone else moves independently, exactly as in M3.
        for pod in self._fleet.pods_with_status(PodStatus.TRAVELING):
            if pod.pod_id in moved_with_swarm:
                continue
            _, pod_events = advance_pod(self._graph, pod, dt, self.time_min, self._config)
            events.extend(pod_events)
            for event in pod_events:
                if event.kind == "arrived":
                    trip_id = self._finish_trip(pod.pod_id, event)
                    if trip_id is not None:
                        completed.append(trip_id)

        return tuple(completed), tuple(events)

    def _finish_trip(self, pod_id: str, event: MovementEvent) -> str | None:
        """Close out an arriving pod's trip record.

        Mirrors M3's own completion bookkeeping field for field; the swarm layer
        needs it because it drives movement itself for platooned pods.
        """
        trip_id = self._pod_trip.get(pod_id)
        if trip_id is None:
            return None
        pod = self._fleet.get_pod(pod_id)
        rec = self._records[trip_id]
        rec.status = TripStatus.COMPLETED
        rec.completed_time_min = event.time_min
        if pod.route is not None:
            rec.route_distance_km = pod.route.total_distance_km
        energy_at_start, time_at_start = self._pod_baseline.pop(pod_id, (0.0, 0.0))
        rec.energy_kwh = pod.total_energy_kwh - energy_at_start
        rec.actual_travel_time_min = pod.total_travel_time_min - time_at_start
        self._leave_swarm(pod_id)
        return trip_id

    # --- swarm bookkeeping ------------------------------------------------------
    def _next_swarm_id(self) -> str:
        self._swarm_counter += 1
        return swarm_id_for(self._swarm_counter)

    def _register_swarm(self, swarm: Swarm) -> None:
        self._swarms[swarm.swarm_id] = swarm
        for pod_id in swarm.pod_ids:
            self._pod_swarm[pod_id] = swarm.swarm_id
        self._formation_count += 1

    def _leave_swarm(self, pod_id: str) -> None:
        swarm_id = self._pod_swarm.pop(pod_id, None)
        if swarm_id is not None:
            self._swarms[swarm_id].mark_departed(pod_id)

    def _split_finished_swarms(self) -> None:
        """Split every ACTIVE swarm whose shared corridor is behind it.

        Members standing at the divergence node are offered to the formation
        planner again, so a subgroup that still shares a corridor continues
        together as a NEW swarm while the rest become independent. Because the
        planner enforces ``min_formation_stability_min``, a continuation group that
        would disband almost at once is never formed — which is what keeps
        formation and splitting from oscillating.
        """
        pods_by_id = {pod.pod_id: pod for pod in self._fleet.pods()}
        for swarm in self.active_swarms():
            if not swarm.corridor_is_complete:
                continue
            swarm.begin_split(self.time_min)
            self._split_count += 1
            self._accumulator.split.append(swarm.swarm_id)

            members = [pods_by_id[p] for p in swarm.members_still_travelling if p in pods_by_id]
            for pod_id in list(swarm.pod_ids):
                self._pod_swarm.pop(pod_id, None)

            candidates = [p for p in members
                          if p.status is PodStatus.TRAVELING and p.remaining_edge_ids]
            if len(candidates) >= 2:
                plan = plan_formation(self._graph, candidates, self._swarm_config)
                for successor in build_swarms(plan, self.time_min, self._next_swarm_id):
                    self._register_swarm(successor)
                    successor.activate()
                    swarm.add_successor(successor.swarm_id)
                    self._accumulator.formed.append(successor.swarm_id)
            swarm.complete(self.time_min)
            logger.debug("split %s at %s -> successors %s", swarm.swarm_id,
                         swarm.current_node_id, swarm.successor_swarm_ids or "(none)")
