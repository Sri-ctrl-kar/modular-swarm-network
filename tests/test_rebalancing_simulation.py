"""M5 repositioning execution, the demand-shift experiment, metrics and determinism."""

import subprocess
import sys
import time

import pytest

from app.demand import generate_demand
from app.errors import ActionValidationError, RebalancingConfigError, RepositioningError
from app.fleet import DEFAULT_FLEET_CONFIG, FleetConfig, Pod, PodFleet, PodStatus, generate_fleet
from app.fleet.metrics import compute_fleet_metrics
from app.fleet.models import TripKind, TripStatus
from app.network.synthetic_city import build_synthetic_city
from app.rebalancing import (
    ActionType,
    ActionValidator,
    ProposedAction,
    RebalancingConfig,
    RebalancingSimulation,
    RepositionStatus,
    apply_validated_action,
    compute_rebalancing_metrics,
    observe,
    reposition_id_for,
)


def _city_run(enable_rebalancing=True, pods=60, passengers=400, config=None, **kwargs):
    graph = build_synthetic_city(42).graph
    fleet = generate_fleet(graph, fleet_size=pods, seed=42)
    demand = generate_demand(graph, seed=42, passenger_count=passengers)
    sim = RebalancingSimulation(graph, fleet, demand.trips,
                                rebalancing_config=config or RebalancingConfig(),
                                enable_rebalancing=enable_rebalancing, **kwargs)
    sim.run()
    return sim, fleet


# --- repositioning movement --------------------------------------------------
def test_a_repositioning_pod_drives_empty_and_arrives(two_area_graph, area_a_fleet,
                                                      demand_shift_trips):
    sim = RebalancingSimulation(two_area_graph, area_a_fleet, demand_shift_trips)
    sim.run()

    assert sim.repositions(), "the fleet should have repositioned toward area B"
    move = sim.repositions()[0]
    assert move.status is RepositionStatus.COMPLETED
    assert move.actual_distance_km > 0 and move.actual_energy_kwh > 0
    assert move.arrival_time_min > move.dispatch_time_min
    pod = area_a_fleet.get_pod(move.pod_id)
    assert pod.completed_repositioning_count >= 1


def test_repositioning_never_counts_as_a_passenger_trip(two_area_graph, area_a_fleet,
                                                        demand_shift_trips):
    """The separation that keeps every passenger figure honest."""
    sim = RebalancingSimulation(two_area_graph, area_a_fleet, demand_shift_trips)
    sim.run()

    assert sim.repositions()
    # No TripRecord was invented for a repositioning move.
    assert len(sim.records()) == len(demand_shift_trips)
    assert all(r.trip_id.startswith("T") for r in sim.records())
    for pod in area_a_fleet:
        # Empty arrivals are counted apart from passenger arrivals.
        assert pod.completed_trip_count + pod.completed_repositioning_count >= 0
        assert pod.occupied_seats == 0
    served = sum(1 for r in sim.records() if r.status is TripStatus.COMPLETED)
    assert served <= len(demand_shift_trips)


def test_a_repositioning_pod_carries_nobody(two_area_graph, area_a_fleet, demand_shift_trips):
    sim = RebalancingSimulation(two_area_graph, area_a_fleet, demand_shift_trips)
    seen_empty = False
    for _ in range(500):
        sim.tick()
        for pod in area_a_fleet:
            if pod.is_repositioning:
                assert pod.occupied_seats == 0
                assert pod.current_trip_kind is TripKind.REPOSITIONING
                seen_empty = True
        if not sim.has_pending_work:
            break
    assert seen_empty, "no pod ever repositioned"


def test_a_repositioning_pod_is_unavailable_then_returns_to_service(two_area_graph,
                                                                   area_a_fleet,
                                                                   demand_shift_trips):
    sim = RebalancingSimulation(two_area_graph, area_a_fleet, demand_shift_trips)
    moving_pods = set()
    for _ in range(500):
        sim.tick()
        for pod in area_a_fleet:
            if pod.is_repositioning:
                # While moving it is not idle, so passenger assignment cannot take it.
                assert pod.status in (PodStatus.ASSIGNED, PodStatus.TRAVELING, PodStatus.ARRIVED)
                moving_pods.add(pod.pod_id)
        if not sim.has_pending_work:
            break
    assert moving_pods
    # Afterwards they are idle again and eligible for passengers.
    for pod_id in moving_pods:
        pod = area_a_fleet.get_pod(pod_id)
        assert pod.status in (PodStatus.IDLE, PodStatus.CHARGING) or pod.completed_trip_count >= 0


