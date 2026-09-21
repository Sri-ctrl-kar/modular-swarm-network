"""M3 pod-to-trip assignment: eligibility, capacity, battery and determinism."""

import pytest

from app.errors import AssignmentError
from app.fleet import (
    NO_BATTERY,
    NO_CAPACITY,
    NO_POD_AT_ORIGIN,
    FleetConfig,
    Pod,
    PodFleet,
    PodStatus,
    assign_trip,
    candidate_pods,
    eligible_pods,
    generate_fleet,
    select_pod,
)
from app.routing import find_route


def _fleet_at(graph, node_id, count=3, **pod_kwargs):
    """A fleet of pods all parked at one node, ids POD00000..."""
    return PodFleet(graph, [Pod(pod_id=f"POD{i:05d}", capacity=4, current_node_id=node_id,
                                **pod_kwargs) for i in range(count)])


# --- the happy path ----------------------------------------------------------
def test_assignment_attaches_the_trip_and_its_route(city_graph, make_trip):
    fleet = _fleet_at(city_graph, "market")
    trip = make_trip("market", "airport", party_size=2)

    result = assign_trip(fleet, city_graph, trip)
    pod = fleet.get_pod(result.pod_id)

    assert result.is_assigned and result.reason is None
    assert pod.status is PodStatus.ASSIGNED
    assert pod.assigned_trip_id == "T000000"
    assert pod.occupied_seats == 2 and pod.available_seats == 2
    # The attached route is exactly what M1's router returns — nothing re-derived.
    expected = find_route(city_graph, "market", "airport", "astar")
    assert pod.route.to_dict() == expected.to_dict()
    assert result.route.to_dict() == expected.to_dict()
    assert pod.remaining_edge_ids == expected.edge_ids


def test_assignment_picks_the_lowest_eligible_pod_id(city_graph, make_trip):
    fleet = _fleet_at(city_graph, "market", count=5)
    assert assign_trip(fleet, city_graph, make_trip("market", "airport")).pod_id == "POD00000"
    # POD00000 is now busy, so the next trip goes to the next lowest.
    assert assign_trip(fleet, city_graph, make_trip("market", "airport", trip_id="T000001")
                       ).pod_id == "POD00001"


def test_assignment_order_ignores_insertion_order(city_graph, make_trip):
    """Building the fleet backwards must not change who gets the trip."""
    forward = PodFleet(city_graph, [Pod(pod_id=f"POD{i:05d}", capacity=4, current_node_id="market")
                                    for i in (0, 1, 2)])
    backward = PodFleet(city_graph, [Pod(pod_id=f"POD{i:05d}", capacity=4, current_node_id="market")
                                     for i in (2, 1, 0)])
    trip = make_trip("market", "airport")
    assert assign_trip(forward, city_graph, trip).pod_id == \
        assign_trip(backward, city_graph, trip).pod_id == "POD00000"


def test_repeated_assignment_is_reproducible(city_graph, make_trip):
    trip = make_trip("market", "airport", party_size=3)
    results = []
    for _ in range(3):
        fleet = generate_fleet(city_graph, fleet_size=40, seed=42)
        result = assign_trip(fleet, city_graph, trip)
        results.append((result.pod_id, result.route.to_dict()))
    assert all(r == results[0] for r in results)


def test_select_pod_takes_the_first_of_a_sorted_tuple(city_graph):
    fleet = _fleet_at(city_graph, "market", count=3)
    pods = fleet.pods()
    assert select_pod(pods) is pods[0]
    assert select_pod(()) is None


# --- eligibility: location --------------------------------------------------
def test_no_pod_at_the_origin_means_no_assignment(city_graph, make_trip):
    """M3 does no repositioning: a pod elsewhere is not eligible."""
    fleet = _fleet_at(city_graph, "airport")
    result = assign_trip(fleet, city_graph, make_trip("market", "airport"))

    assert not result.is_assigned
    assert result.reason == NO_POD_AT_ORIGIN
    assert result.route is not None          # the route exists; the pod does not
    assert all(p.is_idle for p in fleet)     # nothing was mutated


