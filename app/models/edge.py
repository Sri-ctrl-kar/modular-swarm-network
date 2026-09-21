"""Edge model: a directed road segment with a transparent congestion model.

The static attributes (ids, endpoints, distance, base time, capacity, road type)
are immutable. Only ``current_vehicle_count`` may change, and only through
``set_vehicle_count`` which validates the new value. Travel time is always
computed on demand, so routing automatically sees the latest traffic.

See ``app.config`` for the exact congestion formula.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.config import DEFAULT_CONGESTION, CongestionParameters
from app.errors import ModelValidationError


class RoadType(str, Enum):
    LOCAL = "local"
    ARTERIAL = "arterial"
    TRUNK = "trunk"


def _require_positive_number(value: Any, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ModelValidationError(f"{field_name} must be a finite number, got {value!r}")
    if value <= 0:
        raise ModelValidationError(f"{field_name} must be > 0, got {value!r}")


def _require_count(value: Any, field_name: str, *, allow_zero: bool) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ModelValidationError(f"{field_name} must be an integer, got {value!r}")
    if value < 0 or (value == 0 and not allow_zero):
        bound = ">= 0" if allow_zero else "> 0"
        raise ModelValidationError(f"{field_name} must be {bound}, got {value!r}")


@dataclass(frozen=True, eq=False)
class Edge:
    """Directed road segment. Equality is identity; compare with ``to_dict()``."""

    edge_id: str
    source: str
    destination: str
    distance_km: float
    base_travel_time_min: float
    capacity_vehicles_per_hour: int
    road_type: RoadType
    current_vehicle_count: int = 0
    congestion: CongestionParameters = field(default=DEFAULT_CONGESTION)

    def __post_init__(self) -> None:
        for name in ("edge_id", "source", "destination"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ModelValidationError(f"{name} must be a non-empty string, got {value!r}")
        if self.source == self.destination:
            raise ModelValidationError(f"edge {self.edge_id!r}: self-loops are not allowed")
        _require_positive_number(self.distance_km, "distance_km")
        _require_positive_number(self.base_travel_time_min, "base_travel_time_min")
        _require_count(self.capacity_vehicles_per_hour, "capacity_vehicles_per_hour", allow_zero=False)
        _require_count(self.current_vehicle_count, "current_vehicle_count", allow_zero=True)
        if not isinstance(self.congestion, CongestionParameters):
            raise ModelValidationError("congestion must be a CongestionParameters instance")
        try:
            object.__setattr__(self, "road_type", RoadType(self.road_type))
        except ValueError as exc:
            allowed = ", ".join(t.value for t in RoadType)
            raise ModelValidationError(f"road_type must be one of [{allowed}], got {self.road_type!r}") from exc
        object.__setattr__(self, "distance_km", float(self.distance_km))
        object.__setattr__(self, "base_travel_time_min", float(self.base_travel_time_min))

    # --- dynamic state -----------------------------------------------------
    def set_vehicle_count(self, count: int) -> None:
        _require_count(count, "current_vehicle_count", allow_zero=True)
        object.__setattr__(self, "current_vehicle_count", count)

    # --- derived values ----------------------------------------------------
    @property
    def utilization(self) -> float:
        return self.current_vehicle_count / self.capacity_vehicles_per_hour

    @property
    def congestion_multiplier(self) -> float:
        return self.congestion.multiplier(self.utilization)

    @property
    def current_travel_time_min(self) -> float:
        return self.base_travel_time_min * self.congestion_multiplier

    @property
    def free_flow_speed_kmh(self) -> float:
        return self.distance_km / (self.base_travel_time_min / 60.0)

    @property
    def is_overloaded(self) -> bool:
        return self.utilization > 1.0

    def to_dict(self) -> dict[str, Any]:
        """Scenario-file representation (congestion params live at scenario level)."""
        return {
            "edge_id": self.edge_id,
            "source": self.source,
            "destination": self.destination,
            "distance_km": self.distance_km,
            "base_travel_time_min": self.base_travel_time_min,
            "capacity_vehicles_per_hour": self.capacity_vehicles_per_hour,
            "current_vehicle_count": self.current_vehicle_count,
            "road_type": self.road_type.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], congestion: CongestionParameters = DEFAULT_CONGESTION) -> "Edge":
        required = {
            "edge_id", "source", "destination", "distance_km",
            "base_travel_time_min", "capacity_vehicles_per_hour", "road_type",
        }
        missing = required - set(data)
        if missing:
            raise ModelValidationError(f"edge is missing fields: {sorted(missing)}")
        return cls(
            **{key: data[key] for key in required},
            current_vehicle_count=data.get("current_vehicle_count", 0),
            congestion=congestion,
        )
