"""M4 platoon movement, splitting, rejoining, metrics, comparison and determinism."""

import subprocess
import sys
import time

import pytest

from app.demand import generate_demand
from app.errors import SwarmConfigError, SwarmNotFoundError
from app.fleet import DEFAULT_FLEET_CONFIG, FleetConfig, PodStatus, generate_fleet
from app.fleet.metrics import compute_fleet_metrics
from app.swarm import (
    DEFAULT_SWARM_CONFIG,
    SwarmConfig,
    SwarmSimulation,
    SwarmStatus,
    compute_swarm_metrics,
    corridor_progress,
    pods_are_synchronised,
)


def _run(simulation, max_ticks=5000):
    return simulation.run(max_ticks=max_ticks)


# --- platoon movement -------------------------------------------------------
def test_members_travel_the_corridor_together_in_lockstep(corridor_graph, make_corridor_fleet):
    """Shared movement is explicit: the members stay on the same edge at the same
    elapsed time all the way to the divergence node."""
    fleet, sim = make_corridor_fleet(["E", "F"])
    while not sim.swarms():
        sim.tick()
    swarm = sim.swarms()[0]
    assert swarm.status is SwarmStatus.ACTIVE
    assert swarm.pod_ids == ("POD00000", "POD00001")
    assert swarm.corridor.edge_ids == ("C1", "C2", "C3")

    members = [fleet.get_pod(p) for p in swarm.pod_ids]
    checked = 0
    for _ in range(20):                       # the corridor takes 15 min
        sim.tick()
        if swarm.corridor_is_complete:
            break
        # While still inside the corridor the members must share edge and elapsed
        # time. Once the corridor ends they are at the divergence node on their own
        # onward edges, which is exactly when they stop being synchronised.
        travelling = [p for p in members if p.status is PodStatus.TRAVELING]
        assert pods_are_synchronised(travelling), [p.to_dict() for p in travelling]
        checked += 1

    assert checked >= 10, "the platoon should have been checked mid-corridor"
    assert swarm.corridor_is_complete
    assert swarm.shared_distance_km == pytest.approx(12.0)
    assert swarm.current_node_id == "H3"
    # Diverged: same node, no time spent yet, but different onward edges.
    assert {p.current_node_id for p in members} == {"H3"}
    assert {p.current_edge_id for p in members} == {"HE", "HF"}
    assert all(p.current_edge_elapsed_min == 0.0 for p in members)


def test_swarm_progress_is_tracked_and_stops_at_the_divergence_node(corridor_graph,
                                                                    make_corridor_fleet):
    fleet, sim = make_corridor_fleet(["E", "F"])
    while not sim.swarms():
        sim.tick()
    swarm = sim.swarms()[0]

    for _ in range(40):
        sim.tick()
        if swarm.corridor_is_complete:
            break
    assert swarm.corridor_is_complete
    assert swarm.corridor_edges_completed == 3
    assert swarm.current_node_id == "H3"                  # exactly the divergence node
    assert swarm.shared_distance_km == pytest.approx(12.0)
    assert swarm.shared_travel_time_min == pytest.approx(15.0, abs=1.0)
    assert swarm.coordinated_pod_km() == pytest.approx(24.0)   # 12 km x 2 pods


def test_pods_keep_their_own_identity_passengers_and_odometers(corridor_graph,
                                                               make_corridor_fleet):
    """A platoon never merges pods into one anonymous vehicle."""
    fleet, sim = make_corridor_fleet(["E", "F"], party_size=2)
    _run(sim)
    a, b = fleet.get_pod("POD00000"), fleet.get_pod("POD00001")
    assert a.pod_id != b.pod_id
    assert a.current_node_id == "E" and b.current_node_id == "F"    # different endpoints
    assert a.total_distance_km == pytest.approx(20.0)
    assert b.total_distance_km == pytest.approx(16.0)               # own route, own odometer
    assert a.completed_trip_count == 1 and b.completed_trip_count == 1
    assert sim.record("T000000").party_size == 2 and sim.record("T000001").party_size == 2


def test_platooning_grants_no_distance_or_energy_discount(corridor_graph, make_corridor_fleet):
    """The same pod covering the same corridor consumes the same whether it
    platoons or not. Coordination is accounted for in road space only."""
    _, solo = make_corridor_fleet(["E", "F"], enable_swarms=False)
    _run(solo)
    solo_a = solo.fleet.get_pod("POD00000")

    _, platooned = make_corridor_fleet(["E", "F"], enable_swarms=True)
    _run(platooned)
    swarm_a = platooned.fleet.get_pod("POD00000")

    assert platooned.swarms()                              # it really did platoon
    assert swarm_a.total_distance_km == pytest.approx(solo_a.total_distance_km)
    assert swarm_a.total_energy_kwh == pytest.approx(solo_a.total_energy_kwh)
    assert swarm_a.total_travel_time_min == pytest.approx(solo_a.total_travel_time_min)
    assert swarm_a.battery_percent == pytest.approx(solo_a.battery_percent)


