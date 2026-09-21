"""M3 fleet initialisation and the PodFleet container."""

import subprocess
import sys

import pytest

from app.demand.config import BASELINE_DEMAND_PROFILE, PlaceRole
from app.errors import (
    DuplicatePodError,
    FleetConfigError,
    NodeNotFoundError,
    PodNotFoundError,
    PodStateError,
)
from app.fleet import (
    DEFAULT_FLEET_CONFIG,
    FleetConfig,
    Pod,
    PodFleet,
    PodStatus,
    depot_weights,
    generate_fleet,
    pod_id_for,
)


# --- determinism -------------------------------------------------------------
def test_fleet_generation_is_deterministic(city_graph):
    a = generate_fleet(city_graph, fleet_size=60, seed=42)
    b = generate_fleet(city_graph, fleet_size=60, seed=42)
    assert [p.to_dict() for p in a] == [p.to_dict() for p in b]


def test_different_seeds_place_pods_differently(city_graph):
    a = generate_fleet(city_graph, fleet_size=60, seed=42)
    b = generate_fleet(city_graph, fleet_size=60, seed=43)
    assert [p.current_node_id for p in a] != [p.current_node_id for p in b]
    assert [p.pod_id for p in a] == [p.pod_id for p in b]      # ids do not depend on the seed


def test_generation_does_not_touch_global_random_state(city_graph):
    import random
    random.seed(4321)
    expected = [random.random() for _ in range(3)]
    random.seed(4321)
    generate_fleet(city_graph, fleet_size=50, seed=42)
    assert [random.random() for _ in range(3)] == expected


def test_fleet_generation_is_deterministic_across_processes(city_graph):
    script = (
        "from app.network.synthetic_city import build_synthetic_city;"
        "from app.fleet import generate_fleet;"
        "f=generate_fleet(build_synthetic_city(42).graph, fleet_size=60, seed=42);"
        "print('|'.join(p.current_node_id for p in f))"
    )
    outputs = {subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                              check=True).stdout.strip() for _ in range(2)}
    in_process = "|".join(p.current_node_id for p in generate_fleet(city_graph, fleet_size=60, seed=42))
    assert len(outputs) == 1 and outputs.pop() == in_process


# --- pod ids ----------------------------------------------------------------
def test_pod_ids_are_stable_sortable_and_unique(city_graph):
    fleet = generate_fleet(city_graph, fleet_size=12, seed=42)
    assert fleet.pod_ids() == tuple(f"POD{i:05d}" for i in range(12))
    assert len(set(fleet.pod_ids())) == 12
    # lexicographic order equals numeric order, which is what determinism relies on
    assert list(fleet.pod_ids()) == sorted(fleet.pod_ids())


def test_pod_ids_are_a_prefix_when_the_fleet_grows(city_graph):
    small = generate_fleet(city_graph, fleet_size=10, seed=42)
    large = generate_fleet(city_graph, fleet_size=40, seed=42)
    assert [p.to_dict() for p in large.pods()[:10]] == [p.to_dict() for p in small.pods()]


def test_pod_id_for_rejects_bad_indexes():
    assert pod_id_for(0) == "POD00000" and pod_id_for(99999) == "POD99999"
    for bad in (-1, 1.5, "3", True):
        with pytest.raises(FleetConfigError):
            pod_id_for(bad)


# --- size, placement, capacity ----------------------------------------------
@pytest.mark.parametrize("size", [0, 1, 7, 100])
def test_fleet_size_is_exactly_as_requested(city_graph, size):
    fleet = generate_fleet(city_graph, fleet_size=size, seed=42)
    assert len(fleet) == size and len(fleet.pods()) == size


def test_every_pod_starts_on_a_real_network_node(city_graph):
    fleet = generate_fleet(city_graph, fleet_size=100, seed=42)
    known = {n.node_id for n in city_graph.nodes()}
    assert {p.current_node_id for p in fleet} <= known
    assert all(city_graph.has_node(p.current_node_id) for p in fleet)


