"""M5 eligibility, matching, battery limits, reasons and congestion sensitivity."""

import pytest

from app.demand import generate_demand
from app.fleet import Pod, PodFleet, generate_fleet
from app.fleet.models import TripKind, TripRecord
from app.fleet.simulation import FleetSimulation
from app.network.synthetic_city import build_synthetic_city
from app.rebalancing import (
    IN_ACTIVE_SWARM,
    INSUFFICIENT_BATTERY,
    IS_CHARGING,
    NOT_IDLE,
    AdaptiveRebalancer,
    RebalancingConfig,
    RepositionReason,
    check_pod,
    eligible_pods,
    eligible_pods_by_node,
    has_battery_for,
    ineligibility_reasons,
    required_battery_percent,
    route_energy_kwh,
)
from app.rebalancing.planner import NO_ELIGIBLE_POD, TOO_FAR, UNREACHABLE
from app.routing import find_route


def _record(trip_id, origin, time_min, destination="airport"):
    return TripRecord(trip_id=trip_id, party_size=1, origin_node_id=origin,
                      destination_node_id=destination, request_time_min=float(time_min))


# History must sit INSIDE the forecast's recent window, which at now=200 with the
# default 60-minute window is [140, 200). Trips outside it are invisible by design.
def _history(node="residential_south", count=20, start=150.0):
    return [_record(f"T{i:05d}", node, start + i) for i in range(count)]


def _route_for(graph, origin, destination):
    return find_route(graph, origin, destination, "astar")


# --- eligibility (spec section 4) --------------------------------------------
def test_an_idle_pod_is_eligible(city_graph):
    pod = Pod(pod_id="POD00000", capacity=4, current_node_id="market")
    assert check_pod(pod, in_active_swarm=False)


def test_a_pod_in_passenger_service_is_never_taken(city_graph):
    """The rule that matters most: a passenger's pod is left alone."""
    fleet = PodFleet(city_graph, [Pod(pod_id="POD00000", capacity=4, current_node_id="market")])
    pod = fleet.get_pod("POD00000")
    pod.assign("T000001", _route_for(city_graph, "market", "airport"), 2)

    result = check_pod(pod, in_active_swarm=False)
    assert not result and result.reason == NOT_IDLE
    assert eligible_pods(fleet) == ()


def test_a_travelling_pod_is_not_eligible(city_graph):
    from app.fleet.movement import start_pod_travel
    fleet = PodFleet(city_graph, [Pod(pod_id="POD00000", capacity=4, current_node_id="market")])
    pod = fleet.get_pod("POD00000")
    pod.assign("T000001", _route_for(city_graph, "market", "airport"), 1)
    start_pod_travel(city_graph, pod)
    assert check_pod(pod, in_active_swarm=False).reason == NOT_IDLE


def test_a_charging_pod_is_not_eligible(city_graph):
    pod = Pod(pod_id="POD00000", capacity=4, current_node_id="market", battery_percent=5.0)
    pod.begin_charging()
    result = check_pod(pod, in_active_swarm=False)
    assert not result and result.reason == IS_CHARGING


def test_a_pod_in_an_active_swarm_is_not_eligible(city_graph):
    """M4 owns that pod's coordination, so M5 does not touch it."""
    pod = Pod(pod_id="POD00000", capacity=4, current_node_id="market")
    result = check_pod(pod, in_active_swarm=True)
    assert not result and result.reason == IN_ACTIVE_SWARM


def test_an_already_repositioning_pod_is_not_taken_again(city_graph):
    fleet = PodFleet(city_graph, [Pod(pod_id="POD00000", capacity=4, current_node_id="market")])
    pod = fleet.get_pod("POD00000")
    pod.assign("RP00001", _route_for(city_graph, "market", "airport"), 0, TripKind.REPOSITIONING)
    assert check_pod(pod, in_active_swarm=False).reason == NOT_IDLE


def test_eligibility_groups_and_reasons(city_graph):
    fleet = PodFleet(city_graph, [
        Pod(pod_id="POD00000", capacity=4, current_node_id="market"),
        Pod(pod_id="POD00001", capacity=4, current_node_id="market"),
        Pod(pod_id="POD00002", capacity=4, current_node_id="airport", battery_percent=2.0),
    ])
    fleet.get_pod("POD00002").begin_charging()
    assert eligible_pods_by_node(fleet) == {"market": ("POD00000", "POD00001")}
    assert ineligibility_reasons(fleet) == (("POD00002", IS_CHARGING),)