def test_corridor_progress_helper(corridor_graph, make_corridor_fleet):
    fleet, sim = make_corridor_fleet(["E", "F"])
    while not sim.swarms():
        sim.tick()
    swarm = sim.swarms()[0]
    pod = fleet.get_pod("POD00000")
    assert corridor_progress(swarm.corridor, pod) == 0
    for _ in range(40):
        sim.tick()
        if swarm.corridor_is_complete:
            break
    assert corridor_progress(swarm.corridor, pod) == 3


# --- the worked example from the spec ---------------------------------------
def test_four_pods_form_one_swarm_then_split_three_and_one(corridor_graph, make_corridor_fleet):
    """P0,P1,P2,P3 share H0->H3; at H3 the three E-bound pods stay together and
    the F-bound pod becomes independent."""
    fleet, sim = make_corridor_fleet(["E", "F", "E", "E"])
    while not sim.swarms():
        sim.tick()

    first = sim.swarms()[0]
    assert first.size == 4
    assert first.pod_ids == ("POD00000", "POD00001", "POD00002", "POD00003")
    assert first.leader_pod_id == "POD00000"
    assert first.corridor.edge_ids == ("C1", "C2", "C3")
    assert first.corridor.divergence_node_id == "H3"
    assert first.corridor.distance_km == pytest.approx(12.0)

    for _ in range(60):
        sim.tick()
        if len(sim.swarms()) > 1:
            break

    assert first.status is SwarmStatus.COMPLETED
    assert first.split_time_min is not None
    assert len(first.successor_swarm_ids) == 1

    successor = sim.swarm(first.successor_swarm_ids[0])
    assert successor.pod_ids == ("POD00000", "POD00002", "POD00003")   # the E-bound three
    assert "POD00001" not in successor.pod_ids                        # F-bound, now alone
    assert successor.corridor.edge_ids == ("HE", "ME")
    assert sim.swarm_of_pod("POD00001") is None

    _run(sim)
    assert fleet.get_pod("POD00001").current_node_id == "F"
    for pod_id in ("POD00000", "POD00002", "POD00003"):
        assert fleet.get_pod(pod_id).current_node_id == "E"
    assert sim.trip_status_counts()["completed"] == 4


def test_splitting_leaves_a_single_continuing_pod_independent(corridor_graph, make_corridor_fleet):
    """Two pods to E and F: after H3 neither has a partner, so no successor forms."""
    _, sim = make_corridor_fleet(["E", "F"])
    _run(sim)
    swarms = sim.swarms()
    assert len(swarms) == 1
    assert swarms[0].successor_swarm_ids == ()
    assert sim.split_count == 1


def test_identical_destinations_never_need_to_split(corridor_graph, make_corridor_fleet):
    """Pods going to the same place share their whole route to the end."""
    _, sim = make_corridor_fleet(["E", "E"])
    _run(sim)
    swarm = sim.swarms()[0]
    assert swarm.corridor.edge_ids == ("C1", "C2", "C3", "HE", "ME")
    assert swarm.corridor.divergence_node_id == "E"
    assert sim.trip_status_counts()["completed"] == 2


# --- rejoining and stability ------------------------------------------------
def test_a_successor_swarm_is_a_new_swarm_with_its_own_id(corridor_graph, make_corridor_fleet):
    _, sim = make_corridor_fleet(["E", "F", "E"])
    _run(sim)
    ids = [s.swarm_id for s in sim.swarms()]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    assert ids[0] == "SW00001"
    assert sim.formation_count == len(sim.swarms())


def test_stability_window_prevents_a_short_lived_continuation(corridor_graph, make_corridor_fleet):
    """The E tail takes 10 min; demanding 12 min of stability blocks the successor,
    so a group that would disband almost at once never forms."""
    tight = SwarmConfig(min_formation_stability_min=12.0, min_shared_distance_km=3.0)
    _, sim = make_corridor_fleet(["E", "F", "E"], swarm_config=tight)
    _run(sim)
    assert len(sim.swarms()) == 1                     # the first swarm only
    assert sim.swarms()[0].successor_swarm_ids == ()

    relaxed = SwarmConfig(min_formation_stability_min=2.0)
    _, sim2 = make_corridor_fleet(["E", "F", "E"], swarm_config=relaxed)
    _run(sim2)
    assert len(sim2.swarms()) == 2                    # the successor does form


