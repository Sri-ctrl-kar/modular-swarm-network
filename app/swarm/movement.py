"""Platoon movement: advancing a swarm's pods as a coordinated unit.

Shared movement is explicit. The members of an ACTIVE swarm are advanced together
through their shared corridor by one function, stepping to edge boundaries so the
formation stays in lockstep and stops exactly at the divergence node rather than
running past it.

**Pods remain individually identifiable throughout.** Each still owns its route,
battery, passengers and odometer; this module never merges them into one vehicle.

NO SECOND COST OR CONGESTION MODEL
----------------------------------
Every metre of movement here goes through M3's ``advance_pod``, which reads M1's
``edge.current_travel_time_min`` and ``edge.congestion_multiplier``. Platooning
therefore does **not** change any pod's distance, travel time or energy — the
coordination benefit is accounted for separately, in road-space terms only (see
``app/swarm/config.py``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Mapping, Sequence

from app.fleet.config import DEFAULT_FLEET_CONFIG, FleetConfig
from app.fleet.models import Pod, PodStatus
from app.fleet.movement import MovementEvent, advance_pod
from app.network.graph import NetworkGraph
from app.swarm.models import SharedCorridor, Swarm

logger = logging.getLogger(__name__)

TIME_EPSILON_MIN = 1e-9


def corridor_progress(corridor: SharedCorridor, pod: Pod) -> int:
    """How many of the corridor's edges this pod has completed.

    Found by matching the corridor's remaining suffix against the pod's remaining
    route, so it stays correct without the swarm having to remember each member's
    route index.
    """
    remaining = pod.remaining_edge_ids
    for completed in range(corridor.edge_count + 1):
        suffix = corridor.edge_ids[completed:]
        if tuple(remaining[:len(suffix)]) == suffix:
            return completed
    return corridor.edge_count


@dataclass(frozen=True)
class SwarmMovementResult:
    """Outcome of advancing one swarm through part of its corridor."""

    minutes_used: float
    events: tuple[MovementEvent, ...] = ()
    arrived_pod_ids: tuple[str, ...] = ()
    corridor_complete: bool = False


def _active_members(swarm: Swarm, pods: Mapping[str, Pod]) -> list[Pod]:
    """Members that are still travelling with the formation, in pod_id order."""
    members = []
    for pod_id in swarm.members_still_travelling:
        pod = pods.get(pod_id)
        if pod is None or pod.status is not PodStatus.TRAVELING:
            continue
        if pod.current_edge_time_min is None:
            continue
        members.append(pod)
    return members


def advance_swarm(graph: NetworkGraph, swarm: Swarm, pods: Mapping[str, Pod],
                  minutes: float, now_min: float,
                  config: FleetConfig = DEFAULT_FLEET_CONFIG) -> SwarmMovementResult:
    """Advance every member of ``swarm`` together by up to ``minutes``.

    The members share a corridor and were synchronised when they departed, so they
    are on the same edge with the same elapsed time. Each iteration advances all
    of them by the same step, capped at the nearest edge boundary, which keeps
    them in lockstep and lets the loop stop the moment the corridor is finished —
    the pods are then standing at the divergence node, having spent no time on
    their individual onward edges.
    """
    members = _active_members(swarm, pods)
    if not members:
        return SwarmMovementResult(minutes_used=0.0, corridor_complete=True)

    remaining = float(minutes)
    used_total = 0.0
    events: list[MovementEvent] = []
    arrived: list[str] = []

    while remaining > TIME_EPSILON_MIN:
        members = _active_members(swarm, pods)
        if not members:
            break
        # Step to the nearest edge boundary across the formation: the platoon moves
        # at the pace of whichever member finishes its current edge first.
        to_boundary = min(max(0.0, pod.current_edge_time_min - pod.current_edge_elapsed_min)
                          for pod in members)
        step = min(remaining, to_boundary) if to_boundary > TIME_EPSILON_MIN else remaining

        moved_any = False
        for pod in members:
            pod_used, pod_events = advance_pod(graph, pod, step, now_min + used_total, config)
            events.extend(pod_events)
            if pod_used > TIME_EPSILON_MIN:
                moved_any = True
            if pod.status is PodStatus.ARRIVED:
                # This member's own trip ended at a corridor node; it leaves the
                # formation rather than holding the others up.
                arrived.append(pod.pod_id)
                swarm.mark_departed(pod.pod_id)

        used_total += step
        remaining -= step

        leader = pods.get(swarm.leader_pod_id)
        reference = leader if leader is not None and leader.pod_id not in arrived else None
        still = _active_members(swarm, pods)
        if reference is None:
            reference = still[0] if still else None
        if reference is not None:
            completed = corridor_progress(swarm.corridor, reference)
            distance = sum(graph.get_edge(edge_id).distance_km
                           for edge_id in swarm.corridor.edge_ids[:completed])
            swarm.record_progress(node_id=reference.current_node_id, edges_completed=completed,
                                  distance_km=distance,
                                  travel_time_min=swarm.shared_travel_time_min + step)
        else:
            swarm.record_progress(node_id=swarm.current_node_id,
                                  edges_completed=swarm.corridor.edge_count,
                                  distance_km=swarm.corridor.distance_km,
                                  travel_time_min=swarm.shared_travel_time_min + step)

        if swarm.corridor_is_complete or not still:
            break
        if not moved_any:
            break        # nothing consumable this tick; do not spin

    return SwarmMovementResult(minutes_used=used_total, events=tuple(events),
                               arrived_pod_ids=tuple(sorted(arrived)),
                               corridor_complete=swarm.corridor_is_complete)


def pods_are_synchronised(pods: Sequence[Pod]) -> bool:
    """True when every pod sits on the same edge with the same elapsed time.

    Used by tests and assertions to confirm a formation really is travelling
    together rather than merely sharing a route on paper.
    """
    if not pods:
        return True
    first = pods[0]
    return all(pod.current_edge_id == first.current_edge_id
               and abs(pod.current_edge_elapsed_min - first.current_edge_elapsed_min) < 1e-9
               for pod in pods)