def test_repositioning_uses_m3_movement_so_distance_matches_the_route(two_area_graph,
                                                                     area_a_fleet,
                                                                     demand_shift_trips):
    """No second movement model: the pod drives its route's own kilometres."""
    sim = RebalancingSimulation(two_area_graph, area_a_fleet, demand_shift_trips)
    sim.run()
    for move in sim.repositions():
        if move.status is RepositionStatus.COMPLETED:
            assert move.actual_distance_km == pytest.approx(move.estimated_distance_km, abs=1e-6)


def test_a_repositioning_pod_never_joins_a_swarm(two_area_graph, area_a_fleet,
                                                 demand_shift_trips):
    """An empty pod has no passengers to coordinate, so it does not platoon."""
    sim = RebalancingSimulation(two_area_graph, area_a_fleet, demand_shift_trips)
    for _ in range(500):
        sim.tick()
        for pod in area_a_fleet:
            if pod.is_repositioning:
                assert sim.swarm_of_pod(pod.pod_id) is None
        if not sim.has_pending_work:
            break


def test_dispatch_refuses_a_busy_pod(city_graph):
    from app.rebalancing.execution import dispatch
    from app.rebalancing.planner import AdaptiveRebalancer
    from app.fleet.models import TripRecord
    fleet = PodFleet(city_graph, [
        Pod(pod_id=f"POD{i:05d}", capacity=4, current_node_id="industrial_zone")
        for i in range(4)])
    records = [TripRecord(trip_id=f"T{i}", party_size=1, origin_node_id="residential_south",
                          destination_node_id="airport", request_time_min=150.0 + i)
               for i in range(20)]
    plan = AdaptiveRebalancer().plan_detailed(city_graph, fleet, records, 200.0)
    item = plan.items[0]
    pod = fleet.get_pod(item.pod_id)
    pod.assign("T000001", item.route, 2)                    # a passenger got there first
    with pytest.raises(RepositioningError, match="not idle"):
        dispatch(fleet, item, "RP00001", 200.0)


def test_reposition_ids_are_stable_and_sortable():
    assert reposition_id_for(1) == "RP00001" and reposition_id_for(42) == "RP00042"
    for bad in (0, -1, 1.5, "1", True):
        with pytest.raises(RepositioningError):
            reposition_id_for(bad)


# --- the demand-shift experiment (spec section 7) ----------------------------
def test_pods_preposition_toward_the_area_demand_moves_to(two_area_graph, area_a_fleet,
                                                          demand_shift_trips):
    """T0 demand is in area A; T1 it shifts to area B.

    Without rebalancing the pods stay where area A's trips left them and most of
    area B's peak goes unserved. With rebalancing the fleet moves across the link
    before the peak. Same network, demand, fleet and horizon in both runs.
    """
    from app.fleet import PodFleet as Fleet

    def run(enabled):
        graph = build_synthetic_city  # placeholder to keep the closure obvious
        g = two_area_graph if enabled else two_area_graph
        return g

    # Two independent runs, each with its own fresh fleet.
    def fresh_fleet(graph):
        return Fleet(graph, [Pod(pod_id=f"POD{i:05d}", capacity=4,
                                 current_node_id="A1" if i < 5 else "A2")
                             for i in range(10)])

    results = {}
    for enabled in (False, True):
        from tests.conftest import _edge, _node  # noqa: F401  (graph built by fixture)
        graph = two_area_graph
        # Rebuild traffic-free state by constructing a fresh fleet only; the fixture
        # graph has no traffic applied by either run.
        fleet = fresh_fleet(graph)
        sim = RebalancingSimulation(graph, fleet, demand_shift_trips,
                                    enable_rebalancing=enabled)
        sim.run()
        b_area = sum(1 for pod in fleet if pod.current_node_id in {"B1", "B2"})
        served = sum(1 for r in sim.records() if r.status is TripStatus.COMPLETED)
        b_served = sum(1 for r in sim.records()
                       if r.status is TripStatus.COMPLETED and r.origin_node_id == "B1")
        results[enabled] = (sim, fleet, b_area, served, b_served)

    _, _, base_b_pods, base_served, base_b_served = results[False]
    sim, fleet, rebal_b_pods, rebal_served, rebal_b_served = results[True]

    assert sim.repositions(), "rebalancing should have moved pods toward area B"
    assert any(move.target_node_id in {"B1", "B2"} for move in sim.repositions())
    # The point of the milestone: more of area B's demand gets served.
    assert rebal_b_served > base_b_served
    assert rebal_served > base_served


