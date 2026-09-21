"""Central, explicit configuration for Milestone 1.

Every numeric assumption used by the simulator lives here and is mirrored in
``scenarios/baseline.json`` so nothing important is hidden inside algorithm code.

CONGESTION MODEL (exact formula)
--------------------------------
    utilization u = current_vehicle_count / capacity_vehicles_per_hour

    if u <= 1:  multiplier = 1 + alpha * u ** beta                (BPR-style curve)
    if u >  1:  multiplier = 1 + alpha + overload_slope * (u - 1) (steep linear penalty)

    current_travel_time = base_travel_time * multiplier

Defaults: alpha = 0.15, beta = 4.0 (the classic US Bureau of Public Roads values),
overload_slope = 2.0 (a project assumption, NOT an empirically calibrated value).
The function is continuous at u = 1, monotonically non-decreasing, and always
>= 1, so congestion can never make an edge faster than free flow and travel time
can never be negative.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.errors import ModelValidationError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCENARIO_PATH = PROJECT_ROOT / "scenarios" / "baseline.json"

SCENARIO_SCHEMA_VERSION = 1
DEFAULT_SEED = 42

# Mean Earth radius (IUGG). Used only for straight-line (great-circle) distance.
EARTH_RADIUS_KM = 6371.0088

# Speed ceiling for the whole network. Every edge must satisfy
# distance_km / (base_travel_time_min / 60) <= NETWORK_MAX_SPEED_KMH.
# The A* time heuristic divides straight-line distance by this value.
NETWORK_MAX_SPEED_KMH = 100.0

# The A* heuristic is multiplied by this factor (< 1) so floating-point rounding
# can never push it above the true remaining cost.
HEURISTIC_SAFETY_FACTOR = 0.999

# Relative tolerance for geometry validation (float noise only).
GEOMETRY_TOLERANCE = 1e-9


def _is_real_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


@dataclass(frozen=True)
class CongestionParameters:
    """Parameters of the transparent congestion model (see module docstring)."""

    alpha: float = 0.15
    beta: float = 4.0
    overload_slope: float = 2.0

    def __post_init__(self) -> None:
        for field_name in ("alpha", "beta", "overload_slope"):
            value = getattr(self, field_name)
            if not _is_real_number(value) or not math.isfinite(value):
                raise ModelValidationError(f"congestion.{field_name} must be a finite number, got {value!r}")
        if self.alpha < 0:
            raise ModelValidationError("congestion.alpha must be >= 0")
        if self.beta < 1:
            raise ModelValidationError("congestion.beta must be >= 1")
        if self.overload_slope <= 0:
            raise ModelValidationError("congestion.overload_slope must be > 0")

    def multiplier(self, utilization: float) -> float:
        """Return the travel-time multiplier (always >= 1) for a utilization ratio."""
        if not _is_real_number(utilization) or not math.isfinite(utilization) or utilization < 0:
            raise ModelValidationError(f"utilization must be a finite number >= 0, got {utilization!r}")
        if utilization <= 1.0:
            return 1.0 + self.alpha * utilization**self.beta
        return 1.0 + self.alpha + self.overload_slope * (utilization - 1.0)

    def to_dict(self) -> dict[str, float]:
        return {"alpha": self.alpha, "beta": self.beta, "overload_slope": self.overload_slope}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CongestionParameters":
        unknown = set(data) - {"alpha", "beta", "overload_slope"}
        if unknown:
            raise ModelValidationError(f"unknown congestion parameters: {sorted(unknown)}")
        return cls(**data)


DEFAULT_CONGESTION = CongestionParameters()


@dataclass(frozen=True)
class RoadTypeProfile:
    """SYNTHETIC defaults used only by the synthetic city generator."""

    nominal_speed_kmh: float
    default_capacity_vph: int
    detour_factor_min: float  # road distance / straight-line distance
    detour_factor_max: float

    def to_dict(self) -> dict[str, float]:
        return {
            "nominal_speed_kmh": self.nominal_speed_kmh,
            "default_capacity_vph": self.default_capacity_vph,
            "detour_factor_min": self.detour_factor_min,
            "detour_factor_max": self.detour_factor_max,
        }


ROAD_TYPE_PROFILES: dict[str, RoadTypeProfile] = {
    "local": RoadTypeProfile(30.0, 600, 1.30, 1.45),
    "arterial": RoadTypeProfile(50.0, 1500, 1.20, 1.30),
    "trunk": RoadTypeProfile(80.0, 3600, 1.05, 1.12),
}

# Arbitrary anchor for synthetic coordinates. It does NOT represent a real city.
SYNTHETIC_ANCHOR_LAT = 45.0
SYNTHETIC_ANCHOR_LON = 10.0
KM_PER_DEGREE_LAT = 111.195

# Synthetic baseline traffic: each directed edge gets a seeded utilization in this range.
BASELINE_UTILIZATION_RANGE = (0.15, 0.55)
# Synthetic coordinate jitter (km) applied to the hand-authored layout.
COORDINATE_JITTER_KM = 0.3
