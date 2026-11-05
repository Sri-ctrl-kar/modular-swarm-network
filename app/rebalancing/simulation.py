"""The M5 simulation: M4's swarm fleet plus adaptive rebalancing.

``RebalancingSimulation`` extends ``SwarmSimulation``, which extends
``FleetSimulation``, so **M1-M4 needed no edit beyond the one additive change to
``Pod``** that lets a pod be dispatched empty (see ``app/fleet/models.py``,
``TripKind``). Charging, passenger assignment, trip records, the dispatch timeout,
the battery model, swarm formation, platoon movement and splitting are all
inherited unchanged.

``enable_rebalancing=False`` reproduces M4 exactly, which is what makes the
before/after experiment a controlled one: same network, demand, fleet, seed,
horizon and congestion, one switch.

TICK ORDER — M4's order, with a rebalancing cycle appended
---------------------------------------------------------
  1-9.  exactly M4's tick (charging, release, split, form, depart, move, assign,
        expire, clock)
  10.   note any repositioning pods that arrived during step 7
  11.   every ``rebalance_interval_min``: forecast -> demand map -> plan -> dispatch

The cycle runs **after** passenger assignment, so it only ever sees pods that
passenger service did not want. A dispatched pod departs on the next tick, exactly
like a passenger pod, and is excluded from swarm formation: an empty pod has no
passengers to coordinate, so it never platoons.

There is **no RNG anywhere in this layer**; fleet initialisation remains the only
seeded step in the whole simulation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable

from app.demand.config import BASELINE_DEMAND_PROFILE, DemandProfile
from app.demand.models import TripRequest
from app.errors import RebalancingConfigError
from app.fleet.config import DEFAULT_FLEET_CONFIG, FleetConfig
from app.fleet.models import PodStatus, TripStatus
from app.fleet.movement import MovementEvent, start_pod_travel
from app.fleet.pod_fleet import PodFleet
from app.network.graph import NetworkGraph
from app.rebalancing.config import DEFAULT_REBALANCING_CONFIG, RebalancingConfig
from app.rebalancing.demand_map import SpatialDemandMap, build_demand_map
from app.rebalancing.execution import (
    RepositionAssignment,
    RepositionStatus,
    complete,
    dispatch,
    fail,
    reposition_id_for,
)
from app.rebalancing.planner import AdaptiveRebalancer, RepositionPlan
from app.swarm.config import DEFAULT_SWARM_CONFIG, SwarmConfig
from app.swarm.simulation import SwarmSimulation, SwarmTickReport

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RebalancingTickReport:
    """M4's tick report plus what the rebalancer did in the same tick."""

    base: SwarmTickReport
    ran_cycle: bool = False
    dispatched_reposition_ids: tuple[str, ...] = ()
    completed_reposition_ids: tuple[str, ...] = ()
    rejected_count: int = 0
    total_deficit: float | None = None

    @property
    def tick_index(self) -> int:
        return self.base.tick_index

    @property
    def end_time_min(self) -> float:
        return self.base.end_time_min

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.base.to_dict(),
            "ran_cycle": self.ran_cycle,
            "dispatched_reposition_ids": list(self.dispatched_reposition_ids),
            "completed_reposition_ids": list(self.completed_reposition_ids),
            "rejected_count": self.rejected_count,
            "total_deficit": self.total_deficit,
        }