def test_every_pod_starts_idle_full_and_empty(city_graph):
    fleet = generate_fleet(city_graph, fleet_size=30, seed=42)
    assert all(p.status is PodStatus.IDLE for p in fleet)
    assert all(p.battery_percent == DEFAULT_FLEET_CONFIG.initial_battery_percent for p in fleet)
    assert all(p.occupied_seats == 0 and p.assigned_trip_id is None for p in fleet)
    assert fleet.count_by_status() == {"idle": 30, "assigned": 0, "traveling": 0,
                                       "arrived": 0, "charging": 0}


def test_placement_follows_depot_role_weights_not_uniform(city_graph):
    """Hubs are weighted highest, plain junctions lowest: a uniform sampler would
    make these comparable."""
    fleet = generate_fleet(city_graph, fleet_size=400, seed=42)
    per_role = {role: 0 for role in PlaceRole}
    for pod in fleet:
        role = BASELINE_DEMAND_PROFILE.role_for(pod.current_node_id,
                                                city_graph.get_node(pod.current_node_id).node_type)
        per_role[role] += 1
    assert per_role[PlaceRole.HUB] > per_role[PlaceRole.THROUGH]
    assert per_role[PlaceRole.RESIDENTIAL] > 0 and per_role[PlaceRole.EMPLOYMENT] > 0


def test_depot_weights_are_sorted_and_cover_every_node(city_graph):
    weights = depot_weights(city_graph)
    assert [node_id for node_id, _ in weights] == sorted(n.node_id for n in city_graph.nodes())
    assert all(weight >= 0 for _, weight in weights)


def test_capacity_defaults_and_overrides(city_graph):
    assert all(p.capacity == 4 for p in generate_fleet(city_graph, fleet_size=5, seed=42))
    assert all(p.capacity == 6 for p in generate_fleet(city_graph, fleet_size=5, seed=42, capacity=6))
    config = FleetConfig(default_pod_capacity=2)
    assert all(p.capacity == 2 for p in generate_fleet(city_graph, fleet_size=5, seed=42, config=config))


@pytest.mark.parametrize("size", [-1, 1.5, "10", True])
def test_invalid_fleet_size_rejected(city_graph, size):
    with pytest.raises(FleetConfigError):
        generate_fleet(city_graph, fleet_size=size, seed=42)


@pytest.mark.parametrize("capacity", [0, -1, 1.5, True])
def test_invalid_capacity_rejected(city_graph, capacity):
    with pytest.raises(FleetConfigError):
        generate_fleet(city_graph, fleet_size=5, seed=42, capacity=capacity)


@pytest.mark.parametrize("seed", [1.5, "42", True, None])
def test_invalid_seed_rejected(city_graph, seed):
    with pytest.raises(FleetConfigError):
        generate_fleet(city_graph, fleet_size=5, seed=seed)


def test_generate_rejects_a_non_config(city_graph):
    with pytest.raises(FleetConfigError):
        generate_fleet(city_graph, fleet_size=5, seed=42, config={"capacity": 4})


def test_cannot_place_pods_on_an_empty_network():
    from app.network.graph import NetworkGraph
    with pytest.raises(FleetConfigError, match="no nodes"):
        generate_fleet(NetworkGraph(), fleet_size=5, seed=42)


def test_cannot_place_pods_when_no_node_has_weight(city_graph):
    """A config that zeroes every role must fail loudly, not place pods arbitrarily."""
    config = FleetConfig(depot_role_weights={PlaceRole.HUB: 0.0, PlaceRole.RESIDENTIAL: 1.0})
    only_hubs = FleetConfig(depot_role_weights={PlaceRole.HUB: 1.0})
    assert generate_fleet(city_graph, fleet_size=3, seed=42, config=config)
    assert all(BASELINE_DEMAND_PROFILE.role_for(
        p.current_node_id, city_graph.get_node(p.current_node_id).node_type) is PlaceRole.HUB
        for p in generate_fleet(city_graph, fleet_size=5, seed=42, config=only_hubs))


