"""Demand windows and the deterministic demand forecast.

*** This is a deterministic demand forecast. No machine learning or LLM is
    involved, and no predictive accuracy is claimed. ***

FOUR WINDOWS OVER ONE TIMELINE (all in minutes of the simulated day)

    ... ──────────────┬───────────────┬──────────────── ...
        recent        │    current    │  near future
        [t-R, t)      │   [t-C, t]    │   [t, t+H)
                      │               │
                   observed        NOW = t        forecast applies here

* **recent**       ``[t - forecast_recent_window_min, t)`` — the history the
  forecast learns its rate from.
* **current**      ``[t - current_window_min, t]`` — "demand right now", reported
  so a reader can see what the fleet is already facing.
* **near future**  ``[t, t + forecast_horizon_min)`` — the window the forecast is
  *about*. Its actual contents are computed only for evaluation and are never an
  input; see ``actual_demand_in_window``.
* **forecast**     the estimate for the near-future window (this module's output).

Windows are half-open at the end except ``current``, which includes ``t`` so a
trip requested exactly now counts as current demand.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.demand.config import BASELINE_DEMAND_PROFILE, DemandProfile
from app.errors import ForecastError
from app.fleet.models import TripRecord
from app.network.graph import NetworkGraph
from app.rebalancing.config import DEFAULT_REBALANCING_CONFIG, RebalancingConfig

logger = logging.getLogger(__name__)

FORECAST_DECIMALS = 4


@dataclass(frozen=True)
class DemandWindow:
    """A half-open ``[start, end)`` span of simulated minutes, named for reporting."""

    name: str
    start_min: float
    end_min: float
    closed_end: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ForecastError(f"window name must be a non-empty string, got {self.name!r}")
        if self.end_min < self.start_min:
            raise ForecastError(f"window {self.name!r}: end {self.end_min} precedes start "
                                f"{self.start_min}")

    @property
    def length_min(self) -> float:
        return self.end_min - self.start_min

    def contains(self, time_min: float) -> bool:
        if time_min < self.start_min:
            return False
        return time_min <= self.end_min if self.closed_end else time_min < self.end_min

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "start_min": self.start_min, "end_min": self.end_min,
                "length_min": self.length_min}


def demand_windows(now_min: float,
                   config: RebalancingConfig = DEFAULT_REBALANCING_CONFIG
                   ) -> dict[str, DemandWindow]:
    """The three observable windows around ``now_min``, clamped at zero."""
    if isinstance(now_min, bool) or not isinstance(now_min, (int, float)) or now_min < 0:
        raise ForecastError(f"now_min must be a number >= 0, got {now_min!r}")
    now = float(now_min)
    return {
        "recent": DemandWindow("recent", max(0.0, now - config.forecast_recent_window_min), now),
        "current": DemandWindow("current", max(0.0, now - config.current_window_min), now,
                                closed_end=True),
        "near_future": DemandWindow("near_future", now, now + config.forecast_horizon_min),
    }


def demand_in_window(records: Sequence[TripRecord], window: DemandWindow) -> dict[str, int]:
    """{origin node: trips requested inside ``window``}.

    Counts **trips**, not passengers: one pod serves one trip at a time whatever the
    party size. Every requested trip counts, served or not — demand is demand.
    """
    counts: dict[str, int] = {}
    for record in records:
        if window.contains(record.request_time_min):
            counts[record.origin_node_id] = counts.get(record.origin_node_id, 0) + 1
    return counts


def actual_demand_in_window(records: Sequence[TripRecord], window: DemandWindow) -> dict[str, int]:
    """Demand that really falls in a window — **for evaluation only.**

    Provided so a report can put the forecast next to what happened. The forecast
    itself never sees this: ``build_forecast`` is not even given the trip records
    beyond the recent window, and a test pins that its output is unchanged when
    future trips are altered.
    """
    return demand_in_window(records, window)


@dataclass(frozen=True)
class NodeForecast:
    """One node's forecast, with both components kept visible so it is explainable."""

    node_id: str
    recent_trips: int
    recent_rate_per_min: float
    profile_share: float
    recent_component: float
    profile_component: float
    forecast_trips: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "recent_trips": self.recent_trips,
            "recent_rate_per_min": self.recent_rate_per_min,
            "profile_share": self.profile_share,
            "recent_component": self.recent_component,
            "profile_component": self.profile_component,
            "forecast_trips": self.forecast_trips,
        }


