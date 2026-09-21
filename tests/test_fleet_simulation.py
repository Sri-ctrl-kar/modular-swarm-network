"""M3 movement, ticks, completion, battery, metrics, determinism and congestion."""

import subprocess
import sys
import time

import pytest

from app.demand import generate_demand
from app.errors import FleetConfigError
from app.fleet import (
    DEFAULT_FLEET_CONFIG,
    FleetConfig,
    FleetSimulation,
    Pod,
    PodFleet,
    PodStatus,
    TripStatus,
    advance_pod,
    compute_fleet_metrics,
    edge_sample,
    generate_fleet,
    start_pod_travel,
)
from app.routing import find_route


def _sim(graph, fleet, trips, config=DEFAULT_FLEET_CONFIG, **kwargs):
    return FleetSimulation(graph, fleet, trips, config, **kwargs)


# --- movement over a controlled graph ---------------------------------------
def test_pod_moves_across_a_multi_edge_route(diamond_graph, diamond_fleet, make_trip):
    """A -> B -> D is 3 + 3 minutes on the diamond fixture."""
    from app.fleet import assign_trip
    pod = diamond_fleet.get_pod("POD00000")
    assign_trip(diamond_fleet, diamond_graph, make_trip("A", "D"))
    assert pod.remaining_edge_ids == ("AB", "BD")

    start_pod_travel(diamond_graph, pod)
    assert pod.status is PodStatus.TRAVELING and pod.current_edge_id == "AB"
    assert pod.current_edge_time_min == pytest.approx(3.0)

    # Half an edge: still on AB, nothing completed.
    used, events = advance_pod(diamond_graph, pod, 1.5, 0.0)
    assert used == pytest.approx(1.5) and events == ()
    assert pod.current_node_id == "A" and pod.route_index == 0
    assert pod.route_progress == 0.0

    # Finish AB: one edge completed, pod now at B and onto BD.
    used, events = advance_pod(diamond_graph, pod, 1.5, 1.5)
    assert used == pytest.approx(1.5)
    assert [e.kind for e in events] == ["edge_completed"]
    assert events[0].edge_id == "AB" and events[0].node_id == "B"
    assert events[0].time_min == pytest.approx(3.0)
    assert pod.current_node_id == "B" and pod.route_index == 1
    assert pod.current_edge_id == "BD" and pod.route_progress == pytest.approx(0.5)

    # Finish BD: edge completed and arrival, both in one call.
    used, events = advance_pod(diamond_graph, pod, 5.0, 3.0)
    assert used == pytest.approx(3.0)        # only 3 min were left, no overshoot
    assert [e.kind for e in events] == ["edge_completed", "arrived"]
    assert pod.status is PodStatus.ARRIVED and pod.current_node_id == "D"
    assert pod.route_progress == 1.0 and pod.total_travel_time_min == pytest.approx(6.0)
    assert pod.total_distance_km == pytest.approx(4.0)


def test_several_edges_can_finish_inside_one_tick(diamond_graph, diamond_fleet, make_trip):
    from app.fleet import assign_trip
    pod = diamond_fleet.get_pod("POD00000")
    assign_trip(diamond_fleet, diamond_graph, make_trip("A", "D"))
    start_pod_travel(diamond_graph, pod)

    used, events = advance_pod(diamond_graph, pod, 60.0, 0.0)
    assert used == pytest.approx(6.0)
    assert [e.kind for e in events] == ["edge_completed", "edge_completed", "arrived"]
    assert pod.status is PodStatus.ARRIVED


def test_a_very_short_single_edge_route(diamond_graph, diamond_fleet, make_trip):
    """A -> B is one edge: the pod arrives after a single edge completion."""
    from app.fleet import assign_trip
    pod = diamond_fleet.get_pod("POD00000")
    assign_trip(diamond_fleet, diamond_graph, make_trip("A", "B"))
    assert len(pod.route.edge_ids) == 1

    start_pod_travel(diamond_graph, pod)
    _, events = advance_pod(diamond_graph, pod, 10.0, 0.0)
    assert [e.kind for e in events] == ["edge_completed", "arrived"]
    assert pod.current_node_id == "B" and pod.route_progress == 1.0


def test_advancing_a_non_travelling_pod_is_a_no_op(diamond_graph, diamond_fleet):
    pod = diamond_fleet.get_pod("POD00000")
    assert advance_pod(diamond_graph, pod, 5.0, 0.0) == (0.0, ())
    assert pod.status is PodStatus.IDLE


