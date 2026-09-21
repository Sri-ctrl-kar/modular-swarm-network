"""PodFleet: the deterministic collection of pods.

Holds the graph so it can validate that a pod stands on a node that actually
exists — ``Pod`` itself only validates the shape of a node id, because a model
must not depend on the network layer.

Pods are always iterated in ``pod_id`` order, never in insertion order, so no
result can depend on how the fleet was built. There is no module-level mutable
state: a fleet is an ordinary object you create and pass around.
"""

from __future__ import annotations

import logging
from typing import Iterable, Iterator

from app.errors import (
    DuplicatePodError,
    ModelValidationError,
    NodeNotFoundError,
    PodNotFoundError,
    PodStateError,
)
from app.fleet.models import Pod, PodStatus
from app.network.graph import NetworkGraph

logger = logging.getLogger(__name__)

# A pod can leave the fleet only from these states: anything else is mid-trip.
REMOVABLE_STATUSES = frozenset({PodStatus.IDLE, PodStatus.CHARGING})


class PodFleet:
    def __init__(self, graph: NetworkGraph, pods: Iterable[Pod] = ()) -> None:
        self._graph = graph
        self._pods: dict[str, Pod] = {}
        for pod in pods:
            self.add_pod(pod)

    def __len__(self) -> int:
        return len(self._pods)

    def __iter__(self) -> Iterator[Pod]:
        return iter(self.pods())

    def __contains__(self, pod_id: object) -> bool:
        return pod_id in self._pods

    @property
    def graph(self) -> NetworkGraph:
        return self._graph

    # --- membership -------------------------------------------------------------
    def add_pod(self, pod: Pod) -> None:
        if not isinstance(pod, Pod):
            raise ModelValidationError(f"expected a Pod, got {type(pod).__name__}")
        if pod.pod_id in self._pods:
            raise DuplicatePodError(f"pod id {pod.pod_id!r} is already in the fleet")
        if not self._graph.has_node(pod.current_node_id):
            raise NodeNotFoundError(
                f"pod {pod.pod_id!r} stands at {pod.current_node_id!r}, which is not a network node"
            )
        self._pods[pod.pod_id] = pod

    def remove_pod(self, pod_id: str) -> Pod:
        """Remove a pod, but only when it is safe: a pod holding a trip or moving
        stays put and raises instead of silently abandoning its passengers."""
        pod = self.get_pod(pod_id)
        if pod.status not in REMOVABLE_STATUSES:
            allowed = ", ".join(sorted(s.value for s in REMOVABLE_STATUSES))
            raise PodStateError(
                f"pod {pod_id!r} is {pod.status.value} and cannot be removed (safe states: {allowed})"
            )
        if pod.assigned_trip_id is not None:
            raise PodStateError(f"pod {pod_id!r} still holds trip {pod.assigned_trip_id!r}")
        return self._pods.pop(pod_id)

    def get_pod(self, pod_id: str) -> Pod:
        try:
            return self._pods[pod_id]
        except KeyError:
            raise PodNotFoundError(f"no pod with id {pod_id!r}") from None

    def has_pod(self, pod_id: str) -> bool:
        return pod_id in self._pods

    # --- listing ----------------------------------------------------------------
    def pods(self) -> tuple[Pod, ...]:
        """Every pod, in pod_id order (deterministic, never insertion order)."""
        return tuple(self._pods[pod_id] for pod_id in sorted(self._pods))

    def pod_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._pods))

    def pods_with_status(self, status: PodStatus) -> tuple[Pod, ...]:
        status = PodStatus(status)
        return tuple(pod for pod in self.pods() if pod.status is status)

    def idle_pods(self) -> tuple[Pod, ...]:
        return self.pods_with_status(PodStatus.IDLE)

    def count_by_status(self) -> dict[str, int]:
        """Every status appears, including the zeros, so the shape is stable."""
        counts = {status.value: 0 for status in PodStatus}
        for pod in self._pods.values():
            counts[pod.status.value] += 1
        return counts

    def total_capacity(self) -> int:
        return sum(pod.capacity for pod in self._pods.values())

    def occupied_seats(self) -> int:
        return sum(pod.occupied_seats for pod in self._pods.values())

    # --- state changes ----------------------------------------------------------
    def assign_trip(self, pod_id: str, trip_id: str, route, party_size: int) -> Pod:
        """Attach a trip to a specific pod. Validation lives on the Pod."""
        pod = self.get_pod(pod_id)
        pod.assign(trip_id, route, party_size)
        logger.debug("pod %s assigned trip %s (%d seats)", pod_id, trip_id, party_size)
        return pod

    def release_pod(self, pod_id: str) -> str | None:
        pod = self.get_pod(pod_id)
        trip_id = pod.release()
        logger.debug("pod %s released trip %s", pod_id, trip_id)
        return trip_id

    def set_status(self, pod_id: str, status: PodStatus) -> Pod:
        pod = self.get_pod(pod_id)
        pod.set_status(status)
        return pod

    def to_dict(self) -> dict:
        return {
            "pod_count": len(self._pods),
            "total_capacity": self.total_capacity(),
            "count_by_status": self.count_by_status(),
            "pods": [pod.to_dict() for pod in self.pods()],
        }
