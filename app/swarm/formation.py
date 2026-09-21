"""Deterministic swarm formation.

THE ALGORITHM (a deterministic baseline heuristic — **not** globally optimal)
---------------------------------------------------------------------------
Given a set of candidate pods:

1. Group candidates by the node they are standing on, and walk the nodes in
   sorted node-id order.
2. Within a node, walk candidates in sorted ``pod_id`` order. The lowest
   remaining pod id becomes the **leader**.
3. Scan the rest in ``pod_id`` order, admitting a candidate whenever the group it
   would produce still satisfies every compatibility rule, until
   ``max_swarm_size`` is reached.
4. A group of two or more forms a swarm; a leader that attracted nobody stays
   independent.
5. Repeat from step 2 with the pods that are still unassigned at that node, so one
   busy node can produce several swarms.

Every step is a sorted scan against explicit thresholds, so identical inputs give
identical swarms — same membership, same leaders, same ids, in the same order. No
AI/LLM, no scoring model and no randomness is involved anywhere.

This is greedy and first-come: admitting a pod early can prevent a larger group
later. It is a reproducible baseline, not an optimum, and it is not claimed to be.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Sequence

from app.fleet.models import Pod
from app.network.graph import NetworkGraph
from app.swarm.compatibility import check_group
from app.swarm.config import MIN_SWARM_SIZE, DEFAULT_SWARM_CONFIG, SwarmConfig
from app.swarm.models import SharedCorridor, Swarm

logger = logging.getLogger(__name__)

SWARM_ID_TEMPLATE = "SW{index:05d}"


def swarm_id_for(index: int) -> str:
    """Stable, sortable swarm id for a one-based formation index."""
    if isinstance(index, bool) or not isinstance(index, int) or index < 1:
        raise ValueError(f"swarm index must be an int >= 1, got {index!r}")
    return SWARM_ID_TEMPLATE.format(index=index)


@dataclass(frozen=True)
class CandidateGroup:
    """A group the algorithm decided to form, before it becomes a Swarm."""

    pod_ids: tuple[str, ...]
    leader_pod_id: str
    corridor: SharedCorridor

    @property
    def size(self) -> int:
        return len(self.pod_ids)


@dataclass(frozen=True)
class FormationResult:
    """What one formation pass produced."""

    groups: tuple[CandidateGroup, ...] = ()
    independent_pod_ids: tuple[str, ...] = ()

    @property
    def pods_in_groups(self) -> tuple[str, ...]:
        return tuple(sorted(pod_id for group in self.groups for pod_id in group.pod_ids))

    def to_dict(self) -> dict:
        return {
            "groups": [{"pod_ids": list(g.pod_ids), "leader_pod_id": g.leader_pod_id,
                        "corridor": g.corridor.to_dict()} for g in self.groups],
            "independent_pod_ids": list(self.independent_pod_ids),
        }


def plan_formation(graph: NetworkGraph, candidates: Sequence[Pod],
                   config: SwarmConfig = DEFAULT_SWARM_CONFIG) -> FormationResult:
    """Decide which candidate pods platoon together and which stay independent.

    Pure planning: nothing is mutated, no ``Swarm`` is created and no pod moves.
    See the module docstring for the exact ordering rules.
    """
    by_node: dict[str, list[Pod]] = {}
    for pod in sorted(candidates, key=lambda p: p.pod_id):
        by_node.setdefault(pod.current_node_id, []).append(pod)

    groups: list[CandidateGroup] = []
    independent: list[str] = []

    for node_id in sorted(by_node):
        remaining = list(by_node[node_id])          # already in pod_id order
        while remaining:
            leader = remaining.pop(0)
            group = [leader]
            still_available: list[Pod] = []
            for candidate in remaining:
                if len(group) >= config.max_swarm_size:
                    still_available.append(candidate)
                    continue
                trial = group + [candidate]
                if check_group(graph, trial, config):
                    group = trial
                else:
                    still_available.append(candidate)
            remaining = still_available

            if len(group) < MIN_SWARM_SIZE:
                independent.append(leader.pod_id)
                continue
            result = check_group(graph, group, config)
            if not result or result.corridor is None:
                # Defensive: the group was built up through passing checks, so this
                # should be unreachable. Fall back to independence rather than
                # forming something the rules reject.
                independent.extend(pod.pod_id for pod in group)
                continue
            groups.append(CandidateGroup(
                pod_ids=tuple(sorted(pod.pod_id for pod in group)),
                leader_pod_id=min(pod.pod_id for pod in group),
                corridor=result.corridor,
            ))

    return FormationResult(groups=tuple(groups), independent_pod_ids=tuple(sorted(independent)))


def build_swarms(result: FormationResult, formation_time_min: float,
                 next_id: Callable[[], str]) -> tuple[Swarm, ...]:
    """Turn planned groups into ``Swarm`` objects, in planning order.

    ``next_id`` supplies ids so the caller owns the counter and ids stay
    sequential across a whole simulation.
    """
    return tuple(
        Swarm(swarm_id=next_id(), pod_ids=group.pod_ids, leader_pod_id=group.leader_pod_id,
              corridor=group.corridor, formation_time_min=formation_time_min,
              current_node_id=group.corridor.origin_node_id)
        for group in result.groups
    )
