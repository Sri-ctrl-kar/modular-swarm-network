"""The deterministic validator. Nothing reaches the engine without passing it.

*** The AI proposes. This decides. The engine executes. ***

The validator re-derives every fact it needs from the live engine — it never takes
the AI's word for anything, not even for what the AI claims to have seen. It is
deterministic (no clock, no randomness, no network) and read-only: validating a
proposal a thousand times changes nothing.

Its verdict is one of two words, ``APPROVED`` or ``REJECTED``, plus a
machine-readable reason code and the ordered list of checks that ran. The check
list is what makes a rejection auditable: a reader can see which gate closed and
which gates never got the chance.

STALE OBSERVATION PROTECTION
----------------------------
Every action carries the fingerprint of the observation it was reasoned from. Before
approving, the validator builds a **fresh** observation of the simulation as it is
now and compares fingerprints. They differ the instant anything material changes, so
a decision made about one city state can never be applied to another:

    REJECT_STALE_OBSERVATION

``NO_ACTION`` is exempt, and only ``NO_ACTION``: doing nothing is safe in every
state, so a stale "do nothing" is still correct.

WHAT THE CHECKS PROTECT
-----------------------
For ``REQUEST_REBALANCING`` the validator re-checks, from the engine:

* the named nodes exist in *this* graph (an invented node is rejected, not created);
* the pod count is a whole number inside its configured bound;
* rebalancing is enabled on this simulation at all;
* enough pods are genuinely eligible — idle, not charging, not in an active swarm,
  which is M5's rule applied unchanged, so a passenger's pod can never be taken;
* at least one eligible pod clears the battery reserve, so an approval cannot
  strand a pod (the *per-move* battery rule needs a route and stays M5's, applied
  when the deterministic planner builds the move);
* the fleet is not already at its concurrent-repositioning limit.

An approved ``REQUEST_REBALANCING`` therefore authorises exactly one thing: that the
deterministic M5 planner may run a cycle. **Which** pods move, and whether any move
at all is feasible, remains the engine's decision, never the AI's.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Sequence

from app.demand.config import DEMAND_PROFILES
from app.orchestration.actions import AIAction, AIActionType
from app.orchestration.config import DEFAULT_ORCHESTRATION_CONFIG, OrchestrationConfig
from app.orchestration.observation import OrchestrationObservation, build_observation
from app.rebalancing.eligibility import eligible_pods


class Verdict(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


# --- machine-readable rejection reasons -------------------------------------
REJECT_UNKNOWN_ACTION = "REJECT_UNKNOWN_ACTION"
REJECT_STALE_OBSERVATION = "REJECT_STALE_OBSERVATION"
REJECT_UNEXPECTED_PARAMETER = "REJECT_UNEXPECTED_PARAMETER"
REJECT_MISSING_PARAMETER = "REJECT_MISSING_PARAMETER"
REJECT_PARAMETER_TYPE = "REJECT_PARAMETER_TYPE"
REJECT_PARAMETER_OUT_OF_BOUNDS = "REJECT_PARAMETER_OUT_OF_BOUNDS"
REJECT_UNKNOWN_NODE = "REJECT_UNKNOWN_NODE"
REJECT_DUPLICATE_NODE = "REJECT_DUPLICATE_NODE"
REJECT_REBALANCING_DISABLED = "REJECT_REBALANCING_DISABLED"
REJECT_INSUFFICIENT_ELIGIBLE_PODS = "REJECT_INSUFFICIENT_ELIGIBLE_PODS"
REJECT_INSUFFICIENT_BATTERY = "REJECT_INSUFFICIENT_BATTERY"
REJECT_CONCURRENCY_LIMIT = "REJECT_CONCURRENCY_LIMIT"
REJECT_UNKNOWN_SCENARIO = "REJECT_UNKNOWN_SCENARIO"
REJECT_DUPLICATE_SCENARIO = "REJECT_DUPLICATE_SCENARIO"
REJECT_COMPARISON_TOO_LARGE = "REJECT_COMPARISON_TOO_LARGE"
REJECT_NO_SIMULATION = "REJECT_NO_SIMULATION"

#: Parameters each action type may carry. Anything else is rejected outright.
ALLOWED_PARAMETERS: dict[AIActionType, tuple[str, ...]] = {
    AIActionType.REQUEST_REBALANCING: ("target_nodes", "pod_count"),
    AIActionType.RUN_SIMULATION: ("scenario", "horizon_min", "pods", "passengers"),
    AIActionType.COMPARE_SCENARIOS: ("scenarios", "horizon_min"),
    AIActionType.NO_ACTION: (),
}


@dataclass(frozen=True)
class ValidationVerdict:
    """One decision, with the audit trail that produced it."""

    action: AIAction
    verdict: Verdict
    reason_code: str | None = None
    detail: str | None = None
    checks: tuple[tuple[str, bool], ...] = ()
    observed_fingerprint: str | None = None

    def __bool__(self) -> bool:
        return self.verdict is Verdict.APPROVED

    @property
    def approved(self) -> bool:
        return self.verdict is Verdict.APPROVED

    @property
    def is_stale(self) -> bool:
        return self.reason_code == REJECT_STALE_OBSERVATION

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "checks": [{"check": name, "passed": passed} for name, passed in self.checks],
            "observed_fingerprint": self.observed_fingerprint,
        }


class _Checks:
    """Accumulates named checks in the order they ran, so a rejection explains itself."""

    def __init__(self) -> None:
        self._rows: list[tuple[str, bool]] = []

    def record(self, name: str, passed: bool) -> bool:
        self._rows.append((name, passed))
        return passed

    def rows(self) -> tuple[tuple[str, bool], ...]:
        return tuple(self._rows)


class OrchestrationValidator:
    """Checks a proposal against the live engine and explicit bounds.

    Deterministic and side-effect free. It reads the simulation to re-derive facts;
    it never writes to it, and it never hands any part of it back to a caller.
    """

    def __init__(self, config: OrchestrationConfig = DEFAULT_ORCHESTRATION_CONFIG,
                 allowed_scenarios: Sequence[str] | None = None) -> None:
        self._config = config
        self._scenarios = tuple(sorted(allowed_scenarios or DEMAND_PROFILES))

    @property
    def config(self) -> OrchestrationConfig:
        return self._config

    @property
    def allowed_scenarios(self) -> tuple[str, ...]:
        return self._scenarios

    def validate(self, action: AIAction, *, simulation=None,
                 observation: OrchestrationObservation | None = None) -> ValidationVerdict:
        """Decide whether ``action`` may proceed against ``simulation`` as it is now.

        ``observation`` is optional and only ever used to *avoid rebuilding* one that
        the caller already holds; it is never trusted in place of the engine, and the
        staleness check always compares against a fingerprint derived from the engine.
        """
        if not isinstance(action, AIAction):
            raise TypeError(f"expected an AIAction, got {type(action).__name__}")
        checks = _Checks()

        # 1. The whitelist. Unreachable for a constructed AIAction — the enum already
        #    closed that door — and asserted here so the gate is visible in the audit.
        if not checks.record("action_type_whitelisted",
                             action.action_type in ALLOWED_PARAMETERS):
            return self._reject(action, checks, REJECT_UNKNOWN_ACTION,
                                f"{action.action_type!r} is not a permitted action")

        # 2. Parameter keys.
        allowed = ALLOWED_PARAMETERS[action.action_type]
        unexpected = sorted(set(action.parameters) - set(allowed))
        if action.action_type is not AIActionType.NO_ACTION:
            if not checks.record("parameter_keys_allowed", not unexpected):
                return self._reject(action, checks, REJECT_UNEXPECTED_PARAMETER,
                                    f"{unexpected} not permitted for "
                                    f"{action.action_type.value}; allowed: {list(allowed)}")
        else:
            # NO_ACTION is always valid. Stray parameters are recorded and ignored
            # rather than used, because refusing to act is never the unsafe answer.
            checks.record("parameters_ignored_for_no_action", True)

        # 3. Staleness. NO_ACTION is exempt; everything else is tied to its state.
        observed_fingerprint = None
        if action.action_type is not AIActionType.NO_ACTION and \
                self._config.require_fresh_observation:
            if simulation is None and observation is None:
                return self._reject(action, checks, REJECT_NO_SIMULATION,
                                    "no simulation to validate against")
            current = (build_observation(simulation, self._config) if simulation is not None
                       else observation)
            observed_fingerprint = current.fingerprint()
            if not checks.record("observation_is_current",
                                 observed_fingerprint == action.observation_fingerprint):
                return self._reject(
                    action, checks, REJECT_STALE_OBSERVATION,
                    f"action was reasoned from {action.observation_fingerprint[:12]}..., "
                    f"the simulation is now {observed_fingerprint[:12]}...",
                    observed_fingerprint)

        handler = {
            AIActionType.REQUEST_REBALANCING: self._validate_request_rebalancing,
            AIActionType.RUN_SIMULATION: self._validate_run_simulation,
            AIActionType.COMPARE_SCENARIOS: self._validate_compare_scenarios,
            AIActionType.NO_ACTION: self._validate_no_action,
        }[action.action_type]
        return handler(action, checks, simulation, observed_fingerprint)

    # --- per-action checks -------------------------------------------------------
    def _validate_no_action(self, action, checks, simulation, fingerprint) -> ValidationVerdict:
        """Always valid. Doing nothing cannot break a deterministic engine."""
        checks.record("no_action_is_always_permitted", True)
        return ValidationVerdict(action, Verdict.APPROVED, checks=checks.rows(),
                                 observed_fingerprint=fingerprint)

    def _validate_request_rebalancing(self, action, checks, simulation,
                                      fingerprint) -> ValidationVerdict:
        config = self._config
        params = action.parameters

        if simulation is None:
            return self._reject(action, checks, REJECT_NO_SIMULATION,
                                "REQUEST_REBALANCING needs a live simulation to act on",
                                fingerprint)

        # --- target nodes ---------------------------------------------------------
        nodes = params.get("target_nodes")
        if not checks.record("target_nodes_present", nodes is not None):
            return self._reject(action, checks, REJECT_MISSING_PARAMETER, "target_nodes",
                                fingerprint)
        if not checks.record("target_nodes_is_a_list_of_strings",
                             isinstance(nodes, list) and bool(nodes)
                             and all(isinstance(n, str) for n in nodes)):
            return self._reject(action, checks, REJECT_PARAMETER_TYPE,
                                f"target_nodes must be a non-empty list of node ids, got "
                                f"{nodes!r}", fingerprint)
        if not checks.record("target_node_count_within_bound",
                             len(nodes) <= config.max_target_nodes):
            return self._reject(action, checks, REJECT_PARAMETER_OUT_OF_BOUNDS,
                                f"target_nodes has {len(nodes)} entries, the limit is "
                                f"{config.max_target_nodes}", fingerprint)
        if not checks.record("target_nodes_are_distinct", len(set(nodes)) == len(nodes)):
            return self._reject(action, checks, REJECT_DUPLICATE_NODE,
                                f"target_nodes repeats a node: {nodes}", fingerprint)

        known = set(simulation.graph.node_ids()) if hasattr(simulation.graph, "node_ids") \
            else {n.node_id for n in simulation.graph.nodes()}
        unknown = sorted(n for n in nodes if n not in known)
        if not checks.record("target_nodes_exist", not unknown):
            return self._reject(action, checks, REJECT_UNKNOWN_NODE,
                                f"no such node(s) in this network: {unknown}", fingerprint)

        # --- pod count ------------------------------------------------------------
        pod_count = params.get("pod_count")
        if not checks.record("pod_count_present", pod_count is not None):
            return self._reject(action, checks, REJECT_MISSING_PARAMETER, "pod_count",
                                fingerprint)
        if not checks.record("pod_count_is_a_whole_number",
                             isinstance(pod_count, int) and not isinstance(pod_count, bool)):
            return self._reject(action, checks, REJECT_PARAMETER_TYPE,
                                f"pod_count must be a whole number, got {pod_count!r}",
                                fingerprint)
        if not checks.record("pod_count_within_bounds",
                             config.min_pod_count <= pod_count <= config.max_pod_count):
            return self._reject(action, checks, REJECT_PARAMETER_OUT_OF_BOUNDS,
                                f"pod_count={pod_count} is outside "
                                f"[{config.min_pod_count}, {config.max_pod_count}]",
                                fingerprint)

        # --- the engine's own rules, re-derived from the engine --------------------
        if not checks.record("rebalancing_is_enabled", bool(simulation.rebalancing_enabled)):
            return self._reject(action, checks, REJECT_REBALANCING_DISABLED,
                                "this simulation was built with rebalancing disabled",
                                fingerprint)

        def in_active_swarm(pod_id: str) -> bool:
            return simulation.swarm_of_pod(pod_id) is not None

        available = eligible_pods(simulation.fleet, in_active_swarm)
        if not checks.record("enough_eligible_pods", len(available) >= pod_count):
            return self._reject(
                action, checks, REJECT_INSUFFICIENT_ELIGIBLE_PODS,
                f"pod_count={pod_count} but only {len(available)} pod(s) are eligible "
                f"(idle, not charging, not in an active swarm)", fingerprint)

        reserve = simulation.rebalancing_config.reposition_battery_reserve_percent
        with_charge = [pod for pod in available if pod.battery_percent >= reserve]
        if not checks.record("some_eligible_pod_clears_the_battery_reserve",
                             bool(with_charge)):
            return self._reject(
                action, checks, REJECT_INSUFFICIENT_BATTERY,
                f"no eligible pod holds the {reserve}% reserve a repositioning move "
                f"requires before its own energy is counted", fingerprint)

        limit = simulation.rebalancing_config.max_concurrent_repositions
        active = simulation.active_reposition_count()
        if not checks.record("below_concurrent_reposition_limit", active < limit):
            return self._reject(action, checks, REJECT_CONCURRENCY_LIMIT,
                                f"{active} repositioning move(s) are already under way, "
                                f"the limit is {limit}", fingerprint)

        return ValidationVerdict(action, Verdict.APPROVED, checks=checks.rows(),
                                 observed_fingerprint=fingerprint)

    def _validate_run_simulation(self, action, checks, simulation,
                                 fingerprint) -> ValidationVerdict:
        config = self._config
        params = action.parameters

        scenario = params.get("scenario")
        if not checks.record("scenario_present", scenario is not None):
            return self._reject(action, checks, REJECT_MISSING_PARAMETER, "scenario",
                                fingerprint)
        if not checks.record("scenario_exists",
                             isinstance(scenario, str) and scenario in self._scenarios):
            return self._reject(action, checks, REJECT_UNKNOWN_SCENARIO,
                                f"{scenario!r} is not one of {list(self._scenarios)}",
                                fingerprint)

        horizon = params.get("horizon_min")
        if horizon is not None:
            if not checks.record("horizon_is_a_number",
                                 isinstance(horizon, (int, float))
                                 and not isinstance(horizon, bool)):
                return self._reject(action, checks, REJECT_PARAMETER_TYPE,
                                    f"horizon_min must be a number, got {horizon!r}",
                                    fingerprint)
            if not checks.record("horizon_within_bounds",
                                 config.min_horizon_min <= horizon <= config.max_horizon_min):
                return self._reject(action, checks, REJECT_PARAMETER_OUT_OF_BOUNDS,
                                    f"horizon_min={horizon} is outside "
                                    f"[{config.min_horizon_min}, {config.max_horizon_min}]",
                                    fingerprint)

        for name, limit in (("pods", config.max_simulation_pods),
                            ("passengers", config.max_simulation_passengers)):
            value = params.get(name)
            if value is None:
                continue
            if not checks.record(f"{name}_is_a_whole_number",
                                 isinstance(value, int) and not isinstance(value, bool)):
                return self._reject(action, checks, REJECT_PARAMETER_TYPE,
                                    f"{name} must be a whole number, got {value!r}",
                                    fingerprint)
            if not checks.record(f"{name}_within_bounds", 1 <= value <= limit):
                return self._reject(action, checks, REJECT_PARAMETER_OUT_OF_BOUNDS,
                                    f"{name}={value} is outside [1, {limit}]", fingerprint)

        return ValidationVerdict(action, Verdict.APPROVED, checks=checks.rows(),
                                 observed_fingerprint=fingerprint)

    def _validate_compare_scenarios(self, action, checks, simulation,
                                    fingerprint) -> ValidationVerdict:
        config = self._config
        scenarios = action.parameters.get("scenarios")

        if not checks.record("scenarios_present", scenarios is not None):
            return self._reject(action, checks, REJECT_MISSING_PARAMETER, "scenarios",
                                fingerprint)
        if not checks.record("scenarios_is_a_list_of_strings",
                             isinstance(scenarios, list)
                             and all(isinstance(s, str) for s in scenarios)):
            return self._reject(action, checks, REJECT_PARAMETER_TYPE,
                                f"scenarios must be a list of names, got {scenarios!r}",
                                fingerprint)
        if not checks.record("comparison_size_within_limits",
                             config.min_scenarios_per_comparison <= len(scenarios)
                             <= config.max_scenarios_per_comparison):
            return self._reject(
                action, checks, REJECT_COMPARISON_TOO_LARGE,
                f"{len(scenarios)} scenario(s) requested; the comparison must name "
                f"between {config.min_scenarios_per_comparison} and "
                f"{config.max_scenarios_per_comparison}", fingerprint)
        if not checks.record("scenarios_are_distinct", len(set(scenarios)) == len(scenarios)):
            return self._reject(action, checks, REJECT_DUPLICATE_SCENARIO,
                                f"scenarios repeats an entry: {scenarios}", fingerprint)

        unknown = sorted(s for s in scenarios if s not in self._scenarios)
        if not checks.record("scenarios_exist", not unknown):
            return self._reject(action, checks, REJECT_UNKNOWN_SCENARIO,
                                f"unknown scenario(s) {unknown}; known: "
                                f"{list(self._scenarios)}", fingerprint)

        horizon = action.parameters.get("horizon_min")
        if horizon is not None:
            if not checks.record("horizon_is_a_number",
                                 isinstance(horizon, (int, float))
                                 and not isinstance(horizon, bool)):
                return self._reject(action, checks, REJECT_PARAMETER_TYPE,
                                    f"horizon_min must be a number, got {horizon!r}",
                                    fingerprint)
            if not checks.record("horizon_within_bounds",
                                 config.min_horizon_min <= horizon <= config.max_horizon_min):
                return self._reject(action, checks, REJECT_PARAMETER_OUT_OF_BOUNDS,
                                    f"horizon_min={horizon} is outside "
                                    f"[{config.min_horizon_min}, {config.max_horizon_min}]",
                                    fingerprint)

        return ValidationVerdict(action, Verdict.APPROVED, checks=checks.rows(),
                                 observed_fingerprint=fingerprint)

    # --- helper ------------------------------------------------------------------
    @staticmethod
    def _reject(action: AIAction, checks: _Checks, code: str, detail: str,
                fingerprint: str | None = None) -> ValidationVerdict:
        return ValidationVerdict(action, Verdict.REJECTED, code, detail, checks.rows(),
                                 fingerprint)
