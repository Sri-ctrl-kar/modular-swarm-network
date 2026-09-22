"""The spatial demand map: forecast demand against available supply, per node.

Every column has one definition, stated here and nowhere else:

``current_demand``      trips requested from this node inside the *current* window.
``forecast_demand``     the deterministic forecast for the near-future window.
``available_pods``      pods standing at this node that are **eligible to be
                        repositioned** (idle, not charging, not in an active swarm).
                        Battery is not checked here because it depends on where the
                        pod would go; the planner applies it per candidate move.
``demand_supply_ratio`` ``forecast_demand / available_pods``, or ``None`` when no
                        pods are available — undefined, not infinity, and never
                        silently treated as zero.
``balance``             ``available_pods − forecast_demand``. Negative means short.
``deficit``             ``max(0, −balance)`` — pods short of the forecast.
``surplus``             ``max(0, balance)`` — pods spare against the forecast.
``actual_near_future_demand``
                        what really arrives in the near-future window.
                        **Evaluation only.** It is filled from a separate argument
                        and is never read by the forecast or the planner.

Demand is in **trips**, supply in **pods**: one pod serves one trip at a time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from app.demand.config import BASELINE_DEMAND_PROFILE, DemandProfile
from app.fleet.models import TripRecord
from app.fleet.pod_fleet import PodFleet
from app.network.graph import NetworkGraph
from app.rebalancing.config import DEFAULT_REBALANCING_CONFIG, RebalancingConfig
from app.rebalancing.eligibility import eligible_pods_by_node
from app.rebalancing.forecast import (
    DemandForecast,
    build_forecast,
    demand_in_window,
    demand_windows,
)

BALANCE_DECIMALS = 4


@dataclass(frozen=True)
class DemandMapRow:
    """One node's supply/demand picture. See the module docstring for definitions."""

    node_id: str
    current_demand: int
    forecast_demand: float
    available_pods: int
    available_pod_ids: tuple[str, ...]
    demand_supply_ratio: float | None
    balance: float
    deficit: float
    surplus: float
    actual_near_future_demand: int | None = None

    @property
    def is_deficit(self) -> bool:
        return self.deficit > 0.0

    @property
    def is_surplus(self) -> bool:
        return self.surplus > 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "current_demand": self.current_demand,
            "forecast_demand": self.forecast_demand,
            "available_pods": self.available_pods,
            "available_pod_ids": list(self.available_pod_ids),
            "demand_supply_ratio": self.demand_supply_ratio,
            "balance": self.balance,
            "deficit": self.deficit,
            "surplus": self.surplus,
            "actual_near_future_demand": self.actual_near_future_demand,
        }


@dataclass(frozen=True)
class SpatialDemandMap:
    """The whole fleet's spatial picture at one instant, in node_id order."""

    now_min: float
    horizon_min: float
    forecast: DemandForecast
    rows: tuple[DemandMapRow, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "rows", tuple(sorted(self.rows, key=lambda r: r.node_id)))

    def row(self, node_id: str) -> DemandMapRow:
        for row in self.rows:
            if row.node_id == node_id:
                return row
        raise KeyError(f"node {node_id!r} is not in this demand map")

    def deficit_rows(self, minimum: float = 0.0) -> tuple[DemandMapRow, ...]:
        """Short nodes, biggest shortfall first; ties break on node id."""
        rows = [r for r in self.rows if r.deficit > minimum]
        return tuple(sorted(rows, key=lambda r: (-r.deficit, r.node_id)))

    def surplus_rows(self, minimum: float = 0.0) -> tuple[DemandMapRow, ...]:
        """Spare nodes, biggest surplus first; ties break on node id."""
        rows = [r for r in self.rows if r.surplus > minimum]
        return tuple(sorted(rows, key=lambda r: (-r.surplus, r.node_id)))

    @property
    def total_deficit(self) -> float:
        return round(sum(r.deficit for r in self.rows), BALANCE_DECIMALS)

    @property
    def total_surplus(self) -> float:
        return round(sum(r.surplus for r in self.rows), BALANCE_DECIMALS)

    @property
    def total_available_pods(self) -> int:
        return sum(r.available_pods for r in self.rows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "now_min": self.now_min,
            "horizon_min": self.horizon_min,
            "total_deficit": self.total_deficit,
            "total_surplus": self.total_surplus,
            "total_available_pods": self.total_available_pods,
            "rows": [row.to_dict() for row in self.rows],
        }


def build_demand_map(graph: NetworkGraph, fleet: PodFleet, records: Sequence[TripRecord],
                     now_min: float, config: RebalancingConfig = DEFAULT_REBALANCING_CONFIG,
                     profile: DemandProfile = BASELINE_DEMAND_PROFILE,
                     in_active_swarm: Callable[[str], bool] | None = None,
                     *, include_actuals: bool = False) -> SpatialDemandMap:
    """Build the spatial map for ``now_min``.

    ``include_actuals`` fills ``actual_near_future_demand`` from the same records,
    purely so a report can compare forecast with outcome. The forecast is built
    before and independently of it, so switching this flag can never change a
    forecast, a deficit or a decision — a test pins that.
    """
    forecast = build_forecast(graph, records, now_min, config, profile)
    windows = demand_windows(now_min, config)
    current = demand_in_window(records, windows["current"])
    actual = demand_in_window(records, windows["near_future"]) if include_actuals else {}
    available = eligible_pods_by_node(fleet, in_active_swarm)

    rows = []
    for node in sorted(graph.nodes(), key=lambda n: n.node_id):
        node_id = node.node_id
        pod_ids = available.get(node_id, ())
        pods = len(pod_ids)
        forecast_demand = forecast.forecast_for(node_id)
        balance = pods - forecast_demand
        rows.append(DemandMapRow(
            node_id=node_id,
            current_demand=current.get(node_id, 0),
            forecast_demand=forecast_demand,
            available_pods=pods,
            available_pod_ids=pod_ids,
            demand_supply_ratio=(None if pods == 0
                                 else round(forecast_demand / pods, BALANCE_DECIMALS)),
            balance=round(balance, BALANCE_DECIMALS),
            deficit=round(max(0.0, -balance), BALANCE_DECIMALS),
            surplus=round(max(0.0, balance), BALANCE_DECIMALS),
            actual_near_future_demand=(actual.get(node_id, 0) if include_actuals else None),
        ))
    return SpatialDemandMap(now_min=float(now_min), horizon_min=config.forecast_horizon_min,
                            forecast=forecast, rows=tuple(rows))