def test_formation_and_splitting_do_not_oscillate(corridor_graph, make_corridor_fleet):
    """Each swarm forms once and splits at most once; no churn tick after tick."""
    _, sim = make_corridor_fleet(["E", "F", "E", "E"])
    formed, split = [], []
    for _ in range(80):
        report = sim.tick()
        formed.extend(report.formed_swarm_ids)
        split.extend(report.split_swarm_ids)
        if not sim.has_pending_work:
            break
    assert len(formed) == len(set(formed)), "a swarm was formed twice"
    assert len(split) == len(set(split)), "a swarm was split twice"
    assert len(formed) <= 2 and len(split) <= 2


def test_pods_wait_for_partners_then_give_up(corridor_graph, make_corridor_fleet):
    """A pod holds at its origin for the formation delay, then departs alone."""
    config = SwarmConfig(max_formation_delay_min=3.0)
    fleet, sim = make_corridor_fleet(["E"], swarm_config=config)
    pod = fleet.get_pod("POD00000")

    sim.tick()                                   # trip assigned
    assert pod.status is PodStatus.ASSIGNED
    held_ticks = 0
    for _ in range(10):
        report = sim.tick()
        if report.departed_pod_ids:
            break
        assert report.held_pod_ids == ("POD00000",)
        held_ticks += 1
    assert held_ticks >= 2                       # it really waited
    assert pod.status is PodStatus.TRAVELING
    assert sim.swarms() == ()                    # nobody to platoon with


def test_zero_formation_delay_departs_immediately(corridor_graph, make_corridor_fleet):
    fleet, sim = make_corridor_fleet(["E"], swarm_config=SwarmConfig(max_formation_delay_min=0.0))
    sim.tick()
    report = sim.tick()
    assert report.departed_pod_ids == ("POD00000",)
    assert report.held_pod_ids == ()


# --- congestion changes compatibility (requirement 10) -----------------------
def test_congestion_changes_the_swarm_grouping(corridor_graph, make_corridor_fleet):
    """traffic -> route change -> shared corridor change -> compatibility change.

    Free flow: both pods run H0->H3 together and form a swarm.
    C3 congested: the E-bound pod takes the X route, they share nothing, no swarm.
    Same fleet, same trips, same thresholds — only the traffic differs.
    """
    _, clean = make_corridor_fleet(["E", "F"])
    _run(clean)
    assert len(clean.swarms()) == 1
    assert clean.swarms()[0].corridor.edge_ids == ("C1", "C2", "C3")

    congested_graph = corridor_graph
    congested_graph.set_vehicle_count("C3", 3600 * 3)
    _, congested = make_corridor_fleet(["E", "F"], graph=congested_graph)
    _run(congested)
    assert congested.swarms() == (), "congestion should have broken compatibility"
    assert congested.trip_status_counts()["completed"] == 2      # both still served, alone


def test_congestion_can_also_create_a_grouping(corridor_graph, make_corridor_fleet):
    """The reverse direction: congestion that reroutes *both* pods the same way
    keeps them compatible, on a different corridor than before."""
    corridor_graph.set_vehicle_count("C3", 3600 * 3)
    _, sim = make_corridor_fleet(["E", "E"], graph=corridor_graph)
    _run(sim)
    assert len(sim.swarms()) == 1
    assert sim.swarms()[0].corridor.edge_ids == ("X1", "X2")    # the rerouted corridor


def test_swarm_layer_does_not_change_the_congestion_model(corridor_graph, make_corridor_fleet):
    """Corridor travel time is M1's edge time, not a swarm-specific number."""
    _, sim = make_corridor_fleet(["E", "F"])
    while not sim.swarms():
        sim.tick()
    swarm = sim.swarms()[0]
    expected = sum(corridor_graph.get_edge(e).current_travel_time_min
                   for e in swarm.corridor.edge_ids)
    assert swarm.corridor.travel_time_min == pytest.approx(expected)


