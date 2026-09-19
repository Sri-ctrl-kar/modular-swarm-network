import math

import pytest

from app.config import CongestionParameters
from app.errors import ModelValidationError
from app.models.edge import Edge, RoadType
from app.models.network_state import NetworkSnapshot
from app.models.node import Node, NodeType
from app.models.route import Route


def test_valid_node(make_node):
    node = make_node("n1", 45.5, -73.6, "station", "Central")
    assert node.node_type is NodeType.STATION
    assert Node.from_dict(node.to_dict()) == node


@pytest.mark.parametrize("kwargs", [
    {"node_id": ""}, {"node_id": "   "}, {"name": ""},
    {"latitude": 90.1}, {"latitude": -91}, {"longitude": 180.5},
    {"latitude": math.nan}, {"longitude": math.inf}, {"latitude": True},
    {"latitude": "45"}, {"node_type": "airport"},
])
def test_invalid_node(kwargs):
    base = dict(node_id="n1", name="N", latitude=0.0, longitude=0.0, node_type="intersection")
    base.update(kwargs)
    with pytest.raises(ModelValidationError):
        Node(**base)


def test_valid_edge(make_edge):
    edge = make_edge("e1", "a", "b", 5.0, 6.0, capacity=1200, count=0, road_type="trunk")
    assert edge.road_type is RoadType.TRUNK
    assert edge.current_travel_time_min == pytest.approx(6.0)
    assert edge.free_flow_speed_kmh == pytest.approx(50.0)


@pytest.mark.parametrize("kwargs", [
    {"edge_id": ""}, {"source": ""}, {"destination": "a"},
    {"distance_km": 0}, {"distance_km": -1.0}, {"distance_km": math.nan},
    {"road_type": "highway"}, {"current_vehicle_count": -1}, {"current_vehicle_count": 1.5},
])
def test_invalid_edge(make_edge, kwargs):
    base = dict(edge_id="e1", source="a", destination="b", distance_km=1.0, base_travel_time_min=1.0,
                capacity_vehicles_per_hour=100, road_type="local")
    base.update(kwargs)
    with pytest.raises(ModelValidationError):
        Edge(**base)


@pytest.mark.parametrize("capacity", [0, -10, 10.5, True, None])
def test_invalid_capacity(make_edge, capacity):
    with pytest.raises(ModelValidationError):
        make_edge("e1", "a", "b", capacity=capacity)


@pytest.mark.parametrize("time_min", [0, 0.0, -2.0, math.inf, math.nan])
def test_invalid_travel_time(make_edge, time_min):
    with pytest.raises(ModelValidationError):
        make_edge("e1", "a", "b", time_min=time_min)


def test_congestion_formula_values():
    p = CongestionParameters()
    assert p.multiplier(0.0) == 1.0
    assert p.multiplier(0.5) == pytest.approx(1 + 0.15 * 0.5**4)
    assert p.multiplier(1.0) == pytest.approx(1.15)
    assert p.multiplier(2.0) == pytest.approx(1.15 + 2.0)


def test_congestion_is_continuous_and_monotonic():
    p = CongestionParameters()
    assert p.multiplier(1.0 + 1e-9) == pytest.approx(p.multiplier(1.0), abs=1e-6)
    values = [p.multiplier(i / 10) for i in range(0, 50)]
    assert all(b >= a for a, b in zip(values, values[1:]))
    assert min(values) >= 1.0


@pytest.mark.parametrize("kwargs", [{"alpha": -0.1}, {"beta": 0.5}, {"overload_slope": 0}])
def test_invalid_congestion_parameters(kwargs):
    with pytest.raises(ModelValidationError):
        CongestionParameters(**kwargs)


def test_set_vehicle_count_validates(make_edge):
    edge = make_edge("e1", "a", "b")
    edge.set_vehicle_count(500)
    assert edge.current_vehicle_count == 500
    with pytest.raises(ModelValidationError):
        edge.set_vehicle_count(-1)
    assert edge.current_vehicle_count == 500


def test_route_model_rejects_inconsistent_paths():
    with pytest.raises(ModelValidationError):
        Route("a", "b", ("a", "b"), (), 1.0, 1.0, 1.0, "t", 2, "x")


def test_snapshot_rejects_negative_counts():
    with pytest.raises(ModelValidationError):
        NetworkSnapshot(0.0, (("e1", -1),))
