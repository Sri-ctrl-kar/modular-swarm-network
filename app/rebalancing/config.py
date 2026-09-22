"""Assumptions of adaptive fleet rebalancing (M5).

*** M5 uses deterministic, explainable demand forecasting and fleet rebalancing.
    No machine learning or LLM is involved. ***

*** Rebalancing is a heuristic and is not claimed to be globally optimal. ***

Every threshold lives here, as in every layer below. There are no magic numbers
in the forecast, the matching or the movement code.

THE FORECAST IS NOT CLAIRVOYANT
------------------------------
The forecast reads only two things, both available to an operator at the moment it
runs:

1. **Recent observed demand** — trips that were *already requested*, inside
   ``forecast_recent_window_min`` before now.
2. **The published scenario profile** — M2's ``DemandProfile``, whose time-of-day
   production weights are a declared assumption, not an observation of the future.

It never reads the trips that are about to arrive. The simulation holds the whole
trip list, so peeking would be trivial and would make the numbers meaningless;
``build_forecast`` is therefore not given future trips at all, and the actual
near-future demand is computed separately and labelled evaluation-only.

    forecast(node) = horizon
                   × [ w_recent  × recent_rate(node)
                     + w_profile × total_recent_rate × profile_share(node) ]

    recent_rate(node)   = trips requested from node in the recent window / window
    profile_share(node) = production_weight(role(node), upcoming bucket)
                          / Σ over nodes of the same weight
    total_recent_rate   = Σ over nodes of recent_rate

``w_recent + w_profile`` must be 1. The "upcoming bucket" is the profile bucket
covering ``now + forecast_horizon_min``, which is what lets pods move *before* a
peak rather than after it. With no history yet (an empty recent window) the
forecast is zero everywhere and nothing is repositioned — the honest answer.

UNITS
-----
Demand is counted in **trips**, not passengers, because one pod serves one trip at
a time regardless of party size. Supply is counted in **pods**. A node's balance is
therefore ``available_pods − forecast_trips``, directly comparable.

REBALANCING IS NOT FREE
-----------------------
A repositioning pod drives **empty**. Those kilometres, minutes and kWh are real
and are reported separately as deadhead cost — never netted off, never hidden.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from app.errors import RebalancingConfigError

REBALANCING_SCHEMA_VERSION = 1

REBALANCING_PROVENANCE = (
    "SYNTHETIC — forecast weights, windows, priority weights and reposition limits are "
    "invented project assumptions. Forecasting and rebalancing are deterministic and "
    "rule-based: no machine learning and no LLM is involved, and the heuristic is not "
    "claimed to be globally optimal."
)


def _check_number(value: Any, label: str, *, minimum: float | None = None,
                  maximum: float | None = None, allow_equal_min: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise RebalancingConfigError(f"{label} must be a finite number, got {value!r}")
    if minimum is not None:
        if allow_equal_min and value < minimum:
            raise RebalancingConfigError(f"{label} must be >= {minimum}, got {value!r}")
        if not allow_equal_min and value <= minimum:
            raise RebalancingConfigError(f"{label} must be > {minimum}, got {value!r}")
    if maximum is not None and value > maximum:
        raise RebalancingConfigError(f"{label} must be <= {maximum}, got {value!r}")
    return float(value)


def _check_int(value: Any, label: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RebalancingConfigError(f"{label} must be an integer, got {value!r}")
    if value < minimum:
        raise RebalancingConfigError(f"{label} must be >= {minimum}, got {value!r}")
    return value


@dataclass(frozen=True)
class RebalancingConfig:
    """Every tunable assumption of the forecast and the rebalancer."""

    # --- demand windows and the forecast ---------------------------------------
    current_window_min: float = 15.0        # "demand right now"
    forecast_recent_window_min: float = 60.0   # how far back the forecast looks
    forecast_horizon_min: float = 30.0      # how far ahead it forecasts
    forecast_recent_weight: float = 0.5
    forecast_profile_weight: float = 0.5
    # Minimum simulated history before the forecast is trusted at all. Early in a
    # run the recent window is only minutes long, so a single trip would imply a
    # huge hourly rate; below this the forecast is zero everywhere and nothing is
    # repositioned, which is the honest answer to "we have no history yet".
    min_history_min: float = 15.0

    # --- when the rebalancer runs ----------------------------------------------
    rebalance_interval_min: float = 10.0

    # --- what is worth acting on ----------------------------------------------
    min_deficit_to_act: float = 1.0         # ignore fractional shortfalls
    min_surplus_to_release: float = 1.0     # only take a pod from a node with spare
    max_repositions_per_cycle: int = 10
    max_concurrent_repositions: int = 20
    max_reposition_distance_km: float = 25.0
    # A node holding more eligible pods than this above its own forecast is treated
    # as badly clumped, and spare pods may be pushed toward the busiest node even if
    # that node is not itself short. This is what makes SUPPLY_SURPLUS reachable.
    max_surplus_before_drain: float = 8.0

    # --- battery safety (see module note; stricter than M3's passenger reserve) --
    reposition_battery_reserve_percent: float = 15.0

    # --- priority (kept deliberately simple and explainable) -------------------
    priority_deficit_weight: float = 1.0
    priority_distance_weight: float = 0.05

    data_provenance: str = REBALANCING_PROVENANCE

    def __post_init__(self) -> None:
        for name in ("current_window_min", "forecast_recent_window_min", "forecast_horizon_min",
                     "rebalance_interval_min", "max_reposition_distance_km"):
            object.__setattr__(self, name, _check_number(getattr(self, name), name,
                                                         minimum=0.0, allow_equal_min=False))
        object.__setattr__(self, "min_history_min",
                           _check_number(self.min_history_min, "min_history_min", minimum=0.0))
        for name in ("forecast_recent_weight", "forecast_profile_weight"):
            object.__setattr__(self, name, _check_number(getattr(self, name), name,
                                                         minimum=0.0, maximum=1.0))
        total = self.forecast_recent_weight + self.forecast_profile_weight
        if abs(total - 1.0) > 1e-9:
            raise RebalancingConfigError(
                f"forecast_recent_weight + forecast_profile_weight must be 1.0, got {total!r}")
        object.__setattr__(self, "max_surplus_before_drain",
                           _check_number(self.max_surplus_before_drain,
                                         "max_surplus_before_drain", minimum=0.0))
        for name in ("min_deficit_to_act", "min_surplus_to_release",
                     "priority_deficit_weight", "priority_distance_weight"):
            object.__setattr__(self, name, _check_number(getattr(self, name), name, minimum=0.0))
        object.__setattr__(self, "reposition_battery_reserve_percent",
                           _check_number(self.reposition_battery_reserve_percent,
                                         "reposition_battery_reserve_percent",
                                         minimum=0.0, maximum=100.0))
        object.__setattr__(self, "max_repositions_per_cycle",
                           _check_int(self.max_repositions_per_cycle,
                                      "max_repositions_per_cycle", minimum=0))
        object.__setattr__(self, "max_concurrent_repositions",
                           _check_int(self.max_concurrent_repositions,
                                      "max_concurrent_repositions", minimum=0))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": REBALANCING_SCHEMA_VERSION,
            "data_provenance": self.data_provenance,
            "demand_windows": {
                "current_window_min": self.current_window_min,
                "forecast_recent_window_min": self.forecast_recent_window_min,
                "forecast_horizon_min": self.forecast_horizon_min,
                "unit": "demand is counted in trips; supply in pods",
            },
            "forecast": {
                "formula": "horizon * (w_recent * recent_rate(node) + w_profile * "
                           "total_recent_rate * profile_share(node))",
                "forecast_recent_weight": self.forecast_recent_weight,
                "forecast_profile_weight": self.forecast_profile_weight,
                "min_history_min": self.min_history_min,
                "note": "Deterministic demand forecast. Reads only already-requested trips "
                        "and the published scenario profile — never future trips. No ML, "
                        "no LLM, and no predictive accuracy is claimed.",
            },
            "rebalancer": {
                "rebalance_interval_min": self.rebalance_interval_min,
                "min_deficit_to_act": self.min_deficit_to_act,
                "min_surplus_to_release": self.min_surplus_to_release,
                "max_repositions_per_cycle": self.max_repositions_per_cycle,
                "max_concurrent_repositions": self.max_concurrent_repositions,
                "max_reposition_distance_km": self.max_reposition_distance_km,
                "max_surplus_before_drain": self.max_surplus_before_drain,
                "priority_formula": "deficit * priority_deficit_weight "
                                    "- distance_km * priority_distance_weight",
                "priority_deficit_weight": self.priority_deficit_weight,
                "priority_distance_weight": self.priority_distance_weight,
                "note": "A deterministic heuristic, not a global optimum.",
            },
            "battery": {
                "reposition_battery_reserve_percent": self.reposition_battery_reserve_percent,
                "note": "A repositioning move is refused unless battery >= estimated energy "
                        "for the move + this reserve, so a pod arrives able to work.",
            },
        }


DEFAULT_REBALANCING_CONFIG = RebalancingConfig()