@dataclass(frozen=True)
class DemandForecast:
    """A deterministic forecast of trips per node over the near-future window."""

    now_min: float
    horizon_min: float
    upcoming_bucket: str
    recent_window: DemandWindow
    nodes: tuple[NodeForecast, ...]
    recent_weight: float
    profile_weight: float
    #: False while there is too little history to forecast from (see module docstring).
    has_sufficient_history: bool = True

    def __post_init__(self) -> None:
        seen = set()
        for node in self.nodes:
            if node.node_id in seen:
                raise ForecastError(f"duplicate node in forecast: {node.node_id!r}")
            seen.add(node.node_id)
        object.__setattr__(self, "nodes", tuple(sorted(self.nodes, key=lambda n: n.node_id)))

    def forecast_for(self, node_id: str) -> float:
        for node in self.nodes:
            if node.node_id == node_id:
                return node.forecast_trips
        raise ForecastError(f"node {node_id!r} is not in this forecast")

    def node(self, node_id: str) -> NodeForecast:
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        raise ForecastError(f"node {node_id!r} is not in this forecast")

    @property
    def total_forecast_trips(self) -> float:
        return sum(node.forecast_trips for node in self.nodes)

    def top_nodes(self, limit: int = 5) -> tuple[NodeForecast, ...]:
        """Highest forecast first; ties break on node id so the order is stable."""
        ranked = sorted(self.nodes, key=lambda n: (-n.forecast_trips, n.node_id))
        return tuple(ranked[:max(0, limit)])

    def to_dict(self) -> dict[str, Any]:
        return {
            "now_min": self.now_min,
            "horizon_min": self.horizon_min,
            "upcoming_bucket": self.upcoming_bucket,
            "recent_window": self.recent_window.to_dict(),
            "recent_weight": self.recent_weight,
            "profile_weight": self.profile_weight,
            "has_sufficient_history": self.has_sufficient_history,
            "total_forecast_trips": round(self.total_forecast_trips, FORECAST_DECIMALS),
            "nodes": [node.to_dict() for node in self.nodes],
        }


def profile_shares(graph: NetworkGraph, profile: DemandProfile,
                   bucket_name: str) -> dict[str, float]:
    """Each node's share of the profile's production weight in one time bucket.

    This is the "known scenario profile" half of the forecast: a declared
    assumption about where trips start during that part of the day, not an
    observation. Shares sum to 1 (or are all zero if the bucket produces nothing).
    """
    bucket = profile.bucket(bucket_name)
    weights: dict[str, float] = {}
    for node in sorted(graph.nodes(), key=lambda n: n.node_id):
        role = profile.role_for(node.node_id, node.node_type)
        weights[node.node_id] = bucket.production.get(role, 0.0)
    total = sum(weights.values())
    if total <= 0:
        return {node_id: 0.0 for node_id in weights}
    return {node_id: weight / total for node_id, weight in weights.items()}


def build_forecast(graph: NetworkGraph, records: Sequence[TripRecord], now_min: float,
                   config: RebalancingConfig = DEFAULT_REBALANCING_CONFIG,
                   profile: DemandProfile = BASELINE_DEMAND_PROFILE) -> DemandForecast:
    """Forecast trips per node over ``[now, now + horizon)``.

    Deterministic and explainable: two weighted components, both printed on the
    result. ``records`` is filtered to the recent window immediately, so trips that
    have not been requested yet cannot influence the answer even accidentally.
    """
    windows = demand_windows(now_min, config)
    recent = windows["recent"]
    observed = demand_in_window(records, recent)

    if float(now_min) < config.min_history_min:
        # Too little history to estimate a rate from. Report zeros and say so rather
        # than extrapolating a couple of minutes into an hourly figure.
        return DemandForecast(
            now_min=float(now_min), horizon_min=config.forecast_horizon_min,
            upcoming_bucket="insufficient_history", recent_window=recent,
            nodes=tuple(NodeForecast(node_id=node.node_id, recent_trips=0,
                                     recent_rate_per_min=0.0, profile_share=0.0,
                                     recent_component=0.0, profile_component=0.0,
                                     forecast_trips=0.0)
                        for node in sorted(graph.nodes(), key=lambda n: n.node_id)),
            recent_weight=config.forecast_recent_weight,
            profile_weight=config.forecast_profile_weight,
            has_sufficient_history=False)

    # The bucket covering the END of the horizon, so pods move *before* a peak.
    lookahead_min = float(now_min) + config.forecast_horizon_min
    try:
        bucket = profile.bucket_for_time(lookahead_min % 1440.0)
    except Exception as exc:                     # a profile that covers nothing
        raise ForecastError(f"no profile bucket covers minute {lookahead_min}: {exc}") from exc
    shares = profile_shares(graph, profile, bucket.name)

    window_len = max(recent.length_min, 1e-9)
    rates = {node.node_id: observed.get(node.node_id, 0) / window_len
             for node in graph.nodes()}
    total_rate = sum(rates.values())

    horizon = config.forecast_horizon_min
    nodes = []
    for node in sorted(graph.nodes(), key=lambda n: n.node_id):
        node_id = node.node_id
        recent_component = config.forecast_recent_weight * rates[node_id] * horizon
        profile_component = (config.forecast_profile_weight * total_rate
                             * shares.get(node_id, 0.0) * horizon)
        nodes.append(NodeForecast(
            node_id=node_id,
            recent_trips=observed.get(node_id, 0),
            recent_rate_per_min=round(rates[node_id], FORECAST_DECIMALS),
            profile_share=round(shares.get(node_id, 0.0), FORECAST_DECIMALS),
            recent_component=round(recent_component, FORECAST_DECIMALS),
            profile_component=round(profile_component, FORECAST_DECIMALS),
            forecast_trips=round(recent_component + profile_component, FORECAST_DECIMALS),
        ))

    logger.debug("forecast at %.1f min over %s: %.2f trips total", now_min, bucket.name,
                 sum(n.forecast_trips for n in nodes))
    return DemandForecast(now_min=float(now_min), horizon_min=horizon,
                          upcoming_bucket=bucket.name, recent_window=recent,
                          nodes=tuple(nodes),
                          recent_weight=config.forecast_recent_weight,
                          profile_weight=config.forecast_profile_weight)