class RebalancingSimulation(SwarmSimulation):
    def __init__(self, graph: NetworkGraph, fleet: PodFleet, trips: Iterable[TripRequest],
                 config: FleetConfig = DEFAULT_FLEET_CONFIG, *,
                 swarm_config: SwarmConfig = DEFAULT_SWARM_CONFIG,
                 rebalancing_config: RebalancingConfig = DEFAULT_REBALANCING_CONFIG,
                 profile: DemandProfile = BASELINE_DEMAND_PROFILE,
                 enable_swarms: bool = True, enable_rebalancing: bool = True,
                 start_time_min: float = 0.0, algorithm: str = "astar") -> None:
        if not isinstance(rebalancing_config, RebalancingConfig):
            raise RebalancingConfigError(
                f"rebalancing_config must be a RebalancingConfig, "
                f"got {type(rebalancing_config).__name__}")
        if not isinstance(enable_rebalancing, bool):
            raise RebalancingConfigError(
                f"enable_rebalancing must be a bool, got {enable_rebalancing!r}")
        super().__init__(graph, fleet, trips, config, swarm_config=swarm_config,
                         enable_swarms=enable_swarms, start_time_min=start_time_min,
                         algorithm=algorithm)
        self._rebalancing_config = rebalancing_config
        self._enable_rebalancing = enable_rebalancing
        self._profile = profile
        self._rebalancer = AdaptiveRebalancer(rebalancing_config, config, profile, algorithm)
        self._repositions: dict[str, RepositionAssignment] = {}
        self._pod_reposition: dict[str, str] = {}
        self._reposition_counter = 0
        self._cycles_run = 0
        self._rejected_total = 0
        self._last_cycle_min: float | None = None
        self._last_plan: RepositionPlan | None = None
        # (time_min, total_deficit) at every cycle, so before/after deficit is a
        # measured series rather than a single remembered number.
        self._deficit_history: list[tuple[float, float]] = []
        # Odometer readings when a repositioning pod departed, so its deadhead cost
        # is measured rather than estimated.
        self._reposition_baseline: dict[str, tuple[float, float, float]] = {}

    # --- read-only state --------------------------------------------------------
    @property
    def rebalancing_config(self) -> RebalancingConfig:
        return self._rebalancing_config

    @property
    def rebalancing_enabled(self) -> bool:
        return self._enable_rebalancing

    @property
    def rebalancer(self) -> AdaptiveRebalancer:
        return self._rebalancer

    @property
    def cycles_run(self) -> int:
        return self._cycles_run

    @property
    def rejected_request_count(self) -> int:
        return self._rejected_total

    @property
    def last_plan(self) -> RepositionPlan | None:
        return self._last_plan

    def deficit_history(self) -> tuple[tuple[float, float], ...]:
        """(time_min, total forecast deficit) at each rebalancing cycle, in order."""
        return tuple(self._deficit_history)

    def repositions(self) -> tuple[RepositionAssignment, ...]:
        """Every dispatched move, in reposition_id order. Never deleted."""
        return tuple(self._repositions[key] for key in sorted(self._repositions))

    def reposition(self, reposition_id: str) -> RepositionAssignment:
        try:
            return self._repositions[reposition_id]
        except KeyError:
            raise RebalancingConfigError(f"no reposition with id {reposition_id!r}") from None

    def active_reposition_count(self) -> int:
        return sum(1 for a in self._repositions.values() if not a.is_resolved)

    def reposition_status_counts(self) -> dict[str, int]:
        counts = {status.value: 0 for status in RepositionStatus}
        for assignment in self._repositions.values():
            counts[assignment.status.value] += 1
        return counts

    def demand_map(self, *, include_actuals: bool = False) -> SpatialDemandMap:
        """The spatial map as of now. Read-only; safe to call at any time."""
        return build_demand_map(self._graph, self._fleet, self.records(), self.time_min,
                                self._rebalancing_config, self._profile,
                                self._in_active_swarm, include_actuals=include_actuals)

    def _in_active_swarm(self, pod_id: str) -> bool:
        return self.swarm_of_pod(pod_id) is not None

    @property
    def has_pending_work(self) -> bool:
        """M4's condition, plus any repositioning still under way."""
        if super().has_pending_work:
            return True
        return self.active_reposition_count() > 0

    # --- the tick ---------------------------------------------------------------
    def tick(self) -> RebalancingTickReport:
        base = super().tick()
        completed = self._note_arrivals(base.events)
        ran_cycle = False
        dispatched: tuple[str, ...] = ()
        rejected = 0
        total_deficit = None
        if self._is_cycle_due():
            ran_cycle = True
            if self._enable_rebalancing:
                dispatched, rejected, total_deficit = self._run_cycle()
            else:
                # Rebalancing off still *measures* the imbalance on the same cadence
                # and dispatches nothing, so the two modes' deficit series compare
                # like for like.
                total_deficit = self._observe_cycle()
        return RebalancingTickReport(
            base=base, ran_cycle=ran_cycle, dispatched_reposition_ids=dispatched,
            completed_reposition_ids=completed, rejected_count=rejected,
            total_deficit=total_deficit,
        )

    def _is_cycle_due(self) -> bool:
        if self._last_cycle_min is None:
            return True
        return (self.time_min - self._last_cycle_min) >= \
            self._rebalancing_config.rebalance_interval_min

    def _observe_cycle(self) -> float:
        """Record the imbalance without acting on it (rebalancing disabled)."""
        demand_map = build_demand_map(self._graph, self._fleet, self.records(), self.time_min,
                                      self._rebalancing_config, self._profile,
                                      self._in_active_swarm)
        self._last_cycle_min = self.time_min
        self._deficit_history.append((self.time_min, demand_map.total_deficit))
        return demand_map.total_deficit

    def _run_cycle(self) -> tuple[tuple[str, ...], int, float]:
        plan = self._rebalancer.plan_detailed(
            self._graph, self._fleet, self.records(), self.time_min,
            self._in_active_swarm, self.active_reposition_count())
        self._last_cycle_min = self.time_min
        self._last_plan = plan
        self._cycles_run += 1
        self._rejected_total += plan.rejected_count
        self._deficit_history.append((self.time_min, plan.demand_map.total_deficit))

        dispatched = []
        for item in plan.items:
            self._reposition_counter += 1
            assignment = dispatch(self._fleet, item,
                                  reposition_id_for(self._reposition_counter), self.time_min)
            self._repositions[assignment.reposition_id] = assignment
            self._pod_reposition[assignment.pod_id] = assignment.reposition_id
            dispatched.append(assignment.reposition_id)
        return tuple(dispatched), plan.rejected_count, plan.demand_map.total_deficit

    def _note_arrivals(self, events: tuple[MovementEvent, ...]) -> tuple[str, ...]:
        """Close out repositioning moves whose pod arrived during this tick."""
        completed = []
        for event in events:
            if event.kind != "arrived":
                continue
            reposition_id = self._pod_reposition.get(event.pod_id)
            if reposition_id is None:
                continue
            assignment = self._repositions[reposition_id]
            if assignment.is_resolved:
                continue
            pod = self._fleet.get_pod(event.pod_id)
            km0, min0, kwh0 = self._reposition_baseline.pop(event.pod_id, (0.0, 0.0, 0.0))
            complete(assignment, pod, event.time_min,
                     pod.total_distance_km - km0, pod.total_travel_time_min - min0,
                     pod.total_energy_kwh - kwh0)
            assignment.status = RepositionStatus.COMPLETED
            self._pod_reposition.pop(event.pod_id, None)
            completed.append(reposition_id)
        return tuple(completed)

    # --- overridden step: departure ---------------------------------------------
    def _depart_assigned_pods(self) -> tuple[str, ...]:
        """Send repositioning pods on their way, then let M4 handle passenger pods.

        A repositioning pod departs immediately and never joins a swarm: it carries
        nobody, so there is nothing to coordinate and no reason to make it wait for
        a formation partner.
        """
        departed: list[str] = []
        for pod in self._fleet.pods_with_status(PodStatus.ASSIGNED):
            if not pod.is_repositioning:
                continue
            self._reposition_baseline[pod.pod_id] = (
                pod.total_distance_km, pod.total_travel_time_min, pod.total_energy_kwh)
            start_pod_travel(self._graph, pod, self._config)
            reposition_id = self._pod_reposition.get(pod.pod_id)
            if reposition_id is not None:
                self._repositions[reposition_id].status = RepositionStatus.IN_PROGRESS
            departed.append(pod.pod_id)
        return tuple(departed) + super()._depart_assigned_pods()

    def run(self, *, max_ticks: int = 100_000, until_min: float | None = None):
        report = super().run(max_ticks=max_ticks, until_min=until_min)
        # Any move still unresolved when the run stops is recorded as failed, so the
        # history never leaves a move dangling.
        for assignment in self.repositions():
            if not assignment.is_resolved:
                fail(assignment, "run ended before the pod arrived")
        return report
