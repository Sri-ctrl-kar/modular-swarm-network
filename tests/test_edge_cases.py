import pytest

from app.cli.main import main
from app.errors import EdgeNotFoundError, ModelValidationError, NoRouteError
from app.network.graph import NetworkGraph
from app.routing import astar, dijkstra
from app.simulation.state import SimulationState


def test_zero_traffic_is_free_flow(city):
    state = SimulationState.from_scenario(city)
    state.clear_traffic()
    assert all(c == 0 for c in state.vehicle_counts().values())
    for e in state.graph.edges():
        assert e.current_travel_time_min == e.base_travel_time_min


def test_high_traffic_below_capacity(make_edge):
    edge = make_edge("e", "a", "b", time_min=10.0, capacity=1000, count=950)
    assert not edge.is_overloaded
    assert 10.0 < edge.current_travel_time_min < 11.5


def test_overloaded_edge_penalized_strongly(make_edge):
    at_cap = make_edge("e1", "a", "b", time_min=10.0, capacity=1000, count=1000)
    over = make_edge("e2", "a", "b", time_min=10.0, capacity=1000, count=1500)
    assert over.is_overloaded
    assert at_cap.current_travel_time_min == pytest.approx(11.5)
    assert over.current_travel_time_min == pytest.approx(21.5)


def test_extreme_overload_stays_finite_and_positive(make_edge):
    edge = make_edge("e", "a", "b", time_min=1.0, capacity=1, count=10**9)
    assert 0 < edge.current_travel_time_min < float("inf")


def test_disconnected_network(make_node, make_edge):
    g = NetworkGraph()
    for i, lon in enumerate([0.0, 0.01, 0.5, 0.51]):
        g.add_node(make_node(f"n{i}", 0.0, lon))
    g.add_edge(make_edge("e01", "n0", "n1"))
    g.add_edge(make_edge("e10", "n1", "n0"))
    g.add_edge(make_edge("e23", "n2", "n3"))
    g.add_edge(make_edge("e32", "n3", "n2"))
    assert not g.validate_connectivity().is_weakly_connected
    for algorithm in (astar, dijkstra):
        with pytest.raises(NoRouteError):
            algorithm(g, "n0", "n3")


def test_parallel_alternatives_pick_faster_edge(make_node, make_edge):
    g = NetworkGraph()
    g.add_node(make_node("a"))
    g.add_node(make_node("b", 0.0, 0.01))
    g.add_edge(make_edge("slow", "a", "b", 2.0, 5.0, road_type="local"))
    g.add_edge(make_edge("fast", "a", "b", 2.0, 2.0, road_type="trunk"))
    assert astar(g, "a", "b").edge_ids == ("fast",)
    g.set_vehicle_count("fast", 3000)  # u=3 -> 2 * 5.15 = 10.3 min
    assert astar(g, "a", "b").edge_ids == dijkstra(g, "a", "b").edge_ids == ("slow",)


def test_simulation_state_validation(city):
    state = SimulationState.from_scenario(city)
    with pytest.raises(ModelValidationError):
        state.advance(0)
    with pytest.raises(ModelValidationError):
        state.advance(-5)
    with pytest.raises(EdgeNotFoundError):
        state.set_vehicle_count("E999", 1)
    state.set_vehicle_count("E001", 5)
    with pytest.raises(ModelValidationError):
        state.adjust_vehicle_count("E001", -6)
    assert state.adjust_vehicle_count("E001", 10) == 15
    assert state.advance(2.5) == 2.5


def test_traffic_update_changes_cost_without_rebuild(city):
    state = SimulationState.from_scenario(city)
    graph_id = id(state.graph)
    before = state.edge_travel_time_min("E007")
    state.set_utilization("E007", 2.0)
    assert state.edge_travel_time_min("E007") > before
    assert id(state.graph) == graph_id


def test_cli_route_success(capsys):
    assert main(["route", "--from", "North Station", "--to", "Airport", "--algorithm", "both"]) == 0
    out = capsys.readouterr().out
    assert "MODULAR SWARM NETWORK" in out and "Path:" in out
    assert "Optimal cost match (A* vs Dijkstra): YES" in out


def test_cli_unknown_node_is_clean_error(capsys):
    assert main(["route", "--from", "Atlantis", "--to", "Airport"]) == 2
    captured = capsys.readouterr()
    assert "error:" in captured.err and "Traceback" not in captured.err
    assert captured.out == ""


def test_cli_bad_traffic_argument(capsys):
    assert main(["route", "--from", "Market", "--to", "Airport", "--traffic", "E001=lots"]) == 2
    assert main(["route", "--from", "Market", "--to", "Airport", "--traffic", "E001=-4"]) == 2


def test_cli_congestion_demo_reroutes(capsys):
    assert main(["congestion-demo", "--from", "North Station", "--to", "Airport"]) == 0
    assert "Route changed: YES" in capsys.readouterr().out
