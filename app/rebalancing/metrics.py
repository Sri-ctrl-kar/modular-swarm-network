"""Rebalancing metrics and the controlled before/after experiment.

*** Rebalancing is a heuristic; nothing here is claimed to be optimal, and no
    figure is netted, smoothed or presented to flatter it. ***

THE COST IS NEVER HIDDEN
------------------------
A repositioning pod drives empty. Those kilometres are **deadhead**: real distance,
real minutes, real kWh, reported on their own and never subtracted from anything.

``reposition_distance_km``   km driven empty while repositioning (deadhead).
``reposition_energy_kwh``    kWh consumed doing it, from M3's battery model.
``passenger_distance_km``    total pod km **minus** deadhead km — the distance
                             driven with someone aboard.
``pod_distance_km``          every km driven, deadhead included. Rebalancing makes
                             this go **up**, which is the point of reporting it.

THE EFFICIENCY FIGURE — exact definition
----------------------------------------
    rebalancing_trips_per_deadhead_km
        = (passenger trips completed WITH rebalancing
           − passenger trips completed WITHOUT rebalancing)
          / km driven empty WITH rebalancing

It needs **both** runs, so it lives on ``RebalancingComparison`` and never on a
single run's metrics. It is a rate of additional trips per empty kilometre. It can
be zero or negative if rebalancing does not help, and it is ``None`` when no
repositioning happened. Its reciprocal, ``deadhead_km_per_additional_trip``, is
reported alongside because it is easier to reason about.

**This is not a return on investment.** It puts trips over kilometres; it says
nothing about money, emissions or welfare, and it is not a ratio of like to like.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.fleet.metrics import FleetMetrics, compute_fleet_metrics
from app.fleet.models import TripStatus
from app.rebalancing.execution import RepositionAssignment, RepositionStatus
from app.rebalancing.planner import RepositionReason

DISTANCE_DECIMALS = 3
TIME_DECIMALS = 2
ENERGY_DECIMALS = 3
RATE_DECIMALS = 4


def _mean(values: Sequence[float], decimals: int) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), decimals)


@dataclass(frozen=True)
class RebalancingMetrics:
    """One run's rebalancing picture. Definitions are in the module docstring."""

    rebalancing_enabled: bool
    cycles_run: int
    # Requests
    reposition_requests: int          # accepted + rejected across all cycles
    accepted_requests: int
    rejected_requests: int
    completed_repositions: int
    failed_repositions: int
    # Cost (deadhead)
    reposition_distance_km: float
    reposition_energy_kwh: float
    average_reposition_time_min: float | None
    average_reposition_distance_km: float | None
    reposition_reason_counts: tuple[tuple[str, int], ...]
    # Distance split
    pod_distance_km: float
    passenger_distance_km: float
    deadhead_share_percent: float | None
    # Demand balance
    deficit_at_first_cycle: float | None
    deficit_at_last_cycle: float | None
    average_deficit: float | None
    final_total_deficit: float
    available_pods_at_deficit_nodes: int
    deficit_node_count: int
    # Service
    trips_served: int
    unserved_trips: int
    average_wait_min: float | None
    maximum_wait_min: float | None
    average_completion_time_min: float | None

    def to_dict(self) -> dict[str, Any]:
        """JSON-round-trip safe: the counts pair list becomes a dict, as in M2/M3."""
        data = {field: getattr(self, field) for field in self.__dataclass_fields__}
        data["reposition_reason_counts"] = dict(self.reposition_reason_counts)
        return data


def compute_rebalancing_metrics(simulation) -> RebalancingMetrics:
    """Summarise the rebalancing layer of a ``RebalancingSimulation``."""
    fleet = simulation.fleet
    records = simulation.records()
    assignments: tuple[RepositionAssignment, ...] = simulation.repositions()

    completed = [a for a in assignments if a.status is RepositionStatus.COMPLETED]
    failed = [a for a in assignments if a.status is RepositionStatus.FAILED]

    deadhead_km = sum(a.actual_distance_km or 0.0 for a in completed)
    deadhead_kwh = sum(a.actual_energy_kwh or 0.0 for a in completed)
    durations = [a.duration_min for a in completed if a.duration_min is not None]

    reason_counts: dict[str, int] = {reason.value: 0 for reason in RepositionReason}
    for assignment in assignments:
        reason_counts[assignment.reason.value] += 1

    pod_distance = sum(pod.total_distance_km for pod in fleet)
    passenger_distance = max(0.0, pod_distance - deadhead_km)

    history = simulation.deficit_history()
    final_map = simulation.demand_map()
    deficit_rows = final_map.deficit_rows()

    waits = [r.waiting_time_min for r in records if r.waiting_time_min is not None]
    completions = [r.completion_time_min for r in records if r.completion_time_min is not None]
    served = sum(1 for r in records if r.status is TripStatus.COMPLETED)

    return RebalancingMetrics(
        rebalancing_enabled=simulation.rebalancing_enabled,
        cycles_run=simulation.cycles_run,
        reposition_requests=len(assignments) + simulation.rejected_request_count,
        accepted_requests=len(assignments),
        rejected_requests=simulation.rejected_request_count,
        completed_repositions=len(completed),
        failed_repositions=len(failed),
        reposition_distance_km=round(deadhead_km, DISTANCE_DECIMALS),
        reposition_energy_kwh=round(deadhead_kwh, ENERGY_DECIMALS),
        average_reposition_time_min=_mean(durations, TIME_DECIMALS),
        average_reposition_distance_km=_mean(
            [a.actual_distance_km for a in completed if a.actual_distance_km is not None],
            DISTANCE_DECIMALS),
        reposition_reason_counts=tuple(sorted(reason_counts.items())),
        pod_distance_km=round(pod_distance, DISTANCE_DECIMALS),
        passenger_distance_km=round(passenger_distance, DISTANCE_DECIMALS),
        deadhead_share_percent=(round(100.0 * deadhead_km / pod_distance, 2)
                                if pod_distance > 0 else None),
        deficit_at_first_cycle=(round(history[0][1], DISTANCE_DECIMALS) if history else None),
        deficit_at_last_cycle=(round(history[-1][1], DISTANCE_DECIMALS) if history else None),
        average_deficit=_mean([value for _, value in history], DISTANCE_DECIMALS),
        final_total_deficit=final_map.total_deficit,
        available_pods_at_deficit_nodes=sum(row.available_pods for row in deficit_rows),
        deficit_node_count=len(deficit_rows),
        trips_served=served,
        unserved_trips=len(records) - served,
        average_wait_min=_mean(waits, TIME_DECIMALS),
        maximum_wait_min=(round(max(waits), TIME_DECIMALS) if waits else None),
        average_completion_time_min=_mean(completions, TIME_DECIMALS),
    )