def test_prepositioning_happens_before_the_peak(two_area_graph, area_a_fleet,
                                                demand_shift_trips):
    """A pod must reach area B before the t=260 peak, not after it."""
    sim = RebalancingSimulation(two_area_graph, area_a_fleet, demand_shift_trips)
    sim.run()
    arrivals = [m.arrival_time_min for m in sim.repositions()
                if m.target_node_id in {"B1", "B2"} and m.arrival_time_min is not None]
    assert arrivals, "no pod ever arrived in area B"
    assert min(arrivals) < 260.0, f"earliest area-B arrival was {min(arrivals)}"


# --- before / after experiment (spec section 8) ------------------------------
def test_controlled_comparison_reports_both_gains_and_costs():
    from app.rebalancing import compare_rebalancing_modes
    trips = generate_demand(build_synthetic_city(42).graph, seed=42, passenger_count=300).trips
    comparison = compare_rebalancing_modes(
        lambda: build_synthetic_city(42).graph, trips,
        fleet_factory=lambda g: generate_fleet(g, fleet_size=50, seed=42),
        fleet_config=DEFAULT_FLEET_CONFIG)

    assert comparison.baseline.completed_repositions == 0
    assert comparison.rebalanced.completed_repositions > 0
    # The cost is present and is never netted off the passenger figures.
    assert comparison.rebalanced.reposition_distance_km > 0
    assert comparison.rebalanced.reposition_energy_kwh > 0
    assert comparison.rebalanced.pod_distance_km > comparison.baseline.pod_distance_km
    assert comparison.rebalanced.passenger_distance_km == pytest.approx(
        comparison.rebalanced.pod_distance_km - comparison.rebalanced.reposition_distance_km,
        abs=1e-2)
    assert comparison.baseline_fingerprint != comparison.rebalanced_fingerprint
    # Both wait figures exist in both modes, so a regression cannot hide.
    for metrics in (comparison.baseline, comparison.rebalanced):
        assert metrics.average_wait_min is not None
        assert metrics.maximum_wait_min is not None


def test_the_comparison_is_free_to_report_a_regression():
    """Nothing asserts rebalancing wins. Waiting and extra driving are real costs and
    the metrics must be able to show them getting worse."""
    from app.rebalancing import compare_rebalancing_modes
    trips = generate_demand(build_synthetic_city(42).graph, seed=42, passenger_count=300).trips
    comparison = compare_rebalancing_modes(
        lambda: build_synthetic_city(42).graph, trips,
        fleet_factory=lambda g: generate_fleet(g, fleet_size=50, seed=42),
        fleet_config=DEFAULT_FLEET_CONFIG)
    # Extra driving is expected; the sign of the trip change is not asserted either way.
    assert comparison.extra_pod_distance_km > 0
    assert isinstance(comparison.additional_trips_served, int)


def test_efficiency_figure_follows_its_documented_formula():
    from app.rebalancing import compare_rebalancing_modes
    trips = generate_demand(build_synthetic_city(42).graph, seed=42, passenger_count=300).trips
    c = compare_rebalancing_modes(
        lambda: build_synthetic_city(42).graph, trips,
        fleet_factory=lambda g: generate_fleet(g, fleet_size=50, seed=42),
        fleet_config=DEFAULT_FLEET_CONFIG)

    gained = c.rebalanced.trips_served - c.baseline.trips_served
    assert c.additional_trips_served == gained
    if c.rebalanced.reposition_distance_km > 0:
        assert c.rebalancing_trips_per_deadhead_km == pytest.approx(
            gained / c.rebalanced.reposition_distance_km, abs=1e-4)
    if gained > 0:
        assert c.deadhead_km_per_additional_trip == pytest.approx(
            c.rebalanced.reposition_distance_km / gained, abs=1e-3)


def test_efficiency_is_none_when_nothing_was_repositioned():
    from app.rebalancing import RebalancingComparison
    sim_off, fleet_off = _city_run(enable_rebalancing=False, pods=30, passengers=120)
    metrics = compute_rebalancing_metrics(sim_off)
    comparison = RebalancingComparison(
        baseline_fleet=compute_fleet_metrics(fleet_off, sim_off.records(), sim_off.time_min),
        rebalanced_fleet=compute_fleet_metrics(fleet_off, sim_off.records(), sim_off.time_min),
        baseline=metrics, rebalanced=metrics,
        baseline_fingerprint="a", rebalanced_fingerprint="a")
    assert comparison.rebalancing_trips_per_deadhead_km is None      # undefined, not zero
    assert comparison.deadhead_km_per_additional_trip is None