def test_edge_sample_reads_the_networks_current_cost(city_graph):
    edge = city_graph.get_edge("E011")
    time_min, distance_km, energy = edge_sample(city_graph, "E011")
    assert time_min == pytest.approx(edge.current_travel_time_min)
    assert distance_km == pytest.approx(edge.distance_km)
    assert energy == pytest.approx(
        DEFAULT_FLEET_CONFIG.energy_kwh_for(edge.distance_km, edge.congestion_multiplier))


# --- congestion interaction (requirement 12) --------------------------------
def test_congestion_before_an_edge_is_entered_changes_the_pods_travel_time(
        diamond_graph, diamond_fleet, make_trip):
    """traffic -> edge cost -> pod travel time, using M1's model only.

    The pod drives A -> B -> D. While it is on AB, BD is congested. BD is sampled
    when the pod enters it, so the pod pays the new cost.
    """
    from app.fleet import assign_trip
    pod = diamond_fleet.get_pod("POD00000")
    assign_trip(diamond_fleet, diamond_graph, make_trip("A", "D"))
    start_pod_travel(diamond_graph, pod)
    assert pod.current_edge_time_min == pytest.approx(3.0)

    free_flow_bd = diamond_graph.get_edge("BD").current_travel_time_min
    diamond_graph.set_vehicle_count("BD", 2000)          # u = 2 -> multiplier 3.15
    congested_bd = diamond_graph.get_edge("BD").current_travel_time_min
    assert congested_bd > free_flow_bd

    advance_pod(diamond_graph, pod, 3.0, 0.0)            # finish AB, enter BD
    assert pod.current_edge_id == "BD"
    assert pod.current_edge_time_min == pytest.approx(congested_bd)

    _, events = advance_pod(diamond_graph, pod, 100.0, 3.0)
    assert [e.kind for e in events] == ["edge_completed", "arrived"]
    assert pod.total_travel_time_min == pytest.approx(3.0 + congested_bd)
    assert pod.total_travel_time_min > 6.0               # slower than the free-flow trip


def test_congestion_on_the_edge_already_being_driven_does_not_change_that_traversal(
        diamond_graph, diamond_fleet, make_trip):
    """The documented rule: an edge's cost is fixed once the pod is on it."""
    from app.fleet import assign_trip
    pod = diamond_fleet.get_pod("POD00000")
    assign_trip(diamond_fleet, diamond_graph, make_trip("A", "B"))
    start_pod_travel(diamond_graph, pod)

    diamond_graph.set_vehicle_count("AB", 5000)
    _, events = advance_pod(diamond_graph, pod, 3.0, 0.0)
    assert [e.kind for e in events] == ["edge_completed", "arrived"]
    assert pod.total_travel_time_min == pytest.approx(3.0)


def test_congestion_raises_energy_as_well_as_time(diamond_graph, diamond_fleet, make_trip):
    from app.fleet import assign_trip
    pod = diamond_fleet.get_pod("POD00000")
    diamond_graph.set_vehicle_count("AB", 2000)
    assign_trip(diamond_fleet, diamond_graph, make_trip("A", "B"))
    start_pod_travel(diamond_graph, pod)
    advance_pod(diamond_graph, pod, 100.0, 0.0)
    congested_energy = pod.total_energy_kwh

    clean_fleet = PodFleet(diamond_graph, [Pod(pod_id="POD00001", capacity=4, current_node_id="A")])
    diamond_graph.set_vehicle_count("AB", 0)
    clean = clean_fleet.get_pod("POD00001")
    assign_trip(clean_fleet, diamond_graph, make_trip("A", "B", trip_id="T000001"))
    start_pod_travel(diamond_graph, clean)
    advance_pod(diamond_graph, clean, 100.0, 0.0)

    assert congested_energy > clean.total_energy_kwh
    assert congested_energy == pytest.approx(clean.total_energy_kwh * 3.15)   # the BPR multiplier


def test_pod_travel_time_matches_the_networks_own_edge_costs(city_graph, city_fleet, make_trip):
    """No second cost model: a pod's driving time equals the sum of M1 edge times."""
    from app.fleet import assign_trip
    pod_at_market = next(p for p in city_fleet if p.current_node_id == "market") \
        if any(p.current_node_id == "market" for p in city_fleet) else None
    fleet = PodFleet(city_graph, [Pod(pod_id="POD00000", capacity=4, current_node_id="market")])
    pod = fleet.get_pod("POD00000")
    assign_trip(fleet, city_graph, make_trip("market", "airport"))
    expected = sum(city_graph.get_edge(e).current_travel_time_min for e in pod.route.edge_ids)

    start_pod_travel(city_graph, pod)
    advance_pod(city_graph, pod, 1000.0, 0.0)
    assert pod.total_travel_time_min == pytest.approx(expected)
    assert pod.total_travel_time_min == pytest.approx(pod.route.total_travel_time_min)


