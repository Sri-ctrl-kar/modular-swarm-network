"""M6: the deterministic validator — every gate, and the stale-state guard."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.orchestration import (
    AIAction,
    OrchestrationConfig,
    OrchestrationValidator,
    Verdict,
    build_observation,
    no_action,
)
from app.orchestration.validator import (
    REJECT_CONCURRENCY_LIMIT,
    REJECT_COMPARISON_TOO_LARGE,
    REJECT_DUPLICATE_NODE,
    REJECT_DUPLICATE_SCENARIO,
    REJECT_INSUFFICIENT_BATTERY,
    REJECT_INSUFFICIENT_ELIGIBLE_PODS,
    REJECT_MISSING_PARAMETER,
    REJECT_PARAMETER_OUT_OF_BOUNDS,
    REJECT_PARAMETER_TYPE,
    REJECT_REBALANCING_DISABLED,
    REJECT_STALE_OBSERVATION,
    REJECT_UNEXPECTED_PARAMETER,
    REJECT_UNKNOWN_NODE,
    REJECT_UNKNOWN_SCENARIO,
)

OTHER_FINGERPRINT = "0" * 64


def _rebalancing(fingerprint, **params):
    parameters = {"target_nodes": ["market"], "pod_count": 2}
    parameters.update(params)
    return AIAction("REQUEST_REBALANCING", parameters, "a deficit at market",
                    "pods closer to demand", 0.8, fingerprint)


@pytest.fixture
def validator():
    return OrchestrationValidator()


@pytest.fixture
def fingerprint(ai_observation):
    return ai_observation.fingerprint()


# --- the happy path -----------------------------------------------------------
def test_a_sound_rebalancing_proposal_is_approved(validator, ai_simulation, fingerprint):
    verdict = validator.validate(_rebalancing(fingerprint), simulation=ai_simulation)
    assert verdict.verdict is Verdict.APPROVED
    assert verdict.approved and bool(verdict)
    assert verdict.reason_code is None
    assert all(passed for _, passed in verdict.checks)


def test_the_checks_are_recorded_in_order(validator, ai_simulation, fingerprint):
    verdict = validator.validate(_rebalancing(fingerprint), simulation=ai_simulation)
    names = [name for name, _ in verdict.checks]
    assert names[0] == "action_type_whitelisted"
    assert "observation_is_current" in names
    assert names.index("target_nodes_exist") < names.index("enough_eligible_pods")


def test_validating_changes_nothing(validator, ai_simulation, fingerprint):
    before = ai_simulation.snapshot().fingerprint()
    for _ in range(3):
        validator.validate(_rebalancing(fingerprint), simulation=ai_simulation)
    assert ai_simulation.snapshot().fingerprint() == before


def test_the_same_proposal_always_gets_the_same_verdict(validator, ai_simulation,
                                                        fingerprint):
    action = _rebalancing(fingerprint)
    verdicts = {validator.validate(action, simulation=ai_simulation).to_dict()["verdict"]
                for _ in range(5)}
    assert verdicts == {"APPROVED"}


# --- stale observation protection ---------------------------------------------
def test_a_stale_observation_is_rejected(validator, ai_simulation):
    verdict = validator.validate(_rebalancing(OTHER_FINGERPRINT), simulation=ai_simulation)
    assert verdict.reason_code == REJECT_STALE_OBSERVATION
    assert verdict.is_stale
    assert not verdict.approved


def test_a_proposal_goes_stale_the_moment_the_city_moves(validator, ai_simulation):
    action = _rebalancing(build_observation(ai_simulation).fingerprint())
    assert validator.validate(action, simulation=ai_simulation).approved

    ai_simulation.run(until_min=ai_simulation.time_min + 30.0)

    verdict = validator.validate(action, simulation=ai_simulation)
    assert verdict.reason_code == REJECT_STALE_OBSERVATION
    assert verdict.observed_fingerprint != action.observation_fingerprint


def test_the_rejection_names_both_fingerprints(validator, ai_simulation):
    verdict = validator.validate(_rebalancing(OTHER_FINGERPRINT), simulation=ai_simulation)
    assert OTHER_FINGERPRINT[:12] in verdict.detail
    assert verdict.observed_fingerprint[:12] in verdict.detail


def test_no_action_is_valid_even_when_stale(validator, ai_simulation):
    """Doing nothing is safe in any state, so it is the one exemption."""
    verdict = validator.validate(no_action(OTHER_FINGERPRINT, "nothing worth doing"),
                                 simulation=ai_simulation)
    assert verdict.approved


def test_staleness_can_be_switched_off_only_by_configuration(ai_simulation):
    relaxed = OrchestrationValidator(OrchestrationConfig(require_fresh_observation=False))
    assert relaxed.validate(_rebalancing(OTHER_FINGERPRINT),
                            simulation=ai_simulation).approved


# --- parameters ---------------------------------------------------------------
def test_an_unexpected_parameter_is_rejected(validator, ai_simulation, fingerprint):
    action = AIAction("REQUEST_REBALANCING",
                      {"target_nodes": ["market"], "pod_count": 2, "shell": "rm -rf /"},
                      "r", "e", 0.5, fingerprint)
    verdict = validator.validate(action, simulation=ai_simulation)
    assert verdict.reason_code == REJECT_UNEXPECTED_PARAMETER
    assert "shell" in verdict.detail


def test_a_missing_parameter_is_rejected(validator, ai_simulation, fingerprint):
    action = AIAction("REQUEST_REBALANCING", {"pod_count": 2}, "r", "e", 0.5, fingerprint)
    assert validator.validate(action, simulation=ai_simulation).reason_code == \
        REJECT_MISSING_PARAMETER


def test_an_invented_node_is_rejected(validator, ai_simulation, fingerprint):
    action = _rebalancing(fingerprint, target_nodes=["atlantis"])
    verdict = validator.validate(action, simulation=ai_simulation)
    assert verdict.reason_code == REJECT_UNKNOWN_NODE
    assert "atlantis" in verdict.detail


def test_a_repeated_node_is_rejected(validator, ai_simulation, fingerprint):
    action = _rebalancing(fingerprint, target_nodes=["market", "market"])
    assert validator.validate(action, simulation=ai_simulation).reason_code == \
        REJECT_DUPLICATE_NODE


def test_too_many_target_nodes_are_rejected(validator, ai_simulation, fingerprint):
    nodes = [n.node_id for n in ai_simulation.graph.nodes()][:11]
    action = _rebalancing(fingerprint, target_nodes=nodes)
    assert validator.validate(action, simulation=ai_simulation).reason_code == \
        REJECT_PARAMETER_OUT_OF_BOUNDS


@pytest.mark.parametrize("nodes", [[], "market", [1, 2], None])
def test_a_malformed_node_list_is_rejected(validator, ai_simulation, fingerprint, nodes):
    action = _rebalancing(fingerprint, target_nodes=nodes)
    verdict = validator.validate(action, simulation=ai_simulation)
    assert verdict.reason_code in {REJECT_PARAMETER_TYPE, REJECT_MISSING_PARAMETER}


@pytest.mark.parametrize("count", [0, -1, 26, 1000])
def test_an_out_of_range_pod_count_is_rejected(validator, ai_simulation, fingerprint, count):
    action = _rebalancing(fingerprint, pod_count=count)
    assert validator.validate(action, simulation=ai_simulation).reason_code == \
        REJECT_PARAMETER_OUT_OF_BOUNDS


@pytest.mark.parametrize("count", [2.5, "three", True])
def test_a_non_integer_pod_count_is_rejected(validator, ai_simulation, fingerprint, count):
    action = _rebalancing(fingerprint, pod_count=count)
    assert validator.validate(action, simulation=ai_simulation).reason_code == \
        REJECT_PARAMETER_TYPE


# --- the engine's own rules, re-derived ---------------------------------------
def test_more_pods_than_are_eligible_is_rejected(validator, city_graph):
    """Within the configured bound, but more pods than the fleet can actually spare."""
    from app.demand import generate_demand
    from app.fleet import generate_fleet
    from app.rebalancing import RebalancingSimulation
    from app.rebalancing.eligibility import eligible_pods

    fleet = generate_fleet(city_graph, fleet_size=4, seed=42)
    demand = generate_demand(city_graph, seed=42, passenger_count=20)
    simulation = RebalancingSimulation(city_graph, fleet, demand.trips)

    def in_swarm(pod_id):
        return simulation.swarm_of_pod(pod_id) is not None

    available = len(eligible_pods(simulation.fleet, in_swarm))
    assert available < validator.config.max_pod_count   # the bound is not what bites

    fingerprint = build_observation(simulation).fingerprint()
    verdict = validator.validate(_rebalancing(fingerprint, pod_count=available + 1),
                                 simulation=simulation)
    assert verdict.reason_code == REJECT_INSUFFICIENT_ELIGIBLE_PODS
    assert str(available) in verdict.detail


def test_a_passengers_pod_is_never_counted_as_available(validator, ai_simulation):
    """Eligibility is M5's rule unchanged: busy pods are not the AI's to move."""
    from app.fleet.models import PodStatus
    from app.rebalancing.eligibility import eligible_pods

    def in_swarm(pod_id):
        return ai_simulation.swarm_of_pod(pod_id) is not None

    eligible_ids = {pod.pod_id for pod in eligible_pods(ai_simulation.fleet, in_swarm)}
    busy = [p.pod_id for p in ai_simulation.fleet if p.status is not PodStatus.IDLE]
    assert eligible_ids.isdisjoint(busy)


def test_a_fleet_without_charge_is_rejected(validator, city_graph):
    from app.demand import generate_demand
    from app.fleet import generate_fleet
    from app.rebalancing import RebalancingSimulation

    fleet = generate_fleet(city_graph, fleet_size=10, seed=42)
    demand = generate_demand(city_graph, seed=42, passenger_count=30)
    simulation = RebalancingSimulation(city_graph, fleet, demand.trips)
    reserve = simulation.rebalancing_config.reposition_battery_reserve_percent
    for pod in fleet:
        pod.discharge(pod.battery_percent - (reserve - 1.0))

    fingerprint = build_observation(simulation).fingerprint()
    verdict = validator.validate(_rebalancing(fingerprint, pod_count=1),
                                 simulation=simulation)
    assert verdict.reason_code == REJECT_INSUFFICIENT_BATTERY
    assert str(reserve) in verdict.detail


def test_rebalancing_disabled_is_rejected(validator, city_graph):
    from app.demand import generate_demand
    from app.fleet import generate_fleet
    from app.rebalancing import RebalancingSimulation

    fleet = generate_fleet(city_graph, fleet_size=10, seed=42)
    demand = generate_demand(city_graph, seed=42, passenger_count=30)
    simulation = RebalancingSimulation(city_graph, fleet, demand.trips,
                                       enable_rebalancing=False)
    fingerprint = build_observation(simulation).fingerprint()
    assert validator.validate(_rebalancing(fingerprint, pod_count=1),
                              simulation=simulation).reason_code == \
        REJECT_REBALANCING_DISABLED


def test_the_concurrency_limit_is_respected(city_graph):
    from app.demand import generate_demand
    from app.fleet import generate_fleet
    from app.rebalancing import RebalancingSimulation

    fleet = generate_fleet(city_graph, fleet_size=12, seed=42)
    demand = generate_demand(city_graph, seed=42, passenger_count=60)
    config = replace(RebalancingSimulation(city_graph, fleet, demand.trips
                                           ).rebalancing_config,
                     max_concurrent_repositions=0)
    fleet = generate_fleet(city_graph, fleet_size=12, seed=42)
    simulation = RebalancingSimulation(city_graph, fleet, demand.trips,
                                       rebalancing_config=config)
    simulation.run(until_min=120.0)
    fingerprint = build_observation(simulation).fingerprint()
    verdict = OrchestrationValidator().validate(_rebalancing(fingerprint, pod_count=1),
                                                simulation=simulation)
    assert verdict.reason_code == REJECT_CONCURRENCY_LIMIT


def test_rebalancing_without_a_simulation_is_refused(validator, ai_observation):
    verdict = validator.validate(_rebalancing(ai_observation.fingerprint()),
                                 observation=ai_observation)
    assert not verdict.approved


# --- RUN_SIMULATION -----------------------------------------------------------
def _run_simulation(fingerprint, **params):
    return AIAction("RUN_SIMULATION", params, "compare demand", "see the outcome",
                    0.6, fingerprint)


def test_a_known_scenario_is_approved(validator, ai_simulation, fingerprint):
    action = _run_simulation(fingerprint, scenario="baseline", horizon_min=120.0)
    assert validator.validate(action, simulation=ai_simulation).approved


def test_an_invented_scenario_is_rejected(validator, ai_simulation, fingerprint):
    action = _run_simulation(fingerprint, scenario="utopia_hour")
    verdict = validator.validate(action, simulation=ai_simulation)
    assert verdict.reason_code == REJECT_UNKNOWN_SCENARIO


def test_a_missing_scenario_is_rejected(validator, ai_simulation, fingerprint):
    assert validator.validate(_run_simulation(fingerprint), simulation=ai_simulation
                              ).reason_code == REJECT_MISSING_PARAMETER


@pytest.mark.parametrize("horizon", [0.5, 0.0, -10.0, 10_000.0])
def test_an_out_of_range_horizon_is_rejected(validator, ai_simulation, fingerprint, horizon):
    action = _run_simulation(fingerprint, scenario="baseline", horizon_min=horizon)
    assert validator.validate(action, simulation=ai_simulation).reason_code == \
        REJECT_PARAMETER_OUT_OF_BOUNDS


@pytest.mark.parametrize("field,value", [("pods", 0), ("pods", 100_000),
                                         ("passengers", 0), ("passengers", 1_000_000)])
def test_out_of_range_run_sizes_are_rejected(validator, ai_simulation, fingerprint,
                                             field, value):
    action = _run_simulation(fingerprint, scenario="baseline", **{field: value})
    assert validator.validate(action, simulation=ai_simulation).reason_code == \
        REJECT_PARAMETER_OUT_OF_BOUNDS


# --- COMPARE_SCENARIOS --------------------------------------------------------
def _compare(fingerprint, scenarios, **params):
    return AIAction("COMPARE_SCENARIOS", {"scenarios": scenarios, **params},
                    "which demand hurts more", "a side-by-side table", 0.6, fingerprint)


def test_a_two_scenario_comparison_is_approved(validator, ai_simulation, fingerprint):
    action = _compare(fingerprint, ["baseline", "peak_hour"])
    assert validator.validate(action, simulation=ai_simulation).approved


def test_a_single_scenario_comparison_is_rejected(validator, ai_simulation, fingerprint):
    assert validator.validate(_compare(fingerprint, ["baseline"]), simulation=ai_simulation
                              ).reason_code == REJECT_COMPARISON_TOO_LARGE


def test_an_oversized_comparison_is_rejected(validator, ai_simulation, fingerprint):
    action = _compare(fingerprint, ["baseline", "peak_hour", "baseline", "peak_hour",
                                    "baseline"])
    assert validator.validate(action, simulation=ai_simulation).reason_code == \
        REJECT_COMPARISON_TOO_LARGE


def test_a_duplicated_scenario_is_rejected(validator, ai_simulation, fingerprint):
    assert validator.validate(_compare(fingerprint, ["baseline", "baseline"]),
                              simulation=ai_simulation).reason_code == \
        REJECT_DUPLICATE_SCENARIO


def test_an_invented_scenario_in_a_comparison_is_rejected(validator, ai_simulation,
                                                          fingerprint):
    assert validator.validate(_compare(fingerprint, ["baseline", "utopia"]),
                              simulation=ai_simulation).reason_code == \
        REJECT_UNKNOWN_SCENARIO


# --- interface ----------------------------------------------------------------
def test_the_validator_refuses_anything_that_is_not_an_action(validator, ai_simulation):
    with pytest.raises(TypeError):
        validator.validate({"action_type": "NO_ACTION"}, simulation=ai_simulation)


def test_the_verdict_serialises_to_plain_values(validator, ai_simulation, fingerprint):
    import json
    data = validator.validate(_rebalancing(fingerprint), simulation=ai_simulation).to_dict()
    assert json.loads(json.dumps(data)) == data
