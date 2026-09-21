"""M2 routing integration: demand uses M1 routing without changing or duplicating it."""

import pytest

from app.demand import (
    RoutedTrip,
    compute_metrics,
    free_flow_time_min,
    generate_demand,
    route_trip,
    route_trips,
)
from app.demand.models import TripRequest
from app.errors import NodeNotFoundError, NoRouteError
from app.models.route import Route
from app.routing import astar, dijkstra, find_route


def _trip(origin, destination, trip_id="T000000", party_size=1):
    return TripRequest(trip_id=trip_id, passenger_id="P000000", origin_node_id=origin,
                       destination_node_id=destination, request_time_min=480.0,
                       party_size=party_size, trip_purpose="commute", time_bucket="morning_peak")


# --- integration -------------------------------------------------------------
def test_routed_trip_matches_calling_routing_directly(city_graph):
    trip = _trip("north_station", "airport")
    routed = route_trip(city_graph, trip)
    expected = find_route(city_graph, "north_station", "airport", "astar")

    assert routed.is_routed
    assert isinstance(routed.route, Route)
    assert routed.route.to_dict() == expected.to_dict()      # no reimplementation, same result
    assert routed.trip is trip
    assert routed.unroutable_reason is None


def test_route_trips_preserves_order_and_routes_everything(city_graph, demand):
    routed = route_trips(city_graph, demand.trips)
    assert len(routed) == len(demand.trips)
    assert [item.trip.trip_id for item in routed] == [t.trip_id for t in demand.trips]
    assert all(item.is_routed for item in routed)            # the city is strongly connected


@pytest.mark.parametrize("algorithm", ["astar", "dijkstra"])
def test_both_algorithms_are_usable_and_agree_on_cost(city_graph, demand, algorithm):
    routed = route_trips(city_graph, demand.trips[:60], algorithm)
    assert all(item.route.algorithm == algorithm for item in routed)
    for item in routed:
        a = astar(city_graph, item.trip.origin_node_id, item.trip.destination_node_id)
        d = dijkstra(city_graph, item.trip.origin_node_id, item.trip.destination_node_id)
        assert item.route.total_cost == pytest.approx(a.total_cost)
        assert a.total_cost == pytest.approx(d.total_cost)    # M1 guarantee still holds


def test_route_endpoints_match_the_trip(city_graph, demand):
    for item in route_trips(city_graph, demand.trips[:80]):
        assert item.route.origin == item.trip.origin_node_id
        assert item.route.destination == item.trip.destination_node_id
        assert item.route.node_ids[0] == item.trip.origin_node_id
        assert item.route.node_ids[-1] == item.trip.destination_node_id


def test_free_flow_time_is_the_sum_of_base_edge_times(city_graph):
    routed = route_trip(city_graph, _trip("west_hub", "airport"))
    expected = sum(city_graph.get_edge(e).base_travel_time_min for e in routed.route.edge_ids)
    assert routed.free_flow_travel_time_min == pytest.approx(expected)
    assert free_flow_time_min(city_graph, routed.route) == pytest.approx(expected)
    # Under traffic, actual time is never below free flow (congestion multiplier >= 1).
    assert routed.route.total_travel_time_min >= routed.free_flow_travel_time_min
    assert routed.congestion_delay_min >= 0.0


def test_passenger_km_scales_with_party_size(city_graph):
    single = route_trip(city_graph, _trip("market", "airport", party_size=1))
    group = route_trip(city_graph, _trip("market", "airport", party_size=4))
    assert group.passenger_km == pytest.approx(single.passenger_km * 4)


# --- determinism -------------------------------------------------------------
def test_routing_the_same_trips_twice_gives_identical_routes(city_graph, demand):
    first = route_trips(city_graph, demand.trips)
    second = route_trips(city_graph, demand.trips)
    assert [i.to_dict() for i in first] == [j.to_dict() for j in second]


def test_same_demand_and_network_give_identical_routing_metrics(city_graph):
    results = []
    for _ in range(3):
        demand = generate_demand(city_graph, seed=42, passenger_count=200)
        results.append(compute_metrics(demand, route_trips(city_graph, demand.trips)).to_dict())
    assert all(result == results[0] for result in results)


# --- congestion flows through to routed trips --------------------------------
def test_congestion_changes_the_cost_of_a_routed_trip(city_graph):
    """The M1.1 switch scenario, reached through a TripRequest instead of a call
    to the router: demand must see the changed network, not a stale route."""
    trip = _trip("north_station", "east_hub")
    before = route_trip(city_graph, trip)
    assert before.route.edge_ids == ("E009", "E011")

    city_graph.set_vehicle_count("E011", city_graph.get_edge("E011").capacity_vehicles_per_hour * 3)
    after = route_trip(city_graph, trip)

    assert after.route.edge_ids == ("E013", "E003")           # switched to the alternative
    assert after.route.total_travel_time_min != pytest.approx(before.route.total_travel_time_min)
    assert after.route.total_cost > before.route.total_cost
    # free flow is a property of the path, so the new path has its own baseline
    assert after.free_flow_travel_time_min == pytest.approx(
        sum(city_graph.get_edge(e).base_travel_time_min for e in after.route.edge_ids))


