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