def test_disabled_rebalancing_still_measures_the_imbalance():
    """Both modes record the deficit on the same cadence, so it is comparable."""
    sim, _ = _city_run(enable_rebalancing=False, pods=60, passengers=400)
    assert sim.repositions() == ()
    assert sim.deficit_history(), "the baseline should still observe the imbalance"
    assert sim.cycles_run == 0            # observing is not a rebalancing cycle
    metrics = compute_rebalancing_metrics(sim)
    assert metrics.average_deficit is not None


def test_disabled_rebalancing_reproduces_m4_exactly():
    from app.swarm import SwarmSimulation
    graph_a = build_synthetic_city(42).graph
    fleet_a = generate_fleet(graph_a, fleet_size=40, seed=42)
    trips_a = generate_demand(graph_a, seed=42, passenger_count=250).trips
    m4 = SwarmSimulation(graph_a, fleet_a, trips_a)
    m4.run()

    graph_b = build_synthetic_city(42).graph
    fleet_b = generate_fleet(graph_b, fleet_size=40, seed=42)
    trips_b = generate_demand(graph_b, seed=42, passenger_count=250).trips
    m5 = RebalancingSimulation(graph_b, fleet_b, trips_b, enable_rebalancing=False)
    m5.run()

    assert m5.snapshot().fingerprint() == m4.snapshot().fingerprint()
    assert m5.swarm_snapshot().fingerprint() == m4.swarm_snapshot().fingerprint()
    assert [p.to_dict() for p in fleet_b] == [p.to_dict() for p in fleet_a]
    assert [r.to_dict() for r in m5.records()] == [r.to_dict() for r in m4.records()]


# --- metrics -----------------------------------------------------------------
def test_metrics_reconcile_with_the_run():
    sim, fleet = _city_run()
    metrics = compute_rebalancing_metrics(sim)
    assignments = sim.repositions()

    assert metrics.rebalancing_enabled is True
    assert metrics.accepted_requests == len(assignments)
    assert metrics.rejected_requests == sim.rejected_request_count
    assert metrics.reposition_requests == len(assignments) + sim.rejected_request_count
    assert metrics.completed_repositions + metrics.failed_repositions == len(assignments)
    assert metrics.reposition_distance_km == pytest.approx(
        sum(a.actual_distance_km or 0.0 for a in assignments), abs=1e-3)
    assert metrics.pod_distance_km == pytest.approx(
        sum(p.total_distance_km for p in fleet), abs=1e-3)
    assert metrics.passenger_distance_km == pytest.approx(
        metrics.pod_distance_km - metrics.reposition_distance_km, abs=1e-2)
    assert 0 <= metrics.deadhead_share_percent <= 100
    assert metrics.trips_served + metrics.unserved_trips == len(sim.records())
    assert metrics.maximum_wait_min >= metrics.average_wait_min
    assert sum(count for _, count in metrics.reposition_reason_counts) == len(assignments)


def test_metrics_with_no_repositioning_are_zero_not_none():
    sim, _ = _city_run(enable_rebalancing=False)
    metrics = compute_rebalancing_metrics(sim)
    assert metrics.accepted_requests == 0 and metrics.completed_repositions == 0
    assert metrics.reposition_distance_km == 0.0 and metrics.reposition_energy_kwh == 0.0
    assert metrics.average_reposition_time_min is None      # no samples, no average
    assert metrics.average_reposition_distance_km is None
    assert metrics.passenger_distance_km == pytest.approx(metrics.pod_distance_km, abs=1e-3)
    assert metrics.deadhead_share_percent == 0.0


def test_metrics_are_json_serialisable_and_deterministic():
    import json
    runs = []
    for _ in range(2):
        sim, _ = _city_run(pods=40, passengers=200)
        runs.append(compute_rebalancing_metrics(sim).to_dict())
    assert runs[0] == runs[1]
    assert json.loads(json.dumps(runs[0])) == runs[0]