def test_candidate_pods_filters_by_node_and_idleness(city_graph, make_trip):
    fleet = PodFleet(city_graph, [
        Pod(pod_id="POD00000", capacity=4, current_node_id="market"),
        Pod(pod_id="POD00001", capacity=4, current_node_id="airport"),
        Pod(pod_id="POD00002", capacity=4, current_node_id="market"),
    ])
    trip = make_trip("market", "airport")
    assert [p.pod_id for p in candidate_pods(fleet, trip)] == ["POD00000", "POD00002"]

    assign_trip(fleet, city_graph, trip)     # POD00000 becomes busy
    assert [p.pod_id for p in candidate_pods(fleet, trip)] == ["POD00002"]


def test_busy_and_charging_pods_are_never_candidates(city_graph, make_trip):
    fleet = _fleet_at(city_graph, "market", count=2)
    fleet.get_pod("POD00001").begin_charging()
    trip = make_trip("market", "airport")
    assert [p.pod_id for p in candidate_pods(fleet, trip)] == ["POD00000"]
    assign_trip(fleet, city_graph, trip)
    assert candidate_pods(fleet, make_trip("market", "airport", trip_id="T1")) == ()


# --- eligibility: capacity --------------------------------------------------
def test_assignment_rejected_when_party_exceeds_capacity(city_graph, make_trip):
    fleet = PodFleet(city_graph, [Pod(pod_id="POD00000", capacity=2, current_node_id="market")])
    result = assign_trip(fleet, city_graph, make_trip("market", "airport", party_size=3))

    assert not result.is_assigned
    assert result.reason == NO_CAPACITY
    assert fleet.get_pod("POD00000").is_idle and fleet.get_pod("POD00000").occupied_seats == 0


def test_a_bigger_pod_takes_the_party_a_smaller_one_cannot(city_graph, make_trip):
    fleet = PodFleet(city_graph, [
        Pod(pod_id="POD00000", capacity=2, current_node_id="market"),
        Pod(pod_id="POD00001", capacity=6, current_node_id="market"),
    ])
    result = assign_trip(fleet, city_graph, make_trip("market", "airport", party_size=5))
    assert result.pod_id == "POD00001"       # lowest id that actually fits, not simply lowest


@pytest.mark.parametrize("party_size,capacity,expected", [(1, 4, True), (4, 4, True), (5, 4, False)])
def test_capacity_boundary(city_graph, make_trip, party_size, capacity, expected):
    fleet = PodFleet(city_graph, [Pod(pod_id="POD00000", capacity=capacity,
                                      current_node_id="market")])
    result = assign_trip(fleet, city_graph, make_trip("market", "airport", party_size=party_size))
    assert result.is_assigned is expected


# --- eligibility: battery ---------------------------------------------------
def test_a_flat_pod_is_not_eligible(city_graph, make_trip):
    fleet = _fleet_at(city_graph, "market", count=1, battery_percent=1.0)
    result = assign_trip(fleet, city_graph, make_trip("market", "airport"))
    assert not result.is_assigned and result.reason == NO_BATTERY
    assert fleet.get_pod("POD00000").is_idle


def test_a_charged_pod_is_preferred_over_a_flat_one(city_graph, make_trip):
    fleet = PodFleet(city_graph, [
        Pod(pod_id="POD00000", capacity=4, current_node_id="market", battery_percent=2.0),
        Pod(pod_id="POD00001", capacity=4, current_node_id="market", battery_percent=100.0),
    ])
    assert assign_trip(fleet, city_graph, make_trip("market", "airport")).pod_id == "POD00001"