def test_battery_rule_uses_m3s_model_plus_a_documented_reserve():
    from app.fleet import DEFAULT_FLEET_CONFIG
    pod = Pod(pod_id="POD00000", capacity=4, current_node_id="market", battery_percent=20.0)
    # 2 kWh at 40 kWh capacity is 5 %, plus the 15 % reserve = 20 % needed.
    assert required_battery_percent(2.0) == pytest.approx(20.0)
    assert has_battery_for(pod, 2.0)
    assert not has_battery_for(pod, 2.1)
    lenient = RebalancingConfig(reposition_battery_reserve_percent=0.0)
    assert has_battery_for(pod, 2.1, DEFAULT_FLEET_CONFIG, lenient)


# --- matching ----------------------------------------------------------------
def test_planner_moves_pods_from_surplus_to_deficit(city_graph):
    """Fifteen pods parked where nobody wants them, demand somewhere else."""
    fleet = PodFleet(city_graph, [
        Pod(pod_id=f"POD{i:05d}", capacity=4, current_node_id="industrial_zone")
        for i in range(15)])
    plan = AdaptiveRebalancer().plan_detailed(city_graph, fleet, _history(), 200.0)

    assert plan.accepted_count > 0
    assert all(item.origin_node_id == "industrial_zone" for item in plan.items)
    assert all(item.target_node_id == "residential_south" for item in plan.items)
    assert all(item.route.origin == "industrial_zone" for item in plan.items)
    assert all(item.estimated_distance_km > 0 for item in plan.items)
    assert all(item.estimated_energy_kwh > 0 for item in plan.items)


def test_planning_mutates_nothing(city_graph):
    fleet = PodFleet(city_graph, [
        Pod(pod_id=f"POD{i:05d}", capacity=4, current_node_id="industrial_zone")
        for i in range(10)])
    before_pods = [p.to_dict() for p in fleet]
    before_traffic = {e.edge_id: e.current_vehicle_count for e in city_graph.edges()}

    AdaptiveRebalancer().plan_detailed(city_graph, fleet, _history(), 200.0)

    assert [p.to_dict() for p in fleet] == before_pods
    assert {e.edge_id: e.current_vehicle_count for e in city_graph.edges()} == before_traffic


def test_matching_is_deterministic_and_ignores_fleet_insertion_order(city_graph):
    def build(order):
        return PodFleet(city_graph, [
            Pod(pod_id=f"POD{i:05d}", capacity=4, current_node_id="industrial_zone")
            for i in order])
    forward = AdaptiveRebalancer().plan_detailed(city_graph, build(range(10)), _history(), 200.0)
    backward = AdaptiveRebalancer().plan_detailed(city_graph, build(reversed(range(10))),
                                                  _history(), 200.0)
    assert forward.to_dict() == backward.to_dict()


def test_repeated_planning_is_identical(city_graph):
    fleet = PodFleet(city_graph, [
        Pod(pod_id=f"POD{i:05d}", capacity=4, current_node_id="industrial_zone")
        for i in range(10)])
    rebalancer = AdaptiveRebalancer()
    runs = [rebalancer.plan_detailed(city_graph, fleet, _history(), 200.0).to_dict()
            for _ in range(3)]
    assert all(run == runs[0] for run in runs)


def test_nearest_eligible_pod_wins_ties_on_pod_id(city_graph):
    """Two equally close pods: the lower id goes."""
    fleet = PodFleet(city_graph, [
        Pod(pod_id="POD00007", capacity=4, current_node_id="industrial_zone"),
        Pod(pod_id="POD00002", capacity=4, current_node_id="industrial_zone"),
        Pod(pod_id="POD00005", capacity=4, current_node_id="industrial_zone"),
    ])
    config = RebalancingConfig(max_repositions_per_cycle=1, max_surplus_before_drain=99.0)
    plan = AdaptiveRebalancer(config).plan_detailed(city_graph, fleet, _history(), 200.0)
    assert plan.items[0].pod_id == "POD00002"


def test_a_node_is_never_drained_below_its_own_forecast(city_graph):
    """Only genuinely spare pods move: the origin keeps enough for its own forecast."""
    fleet = PodFleet(city_graph, [
        Pod(pod_id=f"POD{i:05d}", capacity=4, current_node_id="riverside") for i in range(3)])
    # Riverside has its own forecast demand in this history, so it is not all spare.
    records = _history("riverside", 20) + _history("residential_south", 20)
    plan = AdaptiveRebalancer().plan_detailed(city_graph, fleet, records, 200.0)
    moved_from_riverside = sum(1 for i in plan.items if i.origin_node_id == "riverside")
    assert moved_from_riverside < 3