# --- the tick loop ----------------------------------------------------------
def test_assigned_is_an_observable_state_then_the_pod_departs(city_graph, make_trip):
    """A pod assigned on one tick boards, and departs on the next."""
    fleet = PodFleet(city_graph, [Pod(pod_id="POD00000", capacity=4, current_node_id="market")])
    sim = _sim(city_graph, fleet, [make_trip("market", "airport")])

    first = sim.tick()
    assert first.assigned_trip_ids == ("T000000",)
    assert first.departed_pod_ids == ()
    assert fleet.get_pod("POD00000").status is PodStatus.ASSIGNED
    assert sim.record("T000000").status is TripStatus.ASSIGNED

    second = sim.tick()
    assert second.departed_pod_ids == ("POD00000",)
    assert fleet.get_pod("POD00000").status is PodStatus.TRAVELING
    assert sim.record("T000000").status is TripStatus.IN_PROGRESS


def test_trip_completion_releases_the_pod_and_keeps_the_record(city_graph, make_trip):
    fleet = PodFleet(city_graph, [Pod(pod_id="POD00000", capacity=4, current_node_id="market")])
    sim = _sim(city_graph, fleet, [make_trip("market", "airport", party_size=3)])
    report = sim.run()

    rec = sim.record("T000000")
    assert rec.status is TripStatus.COMPLETED
    assert rec.pod_id == "POD00000" and rec.party_size == 3
    assert rec.completed_time_min > rec.assigned_time_min >= rec.request_time_min
    assert rec.completion_time_min > 0 and rec.waiting_time_min >= 0
    assert rec.route_distance_km > 0 and rec.energy_kwh > 0
    assert rec.actual_travel_time_min == pytest.approx(rec.route_travel_time_min)

    pod = fleet.get_pod("POD00000")
    assert pod.status is PodStatus.IDLE                 # released after arriving
    assert pod.assigned_trip_id is None and pod.occupied_seats == 0
    assert pod.current_node_id == "airport"             # it really moved
    assert pod.completed_trip_count == 1
    assert report.stopped_because == "no pending work"

    # Records are never deleted.
    assert len(sim.records()) == 1
    assert sim.trip_status_counts()["completed"] == 1


def test_a_pod_serves_several_trips_in_sequence(city_graph, make_trip):
    fleet = PodFleet(city_graph, [Pod(pod_id="POD00000", capacity=4, current_node_id="market")])
    trips = [make_trip("market", "airport", trip_id="T000000", request_time_min=0.0),
             make_trip("airport", "market", trip_id="T000001", request_time_min=200.0)]
    sim = _sim(city_graph, fleet, trips)
    sim.run()

    assert [r.status for r in sim.records()] == [TripStatus.COMPLETED, TripStatus.COMPLETED]
    assert fleet.get_pod("POD00000").completed_trip_count == 2
    assert fleet.get_pod("POD00000").current_node_id == "market"


def test_trips_are_not_assigned_before_they_are_requested(city_graph, make_trip):
    fleet = PodFleet(city_graph, [Pod(pod_id="POD00000", capacity=4, current_node_id="market")])
    sim = _sim(city_graph, fleet, [make_trip("market", "airport", request_time_min=10.0)])

    for _ in range(5):
        report = sim.tick()
        assert report.assigned_trip_ids == ()
    assert sim.record("T000000").status is TripStatus.PENDING
    assert sim.time_min == pytest.approx(5.0)

    sim.run()
    assert sim.record("T000000").status is TripStatus.COMPLETED
    assert sim.record("T000000").assigned_time_min >= 10.0


def test_a_trip_nobody_can_serve_times_out_rather_than_waiting_forever(city_graph, make_trip):
    fleet = PodFleet(city_graph, [Pod(pod_id="POD00000", capacity=4, current_node_id="airport")])
    config = FleetConfig(max_trip_wait_min=10.0)
    sim = _sim(city_graph, fleet, [make_trip("market", "airport")], config)
    report = sim.run()

    rec = sim.record("T000000")
    assert rec.status is TripStatus.FAILED
    assert rec.failure_reason.startswith("no pod available within")
    assert rec.pod_id is None
    assert report.stopped_because == "no pending work"   # the run terminates
    assert sim.time_min <= 20.0


