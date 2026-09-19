"""Simulation state for M1: clock + network + per-edge vehicle counts.

No passengers or pods exist yet (M2/M3). Traffic changes mutate edge counts in
place; routing reads them on every call, so costs update without rebuilding.
"""

from __future__ import annotations

import logging
import math

from app.errors import ModelValidationError
from app.models.network_state import NetworkSnapshot
from app.models.route import Route
from app.network.builder import LoadedScenario
from app.network.graph import NetworkGraph
from app.routing import find_route

logger = logging.getLogger(__name__)


class SimulationState:
    def __init__(self, graph: NetworkGraph, start_time_min: float = 0.0) -> None:
        self._graph = graph
        self._time_min = 0.0
        self._set_time(start_time_min)

    @classmethod
    def from_scenario(cls, scenario: LoadedScenario) -> "SimulationState":
        return cls(scenario.graph, scenario.metadata.start_time_min)

    @property
    def graph(self) -> NetworkGraph:
        return self._graph

    @property
    def time_min(self) -> float:
        return self._time_min

    def _set_time(self, value: float) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ModelValidationError(f"simulation time must be a finite number >= 0, got {value!r}")
        self._time_min = float(value)

    def advance(self, delta_min: float) -> float:
        if isinstance(delta_min, bool) or not isinstance(delta_min, (int, float)) \
                or not math.isfinite(delta_min) or delta_min <= 0:
            raise ModelValidationError(f"delta_min must be a finite number > 0, got {delta_min!r}")
        self._set_time(self._time_min + delta_min)
        logger.debug("simulation time -> %.3f min", self._time_min)
        return self._time_min

    # --- traffic ---------------------------------------------------------------
    def set_vehicle_count(self, edge_id: str, count: int) -> None:
        self._graph.set_vehicle_count(edge_id, count)

    def adjust_vehicle_count(self, edge_id: str, delta: int) -> int:
        if isinstance(delta, bool) or not isinstance(delta, int):
            raise ModelValidationError("delta must be an integer")
        new_count = self._graph.get_edge(edge_id).current_vehicle_count + delta
        if new_count < 0:
            raise ModelValidationError(f"edge {edge_id!r} vehicle count would become negative")
        self._graph.set_vehicle_count(edge_id, new_count)
        return new_count

    def set_utilization(self, edge_id: str, utilization: float) -> int:
        """Set count = round(capacity * utilization). Returns the new count."""
        if isinstance(utilization, bool) or not isinstance(utilization, (int, float)) \
                or not math.isfinite(utilization) or utilization < 0:
            raise ModelValidationError("utilization must be a finite number >= 0")
        edge = self._graph.get_edge(edge_id)
        count = round(edge.capacity_vehicles_per_hour * utilization)
        edge.set_vehicle_count(count)
        return count

    def clear_traffic(self) -> None:
        for edge in self._graph.edges():
            edge.set_vehicle_count(0)

    def vehicle_counts(self) -> dict[str, int]:
        return {e.edge_id: e.current_vehicle_count for e in self._graph.edges()}

    def edge_travel_time_min(self, edge_id: str) -> float:
        return self._graph.edge_travel_time_min(edge_id)

    # --- snapshots -------------------------------------------------------------
    def snapshot(self) -> NetworkSnapshot:
        return NetworkSnapshot(self._time_min, tuple(self.vehicle_counts().items()))

    def restore(self, snapshot: NetworkSnapshot) -> None:
        counts = dict(snapshot.vehicle_counts)
        if set(counts) != set(self.vehicle_counts()):
            raise ModelValidationError("snapshot edge set does not match the network")
        for edge_id, count in counts.items():
            self._graph.set_vehicle_count(edge_id, count)
        self._set_time(snapshot.time_min)

    def route(self, origin: str, destination: str, algorithm: str = "astar") -> Route:
        return find_route(self._graph, origin, destination, algorithm)
