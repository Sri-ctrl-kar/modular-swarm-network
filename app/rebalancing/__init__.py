"""Milestone 5: adaptive fleet rebalancing under changing demand.

*** M5 uses deterministic, explainable demand forecasting and fleet rebalancing.
    No machine learning or LLM is involved. ***

*** Rebalancing is a heuristic and is not claimed to be globally optimal. ***

    demand -> demand imbalance -> rebalancing decision -> pod repositioning
           -> better spatial availability -> more trips served

This package sits above M4 and answers the limitation M3 exposed: pods drift to
wherever demand last took them, so trips go unserved for want of a pod in the right
place rather than for want of a pod. It forecasts demand per node from *already
observed* trips plus the published scenario profile, compares that with eligible
supply, and sends idle pods — **empty**, at a cost it reports in full — toward the
shortfall.

``app/rebalancing/observation.py`` defines the boundary a future M6 orchestrator
would sit above: a read-only observation, an inert structured proposal, and a
deterministic bounded validator. **M5 implements no orchestrator.**
"""

from __future__ import annotations

from app.rebalancing.config import (
    DEFAULT_REBALANCING_CONFIG,
    REBALANCING_PROVENANCE,
    RebalancingConfig,
)
from app.rebalancing.demand_map import DemandMapRow, SpatialDemandMap, build_demand_map
from app.rebalancing.eligibility import (
    IN_ACTIVE_SWARM,
    INSUFFICIENT_BATTERY,
    IS_CHARGING,
    NOT_IDLE,
    EligibilityResult,
    check_pod,
    eligible_pods,
    eligible_pods_by_node,
    has_battery_for,
    ineligibility_reasons,
    required_battery_percent,
)
from app.rebalancing.execution import (
    RepositionAssignment,
    RepositionStatus,
    reposition_id_for,
)
from app.rebalancing.forecast import (
    DemandForecast,
    DemandWindow,
    NodeForecast,
    actual_demand_in_window,
    build_forecast,
    demand_in_window,
    demand_windows,
    profile_shares,
)
from app.rebalancing.metrics import (
    RebalancingComparison,
    RebalancingMetrics,
    compare_rebalancing_modes,
    compute_rebalancing_metrics,
)
from app.rebalancing.observation import (
    ALLOWED_DEMAND_SCENARIOS,
    PARAMETER_BOUNDS,
    ActionType,
    ActionValidator,
    ProposedAction,
    SimulationObservation,
    ValidationResult,
    apply_validated_action,
    observe,
)
from app.rebalancing.planner import (
    AdaptiveRebalancer,
    RejectedMove,
    RepositionPlan,
    RepositionPlanItem,
    RepositionReason,
    classify_reason,
    route_energy_kwh,
)
from app.rebalancing.simulation import RebalancingSimulation, RebalancingTickReport

__all__ = [
    "ALLOWED_DEMAND_SCENARIOS", "ActionType", "ActionValidator", "AdaptiveRebalancer",
    "DEFAULT_REBALANCING_CONFIG", "DemandForecast", "DemandMapRow", "DemandWindow",
    "EligibilityResult", "IN_ACTIVE_SWARM", "INSUFFICIENT_BATTERY", "IS_CHARGING",
    "NOT_IDLE", "NodeForecast", "PARAMETER_BOUNDS", "ProposedAction",
    "REBALANCING_PROVENANCE", "RebalancingComparison", "RebalancingConfig",
    "RebalancingMetrics", "RebalancingSimulation", "RebalancingTickReport", "RejectedMove",
    "RepositionAssignment", "RepositionPlan", "RepositionPlanItem", "RepositionReason",
    "RepositionStatus", "SimulationObservation", "SpatialDemandMap", "ValidationResult",
    "actual_demand_in_window", "apply_validated_action", "build_demand_map", "build_forecast",
    "check_pod", "classify_reason", "compare_rebalancing_modes", "compute_rebalancing_metrics",
    "demand_in_window", "demand_windows", "eligible_pods", "eligible_pods_by_node",
    "has_battery_for", "ineligibility_reasons", "observe", "profile_shares",
    "reposition_id_for", "required_battery_percent", "route_energy_kwh",
]