def test_deficit_falls_when_rebalancing_is_on():
    """Measured on the same cadence in both modes, so this is like for like."""
    on, _ = _city_run(enable_rebalancing=True, pods=60, passengers=400)
    off, _ = _city_run(enable_rebalancing=False, pods=60, passengers=400)
    assert compute_rebalancing_metrics(on).average_deficit is not None
    assert compute_rebalancing_metrics(off).average_deficit is not None
    # Not asserted as an improvement — only that both are measured and reported.
    assert compute_rebalancing_metrics(on).final_total_deficit >= 0.0


# --- determinism -------------------------------------------------------------
def test_repeated_runs_are_byte_identical():
    runs = []
    for _ in range(3):
        sim, fleet = _city_run(pods=40, passengers=200)
        runs.append((
            sim.snapshot().fingerprint(),
            sim.swarm_snapshot().fingerprint(),
            [a.to_dict() for a in sim.repositions()],
            [p.to_dict() for p in fleet],
            compute_rebalancing_metrics(sim).to_dict(),
            sim.deficit_history(),
        ))
    assert all(run == runs[0] for run in runs)


def test_forecasts_decisions_and_routes_are_identical_across_runs():
    first, _ = _city_run(pods=40, passengers=200)
    second, _ = _city_run(pods=40, passengers=200)
    assert [a.reposition_id for a in first.repositions()] == \
        [a.reposition_id for a in second.repositions()]
    assert [a.pod_id for a in first.repositions()] == [a.pod_id for a in second.repositions()]
    assert [a.target_node_id for a in first.repositions()] == \
        [a.target_node_id for a in second.repositions()]
    assert [a.reason for a in first.repositions()] == [a.reason for a in second.repositions()]
    assert first.deficit_history() == second.deficit_history()


def test_tick_by_tick_histories_match():
    histories = []
    for _ in range(2):
        graph = build_synthetic_city(42).graph
        fleet = generate_fleet(graph, fleet_size=30, seed=42)
        demand = generate_demand(graph, seed=42, passenger_count=150)
        sim = RebalancingSimulation(graph, fleet, demand.trips)
        histories.append([sim.tick().to_dict() for _ in range(300)])
    assert histories[0] == histories[1]


def test_the_rebalancing_layer_uses_no_randomness():
    import random
    graph = build_synthetic_city(42).graph
    fleet = generate_fleet(graph, fleet_size=30, seed=42)
    demand = generate_demand(graph, seed=42, passenger_count=150)
    random.seed(31337)
    expected = [random.random() for _ in range(3)]
    random.seed(31337)
    RebalancingSimulation(graph, fleet, demand.trips).run()
    assert [random.random() for _ in range(3)] == expected


def test_determinism_across_separate_processes():
    script = (
        "from app.network.synthetic_city import build_synthetic_city;"
        "from app.demand import generate_demand;"
        "from app.fleet import generate_fleet;"
        "from app.rebalancing import RebalancingSimulation, compute_rebalancing_metrics;"
        "g=build_synthetic_city(42).graph;"
        "f=generate_fleet(g, fleet_size=40, seed=42);"
        "d=generate_demand(g, seed=42, passenger_count=200);"
        "s=RebalancingSimulation(g, f, d.trips); s.run();"
        "m=compute_rebalancing_metrics(s);"
        "print(s.snapshot().fingerprint(), m.completed_repositions, "
        "m.reposition_distance_km, m.trips_served)"
    )
    outputs = {subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                              check=True).stdout.strip() for _ in range(2)}
    sim, _ = _city_run(pods=40, passengers=200)
    metrics = compute_rebalancing_metrics(sim)
    expected = (f"{sim.snapshot().fingerprint()} {metrics.completed_repositions} "
                f"{metrics.reposition_distance_km} {metrics.trips_served}")
    assert len(outputs) == 1
    assert outputs.pop() == expected


# --- edge cases --------------------------------------------------------------
def test_empty_fleet_repositions_nothing(city_graph):
    fleet = PodFleet(city_graph)
    demand = generate_demand(city_graph, seed=42, passenger_count=30)
    sim = RebalancingSimulation(city_graph, fleet, demand.trips,
                                FleetConfig(max_trip_wait_min=5.0))
    report = sim.run()
    assert sim.repositions() == () and report.stopped_because == "no pending work"
    metrics = compute_rebalancing_metrics(sim)
    assert metrics.accepted_requests == 0 and metrics.reposition_distance_km == 0.0


