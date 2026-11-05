"""Executing an approved action — by asking the existing engine, never by reimplementing it.

*** Only an APPROVED verdict reaches this module, and it re-checks that before doing
    anything. There is no second path in. ***

EVERY ACTION MAPS ONTO SOMETHING THAT ALREADY EXISTS
----------------------------------------------------
``REQUEST_REBALANCING``  -> M5's own boundary: a ``ProposedAction`` through M5's
                            ``ActionValidator`` and ``apply_validated_action``, which
                            runs one deterministic rebalancing cycle.
``RUN_SIMULATION``       -> ``app/orchestration/scenarios.run_scenario``, itself only a
                            composition of ``build_synthetic_city``, ``generate_fleet``,
                            ``generate_demand`` and ``RebalancingSimulation``.
``COMPARE_SCENARIOS``    -> the same runner, once per named scenario, tabulated.
``NO_ACTION``            -> nothing runs. Reported as ``SKIPPED``, not as a failure.

Note what ``REQUEST_REBALANCING`` does **not** do. The AI names target nodes and a pod
count; the deterministic planner then decides which pods actually move, using M5's
forecast, surplus rules, distance limit and battery reserve. The AI's numbers are
recorded as its *request*, and the result reports what the engine actually did
alongside them — including how many dispatched moves went to nodes the AI named.
That figure is often lower than the request, and it is reported as it falls. An AI
that asks for five pods has not moved five pods; it has asked a planner that may
move three, or none.

Nothing here executes AI text. No string from a provider is imported, evaluated,
used as a path, or passed to a shell.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from app.errors import ExecutionRefusedError, SwarmNetworkError
from app.orchestration.actions import AIAction, AIActionType
from app.orchestration.config import DEFAULT_ORCHESTRATION_CONFIG, OrchestrationConfig
from app.orchestration.scenarios import ScenarioSpec, run_scenario
from app.orchestration.validator import ValidationVerdict
from app.rebalancing.metrics import compute_rebalancing_metrics
from app.rebalancing.observation import (
    ActionType as EngineActionType,
    ActionValidator as EngineActionValidator,
    ProposedAction as EngineProposedAction,
    apply_validated_action,
)

logger = logging.getLogger(__name__)

#: The metrics compared before and after an action, so a delta is never a surprise.
TRACKED_METRICS = ("trips_served", "unserved_trips", "completed_repositions",
                   "reposition_distance_km", "reposition_energy_kwh", "pod_distance_km",
                   "final_total_deficit", "average_wait_min",
                   "average_completion_time_min")


class ExecutionStatus(str, Enum):
    EXECUTED = "EXECUTED"      # the engine ran and produced a result
    SKIPPED = "SKIPPED"        # NO_ACTION: correct, and nothing to run
    FAILED = "FAILED"          # the engine refused or raised; state is unchanged or
                               # partially advanced, and the record says which


@dataclass(frozen=True)
class ExecutionResult:
    """What the deterministic engine actually did. Never what the AI said it would."""

    action_type: AIActionType
    status: ExecutionStatus
    detail: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    fingerprint_before: str | None = None
    fingerprint_after: str | None = None
    metrics_before: Mapping[str, Any] = field(default_factory=dict)
    metrics_after: Mapping[str, Any] = field(default_factory=dict)
    error_type: str | None = None

    @property
    def executed(self) -> bool:
        return self.status is ExecutionStatus.EXECUTED

    def metrics_delta(self) -> dict[str, Any]:
        """After minus before, for the numbers that can be differenced.

        A metric that is ``None`` on either side has no delta — reported as ``None``
        rather than coerced to zero, because "no trips completed yet" is not "no
        change".
        """
        delta: dict[str, Any] = {}
        for name in TRACKED_METRICS:
            before, after = self.metrics_before.get(name), self.metrics_after.get(name)
            if isinstance(before, (int, float)) and isinstance(after, (int, float)) \
                    and not isinstance(before, bool) and not isinstance(after, bool):
                delta[name] = round(after - before, 4)
            else:
                delta[name] = None
        return delta

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type.value,
            "status": self.status.value,
            "detail": self.detail,
            "payload": dict(self.payload),
            "fingerprint_before": self.fingerprint_before,
            "fingerprint_after": self.fingerprint_after,
            "metrics_before": dict(self.metrics_before),
            "metrics_after": dict(self.metrics_after),
            "metrics_delta": self.metrics_delta(),
            "error_type": self.error_type,
        }


def tracked_metrics(simulation) -> dict[str, Any]:
    metrics = compute_rebalancing_metrics(simulation)
    return {name: getattr(metrics, name) for name in TRACKED_METRICS}


class ActionExecutor:
    """Turns an approved action into a call on the existing deterministic engine."""

    def __init__(self, config: OrchestrationConfig = DEFAULT_ORCHESTRATION_CONFIG) -> None:
        self._config = config

    def execute(self, verdict: ValidationVerdict, *, simulation=None) -> ExecutionResult:
        """Run an approved action. Refuses anything else, loudly.

        A rejected verdict reaching here is a programming error, not a runtime
        condition, so it raises rather than returning a tidy failure: there must be
        no code path in which an unapproved action is "executed" and merely reported
        as unsuccessful.
        """
        if not isinstance(verdict, ValidationVerdict):
            raise TypeError(f"expected a ValidationVerdict, got {type(verdict).__name__}")
        if not verdict.approved:
            raise ExecutionRefusedError(
                f"refusing to execute a {verdict.verdict.value} action "
                f"({verdict.reason_code}): {verdict.detail}")

        action = verdict.action
        handler = {
            AIActionType.NO_ACTION: self._execute_no_action,
            AIActionType.REQUEST_REBALANCING: self._execute_request_rebalancing,
            AIActionType.RUN_SIMULATION: self._execute_run_simulation,
            AIActionType.COMPARE_SCENARIOS: self._execute_compare_scenarios,
        }[action.action_type]
        try:
            return handler(action, simulation)
        except ExecutionRefusedError:
            raise
        except SwarmNetworkError as exc:
            # The engine refused. That is information, not a crash: record it and let
            # the caller carry on with a simulation that is still valid.
            logger.warning("execution of %s failed: %s", action.action_type.value, exc)
            return ExecutionResult(
                action_type=action.action_type, status=ExecutionStatus.FAILED,
                detail=str(exc), error_type=type(exc).__name__)

    # --- the four handlers -------------------------------------------------------
    @staticmethod
    def _execute_no_action(action: AIAction, simulation) -> ExecutionResult:
        return ExecutionResult(
            action_type=action.action_type, status=ExecutionStatus.SKIPPED,
            detail="NO_ACTION: nothing was run, and no deadhead kilometres were spent.")

    @staticmethod
    def _execute_request_rebalancing(action: AIAction, simulation) -> ExecutionResult:
        if simulation is None:
            raise ExecutionRefusedError(
                "REQUEST_REBALANCING needs a live simulation to act on")

        requested_nodes = tuple(action.parameters.get("target_nodes", ()))
        requested_pods = int(action.parameters.get("pod_count", 0))

        before = tracked_metrics(simulation)
        fingerprint_before = simulation.snapshot().fingerprint()
        deficit_before = simulation.demand_map().total_deficit
        known_repositions = {a.reposition_id for a in simulation.repositions()}

        # Straight through M5's own boundary — M6 never calls the engine's internals.
        engine_action = EngineProposedAction(
            action_type=EngineActionType.REQUEST_REBALANCING, parameters={},
            rationale=action.reason)
        engine_verdict = EngineActionValidator().validate(engine_action)
        outcome = apply_validated_action(simulation, engine_verdict)

        dispatched_ids = tuple(outcome["dispatched_reposition_ids"])
        dispatched = [a for a in simulation.repositions()
                      if a.reposition_id in set(dispatched_ids) - known_repositions]
        to_requested = sum(1 for a in dispatched if a.target_node_id in requested_nodes)
        after = tracked_metrics(simulation)

        detail = (f"The deterministic rebalancer ran one cycle: {len(dispatched)} move(s) "
                  f"dispatched, {outcome['rejected_count']} refused by the engine's own "
                  f"rules. The AI asked for {requested_pods} pod(s) toward "
                  f"{len(requested_nodes)} node(s); {to_requested} dispatched move(s) "
                  f"target a node it named. The planner, not the AI, chose every move.")

        return ExecutionResult(
            action_type=action.action_type, status=ExecutionStatus.EXECUTED, detail=detail,
            payload={
                "requested_target_nodes": list(requested_nodes),
                "requested_pod_count": requested_pods,
                "dispatched_reposition_ids": list(dispatched_ids),
                "dispatched_count": len(dispatched),
                "dispatched_to_requested_nodes": to_requested,
                "rejected_by_engine": outcome["rejected_count"],
                "total_deficit_before": deficit_before,
                "total_deficit_after": outcome["total_deficit"],
                "moves": [
                    {"reposition_id": a.reposition_id, "pod_id": a.pod_id,
                     "from_node_id": a.origin_node_id, "to_node_id": a.target_node_id,
                     "reason": a.reason.value,
                     "estimated_distance_km": a.estimated_distance_km}
                    for a in sorted(dispatched, key=lambda a: a.reposition_id)
                ],
                "note": "pod_count and target_nodes are the AI's request. The engine's "
                        "forecast, surplus rule, distance limit and battery reserve "
                        "decide what actually moves.",
            },
            fingerprint_before=fingerprint_before,
            fingerprint_after=simulation.snapshot().fingerprint(),
            metrics_before=before, metrics_after=after)

    def _execute_run_simulation(self, action: AIAction, simulation) -> ExecutionResult:
        params = action.parameters
        spec = ScenarioSpec(
            scenario_id=f"RUN_{params['scenario']}",
            profile_name=params["scenario"],
            pods=int(params.get("pods", ScenarioSpec.pods)),
            passengers=int(params.get("passengers", ScenarioSpec.passengers)),
            horizon_min=params.get("horizon_min"),
        )
        result = run_scenario(spec)
        return ExecutionResult(
            action_type=action.action_type, status=ExecutionStatus.EXECUTED,
            detail=(f"Ran scenario {spec.profile_name!r} deterministically: "
                    f"{result.trips_served} trip(s) served, {result.unserved_trips} "
                    f"unserved, {result.deadhead_distance_km} km driven empty."),
            payload={"result": result.to_dict()},
            fingerprint_after=result.fleet_fingerprint)

    def _execute_compare_scenarios(self, action: AIAction, simulation) -> ExecutionResult:
        params = action.parameters
        horizon = params.get("horizon_min")
        results = []
        for name in params["scenarios"]:
            spec = ScenarioSpec(scenario_id=f"CMP_{name}", profile_name=name,
                                horizon_min=horizon)
            results.append(run_scenario(spec))

        rows = [r.to_dict() for r in results]
        best = max(results, key=lambda r: (r.trips_served, r.spec.profile_name))
        return ExecutionResult(
            action_type=action.action_type, status=ExecutionStatus.EXECUTED,
            detail=(f"Compared {len(results)} scenario(s) under identical seeds, fleet "
                    f"size and passenger count. Most trips served: "
                    f"{best.spec.profile_name} ({best.trips_served}). The scenarios "
                    f"differ in demand, so this ranks outcomes, not policies."),
            payload={"scenarios": rows,
                     "most_trips_served": best.spec.profile_name,
                     "note": "Each row is one deterministic run. Raw totals across "
                             "different demand profiles are not like-for-like; they are "
                             "reported side by side, not differenced."})