def test_battery_reserve_is_respected(city_graph, make_trip):
    """A pod with just enough for the route but not the reserve stays put."""
    from app.fleet.movement import estimated_route_battery_percent
    route = find_route(city_graph, "market", "airport", "astar")
    needed = estimated_route_battery_percent(city_graph, route)

    generous = FleetConfig(assignment_battery_reserve_percent=0.0)
    strict = FleetConfig(assignment_battery_reserve_percent=50.0)
    trip = make_trip("market", "airport")

    just_enough = _fleet_at(city_graph, "market", count=1, battery_percent=needed + 0.01)
    assert assign_trip(just_enough, city_graph, trip, generous).is_assigned
    too_tight = _fleet_at(city_graph, "market", count=1, battery_percent=needed + 0.01)
    assert assign_trip(too_tight, city_graph, trip, strict).reason == NO_BATTERY


def test_eligible_pods_applies_capacity_and_battery_together(city_graph, make_trip):
    fleet = PodFleet(city_graph, [
        Pod(pod_id="POD00000", capacity=1, current_node_id="market"),                    # too small
        Pod(pod_id="POD00001", capacity=4, current_node_id="market", battery_percent=1.0),  # too flat
        Pod(pod_id="POD00002", capacity=4, current_node_id="market"),                    # fine
    ])
    trip = make_trip("market", "airport", party_size=3)
    route = find_route(city_graph, "market", "airport", "astar")
    assert [p.pod_id for p in eligible_pods(fleet, city_graph, trip, route)] == ["POD00002"]


def test_congestion_raises_the_battery_a_route_needs(city_graph):
    """Energy uses the edge's own congestion multiplier, so traffic costs charge."""
    from app.fleet.movement import estimated_route_battery_percent
    route = find_route(city_graph, "north_station", "east_hub", "astar")
    before = estimated_route_battery_percent(city_graph, route)
    for edge_id in route.edge_ids:
        city_graph.set_vehicle_count(edge_id, city_graph.get_edge(edge_id).capacity_vehicles_per_hour * 3)
    assert estimated_route_battery_percent(city_graph, route) > before


# --- unroutable and invalid input -------------------------------------------
def test_an_unroutable_trip_is_reported_as_unroutable(make_node, make_edge, make_trip):
    from app.network.graph import NetworkGraph
    graph = NetworkGraph()
    graph.add_node(make_node("a", 0.0, 0.0))
    graph.add_node(make_node("b", 0.0, 0.01))
    graph.add_edge(make_edge("ab", "a", "b", 1.5, 2.0))       # one way only
    fleet = PodFleet(graph, [Pod(pod_id="POD00000", capacity=4, current_node_id="b")])

    result = assign_trip(fleet, graph, make_trip("b", "a"))
    assert not result.is_assigned and result.is_unroutable
    assert result.route is None and result.reason.startswith("unroutable:")


def test_reasons_distinguish_unroutable_from_merely_unavailable(city_graph, make_trip):
    fleet = _fleet_at(city_graph, "airport", count=1)
    result = assign_trip(fleet, city_graph, make_trip("market", "airport"))
    assert not result.is_assigned and not result.is_unroutable      # a pod may free up later


def test_unknown_nodes_and_bad_input_raise(city_graph, make_trip):
    from app.errors import NodeNotFoundError
    fleet = _fleet_at(city_graph, "market", count=1)
    with pytest.raises(NodeNotFoundError):
        assign_trip(fleet, city_graph, make_trip("market", "atlantis"))
    with pytest.raises(AssignmentError, match="expected a TripRequest"):
        assign_trip(fleet, city_graph, "T000000")


def test_assignment_does_not_mutate_the_network(city_graph, make_trip):
    fleet = _fleet_at(city_graph, "market", count=3)
    before = {e.edge_id: e.current_vehicle_count for e in city_graph.edges()}
    assign_trip(fleet, city_graph, make_trip("market", "airport"))
    assert {e.edge_id: e.current_vehicle_count for e in city_graph.edges()} == before


def test_empty_fleet_assigns_nothing(city_graph, make_trip):
    fleet = PodFleet(city_graph)
    result = assign_trip(fleet, city_graph, make_trip("market", "airport"))
    assert not result.is_assigned and result.reason == NO_POD_AT_ORIGIN
