"""Deterministic compatibility rules for swarm formation.

Every decision here is an explicit numeric comparison against a threshold in
``app/swarm/config.py``. **No AI/LLM, no learned model and no fuzzy score is
involved**, and nothing here is random.

Corridor geometry comes from the pods' existing M1 routes and the network's own
edge distances and times: this module computes no routes and defines no second
cost model. It reads ``edge.current_travel_time_min``, exactly as M3's movement
does, so the corridor it reports is the corridor the pods will actually drive.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

from app.fleet.models import Pod
from app.network.graph import NetworkGraph
from app.swarm.config import MIN_SWARM_SIZE, DEFAULT_SWARM_CONFIG, SwarmConfig
from app.swarm.models import SharedCorridor

logger = logging.getLogger(__name__)

# Reasons a candidate group was rejected, so callers can report *why* without
# re-deriving the rule.
NOT_ENOUGH_PODS = "fewer than two pods"
TOO_MANY_PODS = "more than max_swarm_size pods"
NOT_CO_LOCATED = "pods are not at the same node"
NO_ROUTE = "a pod has no remaining route"
TOO_FEW_SHARED_EDGES = "shared corridor is shorter than min_shared_edges"
TOO_SHORT_SHARED_DISTANCE = "shared corridor is shorter than min_shared_distance_km"
CORRIDOR_TOO_SMALL_A_SHARE = "corridor is too small a share of a member's remaining journey"
CORRIDOR_TOO_BRIEF = "corridor is shorter than min_formation_stability_min"


def shared_edge_prefix(edge_sequences: Sequence[Sequence[str]]) -> tuple[str, ...]:
    """The longest common *prefix* of several edge sequences.

    A prefix, not any shared subsequence: pods only platoon over road they cover
    together starting from where they are now.
    """
    if not edge_sequences:
        return ()
    shortest = min(len(seq) for seq in edge_sequences)
    prefix: list[str] = []
    for index in range(shortest):
        candidate = edge_sequences[0][index]
        if any(seq[index] != candidate for seq in edge_sequences[1:]):
            break
        prefix.append(candidate)
    return tuple(prefix)


def corridor_metrics(graph: NetworkGraph, edge_ids: Sequence[str]) -> tuple[float, float]:
    """(distance_km, travel_time_min) of an edge run under the network's current state."""
    distance = sum(graph.get_edge(edge_id).distance_km for edge_id in edge_ids)
    travel_time = sum(graph.get_edge(edge_id).current_travel_time_min for edge_id in edge_ids)
    return distance, travel_time


def remaining_distance_km(graph: NetworkGraph, pod: Pod) -> float:
    """How far this pod still has to drive on its assigned route."""
    return sum(graph.get_edge(edge_id).distance_km for edge_id in pod.remaining_edge_ids)


def build_corridor(graph: NetworkGraph, pods: Sequence[Pod]) -> SharedCorridor | None:
    """The shared corridor of a candidate group, or None when they share no prefix."""
    if len(pods) < MIN_SWARM_SIZE:
        return None
    prefix = shared_edge_prefix([pod.remaining_edge_ids for pod in pods])
    if not prefix:
        return None
    distance, travel_time = corridor_metrics(graph, prefix)
    return SharedCorridor(
        edge_ids=prefix,
        origin_node_id=pods[0].current_node_id,
        divergence_node_id=graph.get_edge(prefix[-1]).destination,
        distance_km=distance,
        travel_time_min=travel_time,
    )


@dataclass(frozen=True)
class CompatibilityResult:
    """Whether a candidate group may form a swarm, and why not when it may not."""

    is_compatible: bool
    corridor: SharedCorridor | None = None
    reason: str | None = None

    def __bool__(self) -> bool:
        return self.is_compatible


def check_group(graph: NetworkGraph, pods: Sequence[Pod],
                config: SwarmConfig = DEFAULT_SWARM_CONFIG) -> CompatibilityResult:
    """Apply the five compatibility rules plus the stability window.

    The rules are documented in ``app/swarm/config.py``; this function is their
    single implementation, so formation and tests cannot drift apart.
    """
    # Rule 4 — size.
    if len(pods) < MIN_SWARM_SIZE:
        return CompatibilityResult(False, reason=NOT_ENOUGH_PODS)
    if len(pods) > config.max_swarm_size:
        return CompatibilityResult(False, reason=TOO_MANY_PODS)

    # Rule 1 — co-location.
    nodes = {pod.current_node_id for pod in pods}
    if len(nodes) != 1:
        return CompatibilityResult(False, reason=NOT_CO_LOCATED)

    # Rule 5 — every candidate must still have road ahead of it.
    if any(not pod.remaining_edge_ids for pod in pods):
        return CompatibilityResult(False, reason=NO_ROUTE)

    corridor = build_corridor(graph, pods)
    if corridor is None or corridor.edge_count < config.min_shared_edges:
        return CompatibilityResult(False, corridor=corridor, reason=TOO_FEW_SHARED_EDGES)

    # Rule 2 — the corridor is long enough to be worth coordinating over.
    if corridor.distance_km < config.min_shared_distance_km:
        return CompatibilityResult(False, corridor=corridor, reason=TOO_SHORT_SHARED_DISTANCE)

    # Rule 3 — bounded divergence: the corridor must be a real part of every
    # member's remaining journey, not a coincidental first leg of a long trip.
    for pod in pods:
        remaining = remaining_distance_km(graph, pod)
        if remaining <= 0:
            return CompatibilityResult(False, corridor=corridor, reason=NO_ROUTE)
        if corridor.distance_km / remaining < config.min_shared_route_fraction:
            return CompatibilityResult(False, corridor=corridor, reason=CORRIDOR_TOO_SMALL_A_SHARE)

    # Stability — never form a group that would disband almost immediately; this
    # is what prevents formation/split oscillation.
    if corridor.travel_time_min < config.min_formation_stability_min:
        return CompatibilityResult(False, corridor=corridor, reason=CORRIDOR_TOO_BRIEF)

    return CompatibilityResult(True, corridor=corridor)


def are_compatible(graph: NetworkGraph, pods: Sequence[Pod],
                   config: SwarmConfig = DEFAULT_SWARM_CONFIG) -> bool:
    """Convenience predicate over :func:`check_group`."""
    return bool(check_group(graph, pods, config))
