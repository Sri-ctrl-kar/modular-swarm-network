import itertools
import math
import random

import pytest

from app.errors import InvalidCostError, NoRouteError, NodeNotFoundError
from app.models.route import Route
from app.network.graph import NetworkGraph
from app.routing import DISTANCE, astar, custom_cost_model, dijkstra, find_route
from app.routing.costs import time_heuristic_factory

MAJOR = ["north_station", "central_station", "east_hub", "west_hub", "airport", "university",
         "tech_park", "industrial_zone", "riverside", "market", "residential_north", "residential_south"]


@pytest.mark.parametrize("algorithm", [dijkstra, astar])
def test_shortest_path_on_diamond(diamond_graph, algorithm):
    route = algorithm(diamond_graph, "A", "D")
    assert isinstance(route, Route)
    assert route.node_ids == ("A", "B", "D")
    assert route.edge_ids == ("AB", "BD")
    assert route.total_travel_time_min == pytest.approx(6.0)
    assert route.total_distance_km == pytest.approx(4.0)
    assert route.algorithm == algorithm.__name__


def test_dijkstra_and_astar_costs_equal_for_all_city_pairs(city):
    g = city.graph
    ids = [n.node_id for n in g.nodes()]
    for o, d in itertools.permutations(ids, 2):
        a, b = astar(g, o, d), dijkstra(g, o, d)
        assert a.total_cost == pytest.approx(b.total_cost, rel=1e-12, abs=1e-9), (o, d)
        assert a.nodes_visited <= b.nodes_visited, (o, d)


def test_costs_equal_under_random_congestion(city):
    g = city.graph
    rng = random.Random(7)
    for _ in range(5):
        for e in g.edges():
            e.set_vehicle_count(int(e.capacity_vehicles_per_hour * rng.uniform(0, 3)))
        for o, d in itertools.permutations(MAJOR[:6], 2):
            assert astar(g, o, d).total_cost == pytest.approx(dijkstra(g, o, d).total_cost)


def test_heuristic_is_admissible_everywhere(city):
    g = city.graph
    for goal in [n.node_id for n in g.nodes()]:
        h = time_heuristic_factory(g, goal)
        for n in g.nodes():
            true_cost = 0.0 if n.node_id == goal else dijkstra(g, n.node_id, goal).total_cost
            assert h(n.node_id) <= true_cost + 1e-12


def test_heuristic_is_consistent(city):
    g = city.graph
    h = time_heuristic_factory(g, "airport")
    for e in g.edges():
        assert h(e.source) <= e.current_travel_time_min + h(e.destination)


@pytest.mark.parametrize("algorithm", [dijkstra, astar])
def test_origin_equals_destination(city, algorithm):
    route = algorithm(city.graph, "market", "market")
    assert route.node_ids == ("market",)
    assert route.edge_ids == ()
    assert route.total_travel_time_min == 0.0 and route.total_distance_km == 0.0
    assert route.nodes_visited == 1


@pytest.mark.parametrize("algorithm", [dijkstra, astar])
@pytest.mark.parametrize("origin,destination", [("ghost", "airport"), ("airport", "ghost")])
def test_nonexistent_nodes(city, algorithm, origin, destination):
    with pytest.raises(NodeNotFoundError):
        algorithm(city.graph, origin, destination)


@pytest.mark.parametrize("algorithm", [dijkstra, astar])
def test_unreachable_destination(diamond_graph, algorithm):
    with pytest.raises(NoRouteError):
        algorithm(diamond_graph, "D", "A")  # D has no outgoing edges


def test_single_edge_route(make_node, make_edge):
    g = NetworkGraph()
    g.add_node(make_node("a"))
    g.add_node(make_node("b", 0.0, 0.01))
    g.add_edge(make_edge("ab", "a", "b", 1.5, 2.0))
    for algorithm in (dijkstra, astar):
        route = algorithm(g, "a", "b")
        assert route.edge_ids == ("ab",) and route.total_travel_time_min == pytest.approx(2.0)


def test_congestion_changes_optimal_route(diamond_graph):
    before = astar(diamond_graph, "A", "D")
    assert before.edge_ids == ("AB", "BD")
    diamond_graph.set_vehicle_count("AB", 2000)  # u=2 -> 3 * 3.15 = 9.45 min
    after_astar = astar(diamond_graph, "A", "D")
    after_dijkstra = dijkstra(diamond_graph, "A", "D")
    assert after_astar.edge_ids == after_dijkstra.edge_ids == ("AC", "CD")
    assert after_astar.total_travel_time_min == pytest.approx(8.0)
    diamond_graph.set_vehicle_count("AB", 0)
    assert astar(diamond_graph, "A", "D").edge_ids == ("AB", "BD")


def test_congestion_changes_route_in_city(city):
    g = city.graph
    before = astar(g, "north_station", "airport")
    for edge_id in before.edge_ids:
        g.set_vehicle_count(edge_id, g.get_edge(edge_id).capacity_vehicles_per_hour * 3)
    after = astar(g, "north_station", "airport")
    assert after.edge_ids != before.edge_ids
    assert after.total_cost == pytest.approx(dijkstra(g, "north_station", "airport").total_cost)


def test_distance_cost_model(diamond_graph):
    route = astar(diamond_graph, "A", "D", DISTANCE)
    assert route.edge_ids == ("AD",)  # 3 km direct beats 4 km
    assert route.cost_metric == "distance_km"
    assert route.total_cost == pytest.approx(dijkstra(diamond_graph, "A", "D", DISTANCE).total_cost)


@pytest.mark.parametrize("bad", [0.0, -1.0, math.nan, math.inf])
@pytest.mark.parametrize("algorithm", [dijkstra, astar])
def test_invalid_custom_weights_rejected(diamond_graph, algorithm, bad):
    model = custom_cost_model("broken", lambda e: bad)
    with pytest.raises(InvalidCostError):
        algorithm(diamond_graph, "A", "D", model)


def test_find_route_dispatch(diamond_graph):
    assert find_route(diamond_graph, "A", "D", "dijkstra").algorithm == "dijkstra"
    with pytest.raises(ValueError):
        find_route(diamond_graph, "A", "D", "bfs")


def test_astar_visits_fewer_nodes_on_long_trip(city):
    a = astar(city.graph, "west_hub", "airport")
    d = dijkstra(city.graph, "west_hub", "airport")
    assert a.nodes_visited < d.nodes_visited
