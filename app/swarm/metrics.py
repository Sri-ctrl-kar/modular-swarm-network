"""Deterministic swarm metrics and the controlled baseline comparison.

TWO DIFFERENT QUANTITIES, TWO DIFFERENT UNITS
---------------------------------------------
The single most important thing to get right here is that **physical pod distance
and road occupancy are not the same quantity and are not in the same unit.**

``km`` (physical kilometres)
    Actually driven on the tarmac. ``pod_distance_km`` is the sum of every pod's
    odometer. Platooning never reduces it: four pods over a 5 km corridor drive
    5 km each, 20 pod-km, formation or not.

``equiv-km`` (single-pod-equivalent kilometres)
    A *weighted* measure of how much road SPACE was used, where one pod driving
    alone for one km is 1.0 equiv-km by definition. A pod following in formation
    keeps a shorter headway and so is charged only
    ``formation_occupancy_factor`` (0.4) of that. Fields carrying this unit are
    named ``*_equiv_km`` and are **not distances** — do not add them to, or
    compare them against, physical kilometres from anywhere else.

EXACT DEFINITIONS AND FORMULAS
------------------------------
For each swarm *s*: ``d_s`` = ``shared_distance_km`` (corridor length actually
travelled together), ``n_s`` = ``size`` (members), ``f`` =
``formation_occupancy_factor``.

``pod_distance_km``                     [km]
    ``Σ over pods (pod.total_distance_km)``
    Physical kilometres driven by every pod in the fleet.

``total_shared_corridor_distance_km``   [km]
    ``Σ over swarms (d_s)``
    Corridor length traversed under coordination, counted **once per swarm** —
    the "unique corridor distance". For four pods over a 5 km corridor: 5.

``coordinated_pod_km``                  [km]
    ``Σ over swarms (d_s × n_s)``
    Pod-kilometres driven *while in a formation*. Same example: 20. Printed next
    to the previous figure precisely so a platoon is never mistaken for one
    vehicle. (``d_s × n_s`` is exact because a swarm's corridor is the common
    prefix of its members' routes, so no member's trip can end strictly inside
    it — every member drives the whole of ``d_s``. A test pins this.)

``unplatooned_pod_km``                  [km]
    ``pod_distance_km − coordinated_pod_km``
    Pod-kilometres driven outside any formation. Note this counts the solo legs
    of pods that *did* platoon, not only the pods that never joined a swarm.

``road_occupancy_equiv_km``             [equiv-km]
    ``unplatooned_pod_km + Σ over swarms (d_s × (1 + (n_s − 1) × f))``
    Estimated road space used. With no swarms this reduces **exactly** to
    ``pod_distance_km``, because every pod then occupies 1.0 equiv-km per km —
    that identity is asserted by a test, and it is why the independent run
    reports the same number twice rather than through any coincidence.

``road_occupancy_saved_equiv_km``       [equiv-km]
    ``pod_distance_km − road_occupancy_equiv_km``
    which is identically ``Σ over swarms (d_s × (n_s − 1) × (1 − f))``.
    The road space the coordination is estimated to free, **within this run**.

``road_occupancy_saving_percent``       [%]
    ``100 × road_occupancy_saved_equiv_km / pod_distance_km`` — the saving as a
    share of *this run's own* pod-km. Being scale-free, this is the only one of
    these figures that can be compared across two runs directly.

**This is a road-space estimate resting on one invented constant.**
It is not fuel, not energy, not emissions and not time saved, and no such saving
is claimed anywhere.

INSTANTANEOUS VERSUS CUMULATIVE
-------------------------------
``pods_currently_in_swarms`` and ``pods_currently_independent`` are a snapshot of
the moment the metrics were taken. After a completed run every swarm has finished,
so they read 0 and *total_pods* — that is correct, not a bug, and it is why the
cumulative ``distinct_pods_ever_in_a_swarm`` exists and is what the CLI reports.

COMPARING TWO MODES: READ THE RAW TOTALS WITH CARE
--------------------------------------------------
``ModeComparison`` puts an independent and a swarm run side by side on an
identical scenario, but their raw totals are **not** like-for-like, and the
difference is not a platooning effect. Pods wait up to
``max_formation_delay_min`` before departing in swarm mode, which shifts
departures and therefore changes which trips get served at all; distance and
energy totals move with the served set, and riders wait longer.

So a swarm run can legitimately show *higher* pod distance and *higher*
completion time than the baseline. Comparing the two ``road_occupancy_equiv_km``
totals directly conflates coordination with that different served set; compare
``road_occupancy_saving_percent``, which is scale-free, and report the completion
and distance changes honestly alongside it.

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
    # Snapshot of *this instant*; after a finished run these read 0 and total_pods.
    pods_currently_in_swarms: int
    pods_currently_independent: int
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
    # Distance accounting. Units matter: *_km are physical kilometres driven,
    # *_equiv_km are single-pod-equivalent road-space kilometres. See the module
    # docstring for the exact formula behind each one.
    pod_distance_km: float
    coordinated_pod_km: float
    unplatooned_pod_km: float
    road_occupancy_equiv_km: float
    road_occupancy_saved_equiv_km: float
    road_occupancy_saving_percent: float | None
    formation_occupancy_factor: float

    def to_dict(self) -> dict[str, Any]:
        return {field: getattr(self, field) for field in self.__dataclass_fields__}


def compute_swarm_metrics(simulation, config: SwarmConfig | None = None) -> SwarmMetrics:
    """Summarise the swarm layer of a ``SwarmSimulation``.

    ``config`` defaults to the simulation's **own** ``swarm_config``. That matters:
    the occupancy figures depend on ``formation_occupancy_factor``, so computing
    them against a different config than the run used would silently report road
    space under an assumption the simulation never made. Pass ``config`` only to
    deliberately re-score a run under a different headway assumption.
    """
    if config is None:
        config = getattr(simulation, "swarm_config", DEFAULT_SWARM_CONFIG)
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
    # Formation km are a subset of driven km, so this cannot go negative while the
    # pods that platooned are still in the fleet. The guard only catches a pod
    # removed from the fleet after platooning, which would otherwise under-count
    # pod_distance while its swarm still counts.
    unplatooned_pod_km = max(0.0, pod_distance - coordinated_pod_km)

    factor = config.formation_occupancy_factor
    formation_occupancy = sum(
        swarm.shared_distance_km * (1.0 + (swarm.size - 1) * factor) for swarm in swarms
    )
    road_occupancy = unplatooned_pod_km + formation_occupancy
    saved = pod_distance - road_occupancy

    return SwarmMetrics(
        swarms_enabled=simulation.swarms_enabled,
        total_pods=len(pods),
        active_pods=len(active_pods),
        pods_currently_in_swarms=len(in_swarms),
        pods_currently_independent=len(pods) - len(in_swarms),
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
        unplatooned_pod_km=round(unplatooned_pod_km, DISTANCE_DECIMALS),
        road_occupancy_equiv_km=round(road_occupancy, DISTANCE_DECIMALS),
        road_occupancy_saved_equiv_km=round(saved, DISTANCE_DECIMALS),
        road_occupancy_saving_percent=(round(100.0 * saved / pod_distance, 2)
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
