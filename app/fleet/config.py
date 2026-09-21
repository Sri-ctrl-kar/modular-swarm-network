"""Assumptions of the SYNTHETIC pod-fleet simulation (M3).

*** ALL FLEET DATA IS SYNTHETIC. The pods, their capacity, the energy model and
    the charging behaviour are invented for prototyping. This is NOT a physically
    calibrated vehicle, battery or charging model. ***

Every number the fleet simulation uses lives here and nowhere else, mirroring
``app/config.py`` (network) and ``app/demand/config.py`` (demand). No magic
numbers belong in the movement, assignment or battery code.

ENERGY MODEL (a deliberate approximation, not physics)
------------------------------------------------------
    energy_kwh = base_energy_kwh_per_km * distance_km * congestion_factor

``congestion_factor`` is the edge's own ``congestion_multiplier`` from M1 — the
fleet does NOT define a second congestion model. Stop-start traffic therefore
costs proportionally more energy, which is directionally right and numerically
invented.

    battery_percent_drop = energy_kwh / battery_capacity_kwh * 100

At the defaults below one km costs 0.45 % of a full battery, so a pod manages
roughly 40 km of the synthetic city's trips before it needs charging.

WHAT THIS MODEL IGNORES
-----------------------
Gradients, mass, payload, regenerative braking, temperature, battery ageing,
charging curves (charging here is linear), auxiliary loads, and the energy cost
of driving empty to a pickup. M3 also keeps every pod independent: there is no
drafting or platooning benefit, because swarm formation is deferred to M4.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping

from app.demand.config import PlaceRole
from app.errors import FleetConfigError

FLEET_SCHEMA_VERSION = 1
DEFAULT_FLEET_SEED = 42

FLEET_PROVENANCE = (
    "SYNTHETIC — pods, capacities, energy consumption and charging rates are invented "
    "for prototyping. This is a simulation approximation, not a physically calibrated "
    "vehicle or battery model."
)


def _check_number(value: Any, label: str, *, minimum: float | None = None,
                  maximum: float | None = None, allow_equal_min: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise FleetConfigError(f"{label} must be a finite number, got {value!r}")
    if minimum is not None:
        if allow_equal_min and value < minimum:
            raise FleetConfigError(f"{label} must be >= {minimum}, got {value!r}")
        if not allow_equal_min and value <= minimum:
            raise FleetConfigError(f"{label} must be > {minimum}, got {value!r}")
    if maximum is not None and value > maximum:
        raise FleetConfigError(f"{label} must be <= {maximum}, got {value!r}")
    return float(value)


# Where pods start the day. Hubs (terminals and interchange stations) get the most,
# residential areas next, plain junctions least. An invented deployment, not a plan.
DEPOT_ROLE_WEIGHTS: dict[PlaceRole, float] = {
    PlaceRole.HUB: 4.0,
    PlaceRole.RESIDENTIAL: 3.0,
    PlaceRole.EMPLOYMENT: 2.0,
    PlaceRole.MIXED: 2.0,
    PlaceRole.THROUGH: 1.0,
}


@dataclass(frozen=True)
class FleetConfig:
    """Every tunable assumption of the fleet simulation."""

    default_pod_capacity: int = 4
    tick_minutes: float = 1.0

    # Energy (see module docstring — an approximation).
    base_energy_kwh_per_km: float = 0.18
    battery_capacity_kwh: float = 40.0
    initial_battery_percent: float = 100.0

    # How long a request waits for a pod before it is given up as unserviceable.
    # Without this a trip whose origin never sees an idle pod would wait forever.
    max_trip_wait_min: float = 60.0

    # Charging and eligibility.
    low_battery_percent: float = 20.0
    target_charge_percent: float = 90.0
    charge_percent_per_min: float = 1.5
    assignment_battery_reserve_percent: float = 5.0

    depot_role_weights: Mapping[PlaceRole, float] = field(default_factory=lambda: dict(DEPOT_ROLE_WEIGHTS))
    data_provenance: str = FLEET_PROVENANCE

    def __post_init__(self) -> None:
        if isinstance(self.default_pod_capacity, bool) or not isinstance(self.default_pod_capacity, int) \
                or self.default_pod_capacity < 1:
            raise FleetConfigError(f"default_pod_capacity must be an int >= 1, got {self.default_pod_capacity!r}")
        object.__setattr__(self, "tick_minutes",
                           _check_number(self.tick_minutes, "tick_minutes", minimum=0.0, allow_equal_min=False))
        object.__setattr__(self, "base_energy_kwh_per_km",
                           _check_number(self.base_energy_kwh_per_km, "base_energy_kwh_per_km",
                                         minimum=0.0, allow_equal_min=False))
        object.__setattr__(self, "battery_capacity_kwh",
                           _check_number(self.battery_capacity_kwh, "battery_capacity_kwh",
                                         minimum=0.0, allow_equal_min=False))
        object.__setattr__(self, "initial_battery_percent",
                           _check_number(self.initial_battery_percent, "initial_battery_percent",
                                         minimum=0.0, maximum=100.0))
        object.__setattr__(self, "max_trip_wait_min",
                           _check_number(self.max_trip_wait_min, "max_trip_wait_min",
                                         minimum=0.0, allow_equal_min=False))
        object.__setattr__(self, "low_battery_percent",
                           _check_number(self.low_battery_percent, "low_battery_percent",
                                         minimum=0.0, maximum=100.0))
        object.__setattr__(self, "target_charge_percent",
                           _check_number(self.target_charge_percent, "target_charge_percent",
                                         minimum=0.0, maximum=100.0))
        object.__setattr__(self, "charge_percent_per_min",
                           _check_number(self.charge_percent_per_min, "charge_percent_per_min",
                                         minimum=0.0, allow_equal_min=False))
        object.__setattr__(self, "assignment_battery_reserve_percent",
                           _check_number(self.assignment_battery_reserve_percent,
                                         "assignment_battery_reserve_percent", minimum=0.0, maximum=100.0))
        if self.target_charge_percent <= self.low_battery_percent:
            raise FleetConfigError(
                f"target_charge_percent ({self.target_charge_percent}) must exceed "
                f"low_battery_percent ({self.low_battery_percent}), or charging would never finish"
            )

        weights = {}
        for key, value in dict(self.depot_role_weights).items():
            try:
                role = PlaceRole(key)
            except ValueError:
                allowed = ", ".join(r.value for r in PlaceRole)
                raise FleetConfigError(f"depot_role_weights: unknown role {key!r} "
                                       f"(allowed: {allowed})") from None
            weights[role] = _check_number(value, f"depot_role_weights[{role.value}]", minimum=0.0)
        if not weights or sum(weights.values()) <= 0:
            raise FleetConfigError("depot_role_weights needs at least one positive weight")
        object.__setattr__(self, "depot_role_weights", weights)

    # --- derived energy helpers -------------------------------------------------
    @property
    def percent_per_kwh(self) -> float:
        return 100.0 / self.battery_capacity_kwh

    def energy_kwh_for(self, distance_km: float, congestion_multiplier: float) -> float:
        """Energy for one edge traversal. ``congestion_multiplier`` comes from the
        edge itself (M1), so there is no second congestion model here."""
        distance_km = _check_number(distance_km, "distance_km", minimum=0.0)
        congestion_multiplier = _check_number(congestion_multiplier, "congestion_multiplier", minimum=0.0)
        return self.base_energy_kwh_per_km * distance_km * congestion_multiplier

    def battery_drop_percent_for(self, distance_km: float, congestion_multiplier: float) -> float:
        return self.energy_kwh_for(distance_km, congestion_multiplier) * self.percent_per_kwh

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": FLEET_SCHEMA_VERSION,
            "data_provenance": self.data_provenance,
            "default_pod_capacity": self.default_pod_capacity,
            "tick_minutes": self.tick_minutes,
            "energy_model": {
                "formula": "energy_kwh = base_energy_kwh_per_km * distance_km * congestion_multiplier; "
                           "battery_drop_percent = energy_kwh / battery_capacity_kwh * 100",
                "base_energy_kwh_per_km": self.base_energy_kwh_per_km,
                "battery_capacity_kwh": self.battery_capacity_kwh,
                "note": "A simulation approximation. congestion_multiplier is M1's own edge "
                        "multiplier; the fleet defines no second congestion model.",
            },
            "max_trip_wait_min": self.max_trip_wait_min,
            "initial_battery_percent": self.initial_battery_percent,
            "low_battery_percent": self.low_battery_percent,
            "target_charge_percent": self.target_charge_percent,
            "charge_percent_per_min": self.charge_percent_per_min,
            "assignment_battery_reserve_percent": self.assignment_battery_reserve_percent,
            "depot_role_weights": {role.value: w for role, w in sorted(
                self.depot_role_weights.items(), key=lambda item: item[0].value)},
        }


DEFAULT_FLEET_CONFIG = FleetConfig()
