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


# --- M3 fleet fixtures -----------------------------------------------------
@pytest.fixture
def fleet_config():
    from app.fleet.config import DEFAULT_FLEET_CONFIG
    return DEFAULT_FLEET_CONFIG


@pytest.fixture
def city_fleet(city_graph):
    """A small deterministic fleet on the synthetic city."""
    from app.fleet import generate_fleet
    return generate_fleet(city_graph, fleet_size=20, seed=42)


@pytest.fixture
def diamond_fleet(diamond_graph):
    """One pod parked at A, the origin of the diamond's routes."""
    from app.fleet.models import Pod
    from app.fleet.pod_fleet import PodFleet
    return PodFleet(diamond_graph, [Pod(pod_id="POD00000", capacity=4, current_node_id="A")])


@pytest.fixture
def make_trip():
    """Build a TripRequest with sensible defaults."""
    from app.demand.models import TripRequest

    def _make(origin, destination, trip_id="T000000", party_size=1, request_time_min=0.0,
              passenger_id="P000000", purpose="commute", bucket="morning_peak"):
        return TripRequest(trip_id=trip_id, passenger_id=passenger_id, origin_node_id=origin,
                           destination_node_id=destination, request_time_min=request_time_min,
                           party_size=party_size, trip_purpose=purpose, time_bucket=bucket)
    return _make


# --- M4 swarm fixtures -----------------------------------------------------
@pytest.fixture
def swarm_config():
    from app.swarm import DEFAULT_SWARM_CONFIG
    return DEFAULT_SWARM_CONFIG


@pytest.fixture
def corridor_graph():
    """A trunk corridor with a fork, built so swarm behaviour is exactly checkable.

        H0 --C1--> H1 --C2--> H2 --C3--> H3 --HE--> M --ME--> E
                                            \\--HF--> F
        H0 --X1--> X  --X2--> E     (a slower alternative, reaching E only)

    Distances/times, all deliberately round:

    * corridor C1,C2,C3      3 edges, 12 km, 15 min   (the shared run H0..H3)
    * E tail HE,ME           2 edges,  8 km, 10 min   (so E-bound pods can keep
                                                       platooning after H3)
    * F tail HF              1 edge,   4 km,  5 min
    * H0->E via corridor    20 km, 25 min   |  via X: 20 km, 27 min (loses)
    * H0->F via corridor    16 km, 20 min   |  no alternative exists

    Congesting C3 flips E onto the X route while F, having no alternative, stays
    on the corridor — which is how a test shows congestion changing compatibility.
    """
    g = NetworkGraph()
    # 0.03 deg of longitude is ~3.34 km, comfortably under the 4 km road distance,
    # so M1's geometry rule (road >= straight line) holds on every edge.
    coords = {"H0": (0.0, 0.00), "H1": (0.0, 0.03), "H2": (0.0, 0.06), "H3": (0.0, 0.09),
              "M": (0.0, 0.12), "E": (0.015, 0.15), "F": (-0.015, 0.12), "X": (0.04, 0.06)}
    for node_id, (lat, lon) in coords.items():
        g.add_node(_node(node_id, lat, lon, "station"))
    for src, dst, eid in (("H0", "H1", "C1"), ("H1", "H2", "C2"), ("H2", "H3", "C3")):
        g.add_edge(_edge(eid, src, dst, 4.0, 5.0, capacity=3600, road_type="trunk"))
    g.add_edge(_edge("HE", "H3", "M", 4.0, 5.0, capacity=1500))
    g.add_edge(_edge("ME", "M", "E", 4.0, 5.0, capacity=1500))
    g.add_edge(_edge("HF", "H3", "F", 4.0, 5.0, capacity=1500))
    g.add_edge(_edge("X1", "H0", "X", 9.0, 13.0, capacity=1500))
    g.add_edge(_edge("X2", "X", "E", 11.0, 14.0, capacity=1500))
    return g


@pytest.fixture
def make_corridor_fleet(corridor_graph):
    """Put N pods at H0 and assign each a trip to E or F, returning (fleet, sim)."""
    from app.demand.models import TripRequest
    from app.fleet import DEFAULT_FLEET_CONFIG, Pod, PodFleet
    from app.swarm import DEFAULT_SWARM_CONFIG, SwarmSimulation

    def _make(destinations, *, swarm_config=None, enable_swarms=True, party_size=1,
              fleet_config=DEFAULT_FLEET_CONFIG, graph=None):
        graph = graph if graph is not None else corridor_graph
        pods = [Pod(pod_id=f"POD{i:05d}", capacity=4, current_node_id="H0")
                for i in range(len(destinations))]
        fleet = PodFleet(graph, pods)
        trips = [TripRequest(trip_id=f"T{i:06d}", passenger_id=f"P{i:06d}", origin_node_id="H0",
                             destination_node_id=dest, request_time_min=0.0,
                             party_size=party_size, trip_purpose="commute",
                             time_bucket="morning_peak")
                 for i, dest in enumerate(destinations)]
        simulation = SwarmSimulation(
            graph, fleet, trips, fleet_config,
            swarm_config=swarm_config or DEFAULT_SWARM_CONFIG, enable_swarms=enable_swarms)
        return fleet, simulation
    return _make
