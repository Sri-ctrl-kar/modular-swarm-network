"""Fleet rebalancing HOOK — interface only.

*** M4 exposes the interface required for future fleet rebalancing. Actual
    adaptive rebalancing belongs to M5. ***

M3 revealed a real limitation: a pod is only eligible for trips that start at the
node it already occupies, so pods drift to wherever demand last took them and
become spatially stranded. On the seed-42 city with 100 pods and 1,000 trips that
— not fleet capacity — is what bounds the completion rate, while pod utilisation
sits near 6 %.

This module deliberately does **not** fix that. It defines:

* ``RepositionRequest`` — a single "this pod should move there, because …" record;
* ``FleetRebalancer`` — the interface a future planner implements;
* ``SurplusDeficitRebalancer`` — a trivial, deterministic reference planner that
  shows the shape of the data by matching nodes with idle pods against nodes where
  trips went unserved.

Nothing here moves a pod, assigns a trip, or touches the fleet. ``plan()`` is
read-only and returns requests for someone else to act on; no caller in M4
executes them. A real planner (cost-aware, horizon-aware, congestion-aware, and
actually dispatched) is M5's job.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from app.errors import ModelValidationError
from app.fleet.models import TIMED_OUT_PREFIX, PodStatus, TripRecord, TripStatus
from app.fleet.pod_fleet import PodFleet
from app.network.graph import NetworkGraph

logger = logging.getLogger(__name__)

#: Why a reposition was proposed. Free-form, but these are the ones M4 emits.
REASON_UNSERVED_DEMAND = "unserved demand at destination node"


@dataclass(frozen=True)
class RepositionRequest:
    """A *proposed* empty move for one pod. Planning only — never executed in M4."""

    request_id: str
    pod_id: str
    from_node_id: str
    to_node_id: str
    reason: str
    priority: int = 0

    def __post_init__(self) -> None:
        for name in ("request_id", "pod_id", "from_node_id", "to_node_id", "reason"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ModelValidationError(f"{name} must be a non-empty string, got {value!r}")
        if self.from_node_id == self.to_node_id:
            raise ModelValidationError(
                f"reposition {self.request_id!r}: from and to must differ (both {self.from_node_id!r})"
            )
        if isinstance(self.priority, bool) or not isinstance(self.priority, int) or self.priority < 0:
            raise ModelValidationError(f"priority must be an int >= 0, got {self.priority!r}")

    def to_dict(self) -> dict[str, Any]:
        return {"request_id": self.request_id, "pod_id": self.pod_id,
                "from_node_id": self.from_node_id, "to_node_id": self.to_node_id,
                "reason": self.reason, "priority": self.priority}


class FleetRebalancer(Protocol):
    """The interface a future (M5) rebalancing planner implements.

    Implementations must be **read-only and deterministic**: ``plan`` may inspect
    the fleet, the trip records and the network, but must not mutate them, and the
    same inputs must always give the same requests in the same order.
    """

    def plan(self, fleet: PodFleet, records: Sequence[TripRecord],
             graph: NetworkGraph) -> tuple[RepositionRequest, ...]:
        ...


class SurplusDeficitRebalancer:
    """A deliberately naive reference planner, to show the data shape.

    It pairs the nodes where trips went unserved (deficit, most unserved first)
    with the nodes holding the most idle pods (surplus), and proposes one move per
    pair. It ignores distance, travel time, congestion, battery, future demand and
    everything else that would make it any good — **on purpose**. Do not mistake
    this for rebalancing; it exists so M5 has a concrete interface to replace.
    """

    def __init__(self, max_requests: int = 10) -> None:
        if isinstance(max_requests, bool) or not isinstance(max_requests, int) or max_requests < 0:
            raise ModelValidationError(f"max_requests must be an int >= 0, got {max_requests!r}")
        self._max_requests = max_requests

    @staticmethod
    def unserved_by_node(records: Sequence[TripRecord]) -> tuple[tuple[str, int], ...]:
        """(origin node, unserved trip count), busiest first then node id."""
        counts: dict[str, int] = {}
        for record in records:
            timed_out = record.status is TripStatus.FAILED \
                and (record.failure_reason or "").startswith(TIMED_OUT_PREFIX)
            if record.status is TripStatus.PENDING or timed_out:
                counts[record.origin_node_id] = counts.get(record.origin_node_id, 0) + 1
        return tuple(sorted(counts.items(), key=lambda item: (-item[1], item[0])))

    @staticmethod
    def idle_pods_by_node(fleet: PodFleet) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """(node, idle pod ids) with the most idle pods first, then node id."""
        by_node: dict[str, list[str]] = {}
        for pod in fleet.pods():
            if pod.status is PodStatus.IDLE:
                by_node.setdefault(pod.current_node_id, []).append(pod.pod_id)
        return tuple(sorted(((node, tuple(sorted(pods))) for node, pods in by_node.items()),
                            key=lambda item: (-len(item[1]), item[0])))

    def plan(self, fleet: PodFleet, records: Sequence[TripRecord],
             graph: NetworkGraph) -> tuple[RepositionRequest, ...]:
        """Propose repositioning moves. Read-only: nothing is dispatched."""
        deficits = self.unserved_by_node(records)
        surpluses = [list(pods) for _, pods in self.idle_pods_by_node(fleet)]
        surplus_nodes = [node for node, _ in self.idle_pods_by_node(fleet)]

        requests: list[RepositionRequest] = []
        for target_node, unserved in deficits:
            if len(requests) >= self._max_requests:
                break
            for index, node in enumerate(surplus_nodes):
                if node == target_node or not surpluses[index]:
                    continue
                if not graph.has_node(target_node):
                    continue
                pod_id = surpluses[index].pop(0)
                requests.append(RepositionRequest(
                    request_id=f"RB{len(requests) + 1:05d}",
                    pod_id=pod_id, from_node_id=node, to_node_id=target_node,
                    reason=REASON_UNSERVED_DEMAND, priority=unserved,
                ))
                break
        logger.info("rebalancer proposed %d reposition request(s) — none executed (M5 owns that)",
                    len(requests))
        return tuple(requests)