# --- metrics ----------------------------------------------------------------
def test_metrics_define_corridor_and_pod_km_separately(corridor_graph, make_corridor_fleet):
    """Four pods over a 12 km corridor: 12 corridor-km, 48 coordinated pod-km."""
    _, sim = make_corridor_fleet(["E", "F", "F", "F"])
    _run(sim)
    metrics = compute_swarm_metrics(sim)

    assert metrics.swarm_count >= 1
    assert metrics.max_swarm_size == 4
    assert metrics.total_shared_corridor_distance_km >= 12.0
    assert metrics.coordinated_pod_km > metrics.total_shared_corridor_distance_km
    assert metrics.coordinated_pod_km == pytest.approx(
        sum(s.coordinated_pod_km() for s in sim.swarms()))
    # Road occupancy is below raw pod-km, and the gap is the claimed benefit.
    assert metrics.road_occupancy_km < metrics.pod_distance_km
    assert metrics.coordination_benefit_km == pytest.approx(
        metrics.pod_distance_km - metrics.road_occupancy_km, abs=1e-3)
    assert metrics.formation_occupancy_factor == DEFAULT_SWARM_CONFIG.formation_occupancy_factor


def test_metrics_reconcile_with_the_simulation(city_graph):
    fleet = generate_fleet(city_graph, fleet_size=40, seed=42)
    demand = generate_demand(city_graph, seed=42, passenger_count=300)
    sim = SwarmSimulation(city_graph, fleet, demand.trips)
    sim.run()
    metrics = compute_swarm_metrics(sim)

    assert metrics.swarms_enabled is True
    assert metrics.total_pods == 40
    assert metrics.swarm_count == len(sim.swarms()) == sim.formation_count
    assert metrics.split_count == sim.split_count
    assert metrics.completed_swarm_count == sim.swarm_status_counts()["completed"]
    assert metrics.pod_distance_km == pytest.approx(
        sum(p.total_distance_km for p in fleet), abs=1e-3)
    assert metrics.independent_pod_km + metrics.coordinated_pod_km == pytest.approx(
        metrics.pod_distance_km, abs=1e-2)
    assert 0 <= metrics.distinct_pods_ever_in_a_swarm <= 40
    if metrics.swarm_count:
        assert metrics.average_swarm_size >= 2.0
        assert metrics.max_swarm_size >= 2


def test_metrics_with_swarms_disabled_report_zero_not_none(city_graph):
    fleet = generate_fleet(city_graph, fleet_size=20, seed=42)
    demand = generate_demand(city_graph, seed=42, passenger_count=100)
    sim = SwarmSimulation(city_graph, fleet, demand.trips, enable_swarms=False)
    sim.run()
    metrics = compute_swarm_metrics(sim)

    assert metrics.swarms_enabled is False
    assert metrics.swarm_count == 0 and metrics.max_swarm_size == 0
    assert metrics.average_swarm_size is None            # no swarms, no average
    assert metrics.coordinated_pod_km == 0.0
    assert metrics.total_shared_corridor_distance_km == 0.0
    assert metrics.coordination_benefit_km == pytest.approx(0.0, abs=1e-6)
    assert metrics.road_occupancy_km == pytest.approx(metrics.pod_distance_km, abs=1e-3)


def test_metrics_are_json_serialisable_and_deterministic(city_graph):
    import json
    results = []
    for _ in range(2):
        from app.network.synthetic_city import build_synthetic_city
        graph = build_synthetic_city(42).graph
        fleet = generate_fleet(graph, fleet_size=25, seed=42)
        demand = generate_demand(graph, seed=42, passenger_count=150)
        sim = SwarmSimulation(graph, fleet, demand.trips)
        sim.run()
        results.append(compute_swarm_metrics(sim).to_dict())
    assert results[0] == results[1]
    assert json.loads(json.dumps(results[0])) == results[0]


# --- controlled baseline comparison (requirement 13) ------------------------
def test_independent_vs_swarm_on_an_identical_scenario():
    from app.network.synthetic_city import build_synthetic_city
    from app.swarm import compare_modes

    trips = generate_demand(build_synthetic_city(42).graph, seed=42, passenger_count=250).trips
    comparison = compare_modes(
        lambda: build_synthetic_city(42).graph, trips,
        fleet_factory=lambda graph: generate_fleet(graph, fleet_size=40, seed=42),
        fleet_config=DEFAULT_FLEET_CONFIG,
    )

    # Only the swarm run platoons; the baseline is plain M3.
    assert comparison.independent_swarm.swarm_count == 0
    assert comparison.swarm_swarm.swarm_count > 0
    assert comparison.independent_swarm.coordination_benefit_km == pytest.approx(0.0, abs=1e-6)
    assert comparison.swarm_swarm.coordination_benefit_km > 0
    # Road occupancy is the metric coordination is meant to improve.
    assert comparison.swarm_swarm.road_occupancy_km < comparison.independent_swarm.road_occupancy_km
    # The two runs are genuinely different simulations.
    assert comparison.independent_fingerprint != comparison.swarm_fingerprint
    assert comparison.delta("completed_trips") is not None
    assert comparison.to_dict()["swarm"]["swarm"]["swarm_count"] > 0


