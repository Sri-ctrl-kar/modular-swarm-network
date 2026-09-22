"""The M6 boundary: read-only observation, bounded proposals, deterministic validation.

*** M5 does NOT implement M6. There is no Gemini, no LLM and no agent here. ***

This module exists so that a future orchestrator can be bolted on **above** the
deterministic engine without ever being able to reach into it:

    DATA -> DETERMINISTIC SIMULATION -> METRICS -> (M6 ORCHESTRATOR)
         -> STRUCTURED PROPOSAL -> VALIDATOR -> SIMULATION

Three properties make that boundary real rather than decorative:

1. **Observation is read-only.** ``observe()`` returns a frozen, JSON-serialisable
   snapshot of plain values. It hands out no graph, no fleet, no pod and no
   simulation, so a caller holding an observation *cannot* mutate anything through
   it even by accident.
2. **Proposals are data, not calls.** A ``ProposedAction`` is an inert record: an
   action type from a closed enum plus parameters. Nothing executes when one is
   built.
3. **The validator is deterministic and bounded.** Every action type has an
   explicit parameter whitelist with numeric limits. An unknown action, an unknown
   parameter or an out-of-range value is rejected with a machine-readable reason.
   There is no "trusted" path that skips it.

The AI must never directly mutate simulation state, so the only applier here,
``apply_validated_action``, accepts nothing but an already-validated action and
handles exactly one: ``REQUEST_REBALANCING``, which asks the engine to run a cycle
it was going to be able to run anyway. The other action types validate but do not
apply, because choosing when to switch scenarios or compare runs is orchestration —
M6's job, deliberately absent here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from app.errors import ActionValidationError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Observation — read-only, serialisable, no live objects
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SimulationObservation:
    """A frozen snapshot of everything an orchestrator is allowed to see.

    Every field is a plain value, a tuple or a dict of plain values. No graph,
    fleet, pod, swarm or simulation object is reachable from here, which is what
    makes the boundary read-only by construction rather than by convention.
    """

    time_min: float
    network: Mapping[str, Any]
    demand: Mapping[str, Any]
    fleet: Mapping[str, Any]
    swarm: Mapping[str, Any]
    forecast: Mapping[str, Any]
    rebalancing: Mapping[str, Any]
    metrics: Mapping[str, Any]
    fingerprints: Mapping[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "time_min": self.time_min,
            "network": dict(self.network),
            "demand": dict(self.demand),
            "fleet": dict(self.fleet),
            "swarm": dict(self.swarm),
            "forecast": dict(self.forecast),
            "rebalancing": dict(self.rebalancing),
            "metrics": dict(self.metrics),
            "fingerprints": dict(self.fingerprints),
        }


def observe(simulation, *, top_nodes: int = 5) -> SimulationObservation:
    """Build a read-only observation of a ``RebalancingSimulation``."""
    from app.fleet.metrics import compute_fleet_metrics
    from app.rebalancing.metrics import compute_rebalancing_metrics
    from app.swarm.metrics import compute_swarm_metrics

    graph = simulation.graph
    fleet = simulation.fleet
    demand_map = simulation.demand_map()
    fleet_metrics = compute_fleet_metrics(fleet, simulation.records(), simulation.time_min)
    swarm_metrics = compute_swarm_metrics(simulation)
    rebalancing_metrics = compute_rebalancing_metrics(simulation)

    return SimulationObservation(
        time_min=simulation.time_min,
        network={
            "node_count": graph.node_count(),
            "edge_count": graph.edge_count(),
            "overloaded_edges": sum(1 for e in graph.edges() if e.is_overloaded),
            "total_vehicle_count": sum(e.current_vehicle_count for e in graph.edges()),
        },
        demand={
            "trip_count": len(simulation.records()),
            "trip_status_counts": simulation.trip_status_counts(),
            "total_deficit": demand_map.total_deficit,
            "total_surplus": demand_map.total_surplus,
            "top_deficit_nodes": [row.to_dict() for row in demand_map.deficit_rows()[:top_nodes]],
        },
        fleet={
            "pod_count": len(fleet),
            "count_by_status": fleet.count_by_status(),
            "total_capacity": fleet.total_capacity(),
        },
        swarm={
            "swarms_enabled": simulation.swarms_enabled,
            "formation_count": simulation.formation_count,
            "split_count": simulation.split_count,
            "status_counts": simulation.swarm_status_counts(),
        },
        forecast={
            "horizon_min": demand_map.forecast.horizon_min,
            "upcoming_bucket": demand_map.forecast.upcoming_bucket,
            "has_sufficient_history": demand_map.forecast.has_sufficient_history,
            "total_forecast_trips": round(demand_map.forecast.total_forecast_trips, 4),
            "top_nodes": [node.to_dict() for node in demand_map.forecast.top_nodes(top_nodes)],
        },
        rebalancing={
            "enabled": simulation.rebalancing_enabled,
            "cycles_run": simulation.cycles_run,
            "active_repositions": simulation.active_reposition_count(),
            "status_counts": simulation.reposition_status_counts(),
        },
        metrics={
            "fleet": fleet_metrics.to_dict(),
            "swarm": swarm_metrics.to_dict(),
            "rebalancing": rebalancing_metrics.to_dict(),
        },
        fingerprints={
            "fleet": simulation.snapshot().fingerprint(),
            "swarm": simulation.swarm_snapshot().fingerprint(),
        },
    )


# ---------------------------------------------------------------------------
# 2. Proposals — inert data
# ---------------------------------------------------------------------------
class ActionType(str, Enum):
    """The closed set of things an orchestrator may ever propose."""

    REQUEST_REBALANCING = "REQUEST_REBALANCING"
    SET_DEMAND_SCENARIO = "SET_DEMAND_SCENARIO"
    SET_PARAMETER = "SET_PARAMETER"
    COMPARE_SCENARIOS = "COMPARE_SCENARIOS"


@dataclass(frozen=True)
class ProposedAction:
    """An inert proposal. Building one executes nothing."""

    action_type: ActionType
    parameters: Mapping[str, Any] = field(default_factory=dict)
    rationale: str = ""

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "action_type", ActionType(self.action_type))
        except ValueError as exc:
            allowed = ", ".join(a.value for a in ActionType)
            raise ActionValidationError(
                f"unknown action type {self.action_type!r} (allowed: {allowed})") from exc
        if not isinstance(self.parameters, Mapping):
            raise ActionValidationError(
                f"parameters must be a mapping, got {type(self.parameters).__name__}")
        object.__setattr__(self, "parameters", dict(self.parameters))

    def to_dict(self) -> dict[str, Any]:
        return {"action_type": self.action_type.value, "parameters": dict(self.parameters),
                "rationale": self.rationale}


@dataclass(frozen=True)
class ValidationResult:
    """Whether a proposal may proceed, and precisely why not when it may not."""

    action: ProposedAction
    accepted: bool
    reason: str | None = None

    def __bool__(self) -> bool:
        return self.accepted

    def to_dict(self) -> dict[str, Any]:
        return {"action": self.action.to_dict(), "accepted": self.accepted, "reason": self.reason}


# ---------------------------------------------------------------------------
# 3. The validator — deterministic, bounded, no trusted bypass
# ---------------------------------------------------------------------------
#: Parameters an orchestrator may set, with hard numeric bounds. Anything not in
#: this table is rejected, so a proposal can never reach a field nobody vetted.
PARAMETER_BOUNDS: dict[str, tuple[float, float]] = {
    "rebalance_interval_min": (1.0, 240.0),
    "forecast_horizon_min": (1.0, 240.0),
    "forecast_recent_window_min": (1.0, 480.0),
    "min_deficit_to_act": (0.0, 100.0),
    "max_repositions_per_cycle": (0, 100),
    "max_concurrent_repositions": (0, 500),
    "max_reposition_distance_km": (0.1, 100.0),
    "reposition_battery_reserve_percent": (0.0, 90.0),
}

#: Demand scenarios an orchestrator may name.
ALLOWED_DEMAND_SCENARIOS = ("baseline", "peak_hour")

# Machine-readable rejection reasons.
UNKNOWN_PARAMETER = "unknown_parameter"
OUT_OF_BOUNDS = "parameter_out_of_bounds"
NOT_A_NUMBER = "parameter_not_a_number"
MISSING_PARAMETER = "missing_parameter"
UNKNOWN_SCENARIO = "unknown_demand_scenario"
UNEXPECTED_PARAMETER = "unexpected_parameter"


class ActionValidator:
    """Checks a proposal against explicit bounds. Deterministic; mutates nothing."""

    def __init__(self, parameter_bounds: Mapping[str, tuple[float, float]] | None = None,
                 allowed_scenarios: tuple[str, ...] = ALLOWED_DEMAND_SCENARIOS) -> None:
        self._bounds = dict(parameter_bounds or PARAMETER_BOUNDS)
        self._scenarios = tuple(allowed_scenarios)

    @property
    def parameter_bounds(self) -> dict[str, tuple[float, float]]:
        return dict(self._bounds)

    def validate(self, action: ProposedAction) -> ValidationResult:
        if not isinstance(action, ProposedAction):
            raise ActionValidationError(
                f"expected a ProposedAction, got {type(action).__name__}")
        handler = {
            ActionType.REQUEST_REBALANCING: self._validate_request_rebalancing,
            ActionType.SET_PARAMETER: self._validate_set_parameter,
            ActionType.SET_DEMAND_SCENARIO: self._validate_set_demand_scenario,
            ActionType.COMPARE_SCENARIOS: self._validate_compare_scenarios,
        }[action.action_type]
        return handler(action)

    def _validate_request_rebalancing(self, action: ProposedAction) -> ValidationResult:
        if action.parameters:
            return ValidationResult(action, False,
                                    f"{UNEXPECTED_PARAMETER}: {sorted(action.parameters)}")
        return ValidationResult(action, True)

    def _validate_set_parameter(self, action: ProposedAction) -> ValidationResult:
        name = action.parameters.get("name")
        if name is None or "value" not in action.parameters:
            return ValidationResult(action, False, f"{MISSING_PARAMETER}: name and value")
        if name not in self._bounds:
            return ValidationResult(action, False, f"{UNKNOWN_PARAMETER}: {name!r}")
        value = action.parameters["value"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return ValidationResult(action, False, f"{NOT_A_NUMBER}: {value!r}")
        low, high = self._bounds[name]
        if not low <= value <= high:
            return ValidationResult(action, False,
                                    f"{OUT_OF_BOUNDS}: {name}={value!r} not in [{low}, {high}]")
        return ValidationResult(action, True)

    def _validate_set_demand_scenario(self, action: ProposedAction) -> ValidationResult:
        scenario = action.parameters.get("scenario")
        if scenario is None:
            return ValidationResult(action, False, f"{MISSING_PARAMETER}: scenario")
        if scenario not in self._scenarios:
            return ValidationResult(action, False, f"{UNKNOWN_SCENARIO}: {scenario!r}")
        return ValidationResult(action, True)

    def _validate_compare_scenarios(self, action: ProposedAction) -> ValidationResult:
        scenarios = action.parameters.get("scenarios")
        if not isinstance(scenarios, (list, tuple)) or len(scenarios) < 2:
            return ValidationResult(action, False, f"{MISSING_PARAMETER}: two or more scenarios")
        unknown = [s for s in scenarios if s not in self._scenarios]
        if unknown:
            return ValidationResult(action, False, f"{UNKNOWN_SCENARIO}: {unknown}")
        return ValidationResult(action, True)


def apply_validated_action(simulation, result: ValidationResult) -> dict[str, Any]:
    """Apply an **already validated** action. The only way in.

    Refuses anything unvalidated or rejected, and handles only
    ``REQUEST_REBALANCING``. The rest validate here but are applied by M6, because
    deciding when to change scenario or run a comparison is orchestration, and M5
    deliberately does not orchestrate.
    """
    if not isinstance(result, ValidationResult):
        raise ActionValidationError(f"expected a ValidationResult, got {type(result).__name__}")
    if not result.accepted:
        raise ActionValidationError(
            f"refusing to apply a rejected action: {result.reason}")
    action = result.action
    if action.action_type is not ActionType.REQUEST_REBALANCING:
        raise ActionValidationError(
            f"{action.action_type.value} is validated but not applied inside M5 — "
            f"orchestrating it belongs to M6")
    dispatched, rejected, deficit = simulation._run_cycle()   # the engine's own cycle
    logger.info("applied REQUEST_REBALANCING: %d dispatched, %d rejected", len(dispatched), rejected)
    return {"dispatched_reposition_ids": list(dispatched), "rejected_count": rejected,
            "total_deficit": deficit}