def test_an_unroutable_trip_fails_immediately(make_node, make_edge, make_trip):
    from app.network.graph import NetworkGraph
    graph = NetworkGraph()
    graph.add_node(make_node("a", 0.0, 0.0))
    graph.add_node(make_node("b", 0.0, 0.01))
    graph.add_edge(make_edge("ab", "a", "b", 1.5, 2.0))
    fleet = PodFleet(graph, [Pod(pod_id="POD00000", capacity=4, current_node_id="b")])

    sim = _sim(graph, fleet, [make_trip("b", "a")])
    report = sim.tick()
    assert report.failed_trip_ids == ("T000000",)
    assert sim.record("T000000").status is TripStatus.FAILED
    assert sim.record("T000000").failure_reason.startswith("unroutable:")
    assert not sim.has_pending_work


def test_run_honours_max_ticks_and_until_min(city_graph, city_fleet, make_trip):
    demand = generate_demand(city_graph, seed=42, passenger_count=50)
    sim = _sim(city_graph, city_fleet, demand.trips)
    report = sim.run(max_ticks=5)
    assert report.ticks == 5 and report.stopped_because == "max_ticks reached"
    assert sim.time_min == pytest.approx(5.0)

    later = sim.run(until_min=20.0)
    assert later.stopped_because == "until_min reached"
    assert sim.time_min == pytest.approx(20.0)


def test_tick_length_is_configurable(city_graph, make_trip):
    fleet = PodFleet(city_graph, [Pod(pod_id="POD00000", capacity=4, current_node_id="market")])
    sim = _sim(city_graph, fleet, [make_trip("market", "airport")], FleetConfig(tick_minutes=5.0))
    sim.tick()
    assert sim.time_min == pytest.approx(5.0)


@pytest.mark.parametrize("bad", [-1, 1.5, "10", True])
def test_run_rejects_bad_max_ticks(city_graph, city_fleet, bad):
    with pytest.raises(FleetConfigError):
        _sim(city_graph, city_fleet, []).run(max_ticks=bad)


def test_simulation_rejects_bad_construction(city_graph, city_fleet, make_trip):
    with pytest.raises(FleetConfigError, match="must be a FleetConfig"):
        FleetSimulation(city_graph, city_fleet, [], config={"tick": 1})
    with pytest.raises(FleetConfigError, match="start_time_min"):
        _sim(city_graph, city_fleet, [], start_time_min=-1.0)
    with pytest.raises(FleetConfigError, match="expected TripRequest"):
        _sim(city_graph, city_fleet, ["T000000"])
    trip = make_trip("market", "airport")
    with pytest.raises(FleetConfigError, match="duplicate trip id"):
        _sim(city_graph, city_fleet, [trip, trip])


def test_unknown_trip_lookups_raise(city_graph, city_fleet):
    sim = _sim(city_graph, city_fleet, [])
    with pytest.raises(FleetConfigError):
        sim.record("T999999")
    with pytest.raises(FleetConfigError):
        sim.trip_request("T999999")


# --- battery and charging ---------------------------------------------------
def test_driving_consumes_battery_in_proportion_to_distance(city_graph, make_trip):
    fleet = PodFleet(city_graph, [Pod(pod_id="POD00000", capacity=4, current_node_id="market")])
    sim = _sim(city_graph, fleet, [make_trip("market", "airport")])
    sim.run()

    pod = fleet.get_pod("POD00000")
    expected_kwh = DEFAULT_FLEET_CONFIG.base_energy_kwh_per_km * pod.total_distance_km
    assert pod.battery_percent < 100.0
    assert pod.total_energy_kwh >= expected_kwh          # >= because congestion adds
    assert pod.battery_percent == pytest.approx(
        100.0 - pod.total_energy_kwh * DEFAULT_FLEET_CONFIG.percent_per_kwh)


def test_battery_never_goes_negative_over_a_long_run(city_graph):
    """Even with a tiny battery and many trips, no pod ends below zero."""
    config = FleetConfig(battery_capacity_kwh=2.0, low_battery_percent=10.0,
                         charge_percent_per_min=5.0, assignment_battery_reserve_percent=0.0)
    fleet = generate_fleet(city_graph, fleet_size=10, seed=42, config=config)
    demand = generate_demand(city_graph, seed=42, passenger_count=200)
    sim = _sim(city_graph, fleet, demand.trips, config)
    sim.run()
    assert all(0.0 <= pod.battery_percent <= 100.0 for pod in fleet)


