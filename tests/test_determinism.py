import json

from app.config import DEFAULT_SCENARIO_PATH
from app.network.builder import dump_scenario, load_scenario
from app.network.synthetic_city import build_synthetic_city, generate_synthetic_city_dict
from app.routing import astar, dijkstra
from app.simulation.state import SimulationState


def test_same_seed_produces_identical_city():
    assert dump_scenario(generate_synthetic_city_dict(42)) == dump_scenario(generate_synthetic_city_dict(42))


def test_different_seed_produces_different_city():
    assert generate_synthetic_city_dict(42) != generate_synthetic_city_dict(43)


def test_committed_baseline_is_byte_identical_to_generator():
    on_disk = DEFAULT_SCENARIO_PATH.read_text(encoding="utf-8")
    seed = json.loads(on_disk)["seed"]
    assert on_disk == dump_scenario(generate_synthetic_city_dict(seed))


def test_same_input_produces_same_route_on_fresh_networks():
    r1 = astar(build_synthetic_city(42).graph, "north_station", "airport")
    r2 = astar(build_synthetic_city(42).graph, "north_station", "airport")
    assert r1 == r2
    assert r1.to_dict() == r2.to_dict()


def test_repeated_route_calls_are_identical(city):
    for algorithm in (astar, dijkstra):
        results = [algorithm(city.graph, "university", "industrial_zone").to_dict() for _ in range(10)]
        assert all(r == results[0] for r in results)


def test_scenario_loaded_twice_gives_identical_state_and_routes():
    fingerprints, routes = [], []
    for _ in range(2):
        state = SimulationState.from_scenario(load_scenario(DEFAULT_SCENARIO_PATH))
        fingerprints.append(state.snapshot().fingerprint())
        routes.append([state.route(o, d, a).to_dict()
                       for o in ("west_hub", "residential_north")
                       for d in ("airport", "residential_south")
                       for a in ("astar", "dijkstra")])
    assert fingerprints[0] == fingerprints[1]
    assert routes[0] == routes[1]


def test_snapshot_restore_round_trip(city):
    state = SimulationState.from_scenario(city)
    snap = state.snapshot()
    before = state.route("north_station", "airport").to_dict()
    state.set_utilization("E007", 4.0)
    state.advance(15)
    assert state.snapshot().fingerprint() != snap.fingerprint()
    state.restore(snap)
    assert state.snapshot() == snap
    assert state.route("north_station", "airport").to_dict() == before
