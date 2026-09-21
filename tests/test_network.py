import pytest

from app.errors import (
    DuplicateEdgeError,
    DuplicateNodeError,
    EdgeNotFoundError,
    GeometryValidationError,
    NodeNotFoundError,
    ScenarioError,
)
from app.config import DEFAULT_SCENARIO_PATH
from app.network.builder import load_scenario, scenario_from_dict
from app.network.geo import haversine_km
from app.network.graph import NetworkGraph
from app.network.synthetic_city import generate_synthetic_city_dict


def test_adding_nodes(make_node):
    g = NetworkGraph()
    g.add_node(make_node("a"))
    g.add_node(make_node("b", 0.0, 0.01))
    assert g.node_count() == 2
    assert g.get_node("a").node_id == "a"
    assert g.has_node("b") and not g.has_node("z")


def test_duplicate_node_id_rejected(make_node):
    g = NetworkGraph()
    g.add_node(make_node("a"))
    with pytest.raises(DuplicateNodeError):
        g.add_node(make_node("a", name="Other"))


def test_duplicate_node_name_rejected(make_node):
    g = NetworkGraph()
    g.add_node(make_node("a", name="Market"))
    with pytest.raises(DuplicateNodeError):
        g.add_node(make_node("b", name="market"))


def test_adding_edges_and_duplicates(diamond_graph, make_edge):
    assert diamond_graph.edge_count() == 5
    assert diamond_graph.get_edge("AB").destination == "B"
    with pytest.raises(DuplicateEdgeError):
        diamond_graph.add_edge(make_edge("AB", "B", "D"))


def test_edge_with_missing_node_rejected(diamond_graph, make_edge):
    with pytest.raises(NodeNotFoundError):
        diamond_graph.add_edge(make_edge("AX", "A", "X"))
    with pytest.raises(NodeNotFoundError):
        diamond_graph.add_edge(make_edge("XA", "X", "A"))
    assert diamond_graph.edge_count() == 5


def test_unknown_lookups_raise(diamond_graph):
    with pytest.raises(NodeNotFoundError):
        diamond_graph.get_node("nope")
    with pytest.raises(EdgeNotFoundError):
        diamond_graph.get_edge("nope")


def test_adjacency(diamond_graph):
    assert diamond_graph.neighbors("A") == ("B", "C", "D")
    assert [e.edge_id for e in diamond_graph.outgoing_edges("A")] == ["AB", "AC", "AD"]
    assert [e.edge_id for e in diamond_graph.incoming_edges("D")] == ["BD", "CD", "AD"]
    assert diamond_graph.neighbors("D") == ()


def test_graph_statistics(city):
    stats = city.graph.statistics()
    assert 15 <= stats["node_count"] <= 30
    assert 30 <= stats["edge_count"] <= 60
    assert set(stats["edges_by_road_type"]) == {"local", "arterial", "trunk"}
    assert set(stats["nodes_by_type"]) == {"intersection", "station", "terminal"}


def test_connectivity_of_synthetic_city(city):
    report = city.graph.validate_connectivity()
    assert report.is_strongly_connected and report.is_weakly_connected
    assert report.component_count == 1
    assert report.isolated_nodes == ()


def test_connectivity_detects_one_way_and_isolated(diamond_graph, make_node):
    diamond_graph.add_node(make_node("Z", 1.0, 1.0))
    report = diamond_graph.validate_connectivity()
    assert not report.is_strongly_connected
    assert not report.is_weakly_connected
    assert report.isolated_nodes == ("Z",)
    assert report.component_count == 5


def test_empty_graph_is_not_connected():
    report = NetworkGraph().validate_connectivity()
    assert not report.is_strongly_connected and report.node_count == 0


def test_road_shorter_than_straight_line_rejected(make_node, make_edge):
    g = NetworkGraph()
    g.add_node(make_node("a", 0.0, 0.0))
    g.add_node(make_node("b", 0.0, 0.1))  # ~11.1 km apart
    with pytest.raises(GeometryValidationError):
        g.add_edge(make_edge("ab", "a", "b", distance_km=5.0, time_min=30.0))


def test_edge_faster_than_speed_ceiling_rejected(make_node, make_edge):
    g = NetworkGraph(max_speed_kmh=100)
    g.add_node(make_node("a"))
    g.add_node(make_node("b", 0.0, 0.01))
    with pytest.raises(GeometryValidationError):
        g.add_edge(make_edge("ab", "a", "b", distance_km=5.0, time_min=1.0))  # 300 km/h


def test_haversine_known_values():
    assert haversine_km(0, 0, 0, 0) == 0.0
    assert haversine_km(0, 0, 0, 1) == pytest.approx(111.195, rel=1e-4)
    assert haversine_km(10, 20, 30, 40) == pytest.approx(haversine_km(30, 40, 10, 20))


def test_road_distance_never_below_geographic_distance(city):
    g = city.graph
    for e in g.edges():
        straight = haversine_km(g.get_node(e.source).latitude, g.get_node(e.source).longitude,
                                g.get_node(e.destination).latitude, g.get_node(e.destination).longitude)
        assert e.distance_km >= straight


def test_baseline_scenario_loads_and_matches_generator():
    loaded = load_scenario(DEFAULT_SCENARIO_PATH)
    assert loaded.metadata.scenario_id == "baseline"
    assert "SYNTHETIC" in loaded.metadata.data_provenance
    generated = generate_synthetic_city_dict(loaded.metadata.seed)
    assert [n.to_dict() for n in loaded.graph.nodes()] == generated["nodes"]
    assert [e.to_dict() for e in loaded.graph.edges()] == generated["edges"]


def test_resolve_node_by_name_or_id(city):
    g = city.graph
    assert g.resolve_node("north station").node_id == "north_station"
    assert g.resolve_node("airport").name == "Airport"
    with pytest.raises(NodeNotFoundError):
        g.resolve_node("Atlantis")


def test_malformed_scenarios_rejected(tmp_path):
    data = generate_synthetic_city_dict(42)
    with pytest.raises(ScenarioError):
        scenario_from_dict({k: v for k, v in data.items() if k != "nodes"})
    bad = dict(data, schema_version=99)
    with pytest.raises(ScenarioError):
        scenario_from_dict(bad)
    bad_edge = dict(data, edges=data["edges"] + [dict(data["edges"][0], edge_id="EX", distance_km=-1)])
    with pytest.raises(ScenarioError):
        scenario_from_dict(bad_edge)
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(ScenarioError):
        load_scenario(broken)
    with pytest.raises(ScenarioError):
        load_scenario(tmp_path / "missing.json")