def test_a_flat_pod_charges_and_returns_to_service(city_graph, make_trip):
    config = FleetConfig(low_battery_percent=30.0, target_charge_percent=60.0,
                         charge_percent_per_min=10.0)
    fleet = PodFleet(city_graph, [Pod(pod_id="POD00000", capacity=4,
                                      current_node_id="market", battery_percent=5.0)])
    sim = _sim(city_graph, fleet, [make_trip("market", "airport", request_time_min=0.0)], config)
    pod = fleet.get_pod("POD00000")

    sim.tick()
    assert pod.status is PodStatus.CHARGING       # too flat to work
    assert pod.battery_percent == pytest.approx(15.0)
    assert sim.record("T000000").status is TripStatus.PENDING     # not assigned while charging

    while pod.status is PodStatus.CHARGING:
        sim.tick()
    assert pod.battery_percent >= 60.0
    assert pod.status in (PodStatus.IDLE, PodStatus.ASSIGNED)


def test_charging_pods_are_unavailable_for_assignment(city_graph, make_trip):
    config = FleetConfig(low_battery_percent=30.0, max_trip_wait_min=5.0,
                         charge_percent_per_min=0.01, target_charge_percent=99.0)
    fleet = PodFleet(city_graph, [Pod(pod_id="POD00000", capacity=4,
                                      current_node_id="market", battery_percent=1.0)])
    sim = _sim(city_graph, fleet, [make_trip("market", "airport")], config)
    sim.run()
    assert fleet.get_pod("POD00000").status is PodStatus.CHARGING
    assert sim.record("T000000").status is TripStatus.FAILED


def test_battery_stays_within_bounds_at_every_tick(city_graph):
    fleet = generate_fleet(city_graph, fleet_size=15, seed=42)
    demand = generate_demand(city_graph, seed=42, passenger_count=120)
    sim = _sim(city_graph, fleet, demand.trips)
    for _ in range(300):
        sim.tick()
        assert all(0.0 <= pod.battery_percent <= 100.0 for pod in fleet)
        if not sim.has_pending_work:
            break


# --- metrics ----------------------------------------------------------------
def test_metrics_reconcile_with_the_fleet_and_records(city_graph):
    fleet = generate_fleet(city_graph, fleet_size=30, seed=42)
    demand = generate_demand(city_graph, seed=42, passenger_count=200)
    sim = _sim(city_graph, fleet, demand.trips)
    sim.run()
    m = compute_fleet_metrics(fleet, sim.records(), sim.time_min)

    assert m.total_pods == 30 and m.total_capacity == 120
    assert m.total_trips == 200
    counts = sim.trip_status_counts()
    assert m.completed_trips == counts["completed"]
    assert m.failed_trips == counts["failed"]
    assert m.pending_trips == counts["pending"]
    assert m.unroutable_trips + m.timed_out_trips == m.failed_trips
    assert m.unassigned_trips == m.pending_trips + m.timed_out_trips
    assert m.completed_trips + m.failed_trips + m.pending_trips \
        + m.assigned_trips + m.in_progress_trips == 200

    assert m.idle_pods + m.assigned_pods + m.traveling_pods + m.arrived_pods \
        + m.charging_pods == 30
    assert m.total_distance_km == pytest.approx(sum(p.total_distance_km for p in fleet), abs=1e-3)
    assert m.total_energy_kwh == pytest.approx(sum(p.total_energy_kwh for p in fleet), abs=1e-3)
    assert m.total_passengers_carried == sum(
        r.party_size for r in sim.records() if r.status is TripStatus.COMPLETED)
    assert 0.0 <= m.average_pod_utilization <= 1.0
    assert 1.0 <= m.average_passenger_occupancy <= 4.0
    assert m.average_occupancy_rate == pytest.approx(m.average_passenger_occupancy / 4.0, abs=1e-3)
    assert m.average_energy_per_km >= DEFAULT_FLEET_CONFIG.base_energy_kwh_per_km
    assert 0.0 < m.completion_rate <= 1.0