def test_comparison_is_reproducible():
    from app.network.synthetic_city import build_synthetic_city
    from app.swarm import compare_modes

    trips = generate_demand(build_synthetic_city(42).graph, seed=42, passenger_count=150).trips
    runs = [compare_modes(lambda: build_synthetic_city(42).graph, trips,
                          fleet_factory=lambda graph: generate_fleet(graph, fleet_size=30, seed=42),
                          fleet_config=DEFAULT_FLEET_CONFIG).to_dict()
            for _ in range(2)]
    assert runs[0] == runs[1]


def test_disabled_swarms_reproduce_m3_exactly(city_graph):
    """enable_swarms=False must behave like FleetSimulation, pod for pod."""
    from app.fleet import FleetSimulation
    from app.network.synthetic_city import build_synthetic_city

    graph_a = build_synthetic_city(42).graph
    fleet_a = generate_fleet(graph_a, fleet_size=30, seed=42)
    trips_a = generate_demand(graph_a, seed=42, passenger_count=200).trips
    m3 = FleetSimulation(graph_a, fleet_a, trips_a)
    m3.run()

    graph_b = build_synthetic_city(42).graph
    fleet_b = generate_fleet(graph_b, fleet_size=30, seed=42)
    trips_b = generate_demand(graph_b, seed=42, passenger_count=200).trips
    m4 = SwarmSimulation(graph_b, fleet_b, trips_b, enable_swarms=False)
    m4.run()

    assert m4.snapshot().fingerprint() == m3.snapshot().fingerprint()
    assert [p.to_dict() for p in fleet_b] == [p.to_dict() for p in fleet_a]
    assert [r.to_dict() for r in m4.records()] == [r.to_dict() for r in m3.records()]
    assert m4.swarms() == ()


# --- determinism ------------------------------------------------------------
def _swarm_run(seed=42, pods=30, passengers=200):
    from app.network.synthetic_city import build_synthetic_city
    graph = build_synthetic_city(42).graph
    fleet = generate_fleet(graph, fleet_size=pods, seed=seed)
    demand = generate_demand(graph, seed=seed, passenger_count=passengers)
    sim = SwarmSimulation(graph, fleet, demand.trips)
    sim.run()
    return sim, fleet


def test_repeated_simulations_are_byte_identical():
    runs = []
    for _ in range(3):
        sim, fleet = _swarm_run()
        runs.append((
            sim.swarm_snapshot().fingerprint(),
            sim.snapshot().fingerprint(),
            [s.to_dict() for s in sim.swarms()],
            [p.to_dict() for p in fleet],
            compute_swarm_metrics(sim).to_dict(),
        ))
    assert all(run == runs[0] for run in runs)


def test_swarm_ids_membership_and_leaders_are_identical_across_runs():
    first, _ = _swarm_run()
    second, _ = _swarm_run()
    assert [s.swarm_id for s in first.swarms()] == [s.swarm_id for s in second.swarms()]
    assert [s.pod_ids for s in first.swarms()] == [s.pod_ids for s in second.swarms()]
    assert [s.leader_pod_id for s in first.swarms()] == [s.leader_pod_id for s in second.swarms()]
    assert first.formation_count == second.formation_count
    assert first.split_count == second.split_count


def test_tick_by_tick_histories_match():
    histories = []
    for _ in range(2):
        from app.network.synthetic_city import build_synthetic_city
        graph = build_synthetic_city(42).graph
        fleet = generate_fleet(graph, fleet_size=20, seed=42)
        demand = generate_demand(graph, seed=42, passenger_count=120)
        sim = SwarmSimulation(graph, fleet, demand.trips)
        histories.append([sim.tick().to_dict() for _ in range(250)])
    assert histories[0] == histories[1]


def test_the_swarm_layer_uses_no_randomness():
    import random
    from app.network.synthetic_city import build_synthetic_city
    graph = build_synthetic_city(42).graph
    fleet = generate_fleet(graph, fleet_size=20, seed=42)
    demand = generate_demand(graph, seed=42, passenger_count=120)

    random.seed(777)
    expected = [random.random() for _ in range(3)]
    random.seed(777)
    SwarmSimulation(graph, fleet, demand.trips).run()
    assert [random.random() for _ in range(3)] == expected


