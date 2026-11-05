"""What the AI is allowed to see, and nothing else.

*** The observation is the AI's ONLY input. It is a frozen tree of plain values:
    no graph, no fleet, no pod, no swarm, no simulation, no callables, no secrets. ***

HOW THIS RELATES TO THE M5 BOUNDARY
-----------------------------------
M5 published ``app/rebalancing/observation.py`` — a read-only ``observe()`` plus a
bounded validator — precisely so an orchestrator could be bolted on without reaching
into the engine. M6 **composes** that function rather than replacing it:

    observe(simulation)            <- M5's boundary, unchanged
        + derived read-only detail <- this module (battery spread, pod placement,
                                      the worst corridors, swarm sizes)
        = OrchestrationObservation <- the only thing handed to a provider

The derived sections are computed here, in M6, so ``app/rebalancing/`` needs no edit.
They are read-only by construction: every value is copied out as an ``int``,
``float``, ``str``, ``bool``, tuple or dict before it leaves this module, and
``build_observation`` mutates nothing. A caller holding an observation cannot reach
a live object through it, which is what makes the boundary real rather than polite.

DETERMINISM
-----------
The same simulation state must produce a byte-identical observation:

* every collection is built by walking a **sorted** sequence;
* every float is rounded to a fixed number of decimals;
* the only clock is the simulation's own ``time_min`` — no wall-clock timestamps;
* no object identity, memory address or ``repr`` of a live object appears;
* no ``random`` and no Python ``hash()`` — ids are the engine's own stable ids.

``fingerprint()`` is the SHA-256 of the canonical JSON form, so it is identical
across processes and ``PYTHONHASHSEED`` values. The fingerprint is what ties a
proposal to the state it was reasoned from (see ``validator.py``).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from app.orchestration.config import DEFAULT_ORCHESTRATION_CONFIG, OrchestrationConfig
from app.rebalancing.eligibility import eligible_pods, ineligibility_reasons
from app.rebalancing.observation import observe

PERCENT_DECIMALS = 2
VALUE_DECIMALS = 4

#: Battery buckets, in percent. Half-open [low, high) except the last, which is closed.
BATTERY_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("0_20", 0.0, 20.0),
    ("20_40", 20.0, 40.0),
    ("40_60", 40.0, 60.0),
    ("60_80", 60.0, 80.0),
    ("80_100", 80.0, 100.0),
)


def _round(value: float, decimals: int = VALUE_DECIMALS) -> float:
    return round(float(value), decimals)


def canonical_json(data: Any) -> str:
    """The one serialisation used for fingerprints: sorted keys, no whitespace."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint_of(data: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical JSON form. Stable across processes."""
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class OrchestrationObservation:
    """A frozen, JSON-serialisable snapshot. The AI's whole world.

    Sections mirror the engine's layers so a reader can trace any number back to the
    milestone that owns it: ``network`` (M1), ``demand``/``forecast`` (M2/M5),
    ``fleet`` (M3), ``swarm`` (M4), ``rebalancing`` (M5), ``metrics`` (M3-M5).
    """

    time_min: float
    network: Mapping[str, Any]
    demand: Mapping[str, Any]
    fleet: Mapping[str, Any]
    swarm: Mapping[str, Any]
    forecast: Mapping[str, Any]
    rebalancing: Mapping[str, Any]
    metrics: Mapping[str, Any]
    engine_fingerprints: Mapping[str, str]
    provenance: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "time_min": self.time_min,
            "network": dict(self.network),
            "demand": dict(self.demand),
            "fleet": dict(self.fleet),
            "swarm": dict(self.swarm),
            "forecast": dict(self.forecast),
            "rebalancing": dict(self.rebalancing),
            "metrics": dict(self.metrics),
            "engine_fingerprints": dict(self.engine_fingerprints),
            "provenance": self.provenance,
        }

    def fingerprint(self) -> str:
        """The identity of this exact state. Ties a proposal to what produced it."""
        return fingerprint_of(self.to_dict())

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    # --- human- and prompt-facing helpers ---------------------------------------
    def deficit_node_ids(self) -> tuple[str, ...]:
        """Nodes the engine currently forecasts short of pods, worst first."""
        return tuple(str(row["node_id"]) for row in self.demand["top_deficit_nodes"])

    def headline(self) -> str:
        """One line naming the problem, for the audit record's 'observed problem'."""
        deficit = self.demand["total_deficit"]
        nodes = self.deficit_node_ids()
        if not nodes or deficit <= 0:
            return (f"minute {self.time_min:.1f}: no forecast pod deficit "
                    f"({self.fleet['idle_eligible_pods']} pods eligible to move)")
        return (f"minute {self.time_min:.1f}: forecast deficit {deficit} pods across "
                f"{self.demand['deficit_node_count']} node(s), worst at {nodes[0]}")


