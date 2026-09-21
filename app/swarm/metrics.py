"""Deterministic swarm metrics and the controlled baseline comparison.

WHAT EACH DISTANCE METRIC MEANS (read this before quoting any of them)
---------------------------------------------------------------------
``pod_distance_km``
    Total kilometres physically driven by all pods. **Platooning does not reduce
    this.** Four pods over a 5 km corridor drive 5 km each: 20 pod-km.

``shared_corridor_distance_km``
    Corridor kilometres traversed under coordination, counted **once per swarm**.
    For the example above: 5 km. This is the "unique corridor distance".

``coordinated_pod_km``
    Pod-kilometres driven while in formation: corridor length × members. For the
    example: 20 km. Reported next to the previous figure precisely so nobody
    mistakes a platoon for a single vehicle.

``independent_pod_km``
    ``pod_distance_km`` minus ``coordinated_pod_km`` — distance driven alone.

``road_occupancy_km``
    An **estimate of road space used**, under the headway assumption in
    ``app/swarm/config.py``: a pod following in formation is charged
    ``formation_occupancy_factor`` of an independent pod's road space.

        road_occupancy_km = independent_pod_km
                          + Σ over swarms [ corridor_km × (1 + (n-1) × factor) ]

``coordination_benefit_km``
    ``pod_distance_km − road_occupancy_km``: the road space the coordination is
    estimated to free up. **This is a road-space figure only.** It is not fuel,
    not energy, not emissions and not time saved, and no such saving is claimed.

A WARNING ABOUT COMPARING TOTALS ACROSS MODES
---------------------------------------------
``ModeComparison`` puts independent and swarm runs side by side on an identical
scenario, but their fleet totals still differ — and *not* because platooning
discounts anything. Pods wait up to ``max_formation_delay_min`` before departing
in swarm mode, which shifts departures and therefore changes which trips get
served at all. Distance and energy totals move with the served set. Waiting also
costs riders time, so expect average wait and completion time to rise. Report
those honestly rather than quoting only the road-occupancy figure.

Averages are rounded modestly, and an average over zero samples is ``None``
rather than a misleading ``0.0``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.fleet.metrics import FleetMetrics, compute_fleet_metrics
from app.fleet.models import PodStatus
from app.swarm.config import DEFAULT_SWARM_CONFIG, SwarmConfig
from app.swarm.models import Swarm, SwarmStatus

DISTANCE_DECIMALS = 3
TIME_DECIMALS = 2
RATIO_DECIMALS = 4


def _mean(values: Sequence[float], decimals: int) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), decimals)


@dataclass(frozen=True)
class SwarmMetrics:
    """Aggregate view of swarm activity over one simulation."""

    swarms_enabled: bool
    # Participation
    total_pods: int
    active_pods: int
    pods_in_swarms: int
    independent_pods: int
    swarm_participation_percent: float | None
    distinct_pods_ever_in_a_swarm: int
    # Swarms
    swarm_count: int
    active_swarm_count: int
    completed_swarm_count: int
    average_swarm_size: float | None
    max_swarm_size: int
    formation_count: int
    split_count: int
    formation_success_rate: float | None
    average_swarm_duration_min: float | None
    # Corridors
    average_shared_corridor_distance_km: float | None
    total_shared_corridor_distance_km: float
    average_shared_corridor_edges: float | None
    # Distance accounting (see module docstring for exact definitions)
    pod_distance_km: float
    coordinated_pod_km: float
    independent_pod_km: float
    road_occupancy_km: float
    coordination_benefit_km: float
    road_occupancy_saving_percent: float | None
    formation_occupancy_factor: float

    def to_dict(self) -> dict[str, Any]:
        return {field: getattr(self, field) for field in self.__dataclass_fields__}


def compute_swarm_metrics(simulation, config: SwarmConfig = DEFAULT_SWARM_CONFIG) -> SwarmMetrics:
    """Summarise the swarm layer of a ``SwarmSimulation``."""
    fleet = simulation.fleet
    pods = fleet.pods()
    swarms: tuple[Swarm, ...] = simulation.swarms()

    in_swarms = set(simulation.pods_in_swarms())
    active_pods = [p for p in pods if p.status in (PodStatus.ASSIGNED, PodStatus.TRAVELING,
                                                   PodStatus.ARRIVED)]
    ever_in_a_swarm = {pod_id for swarm in swarms for pod_id in swarm.pod_ids}

    sizes = [swarm.size for swarm in swarms]
    corridor_distances = [swarm.corridor.distance_km for swarm in swarms]
    corridor_edges = [float(swarm.corridor.edge_count) for swarm in swarms]
    durations = [swarm.duration_min(simulation.time_min) for swarm in swarms]

    pod_distance = sum(pod.total_distance_km for pod in pods)
    # Distance actually covered in formation, from each swarm's own progress.
    coordinated_pod_km = sum(swarm.coordinated_pod_km() for swarm in swarms)
    shared_corridor_distance = sum(swarm.shared_distance_km for swarm in swarms)
    independent_pod_km = max(0.0, pod_distance - coordinated_pod_km)

    factor = config.formation_occupancy_factor
    formation_occupancy = sum(
        swarm.shared_distance_km * (1.0 + (swarm.size - 1) * factor) for swarm in swarms
    )
    road_occupancy = independent_pod_km + formation_occupancy
    benefit = pod_distance - road_occupancy

    return SwarmMetrics(
        swarms_enabled=simulation.swarms_enabled,
        total_pods=len(pods),
        active_pods=len(active_pods),
        pods_in_swarms=len(in_swarms),
        independent_pods=len(pods) - len(in_swarms),
        swarm_participation_percent=(round(100.0 * len(ever_in_a_swarm) / len(pods), 2)
                                     if pods else None),
        distinct_pods_ever_in_a_swarm=len(ever_in_a_swarm),
        swarm_count=len(swarms),
        active_swarm_count=sum(1 for s in swarms if s.status is SwarmStatus.ACTIVE),
        completed_swarm_count=sum(1 for s in swarms if s.status is SwarmStatus.COMPLETED),
        average_swarm_size=_mean([float(s) for s in sizes], RATIO_DECIMALS),
        max_swarm_size=max(sizes) if sizes else 0,
        formation_count=simulation.formation_count,
        split_count=simulation.split_count,
        # Share of ticks-with-candidates that produced at least one formation.
        formation_success_rate=(round(simulation.formation_count / simulation.formation_attempts,
                                      RATIO_DECIMALS)
                                if simulation.formation_attempts else None),
        average_swarm_duration_min=_mean(durations, TIME_DECIMALS),
        average_shared_corridor_distance_km=_mean(corridor_distances, DISTANCE_DECIMALS),
        total_shared_corridor_distance_km=round(shared_corridor_distance, DISTANCE_DECIMALS),
        average_shared_corridor_edges=_mean(corridor_edges, RATIO_DECIMALS),
        pod_distance_km=round(pod_distance, DISTANCE_DECIMALS),
        coordinated_pod_km=round(coordinated_pod_km, DISTANCE_DECIMALS),
        independent_pod_km=round(independent_pod_km, DISTANCE_DECIMALS),
        road_occupancy_km=round(road_occupancy, DISTANCE_DECIMALS),
        coordination_benefit_km=round(benefit, DISTANCE_DECIMALS),
        road_occupancy_saving_percent=(round(100.0 * benefit / pod_distance, 2)
                                       if pod_distance > 0 else None),
        formation_occupancy_factor=factor,
    )


@dataclass(frozen=True)
class ModeComparison:
    """A controlled independent-vs-swarm comparison of one identical scenario."""

    independent_fleet: FleetMetrics
    swarm_fleet: FleetMetrics
    independent_swarm: SwarmMetrics
    swarm_swarm: SwarmMetrics
    independent_fingerprint: str
    swarm_fingerprint: str

    def delta(self, field: str) -> float | None:
        """swarm − independent for a numeric fleet metric, or None if either is None."""
        a = getattr(self.swarm_fleet, field)
        b = getattr(self.independent_fleet, field)
        if a is None or b is None:
            return None
        return round(a - b, 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "independent": {"fleet": self.independent_fleet.to_dict(),
                            "swarm": self.independent_swarm.to_dict(),
                            "fingerprint": self.independent_fingerprint},
            "swarm": {"fleet": self.swarm_fleet.to_dict(),
                      "swarm": self.swarm_swarm.to_dict(),
                      "fingerprint": self.swarm_fingerprint},
        }


def compare_modes(graph_factory, trips, *, fleet_factory, fleet_config, swarm_config=DEFAULT_SWARM_CONFIG,
                  max_ticks: int = 100_000, algorithm: str = "astar") -> ModeComparison:
    """Run the *same* scenario twice — independent, then swarm — and measure both.

    ``graph_factory`` and ``fleet_factory`` are callables so each run gets a fresh
    network and fleet: the two runs must not inherit each other's traffic, pod
    positions or batteries. Everything else (demand, seed, config, horizon) is
    identical, so the only difference is whether swarms are enabled.
    """
    from app.swarm.simulation import SwarmSimulation

    results = {}
    for enabled in (False, True):
        graph = graph_factory()
        fleet = fleet_factory(graph)
        simulation = SwarmSimulation(graph, fleet, trips, fleet_config,
                                     swarm_config=swarm_config, enable_swarms=enabled,
                                     algorithm=algorithm)
        simulation.run(max_ticks=max_ticks)
        results[enabled] = (
            compute_fleet_metrics(fleet, simulation.records(), simulation.time_min),
            compute_swarm_metrics(simulation, swarm_config),
            simulation.snapshot().fingerprint(),
        )

    return ModeComparison(
        independent_fleet=results[False][0], swarm_fleet=results[True][0],
        independent_swarm=results[False][1], swarm_swarm=results[True][1],
        independent_fingerprint=results[False][2], swarm_fingerprint=results[True][2],
    )