def test_determinism_across_separate_processes():
    script = (
        "from app.network.synthetic_city import build_synthetic_city;"
        "from app.demand import generate_demand;"
        "from app.fleet import generate_fleet;"
        "from app.swarm import SwarmSimulation, compute_swarm_metrics;"
        "g=build_synthetic_city(42).graph;"
        "f=generate_fleet(g, fleet_size=30, seed=42);"
        "d=generate_demand(g, seed=42, passenger_count=200);"
        "s=SwarmSimulation(g, f, d.trips); s.run();"
        "m=compute_swarm_metrics(s);"
        "print(s.swarm_snapshot().fingerprint(), m.swarm_count, m.split_count, m.coordination_benefit_km)"
    )
    outputs = {subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                              check=True).stdout.strip() for _ in range(2)}
    sim, _ = _swarm_run()
    metrics = compute_swarm_metrics(sim)
    expected = (f"{sim.swarm_snapshot().fingerprint()} {metrics.swarm_count} "
                f"{metrics.split_count} {metrics.coordination_benefit_km}")
    assert len(outputs) == 1
    assert outputs.pop() == expected


def test_different_seeds_diverge():
    a, _ = _swarm_run(seed=42)
    b, _ = _swarm_run(seed=7)
    assert a.swarm_snapshot().fingerprint() != b.swarm_snapshot().fingerprint()


# --- edge cases -------------------------------------------------------------
def test_empty_fleet_forms_no_swarms(city_graph):
    from app.fleet import PodFleet
    fleet = PodFleet(city_graph)
    demand = generate_demand(city_graph, seed=42, passenger_count=20)
    sim = SwarmSimulation(city_graph, fleet, demand.trips, FleetConfig(max_trip_wait_min=5.0))
    report = sim.run()
    assert sim.swarms() == () and sim.formation_count == 0 and sim.split_count == 0
    assert report.stopped_because == "no pending work"
    metrics = compute_swarm_metrics(sim)
    assert metrics.total_pods == 0 and metrics.swarm_count == 0
    assert metrics.swarm_participation_percent is None
    assert sim.swarm_snapshot().swarms == ()


def test_zero_trips_forms_no_swarms(city_graph):
    fleet = generate_fleet(city_graph, fleet_size=10, seed=42)
    before = [p.to_dict() for p in fleet]
    sim = SwarmSimulation(city_graph, fleet, [])
    report = sim.run()
    assert report.ticks == 0 and sim.swarms() == ()
    assert [p.to_dict() for p in fleet] == before
    assert sim.formation_attempts == 0


def test_a_single_pod_never_forms_a_swarm(corridor_graph, make_corridor_fleet):
    fleet, sim = make_corridor_fleet(["E"])
    sim.run()
    assert sim.swarms() == () and sim.formation_count == 0
    assert fleet.get_pod("POD00000").current_node_id == "E"      # it still travelled
    assert sim.trip_status_counts()["completed"] == 1


def test_unknown_swarm_lookup_raises(city_graph):
    fleet = generate_fleet(city_graph, fleet_size=5, seed=42)
    sim = SwarmSimulation(city_graph, fleet, [])
    with pytest.raises(SwarmNotFoundError, match="no swarm with id"):
        sim.swarm("SW99999")
    assert sim.swarm_of_pod("POD00000") is None


@pytest.mark.parametrize("bad", [{"min_shared_edges": 2}, "default", 3])
def test_bad_swarm_config_rejected(city_graph, bad):
    fleet = generate_fleet(city_graph, fleet_size=5, seed=42)
    with pytest.raises(SwarmConfigError, match="must be a SwarmConfig"):
        SwarmSimulation(city_graph, fleet, [], swarm_config=bad)


def test_enable_swarms_must_be_a_bool(city_graph):
    fleet = generate_fleet(city_graph, fleet_size=5, seed=42)
    with pytest.raises(SwarmConfigError, match="must be a bool"):
        SwarmSimulation(city_graph, fleet, [], enable_swarms="yes")


def test_swarm_records_are_never_deleted(city_graph):
    fleet = generate_fleet(city_graph, fleet_size=30, seed=42)
    demand = generate_demand(city_graph, seed=42, passenger_count=200)
    sim = SwarmSimulation(city_graph, fleet, demand.trips)
    sim.run()
    assert len(sim.swarms()) == sim.formation_count
    assert all(s.status is SwarmStatus.COMPLETED for s in sim.swarms())
    assert sim.swarm_status_counts()["completed"] == len(sim.swarms())