def test_congestion_raises_average_travel_time_across_the_whole_demand(city_graph, demand):
    before = compute_metrics(demand, route_trips(city_graph, demand.trips))
    for edge in city_graph.edges():
        edge.set_vehicle_count(edge.capacity_vehicles_per_hour * 2)
    after = compute_metrics(demand, route_trips(city_graph, demand.trips))

    assert after.average_route_travel_time_min > before.average_route_travel_time_min
    # Free-flow times describe the chosen paths, which congestion may reroute,
    # but the trip count is unchanged.
    assert after.routed_trips == before.routed_trips
    assert after.unroutable_trips == 0


def test_restoring_traffic_restores_routed_costs(city_graph, demand):
    from app.simulation.state import SimulationState
    state = SimulationState(city_graph)
    snapshot = state.snapshot()
    before = [i.to_dict() for i in route_trips(city_graph, demand.trips[:50])]

    state.set_utilization("E011", 3.0)
    assert [i.to_dict() for i in route_trips(city_graph, demand.trips[:50])] != before

    state.restore(snapshot)
    assert [i.to_dict() for i in route_trips(city_graph, demand.trips[:50])] == before


# --- unroutable and invalid trips -------------------------------------------
def test_unreachable_destination_is_recorded_not_raised(make_node, make_edge):
    """A one-way network: B cannot reach A. That is a demand fact, not a crash."""
    from app.network.graph import NetworkGraph
    graph = NetworkGraph()
    graph.add_node(make_node("a", 0.0, 0.0))
    graph.add_node(make_node("b", 0.0, 0.01))
    graph.add_edge(make_edge("ab", "a", "b", 1.5, 2.0))

    routed = route_trip(graph, _trip("b", "a"))
    assert not routed.is_routed
    assert routed.route is None
    assert routed.free_flow_travel_time_min is None
    assert routed.unroutable_reason and "b" in routed.unroutable_reason
    assert routed.passenger_km == 0.0 and routed.congestion_delay_min == 0.0

    metrics = compute_metrics(generate_demand(graph, seed=1, passenger_count=0), [routed])
    assert metrics.routed_trips == 0 and metrics.unroutable_trips == 1
    assert metrics.average_route_distance_km is None          # no fake 0.0


def test_mixed_routable_and_unroutable_trips_are_counted_separately(make_node, make_edge):
    from app.network.graph import NetworkGraph
    graph = NetworkGraph()
    graph.add_node(make_node("a", 0.0, 0.0))
    graph.add_node(make_node("b", 0.0, 0.01))
    graph.add_edge(make_edge("ab", "a", "b", 1.5, 2.0))

    routed = route_trips(graph, [_trip("a", "b", "T0"), _trip("b", "a", "T1")])
    assert [item.is_routed for item in routed] == [True, False]
    demand = generate_demand(graph, seed=1, passenger_count=0)
    metrics = compute_metrics(demand, routed)
    assert metrics.routed_trips == 1 and metrics.unroutable_trips == 1


def test_unknown_node_in_a_trip_still_raises(city_graph):
    """An unroutable trip is data; a nonexistent node is a bug and must surface."""
    with pytest.raises(NodeNotFoundError):
        route_trip(city_graph, _trip("north_station", "atlantis"))
    with pytest.raises(NodeNotFoundError):
        route_trips(city_graph, [_trip("atlantis", "airport")])


def test_unknown_algorithm_raises(city_graph):
    with pytest.raises(ValueError, match="unknown algorithm"):
        route_trip(city_graph, _trip("market", "airport"), "bfs")


def test_metrics_without_routing_leave_route_fields_unset(demand):
    metrics = compute_metrics(demand)
    assert metrics.routed_trips is None
    assert metrics.unroutable_trips is None
    assert metrics.average_route_distance_km is None
    assert metrics.average_route_travel_time_min is None


# --- layering ---------------------------------------------------------------
def test_lower_layers_do_not_import_the_demand_layer():
    """Invariant: models <- network <- routing <- simulation <- demand. The
    dependency must never point back up."""
    from pathlib import Path
    from app.config import PROJECT_ROOT

    offenders = []
    for layer in ("models", "network", "routing", "simulation"):
        for path in sorted((PROJECT_ROOT / "app" / layer).rglob("*.py")):
            if "app.demand" in path.read_text(encoding="utf-8"):
                offenders.append(str(path.relative_to(PROJECT_ROOT)))
    assert offenders == [], f"lower layers import the demand layer: {offenders}"


def test_routing_a_trip_does_not_mutate_the_network(city_graph, demand):
    before = {edge.edge_id: edge.current_vehicle_count for edge in city_graph.edges()}
    route_trips(city_graph, demand.trips)
    assert {edge.edge_id: edge.current_vehicle_count for edge in city_graph.edges()} == before


def test_ten_thousand_trips_route_in_reasonable_time(city_graph):
    import time
    demand = generate_demand(city_graph, seed=42, passenger_count=10_000)
    started = time.perf_counter()
    routed = route_trips(city_graph, demand.trips)
    elapsed = time.perf_counter() - started
    assert len(routed) == 10_000
    assert all(item.is_routed for item in routed)
    assert elapsed < 30.0, f"routing 10k trips took {elapsed:.2f}s"