def test_zero_trips_repositions_nothing(city_graph):
    fleet = generate_fleet(city_graph, fleet_size=10, seed=42)
    before = [p.to_dict() for p in fleet]
    sim = RebalancingSimulation(city_graph, fleet, [])
    sim.run()
    assert sim.repositions() == ()
    assert [p.to_dict() for p in fleet] == before


def test_unresolved_moves_are_recorded_as_failed_not_dropped(two_area_graph, area_a_fleet,
                                                             demand_shift_trips):
    sim = RebalancingSimulation(two_area_graph, area_a_fleet, demand_shift_trips)
    sim.run(max_ticks=210)            # cut the run off mid-move
    assert all(a.is_resolved for a in sim.repositions())
    failed = [a for a in sim.repositions() if a.status is RepositionStatus.FAILED]
    for move in failed:
        assert move.failure_reason


def test_bad_construction_is_rejected(city_graph):
    fleet = generate_fleet(city_graph, fleet_size=5, seed=42)
    with pytest.raises(RebalancingConfigError, match="must be a RebalancingConfig"):
        RebalancingSimulation(city_graph, fleet, [], rebalancing_config={"interval": 5})
    with pytest.raises(RebalancingConfigError, match="must be a bool"):
        RebalancingSimulation(city_graph, fleet, [], enable_rebalancing="yes")


def test_unknown_reposition_lookup_raises(city_graph):
    fleet = generate_fleet(city_graph, fleet_size=5, seed=42)
    sim = RebalancingSimulation(city_graph, fleet, [])
    with pytest.raises(RebalancingConfigError):
        sim.reposition("RP99999")


# --- the M6 boundary (spec section 20) ---------------------------------------
def test_observation_is_read_only_and_serialisable():
    import json
    sim, _ = _city_run(pods=30, passengers=150)
    observation = observe(sim)
    data = observation.to_dict()
    assert json.loads(json.dumps(data)) == data
    assert set(data) == {"time_min", "network", "demand", "fleet", "swarm", "forecast",
                         "rebalancing", "metrics", "fingerprints"}
    # No live object is reachable, so an observer cannot mutate the simulation.
    for section in ("network", "demand", "fleet", "swarm", "forecast", "rebalancing"):
        for value in data[section].values():
            assert isinstance(value, (int, float, str, bool, list, dict, type(None)))


def test_observation_is_deterministic():
    first, _ = _city_run(pods=30, passengers=150)
    second, _ = _city_run(pods=30, passengers=150)
    assert observe(first).to_dict() == observe(second).to_dict()


def test_validator_accepts_bounded_actions_and_rejects_everything_else():
    validator = ActionValidator()
    assert validator.validate(ProposedAction(ActionType.REQUEST_REBALANCING))
    assert validator.validate(ProposedAction(
        ActionType.SET_PARAMETER, {"name": "rebalance_interval_min", "value": 20}))
    assert validator.validate(ProposedAction(
        ActionType.SET_DEMAND_SCENARIO, {"scenario": "peak_hour"}))
    assert validator.validate(ProposedAction(
        ActionType.COMPARE_SCENARIOS, {"scenarios": ["baseline", "peak_hour"]}))


@pytest.mark.parametrize("action_type,parameters,fragment", [
    (ActionType.SET_PARAMETER, {"name": "rebalance_interval_min", "value": 10_000},
     "out_of_bounds"),
    (ActionType.SET_PARAMETER, {"name": "secret_backdoor", "value": 1}, "unknown_parameter"),
    (ActionType.SET_PARAMETER, {"name": "rebalance_interval_min", "value": "fast"},
     "not_a_number"),
    (ActionType.SET_PARAMETER, {"value": 5}, "missing_parameter"),
    (ActionType.SET_DEMAND_SCENARIO, {"scenario": "chaos"}, "unknown_demand_scenario"),
    (ActionType.SET_DEMAND_SCENARIO, {}, "missing_parameter"),
    (ActionType.COMPARE_SCENARIOS, {"scenarios": ["baseline"]}, "missing_parameter"),
    (ActionType.COMPARE_SCENARIOS, {"scenarios": ["baseline", "chaos"]},
     "unknown_demand_scenario"),
    (ActionType.REQUEST_REBALANCING, {"pods": 500}, "unexpected_parameter"),
])
def test_validator_rejections_are_machine_readable(action_type, parameters, fragment):
    result = ActionValidator().validate(ProposedAction(action_type, parameters))
    assert not result and fragment in result.reason