# --- rebalancing hook (requirement 11) --------------------------------------
def test_rebalancer_plans_but_never_executes(city_graph):
    from app.swarm import REASON_UNSERVED_DEMAND, RepositionRequest, SurplusDeficitRebalancer

    fleet = generate_fleet(city_graph, fleet_size=40, seed=42)
    demand = generate_demand(city_graph, seed=42, passenger_count=300)
    sim = SwarmSimulation(city_graph, fleet, demand.trips)
    sim.run()

    pods_before = [p.to_dict() for p in fleet]
    records_before = [r.to_dict() for r in sim.records()]
    traffic_before = {e.edge_id: e.current_vehicle_count for e in city_graph.edges()}

    requests = SurplusDeficitRebalancer(max_requests=5).plan(fleet, sim.records(), city_graph)

    assert requests and len(requests) <= 5
    assert all(isinstance(r, RepositionRequest) for r in requests)
    assert all(r.reason == REASON_UNSERVED_DEMAND for r in requests)
    assert all(r.from_node_id != r.to_node_id for r in requests)
    assert all(city_graph.has_node(r.to_node_id) for r in requests)
    assert [r.request_id for r in requests] == [f"RB{i + 1:05d}" for i in range(len(requests))]
    # Nothing was executed: no pod moved, no record changed, no traffic changed.
    assert [p.to_dict() for p in fleet] == pods_before
    assert [r.to_dict() for r in sim.records()] == records_before
    assert {e.edge_id: e.current_vehicle_count for e in city_graph.edges()} == traffic_before


def test_rebalancer_is_deterministic(city_graph):
    from app.swarm import SurplusDeficitRebalancer
    fleet = generate_fleet(city_graph, fleet_size=30, seed=42)
    demand = generate_demand(city_graph, seed=42, passenger_count=200)
    sim = SwarmSimulation(city_graph, fleet, demand.trips)
    sim.run()
    planner = SurplusDeficitRebalancer(max_requests=8)
    first = [r.to_dict() for r in planner.plan(fleet, sim.records(), city_graph)]
    second = [r.to_dict() for r in planner.plan(fleet, sim.records(), city_graph)]
    assert first == second


def test_reposition_request_validation():
    from app.errors import ModelValidationError
    from app.swarm import RepositionRequest
    with pytest.raises(ModelValidationError, match="must differ"):
        RepositionRequest(request_id="RB1", pod_id="POD00000", from_node_id="a",
                          to_node_id="a", reason="x")
    with pytest.raises(ModelValidationError):
        RepositionRequest(request_id="", pod_id="POD00000", from_node_id="a",
                          to_node_id="b", reason="x")
    with pytest.raises(ModelValidationError):
        RepositionRequest(request_id="RB1", pod_id="POD00000", from_node_id="a",
                          to_node_id="b", reason="x", priority=-1)


def test_rebalancer_respects_its_request_cap(city_graph):
    from app.swarm import SurplusDeficitRebalancer
    fleet = generate_fleet(city_graph, fleet_size=30, seed=42)
    demand = generate_demand(city_graph, seed=42, passenger_count=200)
    sim = SwarmSimulation(city_graph, fleet, demand.trips)
    sim.run()
    assert SurplusDeficitRebalancer(max_requests=0).plan(fleet, sim.records(), city_graph) == ()
    assert len(SurplusDeficitRebalancer(max_requests=2).plan(fleet, sim.records(), city_graph)) <= 2


# --- layering and M1/M2/M3 regression ---------------------------------------
def test_lower_layers_do_not_import_the_swarm_layer():
    """models <- network <- routing <- simulation <- demand <- fleet <- swarm."""
    from app.config import PROJECT_ROOT
    offenders = []
    for layer in ("models", "network", "routing", "simulation", "demand", "fleet"):
        for path in sorted((PROJECT_ROOT / "app" / layer).rglob("*.py")):
            if "app.swarm" in path.read_text(encoding="utf-8"):
                offenders.append(str(path.relative_to(PROJECT_ROOT)))
    assert offenders == [], f"lower layers import the swarm layer: {offenders}"


def test_m3_pod_status_still_has_no_swarm_states():
    """M4 adds no pod states: swarm membership lives in the swarm layer."""
    from app.fleet import ALLOWED_TRANSITIONS
    assert {s.value for s in PodStatus} == {"idle", "assigned", "traveling", "arrived", "charging"}
    assert set(ALLOWED_TRANSITIONS) == set(PodStatus)


def test_a_swarm_run_leaves_the_network_and_demand_untouched(city_graph):
    traffic_before = {e.edge_id: e.current_vehicle_count for e in city_graph.edges()}
    demand = generate_demand(city_graph, seed=42, passenger_count=150)
    trips_before = [t.to_dict() for t in demand.trips]
    demand_fingerprint = demand.snapshot().fingerprint()

    fleet = generate_fleet(city_graph, fleet_size=30, seed=42)
    SwarmSimulation(city_graph, fleet, demand.trips).run()

    assert {e.edge_id: e.current_vehicle_count for e in city_graph.edges()} == traffic_before
    assert [t.to_dict() for t in demand.trips] == trips_before
    assert demand.snapshot().fingerprint() == demand_fingerprint