def test_metrics_avoid_fake_precision_and_empty_averages(city_graph):
    fleet = generate_fleet(city_graph, fleet_size=3, seed=42)
    sim = _sim(city_graph, fleet, [])
    m = compute_fleet_metrics(fleet, sim.records(), sim.time_min)

    assert m.total_trips == 0 and m.completed_trips == 0
    assert m.average_passenger_occupancy is None     # no fake 0.0
    assert m.average_trip_completion_time_min is None
    assert m.average_energy_per_km is None
    assert m.completion_rate is None
    assert m.total_distance_km == 0.0

    # Rounding is applied, not raw floats.
    fleet2 = generate_fleet(city_graph, fleet_size=5, seed=42)
    demand = generate_demand(city_graph, seed=42, passenger_count=30)
    sim2 = _sim(city_graph, fleet2, demand.trips)
    sim2.run()
    m2 = compute_fleet_metrics(fleet2, sim2.records(), sim2.time_min)
    assert m2.total_energy_kwh == round(m2.total_energy_kwh, 3)
    assert m2.total_distance_km == round(m2.total_distance_km, 3)


def test_metrics_are_json_serialisable_and_deterministic(city_graph):
    import json
    results = []
    for _ in range(2):
        fleet = generate_fleet(city_graph, fleet_size=20, seed=42)
        demand = generate_demand(city_graph, seed=42, passenger_count=100)
        sim = _sim(city_graph, fleet, demand.trips)
        sim.run()
        results.append(compute_fleet_metrics(fleet, sim.records(), sim.time_min).to_dict())
    assert results[0] == results[1]
    assert json.loads(json.dumps(results[0])) == results[0]


# --- determinism ------------------------------------------------------------
def _run_once(seed=42, passengers=150, pods=20):
    from app.network.synthetic_city import build_synthetic_city
    graph = build_synthetic_city(42).graph
    fleet = generate_fleet(graph, fleet_size=pods, seed=seed)
    demand = generate_demand(graph, seed=seed, passenger_count=passengers)
    sim = FleetSimulation(graph, fleet, demand.trips)
    sim.run()
    return sim, fleet


def test_repeated_simulations_are_byte_identical():
    runs = []
    for _ in range(3):
        sim, fleet = _run_once()
        runs.append((
            sim.snapshot().fingerprint(),
            [p.to_dict() for p in fleet],
            [r.to_dict() for r in sim.records()],
            compute_fleet_metrics(fleet, sim.records(), sim.time_min).to_dict(),
        ))
    assert all(run == runs[0] for run in runs)


def test_final_fleet_state_and_trajectories_are_identical():
    first, fleet_a = _run_once()
    second, fleet_b = _run_once()
    assert [p.current_node_id for p in fleet_a] == [p.current_node_id for p in fleet_b]
    assert [p.total_distance_km for p in fleet_a] == [p.total_distance_km for p in fleet_b]
    assert [p.battery_percent for p in fleet_a] == [p.battery_percent for p in fleet_b]
    assert [p.completed_trip_count for p in fleet_a] == [p.completed_trip_count for p in fleet_b]
    assert first.snapshot() == second.snapshot()


def test_tick_by_tick_histories_match():
    """Not just the end state: every tick must report the same thing."""
    histories = []
    for _ in range(2):
        from app.network.synthetic_city import build_synthetic_city
        graph = build_synthetic_city(42).graph
        fleet = generate_fleet(graph, fleet_size=10, seed=42)
        demand = generate_demand(graph, seed=42, passenger_count=60)
        sim = FleetSimulation(graph, fleet, demand.trips)
        histories.append([sim.tick().to_dict() for _ in range(200)])
    assert histories[0] == histories[1]


def test_different_seeds_diverge():
    a, _ = _run_once(seed=42)
    b, _ = _run_once(seed=7)
    assert a.snapshot().fingerprint() != b.snapshot().fingerprint()


def test_simulation_uses_no_randomness_at_all():
    """Fleet init is the only seeded step; the tick loop must not draw at all."""
    import random
    from app.network.synthetic_city import build_synthetic_city
    graph = build_synthetic_city(42).graph
    fleet = generate_fleet(graph, fleet_size=10, seed=42)
    demand = generate_demand(graph, seed=42, passenger_count=60)

    random.seed(999)
    expected = [random.random() for _ in range(3)]
    random.seed(999)
    FleetSimulation(graph, fleet, demand.trips).run()
    assert [random.random() for _ in range(3)] == expected


