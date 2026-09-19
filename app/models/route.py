"""Route result shared by every routing algorithm."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.errors import ModelValidationError


@dataclass(frozen=True)
class Route:
    origin: str
    destination: str
    node_ids: tuple[str, ...]
    edge_ids: tuple[str, ...]
    total_distance_km: float
    total_travel_time_min: float
    total_cost: float
    cost_metric: str
    nodes_visited: int
    algorithm: str

    def __post_init__(self) -> None:
        if not self.node_ids or self.node_ids[0] != self.origin or self.node_ids[-1] != self.destination:
            raise ModelValidationError("route node_ids must start at origin and end at destination")
        if len(self.node_ids) != len(self.edge_ids) + 1:
            raise ModelValidationError("route must have exactly one more node than edges")
        if min(self.total_distance_km, self.total_travel_time_min, self.total_cost) < 0:
            raise ModelValidationError("route totals must be non-negative")
        if self.nodes_visited < 1:
            raise ModelValidationError("nodes_visited must be >= 1")

    @property
    def hop_count(self) -> int:
        return len(self.edge_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "origin": self.origin,
            "destination": self.destination,
            "node_ids": list(self.node_ids),
            "edge_ids": list(self.edge_ids),
            "total_distance_km": self.total_distance_km,
            "total_travel_time_min": self.total_travel_time_min,
            "total_cost": self.total_cost,
            "cost_metric": self.cost_metric,
            "nodes_visited": self.nodes_visited,
            "algorithm": self.algorithm,
        }