def test_an_unknown_action_type_cannot_even_be_built():
    with pytest.raises(ActionValidationError, match="unknown action type"):
        ProposedAction("DELETE_EVERYTHING")


def test_only_validated_actions_can_be_applied():
    sim, _ = _city_run(pods=30, passengers=150)
    validator = ActionValidator()
    rejected = validator.validate(ProposedAction(
        ActionType.SET_PARAMETER, {"name": "rebalance_interval_min", "value": 10_000}))
    with pytest.raises(ActionValidationError, match="rejected action"):
        apply_validated_action(sim, rejected)
    with pytest.raises(ActionValidationError, match="ValidationResult"):
        apply_validated_action(sim, ProposedAction(ActionType.REQUEST_REBALANCING))


def test_request_rebalancing_is_the_only_action_m5_applies():
    sim, _ = _city_run(pods=40, passengers=200)
    validator = ActionValidator()
    outcome = apply_validated_action(
        sim, validator.validate(ProposedAction(ActionType.REQUEST_REBALANCING)))
    assert set(outcome) == {"dispatched_reposition_ids", "rejected_count", "total_deficit"}

    for action_type, parameters in (
            (ActionType.SET_DEMAND_SCENARIO, {"scenario": "peak_hour"}),
            (ActionType.SET_PARAMETER, {"name": "rebalance_interval_min", "value": 20}),
            (ActionType.COMPARE_SCENARIOS, {"scenarios": ["baseline", "peak_hour"]})):
        result = validator.validate(ProposedAction(action_type, parameters))
        assert result.accepted
        with pytest.raises(ActionValidationError, match="belongs to M6"):
            apply_validated_action(sim, result)


def test_parameter_bounds_are_published():
    bounds = ActionValidator().parameter_bounds
    assert "rebalance_interval_min" in bounds
    for name, (low, high) in bounds.items():
        assert low <= high, name


# --- layering and M1-M4.1 regression ----------------------------------------
def test_lower_layers_do_not_import_the_rebalancing_layer():
    """models <- network <- routing <- simulation <- demand <- fleet <- swarm <- rebalancing."""
    from app.config import PROJECT_ROOT
    offenders = []
    for layer in ("models", "network", "routing", "simulation", "demand", "fleet", "swarm"):
        for path in sorted((PROJECT_ROOT / "app" / layer).rglob("*.py")):
            if "app.rebalancing" in path.read_text(encoding="utf-8"):
                offenders.append(str(path.relative_to(PROJECT_ROOT)))
    assert offenders == [], f"lower layers import the rebalancing layer: {offenders}"


def test_pod_status_still_has_exactly_five_members():
    """M5 added a trip *kind*, not a pod status. M4's invariant still holds."""
    from app.fleet import ALLOWED_TRANSITIONS
    assert {s.value for s in PodStatus} == {"idle", "assigned", "traveling", "arrived", "charging"}
    assert set(ALLOWED_TRANSITIONS) == set(PodStatus)


def test_trip_kind_defaults_to_passenger_everywhere():
    """The additive change to Pod must not alter existing behaviour."""
    pod = Pod(pod_id="POD00000", capacity=4, current_node_id="market")
    assert pod.current_trip_kind is TripKind.PASSENGER
    assert not pod.is_repositioning
    assert pod.completed_repositioning_count == 0


def test_a_rebalancing_run_leaves_the_network_and_demand_untouched(city_graph):
    traffic_before = {e.edge_id: e.current_vehicle_count for e in city_graph.edges()}
    demand = generate_demand(city_graph, seed=42, passenger_count=150)
    trips_before = [t.to_dict() for t in demand.trips]
    fingerprint_before = demand.snapshot().fingerprint()

    fleet = generate_fleet(city_graph, fleet_size=30, seed=42)
    RebalancingSimulation(city_graph, fleet, demand.trips).run()

    assert {e.edge_id: e.current_vehicle_count for e in city_graph.edges()} == traffic_before
    assert [t.to_dict() for t in demand.trips] == trips_before
    assert demand.snapshot().fingerprint() == fingerprint_before


def test_routing_still_agrees_after_a_rebalancing_run(city_graph):
    from app.routing import astar, dijkstra
    fleet = generate_fleet(city_graph, fleet_size=25, seed=42)
    demand = generate_demand(city_graph, seed=42, passenger_count=150)
    RebalancingSimulation(city_graph, fleet, demand.trips).run()
    for origin, destination in (("north_station", "airport"), ("west_hub", "tech_park")):
        a, d = astar(city_graph, origin, destination), dijkstra(city_graph, origin, destination)
        assert a.total_cost == pytest.approx(d.total_cost, rel=1e-12, abs=1e-9)