# ---------------------------------------------------------------------------
# Derived sections — read the engine once, copy out plain values, keep nothing
# ---------------------------------------------------------------------------
def _network_detail(graph, top_edges: int) -> dict[str, Any]:
    edges = sorted(graph.edges(), key=lambda e: e.edge_id)
    utilizations = [e.utilization for e in edges]
    worst = sorted(edges, key=lambda e: (-e.utilization, e.edge_id))[:top_edges]
    return {
        "average_utilization_percent": (_round(100.0 * sum(utilizations) / len(utilizations),
                                               PERCENT_DECIMALS) if utilizations else 0.0),
        "most_congested_edges": [
            {
                "edge_id": e.edge_id,
                "from_node_id": e.source,
                "to_node_id": e.destination,
                "road_type": e.road_type.value,
                "utilization_percent": _round(100.0 * e.utilization, PERCENT_DECIMALS),
                "congestion_multiplier": _round(e.congestion_multiplier),
                "travel_time_min": _round(e.current_travel_time_min),
                "is_overloaded": bool(e.is_overloaded),
            }
            for e in worst
        ],
    }


def _fleet_detail(fleet, in_active_swarm, top_nodes: int) -> dict[str, Any]:
    pods = sorted(fleet.pods(), key=lambda p: p.pod_id)
    batteries = [p.battery_percent for p in pods]
    buckets = {name: 0 for name, _, _ in BATTERY_BUCKETS}
    for level in batteries:
        for name, low, high in BATTERY_BUCKETS:
            if low <= level < high or (high >= 100.0 and level >= high):
                buckets[name] += 1
                break
    by_node: dict[str, int] = {}
    for pod in pods:
        by_node[pod.current_node_id] = by_node.get(pod.current_node_id, 0) + 1
    busiest = sorted(by_node.items(), key=lambda kv: (-kv[1], kv[0]))[:top_nodes]

    eligible = eligible_pods(fleet, in_active_swarm)
    reason_counts: dict[str, int] = {}
    for _, reason in ineligibility_reasons(fleet, in_active_swarm):
        reason_counts[reason] = reason_counts.get(reason, 0) + 1

    return {
        "battery_distribution_percent_buckets": dict(sorted(buckets.items())),
        "average_battery_percent": (_round(sum(batteries) / len(batteries), PERCENT_DECIMALS)
                                    if batteries else 0.0),
        "minimum_battery_percent": (_round(min(batteries), PERCENT_DECIMALS)
                                    if batteries else 0.0),
        "occupied_seats": fleet.occupied_seats(),
        "idle_eligible_pods": len(eligible),
        "ineligible_pod_reason_counts": dict(sorted(reason_counts.items())),
        "pods_at_busiest_nodes": [{"node_id": node, "pod_count": count}
                                  for node, count in busiest],
    }


def _swarm_detail(simulation, top_swarms: int) -> dict[str, Any]:
    active = sorted(simulation.active_swarms(), key=lambda s: s.swarm_id)
    size_counts: dict[str, int] = {}
    for swarm in active:
        size_counts[str(swarm.size)] = size_counts.get(str(swarm.size), 0) + 1
    listed = sorted(active, key=lambda s: (-s.size, s.swarm_id))[:top_swarms]
    return {
        "active_swarm_count": len(active),
        "active_swarm_size_counts": dict(sorted(size_counts.items())),
        "average_active_swarm_size": (_round(sum(s.size for s in active) / len(active), 2)
                                      if active else None),
        "active_corridors": [
            {
                "swarm_id": s.swarm_id,
                "size": s.size,
                "status": s.status.value,
                "origin_node_id": s.corridor.origin_node_id,
                "divergence_node_id": s.corridor.divergence_node_id,
                "corridor_edge_count": s.corridor.edge_count,
                "corridor_distance_km": _round(s.corridor.distance_km),
            }
            for s in listed
        ],
    }


def build_observation(simulation,
                      config: OrchestrationConfig = DEFAULT_ORCHESTRATION_CONFIG,
                      ) -> OrchestrationObservation:
    """Build the AI-facing observation of a ``RebalancingSimulation``.

    Reads the engine; changes nothing. Everything that leaves this function is a
    plain value, so the caller — and therefore the provider — holds no handle on
    the simulation at all.
    """
    base = observe(simulation, top_nodes=config.top_nodes)
    graph = simulation.graph
    fleet = simulation.fleet
    demand_map = simulation.demand_map()
    # Public accessor only: M6 asks the swarm layer a question, it does not reach
    # into it. M4 owns any pod that is coordinating, and such a pod is never moved.
    def in_active_swarm(pod_id: str) -> bool:
        return simulation.swarm_of_pod(pod_id) is not None

    network = {**dict(base.network), **_network_detail(graph, config.top_edges)}
    fleet_section = {**dict(base.fleet),
                     **_fleet_detail(fleet, in_active_swarm, config.top_nodes)}
    swarm_section = {**dict(base.swarm), **_swarm_detail(simulation, config.top_swarms)}

    demand_section = dict(base.demand)
    demand_section["deficit_node_count"] = len(demand_map.deficit_rows())
    demand_section["surplus_node_count"] = len(demand_map.surplus_rows())
    demand_section["top_surplus_nodes"] = [
        row.to_dict() for row in demand_map.surplus_rows()[:config.top_nodes]]

    return OrchestrationObservation(
        time_min=_round(base.time_min, 4),
        network=network,
        demand=demand_section,
        fleet=fleet_section,
        swarm=swarm_section,
        forecast=dict(base.forecast),
        rebalancing=dict(base.rebalancing),
        metrics=dict(base.metrics),
        engine_fingerprints=dict(base.fingerprints),
        provenance=config.data_provenance,
    )