@dataclass(frozen=True)
class RebalancingComparison:
    """A controlled no-rebalancing versus adaptive-rebalancing comparison."""

    baseline_fleet: FleetMetrics
    rebalanced_fleet: FleetMetrics
    baseline: RebalancingMetrics
    rebalanced: RebalancingMetrics
    baseline_fingerprint: str
    rebalanced_fingerprint: str

    @property
    def additional_trips_served(self) -> int:
        """Can be negative: rebalancing is allowed to make things worse."""
        return self.rebalanced.trips_served - self.baseline.trips_served

    @property
    def rebalancing_trips_per_deadhead_km(self) -> float | None:
        """Additional passenger trips per empty kilometre. See the module docstring.

        ``None`` when no repositioning happened — undefined, not zero.
        """
        km = self.rebalanced.reposition_distance_km
        if km <= 0:
            return None
        return round(self.additional_trips_served / km, RATE_DECIMALS)

    @property
    def deadhead_km_per_additional_trip(self) -> float | None:
        """The same trade-off the easier way round. ``None`` when no trips were gained."""
        gained = self.additional_trips_served
        if gained <= 0:
            return None
        return round(self.rebalanced.reposition_distance_km / gained, DISTANCE_DECIMALS)

    @property
    def extra_pod_distance_km(self) -> float:
        """Total extra driving, deadhead and extra passenger km together."""
        return round(self.rebalanced.pod_distance_km - self.baseline.pod_distance_km,
                     DISTANCE_DECIMALS)

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline": {"fleet": self.baseline_fleet.to_dict(),
                         "rebalancing": self.baseline.to_dict(),
                         "fingerprint": self.baseline_fingerprint},
            "rebalanced": {"fleet": self.rebalanced_fleet.to_dict(),
                           "rebalancing": self.rebalanced.to_dict(),
                           "fingerprint": self.rebalanced_fingerprint},
            "additional_trips_served": self.additional_trips_served,
            "rebalancing_trips_per_deadhead_km": self.rebalancing_trips_per_deadhead_km,
            "deadhead_km_per_additional_trip": self.deadhead_km_per_additional_trip,
            "extra_pod_distance_km": self.extra_pod_distance_km,
        }


def compare_rebalancing_modes(graph_factory, trips, *, fleet_factory, fleet_config,
                              swarm_config=None, rebalancing_config=None, profile=None,
                              enable_swarms: bool = True, max_ticks: int = 100_000,
                              algorithm: str = "astar") -> RebalancingComparison:
    """Run the *same* scenario twice — without, then with rebalancing.

    Each run gets a fresh network and fleet from the factories, so neither inherits
    the other's traffic, pod positions or batteries. Everything else is identical:
    the only difference is ``enable_rebalancing``.
    """
    from app.demand.config import BASELINE_DEMAND_PROFILE
    from app.rebalancing.config import DEFAULT_REBALANCING_CONFIG
    from app.rebalancing.simulation import RebalancingSimulation
    from app.swarm.config import DEFAULT_SWARM_CONFIG

    swarm_config = swarm_config or DEFAULT_SWARM_CONFIG
    rebalancing_config = rebalancing_config or DEFAULT_REBALANCING_CONFIG
    profile = profile or BASELINE_DEMAND_PROFILE

    results = {}
    for enabled in (False, True):
        graph = graph_factory()
        fleet = fleet_factory(graph)
        simulation = RebalancingSimulation(
            graph, fleet, trips, fleet_config, swarm_config=swarm_config,
            rebalancing_config=rebalancing_config, profile=profile,
            enable_swarms=enable_swarms, enable_rebalancing=enabled, algorithm=algorithm)
        simulation.run(max_ticks=max_ticks)
        results[enabled] = (
            compute_fleet_metrics(fleet, simulation.records(), simulation.time_min),
            compute_rebalancing_metrics(simulation),
            simulation.snapshot().fingerprint(),
        )

    return RebalancingComparison(
        baseline_fleet=results[False][0], rebalanced_fleet=results[True][0],
        baseline=results[False][1], rebalanced=results[True][1],
        baseline_fingerprint=results[False][2], rebalanced_fingerprint=results[True][2],
    )