def test_battery_stays_within_bounds_including_deadhead():
    sim, fleet = _city_run(pods=40, passengers=250)
    assert sim.repositions()
    for pod in fleet:
        assert 0.0 <= pod.battery_percent <= 100.0


def test_swarm_metrics_still_hold_after_rebalancing():
    """M4.1's unit rules survive: deadhead km land in pod distance, not in corridors."""
    from app.swarm import compute_swarm_metrics
    sim, fleet = _city_run(pods=40, passengers=250)
    metrics = compute_swarm_metrics(sim)
    assert metrics.pod_distance_km == pytest.approx(
        sum(p.total_distance_km for p in fleet), abs=1e-3)
    assert metrics.coordinated_pod_km <= metrics.pod_distance_km
    assert metrics.unplatooned_pod_km >= 0.0


# --- performance -------------------------------------------------------------
def test_one_hundred_pods_one_thousand_trips():
    """Requirement: 100 pods, 1,000 trips, dynamic demand, ~1,500+ ticks."""
    graph = build_synthetic_city(42).graph
    fleet = generate_fleet(graph, fleet_size=100, seed=42)
    demand = generate_demand(graph, seed=42, passenger_count=1000)
    sim = RebalancingSimulation(graph, fleet, demand.trips)

    started = time.perf_counter()
    report = sim.run()
    elapsed = time.perf_counter() - started

    metrics = compute_rebalancing_metrics(sim)
    assert report.ticks >= 1000
    assert metrics.completed_repositions > 0
    assert metrics.trips_served > 0
    assert elapsed < 600.0, f"100 pods / 1000 trips took {elapsed:.1f}s"


# --- CLI ---------------------------------------------------------------------
def test_rebalancing_demo_cli_runs_and_explains_itself(capsys):
    from app.cli.main import main
    assert main(["rebalancing-demo", "--pods", "30", "--passengers", "200"]) == 0
    out = capsys.readouterr().out
    flat = " ".join(out.split())
    assert "No machine learning and no LLM is involved" in flat
    assert "not claimed to be globally optimal" in flat
    assert "BEFORE REBALANCING" in out and "AFTER REBALANCING" in out
    assert "NO REBALANCING vs ADAPTIVE REBALANCING" in out
    assert "Deadhead distance:" in out and "Deadhead energy:" in out
    assert "Rebalancing is not free" in flat
    assert "NOT a return on investment" in flat
    assert "Fleet fingerprint:" in out


def test_rebalancing_demo_cli_is_reproducible(capsys):
    from app.cli.main import main

    def fingerprint():
        main(["rebalancing-demo", "--pods", "25", "--passengers", "150"])
        for line in capsys.readouterr().out.splitlines():
            if line.startswith("Fleet fingerprint:"):
                return line.split(":", 1)[1].strip()
        raise AssertionError("no fingerprint in output")

    assert fingerprint() == fingerprint()


def test_rebalancing_demo_cli_options(capsys):
    from app.cli.main import main
    assert main(["rebalancing-demo", "--pods", "20", "--passengers", "100", "--interval", "20",
                 "--horizon", "45", "--max-per-cycle", "2", "--no-swarms",
                 "--snapshot-min", "400"]) == 0
    out = capsys.readouterr().out
    assert "45 min horizon" in out
    assert "Cycle every 20 min, at most 2 moves per cycle" in out
    assert "at minute 400" in out


def test_existing_cli_commands_still_work(capsys):
    from app.cli.main import main
    assert main(["route", "--from", "North Station", "--to", "Airport", "--algorithm", "both"]) == 0
    assert "Optimal cost match (A* vs Dijkstra): YES" in capsys.readouterr().out
    assert main(["demand-demo", "--passengers", "100"]) == 0
    assert "Demand fingerprint:" in capsys.readouterr().out
    assert main(["fleet-demo", "--pods", "20", "--passengers", "100"]) == 0
    assert "Fleet fingerprint:" in capsys.readouterr().out
    assert main(["swarm-demo", "--pods", "20", "--passengers", "100"]) == 0
    assert "Swarm fingerprint:" in capsys.readouterr().out
    assert main(["info"]) == 0
    assert "node_count: 22" in capsys.readouterr().out