def test_determinism_across_separate_processes():
    script = (
        "from app.network.synthetic_city import build_synthetic_city;"
        "from app.demand import generate_demand;"
        "from app.fleet import FleetSimulation, generate_fleet, compute_fleet_metrics;"
        "g=build_synthetic_city(42).graph;"
        "f=generate_fleet(g, fleet_size=20, seed=42);"
        "d=generate_demand(g, seed=42, passenger_count=150);"
        "s=FleetSimulation(g, f, d.trips); s.run();"
        "m=compute_fleet_metrics(f, s.records(), s.time_min);"
        "print(s.snapshot().fingerprint(), m.completed_trips, m.total_distance_km, m.total_energy_kwh)"
    )
    outputs = {subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                              check=True).stdout.strip() for _ in range(2)}
    sim, fleet = _run_once()
    m = compute_fleet_metrics(fleet, sim.records(), sim.time_min)
    expected = f"{sim.snapshot().fingerprint()} {m.completed_trips} {m.total_distance_km} {m.total_energy_kwh}"
    assert len(outputs) == 1
    assert outputs.pop() == expected


# --- edge cases -------------------------------------------------------------
def test_empty_fleet_serves_nothing_but_does_not_crash(city_graph):
    fleet = PodFleet(city_graph)
    demand = generate_demand(city_graph, seed=42, passenger_count=20)
    sim = _sim(city_graph, fleet, demand.trips, FleetConfig(max_trip_wait_min=5.0))
    report = sim.run()

    assert len(fleet) == 0
    assert sim.trip_status_counts()["completed"] == 0
    assert sim.trip_status_counts()["failed"] == 20
    assert report.stopped_because == "no pending work"
    m = compute_fleet_metrics(fleet, sim.records(), sim.time_min)
    assert m.total_pods == 0 and m.completed_trips == 0 and m.unassigned_trips == 20
    assert m.average_pod_utilization is None
    assert sim.snapshot().pods == ()


def test_zero_trips_leaves_the_fleet_untouched(city_graph):
    fleet = generate_fleet(city_graph, fleet_size=10, seed=42)
    before = [p.to_dict() for p in fleet]
    sim = _sim(city_graph, fleet, [])
    report = sim.run()

    assert report.ticks == 0 and report.stopped_because == "no pending work"
    assert not sim.has_pending_work
    assert [p.to_dict() for p in fleet] == before
    assert sim.records() == ()
    assert sim.trip_status_counts() == {"pending": 0, "assigned": 0, "in_progress": 0,
                                        "completed": 0, "failed": 0}


def test_single_pod_single_trip(city_graph, make_trip):
    fleet = PodFleet(city_graph, [Pod(pod_id="POD00000", capacity=4, current_node_id="market")])
    sim = _sim(city_graph, fleet, [make_trip("market", "airport")])
    sim.run()
    assert sim.trip_status_counts()["completed"] == 1
    assert fleet.get_pod("POD00000").current_node_id == "airport"


def test_pending_work_flag_tracks_the_simulation(city_graph, make_trip):
    fleet = PodFleet(city_graph, [Pod(pod_id="POD00000", capacity=4, current_node_id="market")])
    sim = _sim(city_graph, fleet, [make_trip("market", "airport")])
    assert sim.has_pending_work
    sim.run()
    assert not sim.has_pending_work


def test_snapshot_tracks_the_clock_and_trip_counts(city_graph, make_trip):
    fleet = PodFleet(city_graph, [Pod(pod_id="POD00000", capacity=4, current_node_id="market")])
    sim = _sim(city_graph, fleet, [make_trip("market", "airport")])
    start = sim.snapshot()
    assert start.time_min == 0.0
    assert dict(start.trip_status_counts)["pending"] == 1

    sim.run()
    end = sim.snapshot()
    assert end.time_min > 0.0
    assert dict(end.trip_status_counts)["completed"] == 1
    assert end.fingerprint() != start.fingerprint()


# --- layering and M1/M2 regression ------------------------------------------
def test_lower_layers_do_not_import_the_fleet_layer():
    """models <- network <- routing <- simulation <- demand <- fleet."""
    from app.config import PROJECT_ROOT
    offenders = []
    for layer in ("models", "network", "routing", "simulation", "demand"):
        for path in sorted((PROJECT_ROOT / "app" / layer).rglob("*.py")):
            if "app.fleet" in path.read_text(encoding="utf-8"):
                offenders.append(str(path.relative_to(PROJECT_ROOT)))
    assert offenders == [], f"lower layers import the fleet layer: {offenders}"