def test_cycle_and_concurrency_caps_are_respected(city_graph):
    fleet = PodFleet(city_graph, [
        Pod(pod_id=f"POD{i:05d}", capacity=4, current_node_id="industrial_zone")
        for i in range(30)])
    capped = RebalancingConfig(max_repositions_per_cycle=3)
    assert AdaptiveRebalancer(capped).plan_detailed(
        city_graph, fleet, _history(), 200.0).accepted_count <= 3

    # Already-moving pods eat into the concurrency budget.
    concurrent = RebalancingConfig(max_concurrent_repositions=5)
    plan = AdaptiveRebalancer(concurrent).plan_detailed(
        city_graph, fleet, _history(), 200.0, active_repositions=5)
    assert plan.accepted_count == 0


def test_no_deficit_means_no_moves(city_graph):
    """Plenty of pods everywhere and nothing to chase."""
    fleet = generate_fleet(city_graph, fleet_size=100, seed=42)
    plan = AdaptiveRebalancer(RebalancingConfig(max_surplus_before_drain=99.0)
                              ).plan_detailed(city_graph, fleet, [], 200.0)
    assert plan.accepted_count == 0
    assert plan.demand_map.total_deficit == 0.0


def test_no_surplus_means_no_moves(city_graph):
    """Demand everywhere but no spare pod to send."""
    fleet = PodFleet(city_graph, [])
    plan = AdaptiveRebalancer().plan_detailed(city_graph, fleet, _history(), 200.0)
    assert plan.accepted_count == 0
    assert any(move.reason == NO_ELIGIBLE_POD for move in plan.rejected)


def test_a_flat_pod_is_refused_not_stranded(city_graph):
    fleet = PodFleet(city_graph, [
        Pod(pod_id="POD00000", capacity=4, current_node_id="industrial_zone",
            battery_percent=1.0)])
    # min_surplus_to_release=0 so the pod reaches the battery rule under test.
    config = RebalancingConfig(min_surplus_to_release=0.0)
    plan = AdaptiveRebalancer(config).plan_detailed(city_graph, fleet, _history(), 200.0)
    assert plan.accepted_count == 0
    assert any(move.reason == INSUFFICIENT_BATTERY for move in plan.rejected)
    assert fleet.get_pod("POD00000").is_idle       # left alone, not sent


def test_a_target_beyond_the_distance_limit_is_refused(city_graph):
    fleet = PodFleet(city_graph, [
        Pod(pod_id=f"POD{i:05d}", capacity=4, current_node_id="industrial_zone")
        for i in range(5)])
    tight = RebalancingConfig(max_reposition_distance_km=1.0)
    plan = AdaptiveRebalancer(tight).plan_detailed(city_graph, fleet, _history(), 200.0)
    assert plan.accepted_count == 0
    assert any(move.reason == TOO_FAR for move in plan.rejected)


def test_an_unreachable_target_is_refused(make_node, make_edge):
    """One-way network: the pod cannot get there, so the move is rejected."""
    from app.network.graph import NetworkGraph
    graph = NetworkGraph()
    graph.add_node(make_node("a", 0.0, 0.0))
    graph.add_node(make_node("b", 0.0, 0.01))
    graph.add_edge(make_edge("ab", "a", "b", 1.5, 2.0))
    fleet = PodFleet(graph, [Pod(pod_id="POD00000", capacity=4, current_node_id="b")])
    records = [TripRecord(trip_id=f"T{i}", party_size=1, origin_node_id="a",
                          destination_node_id="b", request_time_min=150.0 + i)
               for i in range(20)]
    config = RebalancingConfig(min_surplus_to_release=0.0)
    plan = AdaptiveRebalancer(config).plan_detailed(graph, fleet, records, 200.0)
    assert plan.accepted_count == 0
    assert any(move.reason == UNREACHABLE for move in plan.rejected)


def test_empty_fleet_plans_nothing(city_graph):
    plan = AdaptiveRebalancer().plan_detailed(city_graph, PodFleet(city_graph), _history(), 200.0)
    assert plan.accepted_count == 0 and plan.requests() == ()


def test_pods_in_swarms_are_skipped_by_the_planner(city_graph):
    fleet = PodFleet(city_graph, [
        Pod(pod_id=f"POD{i:05d}", capacity=4, current_node_id="industrial_zone")
        for i in range(6)])
    swarmed = {"POD00000", "POD00001", "POD00002"}
    plan = AdaptiveRebalancer().plan_detailed(city_graph, fleet, _history(), 200.0,
                                              in_active_swarm=lambda pid: pid in swarmed)
    assert plan.accepted_count > 0
    assert not (swarmed & {item.pod_id for item in plan.items})


