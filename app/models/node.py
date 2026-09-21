"""Node model: an intersection, station or terminal with WGS84-style coordinates."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.errors import ModelValidationError


class NodeType(str, Enum):
    INTERSECTION = "intersection"
    STATION = "station"
    TERMINAL = "terminal"


def _require_text(value: Any, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ModelValidationError(f"{field_name} must be a non-empty string, got {value!r}")


def _require_coordinate(value: Any, field_name: str, limit: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ModelValidationError(f"{field_name} must be a finite number, got {value!r}")
    if not -limit <= value <= limit:
        raise ModelValidationError(f"{field_name} must be within [-{limit}, {limit}], got {value!r}")


@dataclass(frozen=True)
class Node:
    """Immutable network node."""

    node_id: str
    name: str
    latitude: float
    longitude: float
    node_type: NodeType

    def __post_init__(self) -> None:
        _require_text(self.node_id, "node_id")
        _require_text(self.name, "name")
        if self.node_id != self.node_id.strip():
            raise ModelValidationError(f"node_id must not have surrounding whitespace: {self.node_id!r}")
        _require_coordinate(self.latitude, "latitude", 90.0)
        _require_coordinate(self.longitude, "longitude", 180.0)
        try:
            object.__setattr__(self, "node_type", NodeType(self.node_type))
        except ValueError as exc:
            allowed = ", ".join(t.value for t in NodeType)
            raise ModelValidationError(f"node_type must be one of [{allowed}], got {self.node_type!r}") from exc
        object.__setattr__(self, "latitude", float(self.latitude))
        object.__setattr__(self, "longitude", float(self.longitude))

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "name": self.name,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "node_type": self.node_type.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Node":
        required = {"node_id", "name", "latitude", "longitude", "node_type"}
        missing = required - set(data)
        if missing:
            raise ModelValidationError(f"node is missing fields: {sorted(missing)}")
        return cls(**{key: data[key] for key in required})