# --- PodFleet container -----------------------------------------------------
def test_add_get_and_contains(city_graph):
    fleet = PodFleet(city_graph)
    pod = Pod(pod_id="POD00000", capacity=4, current_node_id="market")
    fleet.add_pod(pod)
    assert len(fleet) == 1 and "POD00000" in fleet
    assert fleet.get_pod("POD00000") is pod
    assert fleet.has_pod("POD00000") and not fleet.has_pod("POD00099")


def test_duplicate_pod_id_rejected(city_graph):
    fleet = PodFleet(city_graph, [Pod(pod_id="POD00000", capacity=4, current_node_id="market")])
    with pytest.raises(DuplicatePodError, match="already in the fleet"):
        fleet.add_pod(Pod(pod_id="POD00000", capacity=4, current_node_id="airport"))


def test_pod_on_a_nonexistent_node_rejected(city_graph):
    """The fleet holds the graph, so this is the layer that checks node existence."""
    fleet = PodFleet(city_graph)
    with pytest.raises(NodeNotFoundError, match="not a network node"):
        fleet.add_pod(Pod(pod_id="POD00000", capacity=4, current_node_id="atlantis"))


def test_get_unknown_pod_raises(city_fleet):
    with pytest.raises(PodNotFoundError, match="no pod with id"):
        city_fleet.get_pod("POD99999")


def test_add_rejects_non_pod(city_graph):
    from app.errors import ModelValidationError
    with pytest.raises(ModelValidationError):
        PodFleet(city_graph).add_pod("POD00000")


def test_listing_is_always_in_pod_id_order(city_graph):
    """Insertion order must not leak into results."""
    pods = [Pod(pod_id=f"POD{i:05d}", capacity=4, current_node_id="market") for i in (5, 1, 9, 0)]
    fleet = PodFleet(city_graph, pods)
    assert fleet.pod_ids() == ("POD00000", "POD00001", "POD00005", "POD00009")
    assert [p.pod_id for p in fleet] == list(fleet.pod_ids())
    assert [p.pod_id for p in fleet.pods()] == sorted(p.pod_id for p in pods)


def test_counts_and_seat_totals(city_graph, make_trip):
    from app.models.route import Route
    fleet = PodFleet(city_graph, [
        Pod(pod_id="POD00000", capacity=4, current_node_id="market"),
        Pod(pod_id="POD00001", capacity=6, current_node_id="airport"),
    ])
    assert fleet.total_capacity() == 10 and fleet.occupied_seats() == 0
    route = Route(origin="market", destination="airport", node_ids=("market", "airport"),
                  edge_ids=("E016",), total_distance_km=4.0, total_travel_time_min=5.0,
                  total_cost=5.0, cost_metric="travel_time_min", nodes_visited=2, algorithm="astar")
    fleet.assign_trip("POD00000", "T1", route, party_size=3)
    assert fleet.occupied_seats() == 3
    assert fleet.count_by_status()["assigned"] == 1 and fleet.count_by_status()["idle"] == 1
    assert [p.pod_id for p in fleet.idle_pods()] == ["POD00001"]
    assert [p.pod_id for p in fleet.pods_with_status(PodStatus.ASSIGNED)] == ["POD00000"]


def test_release_through_the_fleet(city_graph):
    from app.models.route import Route
    fleet = PodFleet(city_graph, [Pod(pod_id="POD00000", capacity=4, current_node_id="market")])
    route = Route(origin="market", destination="airport", node_ids=("market", "airport"),
                  edge_ids=("E016",), total_distance_km=4.0, total_travel_time_min=5.0,
                  total_cost=5.0, cost_metric="travel_time_min", nodes_visited=2, algorithm="astar")
    fleet.assign_trip("POD00000", "T1", route, party_size=2)
    assert fleet.release_pod("POD00000") == "T1"
    assert fleet.get_pod("POD00000").is_idle and fleet.occupied_seats() == 0