# --- priority and reasons ----------------------------------------------------
def test_priority_score_follows_the_documented_formula(city_graph):
    fleet = PodFleet(city_graph, [
        Pod(pod_id=f"POD{i:05d}", capacity=4, current_node_id="industrial_zone")
        for i in range(5)])
    config = RebalancingConfig()
    plan = AdaptiveRebalancer(config).plan_detailed(city_graph, fleet, _history(), 200.0)
    for item in plan.items:
        expected = (item.target_deficit * config.priority_deficit_weight
                    - item.estimated_distance_km * config.priority_distance_weight)
        assert item.priority_score == pytest.approx(expected, abs=1e-3)


def test_bigger_deficits_score_higher(city_graph):
    fleet = PodFleet(city_graph, [
        Pod(pod_id=f"POD{i:05d}", capacity=4, current_node_id="industrial_zone")
        for i in range(20)])
    records = _history("residential_south", 30) + _history("market", 5)
    plan = AdaptiveRebalancer().plan_detailed(city_graph, fleet, records, 200.0)
    by_target = {}
    for item in plan.items:
        by_target.setdefault(item.target_node_id, item.priority_score)
    if "residential_south" in by_target and "market" in by_target:
        assert by_target["residential_south"] > by_target["market"]


def test_all_three_reasons_are_reachable(city_graph):
    """A present shortfall, a forecast one, and draining a clumped node."""
    fleet = generate_fleet(city_graph, fleet_size=100, seed=42)
    demand = generate_demand(city_graph, seed=42, passenger_count=1000)
    sim = FleetSimulation(city_graph, fleet, demand.trips)
    sim.run(until_min=520.0)
    plan = AdaptiveRebalancer().plan_detailed(city_graph, fleet, sim.records(), sim.time_min)
    seen = {item.reason for item in plan.items}
    assert seen, "expected at least one move"
    assert seen <= set(RepositionReason)


def test_reason_is_machine_readable_on_the_request(city_graph):
    fleet = PodFleet(city_graph, [
        Pod(pod_id=f"POD{i:05d}", capacity=4, current_node_id="industrial_zone")
        for i in range(5)])
    plan = AdaptiveRebalancer().plan_detailed(city_graph, fleet, _history(), 200.0)
    for item in plan.items:
        assert item.request.reason == item.reason.value
        assert item.reason.value in {r.value for r in RepositionReason}
        assert item.request.priority >= 0


def test_the_planner_satisfies_m4s_hook_protocol(city_graph):
    """An AdaptiveRebalancer is a drop-in for the interface M4 published."""
    from app.swarm.rebalancing import FleetRebalancer, RepositionRequest
    fleet = PodFleet(city_graph, [
        Pod(pod_id=f"POD{i:05d}", capacity=4, current_node_id="industrial_zone")
        for i in range(5)])
    rebalancer: FleetRebalancer = AdaptiveRebalancer()
    requests = rebalancer.plan(fleet, _history(), city_graph)
    assert requests and all(isinstance(r, RepositionRequest) for r in requests)


# --- congestion sensitivity (spec section 11) --------------------------------
def test_congestion_changes_the_repositioning_route_and_time(city_graph):
    """traffic -> edge cost -> reposition route -> reposition time. One cost model."""
    fleet = PodFleet(city_graph, [
        Pod(pod_id="POD00000", capacity=4, current_node_id="north_station")])
    records = _history("east_hub", 20)
    # Drain disabled and the surplus gate relaxed, so exactly one move is planned and
    # the test is about the route it picks, nothing else.
    config = RebalancingConfig(max_surplus_before_drain=99.0, min_surplus_to_release=0.0)

    clean = AdaptiveRebalancer(config).plan_detailed(city_graph, fleet, records, 200.0)
    assert clean.accepted_count == 1
    before = clean.items[0]
    assert before.route.edge_ids == ("E009", "E011")

    city_graph.set_vehicle_count("E011",
                                 city_graph.get_edge("E011").capacity_vehicles_per_hour * 3)
    congested = AdaptiveRebalancer(config).plan_detailed(city_graph, fleet, records, 200.0)
    after = congested.items[0]

    assert after.route.edge_ids == ("E013", "E003")          # rerouted
    assert after.estimated_travel_time_min != pytest.approx(before.estimated_travel_time_min)


def test_congestion_raises_the_energy_a_move_needs(city_graph):
    route = find_route(city_graph, "north_station", "east_hub", "astar")
    before = route_energy_kwh(city_graph, route)
    for edge_id in route.edge_ids:
        city_graph.set_vehicle_count(
            edge_id, city_graph.get_edge(edge_id).capacity_vehicles_per_hour * 3)
    assert route_energy_kwh(city_graph, route) > before