def test_the_fleet_does_not_disturb_m1_or_m2(city_graph):
    """Running a fleet must leave the network's traffic and M2's demand untouched."""
    traffic_before = {e.edge_id: e.current_vehicle_count for e in city_graph.edges()}
    demand = generate_demand(city_graph, seed=42, passenger_count=100)
    trips_before = [t.to_dict() for t in demand.trips]
    fingerprint_before = demand.snapshot().fingerprint()

    fleet = generate_fleet(city_graph, fleet_size=20, seed=42)
    _sim(city_graph, fleet, demand.trips).run()

    assert {e.edge_id: e.current_vehicle_count for e in city_graph.edges()} == traffic_before
    assert [t.to_dict() for t in demand.trips] == trips_before
    assert demand.snapshot().fingerprint() == fingerprint_before


def test_routing_still_agrees_after_a_fleet_run(city_graph):
    """M1's A*/Dijkstra guarantee must survive M3."""
    from app.routing import astar, dijkstra
    fleet = generate_fleet(city_graph, fleet_size=15, seed=42)
    demand = generate_demand(city_graph, seed=42, passenger_count=80)
    _sim(city_graph, fleet, demand.trips).run()

    for origin, destination in (("north_station", "airport"), ("west_hub", "tech_park")):
        a = astar(city_graph, origin, destination)
        d = dijkstra(city_graph, origin, destination)
        assert a.total_cost == pytest.approx(d.total_cost, rel=1e-12, abs=1e-9)


# --- performance ------------------------------------------------------------
def test_one_hundred_pods_and_one_thousand_trips(city_graph):
    """Requirement: 100 pods / 1,000 trips with plain stdlib."""
    fleet = generate_fleet(city_graph, fleet_size=100, seed=42)
    demand = generate_demand(city_graph, seed=42, passenger_count=1000)
    sim = _sim(city_graph, fleet, demand.trips)

    started = time.perf_counter()
    report = sim.run()
    elapsed = time.perf_counter() - started

    m = compute_fleet_metrics(fleet, sim.records(), sim.time_min)
    assert m.total_pods == 100 and m.total_trips == 1000
    assert m.completed_trips > 0 and m.total_distance_km > 0
    assert report.stopped_because == "no pending work"
    assert elapsed < 120.0, f"100 pods / 1000 trips took {elapsed:.1f}s"


# --- CLI --------------------------------------------------------------------
def test_fleet_demo_cli_runs_and_reports(capsys):
    from app.cli.main import main
    assert main(["fleet-demo", "--pods", "20", "--passengers", "100"]) == 0
    out = capsys.readouterr().out
    assert "SYNTHETIC" in out
    assert "deferred to M4" in out
    assert "Fleet: 20 pods" in out
    assert "Capacity: 4 seats per pod" in out
    assert "Trips: 100" in out
    assert "Assigned and completed:" in out
    assert "Average occupancy:" in out
    assert "Distance travelled:" in out
    assert "Consumed:" in out
    assert "Final fleet state" in out
    assert "Fleet fingerprint:" in out


def test_fleet_demo_cli_is_reproducible(capsys):
    from app.cli.main import main

    def fingerprint():
        main(["fleet-demo", "--pods", "20", "--passengers", "100"])
        for line in capsys.readouterr().out.splitlines():
            if line.startswith("Fleet fingerprint:"):
                return line.split(":", 1)[1].strip()
        raise AssertionError("no fingerprint in output")

    assert fingerprint() == fingerprint()


def test_fleet_demo_cli_options(capsys):
    from app.cli.main import main
    assert main(["fleet-demo", "--pods", "5", "--passengers", "20", "--capacity", "6",
                 "--tick-minutes", "2", "--profile", "peak_hour", "--algorithm", "dijkstra"]) == 0
    out = capsys.readouterr().out
    assert "Capacity: 6 seats per pod (30 seats total)" in out
    assert "ticks of 2 min" in out
    assert "peak_hour" in out


def test_fleet_demo_cli_rejects_bad_input(capsys):
    from app.cli.main import main
    assert main(["fleet-demo", "--pods", "-5"]) == 2
    assert "fleet_size" in capsys.readouterr().err


def test_existing_cli_commands_still_work(capsys):
    from app.cli.main import main
    assert main(["route", "--from", "North Station", "--to", "Airport", "--algorithm", "both"]) == 0
    assert "Optimal cost match (A* vs Dijkstra): YES" in capsys.readouterr().out
    assert main(["demand-demo", "--passengers", "100"]) == 0
    assert "Demand fingerprint:" in capsys.readouterr().out
    assert main(["info"]) == 0
    assert "node_count: 22" in capsys.readouterr().out