def test_remove_pod_only_when_safe(city_graph):
    from app.models.route import Route
    fleet = PodFleet(city_graph, [
        Pod(pod_id="POD00000", capacity=4, current_node_id="market"),
        Pod(pod_id="POD00001", capacity=4, current_node_id="market"),
    ])
    assert fleet.remove_pod("POD00001").pod_id == "POD00001"
    assert len(fleet) == 1

    route = Route(origin="market", destination="airport", node_ids=("market", "airport"),
                  edge_ids=("E016",), total_distance_km=4.0, total_travel_time_min=5.0,
                  total_cost=5.0, cost_metric="travel_time_min", nodes_visited=2, algorithm="astar")
    fleet.assign_trip("POD00000", "T1", route, party_size=1)
    with pytest.raises(PodStateError, match="cannot be removed"):
        fleet.remove_pod("POD00000")        # mid-trip pods are not abandoned
    assert fleet.has_pod("POD00000")


def test_removing_a_charging_pod_is_allowed(city_graph):
    fleet = PodFleet(city_graph, [Pod(pod_id="POD00000", capacity=4,
                                      current_node_id="market", battery_percent=5.0)])
    fleet.get_pod("POD00000").begin_charging()
    assert fleet.remove_pod("POD00000").pod_id == "POD00000"


def test_removing_an_unknown_pod_raises(city_fleet):
    with pytest.raises(PodNotFoundError):
        city_fleet.remove_pod("POD99999")


def test_fleet_to_dict_is_deterministic(city_fleet):
    assert city_fleet.to_dict() == city_fleet.to_dict()
    assert city_fleet.to_dict()["pod_count"] == len(city_fleet)
    assert [p["pod_id"] for p in city_fleet.to_dict()["pods"]] == list(city_fleet.pod_ids())


# --- FleetConfig validation -------------------------------------------------
@pytest.mark.parametrize("field,value", [
    ("default_pod_capacity", 0), ("default_pod_capacity", 1.5),
    ("tick_minutes", 0.0), ("tick_minutes", -1.0), ("tick_minutes", float("nan")),
    ("base_energy_kwh_per_km", 0.0), ("battery_capacity_kwh", 0.0),
    ("initial_battery_percent", 101.0), ("low_battery_percent", -1.0),
    ("charge_percent_per_min", 0.0), ("assignment_battery_reserve_percent", 101.0),
    ("max_trip_wait_min", 0.0), ("max_trip_wait_min", -5.0),
])
def test_invalid_fleet_config_rejected(field, value):
    with pytest.raises(FleetConfigError):
        FleetConfig(**{field: value})


def test_charge_target_must_exceed_low_threshold():
    with pytest.raises(FleetConfigError, match="must exceed"):
        FleetConfig(low_battery_percent=50.0, target_charge_percent=40.0)


def test_unknown_depot_role_rejected():
    with pytest.raises(FleetConfigError, match="unknown role"):
        FleetConfig(depot_role_weights={"spaceport": 1.0})
    with pytest.raises(FleetConfigError, match="positive weight"):
        FleetConfig(depot_role_weights={PlaceRole.HUB: 0.0})


def test_energy_helpers_match_the_documented_formula():
    config = FleetConfig(base_energy_kwh_per_km=0.2, battery_capacity_kwh=50.0)
    assert config.energy_kwh_for(10.0, 1.0) == pytest.approx(2.0)
    assert config.energy_kwh_for(10.0, 2.0) == pytest.approx(4.0)      # congestion doubles it
    assert config.battery_drop_percent_for(10.0, 1.0) == pytest.approx(4.0)
    assert config.percent_per_kwh == pytest.approx(2.0)


def test_config_dict_documents_the_energy_model_as_an_approximation():
    data = DEFAULT_FLEET_CONFIG.to_dict()
    assert "SYNTHETIC" in data["data_provenance"]
    assert "approximation" in data["energy_model"]["note"]
    assert "congestion_multiplier" in data["energy_model"]["formula"]
