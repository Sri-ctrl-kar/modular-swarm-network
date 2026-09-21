"""Deterministic SYNTHETIC fleet initialisation.

*** The deployment below is invented. It does not represent any real fleet
    positioning, depot plan or operational rollout. ***

Pods are placed on network nodes with weights taken from ``FleetConfig``'s
``depot_role_weights``, resolved through M2's ``PlaceRole`` classification — the
fleet deliberately reuses that role map instead of inventing a second one.

Determinism works exactly as in M1's city and M2's demand: a local
``random.Random(seed)`` (the global ``random`` module is never touched), consumed
in a fixed order, drawing from a node list sorted by node id so nothing depends
on the graph's insertion order.

RNG CONSUMPTION ORDER: one draw per pod, in pod index order, for its start node.
"""

from __future__ import annotations

import logging
import random
from typing import Sequence

from app.demand.config import BASELINE_DEMAND_PROFILE, DemandProfile
from app.errors import FleetConfigError
from app.fleet.config import DEFAULT_FLEET_CONFIG, DEFAULT_FLEET_SEED, FleetConfig
from app.fleet.models import Pod
from app.fleet.pod_fleet import PodFleet
from app.network.graph import NetworkGraph

logger = logging.getLogger(__name__)

POD_ID_TEMPLATE = "POD{index:05d}"


def pod_id_for(index: int) -> str:
    """Stable, sortable pod id for a zero-based index (POD00000, POD00001, ...)."""
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise FleetConfigError(f"pod index must be an int >= 0, got {index!r}")
    return POD_ID_TEMPLATE.format(index=index)


def _weighted_choice(rng: random.Random, weighted: Sequence[tuple[str, float]]) -> str:
    total = sum(weight for _, weight in weighted)
    if not weighted or total <= 0:
        raise FleetConfigError("cannot place a pod: no node has a positive depot weight")
    threshold = rng.random() * total
    running = 0.0
    for node_id, weight in weighted:
        running += weight
        if threshold < running:
            return node_id
    return weighted[-1][0]


def depot_weights(graph: NetworkGraph, config: FleetConfig = DEFAULT_FLEET_CONFIG,
                  profile: DemandProfile = BASELINE_DEMAND_PROFILE) -> tuple[tuple[str, float], ...]:
    """(node_id, weight) for every node, sorted by node id."""
    weighted = []
    for node_id in sorted(node.node_id for node in graph.nodes()):
        role = profile.role_for(node_id, graph.get_node(node_id).node_type)
        weighted.append((node_id, config.depot_role_weights.get(role, 0.0)))
    return tuple(weighted)


def generate_fleet(graph: NetworkGraph, *, fleet_size: int = 100, seed: int = DEFAULT_FLEET_SEED,
                   config: FleetConfig = DEFAULT_FLEET_CONFIG,
                   profile: DemandProfile = BASELINE_DEMAND_PROFILE,
                   capacity: int | None = None) -> PodFleet:
    """Build a fleet of ``fleet_size`` pods placed deterministically on the network."""
    if isinstance(fleet_size, bool) or not isinstance(fleet_size, int) or fleet_size < 0:
        raise FleetConfigError(f"fleet_size must be an int >= 0, got {fleet_size!r}")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise FleetConfigError(f"seed must be an int, got {seed!r}")
    if not isinstance(config, FleetConfig):
        raise FleetConfigError(f"config must be a FleetConfig, got {type(config).__name__}")
    if capacity is None:
        capacity = config.default_pod_capacity
    if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
        raise FleetConfigError(f"capacity must be an int >= 1, got {capacity!r}")
    if graph.node_count() == 0:
        raise FleetConfigError("cannot place pods on a network with no nodes")

    weighted = depot_weights(graph, config, profile)
    rng = random.Random(seed)
    pods = [
        Pod(pod_id=pod_id_for(index), capacity=capacity,
            current_node_id=_weighted_choice(rng, weighted),
            battery_percent=config.initial_battery_percent)
        for index in range(fleet_size)
    ]
    logger.info("generated %d pods (seed %d, capacity %d)", len(pods), seed, capacity)
    return PodFleet(graph, pods)
