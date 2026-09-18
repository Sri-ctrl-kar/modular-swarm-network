"""Lightweight directed weighted graph with dynamic edge costs.

Adjacency lists hold references to ``Edge`` objects, so changing an edge's
vehicle count changes its routing cost immediately — no rebuild required.

Insertion order is preserved everywhere (dicts/lists), which makes iteration,
and therefore routing tie-breaking, fully deterministic.
"""

from __future__ import annotations

import logging
import math
from collections import Counter, deque
from dataclasses import dataclass
from typing import Any

from app.config import GEOMETRY_TOLERANCE, NETWORK_MAX_SPEED_KMH
from app.errors import (
    DuplicateEdgeError,
    DuplicateNodeError,
    EdgeNotFoundError,
    GeometryValidationError,
    ModelValidationError,
    NodeNotFoundError,
)
from app.models.edge import Edge
from app.models.node import Node
from app.network.geo import node_distance_km

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConnectivityReport:
    node_count: int
    is_strongly_connected: bool
    is_weakly_connected: bool
    strongly_connected_components: tuple[tuple[str, ...], ...]
    isolated_nodes: tuple[str, ...]

    @property
    def component_count(self) -> int:
        return len(self.strongly_connected_components)


class NetworkGraph:
    def __init__(self, *, max_speed_kmh: float = NETWORK_MAX_SPEED_KMH) -> None:
        if isinstance(max_speed_kmh, bool) or not isinstance(max_speed_kmh, (int, float)) \
                or not math.isfinite(max_speed_kmh) or max_speed_kmh <= 0:
            raise ModelValidationError(f"max_speed_kmh must be a finite number > 0, got {max_speed_kmh!r}")
        self._max_speed_kmh = float(max_speed_kmh)
        self._nodes: dict[str, Node] = {}
        self._edges: dict[str, Edge] = {}
        self._outgoing: dict[str, list[Edge]] = {}
        self._incoming: dict[str, list[Edge]] = {}
        self._name_index: dict[str, str] = {}

    # --- construction --------------------------------------------------------
    def add_node(self, node: Node) -> None:
        if not isinstance(node, Node):
            raise TypeError("add_node expects a Node")
        if node.node_id in self._nodes:
            raise DuplicateNodeError(f"node id already exists: {node.node_id!r}")
        key = node.name.casefold()
        if key in self._name_index:
            raise DuplicateNodeError(f"node name already exists: {node.name!r}")
        self._nodes[node.node_id] = node
        self._outgoing[node.node_id] = []
        self._incoming[node.node_id] = []
        self._name_index[key] = node.node_id
        logger.debug("added node %s (%s)", node.node_id, node.node_type.value)

    def add_edge(self, edge: Edge) -> None:
        if not isinstance(edge, Edge):
            raise TypeError("add_edge expects an Edge")
        if edge.edge_id in self._edges:
            raise DuplicateEdgeError(f"edge id already exists: {edge.edge_id!r}")
        source = self.get_node(edge.source)
        destination = self.get_node(edge.destination)

        straight_km = node_distance_km(source, destination)
        if edge.distance_km < straight_km * (1.0 - GEOMETRY_TOLERANCE):
            raise GeometryValidationError(
                f"edge {edge.edge_id!r}: road distance {edge.distance_km:.6f} km is shorter than "
                f"straight-line distance {straight_km:.6f} km"
            )
        if edge.free_flow_speed_kmh > self._max_speed_kmh * (1.0 + GEOMETRY_TOLERANCE):
            raise GeometryValidationError(
                f"edge {edge.edge_id!r}: free-flow speed {edge.free_flow_speed_kmh:.2f} km/h exceeds "
                f"network ceiling {self._max_speed_kmh:.2f} km/h"
            )
        self._edges[edge.edge_id] = edge
        self._outgoing[edge.source].append(edge)
        self._incoming[edge.destination].append(edge)
        logger.debug("added edge %s %s->%s", edge.edge_id, edge.source, edge.destination)

    # --- lookup --------------------------------------------------------------
    def get_node(self, node_id: str) -> Node:
        try:
            return self._nodes[node_id]
        except (KeyError, TypeError):
            raise NodeNotFoundError(f"unknown node: {node_id!r}") from None

    def get_edge(self, edge_id: str) -> Edge:
        try:
            return self._edges[edge_id]
        except (KeyError, TypeError):
            raise EdgeNotFoundError(f"unknown edge: {edge_id!r}") from None

    def has_node(self, node_id: str) -> bool:
        return node_id in self._nodes

    def has_edge(self, edge_id: str) -> bool:
        return edge_id in self._edges

    def resolve_node(self, reference: str) -> Node:
        """Find a node by exact id, then by case-insensitive name."""
        if isinstance(reference, str):
            if reference in self._nodes:
                return self._nodes[reference]
            node_id = self._name_index.get(reference.strip().casefold())
            if node_id is not None:
                return self._nodes[node_id]
        raise NodeNotFoundError(f"no node with id or name {reference!r}")

    def outgoing_edges(self, node_id: str) -> tuple[Edge, ...]:
        self.get_node(node_id)
        return tuple(self._outgoing[node_id])

    def incoming_edges(self, node_id: str) -> tuple[Edge, ...]:
        self.get_node(node_id)
        return tuple(self._incoming[node_id])

    def neighbors(self, node_id: str) -> tuple[str, ...]:
        """Distinct successor node ids, in edge-insertion order."""
        return tuple(dict.fromkeys(e.destination for e in self.outgoing_edges(node_id)))

    def nodes(self) -> tuple[Node, ...]:
        return tuple(self._nodes.values())

    def edges(self) -> tuple[Edge, ...]:
        return tuple(self._edges.values())

    def node_count(self) -> int:
        return len(self._nodes)

    def edge_count(self) -> int:
        return len(self._edges)

    @property
    def max_speed_kmh(self) -> float:
        return self._max_speed_kmh

    # --- dynamic costs -------------------------------------------------------
    def set_vehicle_count(self, edge_id: str, count: int) -> None:
        self.get_edge(edge_id).set_vehicle_count(count)
        logger.debug("edge %s vehicle count -> %s", edge_id, count)

    def edge_travel_time_min(self, edge_id: str) -> float:
        return self.get_edge(edge_id).current_travel_time_min

    # --- analysis ------------------------------------------------------------
    def validate_connectivity(self) -> ConnectivityReport:
        """Strong/weak connectivity via iterative Kosaraju (deterministic output)."""
        order: list[str] = []
        visited: set[str] = set()
        for start in self._nodes:
            if start in visited:
                continue
            visited.add(start)
            stack = [(start, iter(self._outgoing[start]))]
            while stack:
                node, it = stack[-1]
                for edge in it:
                    if edge.destination not in visited:
                        visited.add(edge.destination)
                        stack.append((edge.destination, iter(self._outgoing[edge.destination])))
                        break
                else:
                    stack.pop()
                    order.append(node)

        assigned: set[str] = set()
        components: list[tuple[str, ...]] = []
        for start in reversed(order):
            if start in assigned:
                continue
            assigned.add(start)
            members, stack2 = [], [start]
            while stack2:
                node = stack2.pop()
                members.append(node)
                for edge in self._incoming[node]:
                    if edge.source not in assigned:
                        assigned.add(edge.source)
                        stack2.append(edge.source)
            components.append(tuple(sorted(members)))
        components.sort(key=lambda comp: (-len(comp), comp))

        weakly = False
        if self._nodes:
            first = next(iter(self._nodes))
            seen, queue = {first}, deque([first])
            while queue:
                node = queue.popleft()
                for nxt in [e.destination for e in self._outgoing[node]] + [e.source for e in self._incoming[node]]:
                    if nxt not in seen:
                        seen.add(nxt)
                        queue.append(nxt)
            weakly = len(seen) == len(self._nodes)

        isolated = tuple(n for n in self._nodes if not self._outgoing[n] and not self._incoming[n])
        report = ConnectivityReport(
            node_count=len(self._nodes),
            is_strongly_connected=bool(self._nodes) and len(components) == 1,
            is_weakly_connected=weakly,
            strongly_connected_components=tuple(components),
            isolated_nodes=isolated,
        )
        logger.info("connectivity: strong=%s weak=%s components=%d",
                    report.is_strongly_connected, report.is_weakly_connected, report.component_count)
        return report

    def statistics(self) -> dict[str, Any]:
        edges = self._edges.values()
        return {
            "node_count": self.node_count(),
            "edge_count": self.edge_count(),
            "nodes_by_type": dict(sorted(Counter(n.node_type.value for n in self._nodes.values()).items())),
            "edges_by_road_type": dict(sorted(Counter(e.road_type.value for e in edges).items())),
            "total_road_km": round(math.fsum(e.distance_km for e in edges), 3),
            "overloaded_edges": sum(1 for e in edges if e.is_overloaded),
            "max_speed_kmh": self._max_speed_kmh,
        }
