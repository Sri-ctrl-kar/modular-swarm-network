"""Shared fixtures. Small hand-built graphs use coordinates ~1 km apart near
(0, 0) so every edge satisfies the graph's geometry rules."""

import pytest

from app.models.edge import Edge
from app.models.node import Node
from app.network.graph import NetworkGraph
from app.network.synthetic_city import build_synthetic_city


def _node(node_id, lat=0.0, lon=0.0, node_type="intersection", name=None):
    return Node(node_id=node_id, name=name or node_id.upper(), latitude=lat, longitude=lon, node_type=node_type)


def _edge(edge_id, src, dst, distance_km=2.0, time_min=3.0, capacity=1000, count=0, road_type="arterial"):
    return Edge(edge_id=edge_id, source=src, destination=dst, distance_km=distance_km,
                base_travel_time_min=time_min, capacity_vehicles_per_hour=capacity,
                current_vehicle_count=count, road_type=road_type)


@pytest.fixture
def make_node():
    return _node


@pytest.fixture
def make_edge():
    return _edge


@pytest.fixture
def diamond_graph():
    """A -> B -> D  (3 + 3 = 6 min, fastest)
       A -> C -> D  (4 + 4 = 8 min)
       A -> D       (10 min direct, slow local road)"""
    g = NetworkGraph()
    g.add_node(_node("A", 0.0, 0.0))
    g.add_node(_node("B", 0.005, 0.01))
    g.add_node(_node("C", -0.005, 0.01))
    g.add_node(_node("D", 0.0, 0.02))
    g.add_edge(_edge("AB", "A", "B", 2.0, 3.0))
    g.add_edge(_edge("BD", "B", "D", 2.0, 3.0))
    g.add_edge(_edge("AC", "A", "C", 2.0, 4.0))
    g.add_edge(_edge("CD", "C", "D", 2.0, 4.0))
    g.add_edge(_edge("AD", "A", "D", 3.0, 10.0, road_type="local"))
    return g


@pytest.fixture
def city():
    return build_synthetic_city(42)


# --- M2 demand fixtures ----------------------------------------------------
@pytest.fixture
def city_graph(city):
    return city.graph


@pytest.fixture
def demand(city_graph):
    """A small but representative baseline demand set (fast for tests)."""
    from app.demand import generate_demand
    return generate_demand(city_graph, seed=42, passenger_count=300)


@pytest.fixture
def tiny_graph():
    """Two nodes, one two-way link: the smallest graph that can carry demand."""
    g = NetworkGraph()
    g.add_node(_node("home", 0.0, 0.0, "station", name="Home"))
    g.add_node(_node("work", 0.0, 0.02, "station", name="Work"))
    g.add_edge(_edge("hw", "home", "work", 2.5, 4.0))
    g.add_edge(_edge("wh", "work", "home", 2.5, 4.0))
    return g


@pytest.fixture
def tiny_profile():
    """One bucket, both nodes usable, so tiny graphs can generate demand."""
    from app.demand.config import DemandProfile, TimeBucket
    return DemandProfile(
        profile_id="tiny",
        description="Single-bucket profile for small-graph tests.",
        buckets=(TimeBucket(name="morning_peak", windows=((360.0, 600.0),), trip_share=1.0,
                            production={"residential": 1.0, "mixed": 1.0},
                            attraction={"employment": 1.0, "mixed": 1.0}),),
        party_size_distribution=((1, 0.5), (2, 0.5)),
        node_roles={"home": "residential", "work": "employment"},
        default_roles_by_node_type={"station": "mixed", "terminal": "hub", "intersection": "through"},
        home_role_weights={"residential": 1.0},
        commuter_share=0.5,
        gravity_distance_exponent=1.0,
    )