def test_routing_still_agrees_after_a_swarm_run(city_graph):
    from app.routing import astar, dijkstra
    fleet = generate_fleet(city_graph, fleet_size=20, seed=42)
    demand = generate_demand(city_graph, seed=42, passenger_count=120)
    SwarmSimulation(city_graph, fleet, demand.trips).run()
    for origin, destination in (("north_station", "airport"), ("west_hub", "tech_park")):
        a, d = astar(city_graph, origin, destination), dijkstra(city_graph, origin, destination)
        assert a.total_cost == pytest.approx(d.total_cost, rel=1e-12, abs=1e-9)


def test_battery_and_seat_rules_still_hold_in_a_swarm(corridor_graph, make_corridor_fleet):
    """Coordination grants no free battery and moves no passenger between pods."""
    fleet, sim = make_corridor_fleet(["E", "F", "E"], party_size=3)
    sim.run()
    for pod in fleet:
        assert 0.0 <= pod.battery_percent <= 100.0
        assert pod.occupied_seats == 0                 # released after arrival
        assert pod.capacity == 4
    for record in sim.records():
        assert record.party_size == 3                  # nobody was moved or merged


# --- performance ------------------------------------------------------------
def test_one_hundred_pods_one_thousand_trips(city_graph):
    """Requirement: 100 pods, 1,000 trips, ~1,500 ticks, stdlib only."""
    fleet = generate_fleet(city_graph, fleet_size=100, seed=42)
    demand = generate_demand(city_graph, seed=42, passenger_count=1000)
    sim = SwarmSimulation(city_graph, fleet, demand.trips)

    started = time.perf_counter()
    report = sim.run()
    elapsed = time.perf_counter() - started

    metrics = compute_swarm_metrics(sim)
    assert report.ticks >= 1000
    assert metrics.total_pods == 100
    assert metrics.swarm_count > 0
    assert report.stopped_because == "no pending work"
    assert elapsed < 180.0, f"100 pods / 1000 trips took {elapsed:.1f}s"


# --- CLI --------------------------------------------------------------------
def test_swarm_demo_cli_runs_and_explains_itself(capsys):
    from app.cli.main import main
    assert main(["swarm-demo", "--pods", "30", "--passengers", "200"]) == 0
    out = capsys.readouterr().out
    assert "No AI/LLM is involved" in out
    assert "coordination layer over individual pods, not one vehicle" in out
    assert "Swarms formed:" in out
    assert "Average size:" in out
    assert "Largest:" in out
    assert "Splits at divergence:" in out
    assert "Total shared corridor:" in out
    assert "Independent (M3) vs swarm (M4)" in out
    assert "no fuel, energy or emissions saving is claimed" in out
    assert "Rebalancing hook:" in out
    assert "belongs to M5" in out
    assert "Swarm fingerprint:" in out


def test_swarm_demo_cli_is_reproducible(capsys):
    from app.cli.main import main

    def fingerprint():
        main(["swarm-demo", "--pods", "25", "--passengers", "150"])
        for line in capsys.readouterr().out.splitlines():
            if line.startswith("Swarm fingerprint:"):
                return line.split(":", 1)[1].strip()
        raise AssertionError("no fingerprint in output")

    assert fingerprint() == fingerprint()


def test_swarm_demo_cli_options(capsys):
    from app.cli.main import main
    assert main(["swarm-demo", "--pods", "20", "--passengers", "100", "--formation-delay", "10",
                 "--max-swarm-size", "3", "--examples", "1", "--profile", "peak_hour"]) == 0
    out = capsys.readouterr().out
    assert "wait <= 10 min" in out
    assert "<= 3 pods" in out
    assert "peak_hour" in out


def test_existing_cli_commands_still_work(capsys):
    from app.cli.main import main
    assert main(["route", "--from", "North Station", "--to", "Airport", "--algorithm", "both"]) == 0
    assert "Optimal cost match (A* vs Dijkstra): YES" in capsys.readouterr().out
    assert main(["demand-demo", "--passengers", "100"]) == 0
    assert "Demand fingerprint:" in capsys.readouterr().out
    assert main(["fleet-demo", "--pods", "20", "--passengers", "100"]) == 0
    assert "Fleet fingerprint:" in capsys.readouterr().out
    assert main(["info"]) == 0
    assert "node_count: 22" in capsys.readouterr().out
