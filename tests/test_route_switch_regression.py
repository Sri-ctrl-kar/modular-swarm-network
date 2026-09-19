"""M1.1 regression: congestion must be able to change the optimal route.

This guards the whole M1 chain end to end — congestion model -> dynamic edge
cost -> graph -> routing -> chosen path — by proving that severe traffic on one
edge of the optimal route makes the router switch to a genuinely cheaper
alternative, and that undoing the traffic restores the original answer exactly.

THE SCENARIO (fixed, deterministic, taken from the seed-42 baseline city)

    origin       north_station  (North Station)
    destination  east_hub       (East Hub)

    baseline optimum   E009 -> E011   North Station -> Northgate Junction -> East Hub
    alternative        E013 -> E003   North Station -> Central Station    -> East Hub

    congestion applied  E011 set to utilization 3.0
                        (capacity 1500 veh/h -> 4500 veh/h, multiplier x5.15)

The two paths are fully edge-disjoint, so the switch cannot be an artifact of a
shared sub-path. Requirement 7(a) is satisfied by the baseline network itself,
so no purpose-built graph is needed here; ``test_congestion_changes_optimal_route``
in ``test_routing.py`` already covers the minimal hand-built (diamond) case.

Severe but VALID congestion: the count stays a non-negative integer, so every
Edge invariant still holds and the multiplier stays >= 1 (asserted below).
"""

import pytest

from app.config import DEFAULT_SCENARIO_PATH
from app.network.builder import load_scenario
from app.network.synthetic_city import build_synthetic_city
from app.routing import astar, dijkstra
from app.simulation.state import SimulationState

ORIGIN = "north_station"
DESTINATION = "east_hub"
CONGESTED_EDGE = "E011"
SEVERE_UTILIZATION = 3.0

BASELINE_EDGES = ("E009", "E011")
ALTERNATIVE_EDGES = ("E013", "E003")


def _path_cost(graph, edge_ids):
    """Travel time of an explicit edge sequence under the graph's CURRENT traffic."""
    return sum(graph.get_edge(edge_id).current_travel_time_min for edge_id in edge_ids)


def _is_feasible_path(graph, origin, destination, edge_ids):
    """True if edge_ids is a connected chain of real edges from origin to destination."""
    node = origin
    for edge_id in edge_ids:
        if not graph.has_edge(edge_id):
            return False
        edge = graph.get_edge(edge_id)
        if edge.source != node:
            return False
        node = edge.destination
    return node == destination


def _congest(graph, edge_id=CONGESTED_EDGE, utilization=SEVERE_UTILIZATION):
    """Apply severe but valid congestion. Returns (previous_count, new_count)."""
    edge = graph.get_edge(edge_id)
    previous = edge.current_vehicle_count
    new_count = int(round(edge.capacity_vehicles_per_hour * utilization))
    graph.set_vehicle_count(edge_id, new_count)
    return previous, new_count


# --- requirement 2: the pair really does have two distinct feasible routes ----
def test_pair_has_at_least_two_distinct_feasible_routes(city):
    graph = city.graph
    assert _is_feasible_path(graph, ORIGIN, DESTINATION, BASELINE_EDGES)
    assert _is_feasible_path(graph, ORIGIN, DESTINATION, ALTERNATIVE_EDGES)
    assert BASELINE_EDGES != ALTERNATIVE_EDGES
    # fully edge-disjoint: the alternative is a real detour, not a shared sub-path
    assert not set(BASELINE_EDGES) & set(ALTERNATIVE_EDGES)


def test_baseline_optimum_is_the_expected_route(city):
    route = astar(city.graph, ORIGIN, DESTINATION)
    assert route.edge_ids == BASELINE_EDGES
    assert route.node_ids == (ORIGIN, "jct_northgate", DESTINATION)


# --- requirements 3-7: congestion switches the optimal route ------------------
def test_severe_congestion_switches_the_optimal_route(city):
    graph = city.graph

    before = astar(graph, ORIGIN, DESTINATION)
    recorded_cost, recorded_edges = before.total_cost, before.edge_ids
    assert recorded_edges == BASELINE_EDGES

    _congest(graph)

    after = astar(graph, ORIGIN, DESTINATION)
    assert after.edge_ids != recorded_edges, "congestion did not change the route"
    assert after.edge_ids == ALTERNATIVE_EDGES
    assert after.total_cost > recorded_cost  # the detour costs more than free-flow did
    assert CONGESTED_EDGE not in after.edge_ids


# --- requirement 8: the new route is genuinely cheaper under the new costs ----
def test_switched_route_is_cheaper_under_modified_costs(city):
    graph = city.graph
    original_edges = astar(graph, ORIGIN, DESTINATION).edge_ids

    _congest(graph)

    new_route = astar(graph, ORIGIN, DESTINATION)
    old_path_now = _path_cost(graph, original_edges)
    new_path_now = _path_cost(graph, new_route.edge_ids)

    assert new_path_now < old_path_now, (new_path_now, old_path_now)
    assert new_route.total_cost == pytest.approx(new_path_now)
    # the router really did pick the cheapest of the two, not merely a different one
    assert new_path_now == pytest.approx(min(new_path_now, old_path_now))


def test_congested_edge_got_more_expensive_not_the_alternative(city):
    """The switch must come from the congested edge, not from the detour changing."""
    graph = city.graph
    alternative_before = _path_cost(graph, ALTERNATIVE_EDGES)
    congested_before = graph.get_edge(CONGESTED_EDGE).current_travel_time_min

    _congest(graph)

    assert graph.get_edge(CONGESTED_EDGE).current_travel_time_min > congested_before
    assert _path_cost(graph, ALTERNATIVE_EDGES) == pytest.approx(alternative_before)


# --- requirement 9: Dijkstra and A* still agree ------------------------------
def test_dijkstra_and_astar_agree_before_during_and_after(city):
    graph = city.graph

    for stage in ("before", "after", "restored"):
        if stage == "after":
            previous, _ = _congest(graph)
        if stage == "restored":
            graph.set_vehicle_count(CONGESTED_EDGE, previous)

        a, d = astar(graph, ORIGIN, DESTINATION), dijkstra(graph, ORIGIN, DESTINATION)
        assert a.total_cost == pytest.approx(d.total_cost, rel=1e-12, abs=1e-9), stage
        assert a.edge_ids == d.edge_ids, stage
        assert a.nodes_visited <= d.nodes_visited, stage


# --- requirement 10: deterministic across repeated executions -----------------
def test_route_switch_is_deterministic_across_repeated_executions():
    """Replay the whole before/after experiment on fresh networks; results must
    be bit-identical every time (same seed, no randomness anywhere)."""
    runs = []
    for _ in range(5):
        graph = build_synthetic_city(42).graph
        before = astar(graph, ORIGIN, DESTINATION)
        _congest(graph)
        after = astar(graph, ORIGIN, DESTINATION)
        runs.append((before.to_dict(), after.to_dict(), _path_cost(graph, before.edge_ids)))

    assert all(run == runs[0] for run in runs)
    # and the recorded totals are exact, not just consistent
    assert runs[0][0]["edge_ids"] == list(BASELINE_EDGES)
    assert runs[0][1]["edge_ids"] == list(ALTERNATIVE_EDGES)


def test_switch_is_identical_when_loaded_from_the_committed_scenario():
    """The committed scenario file must reproduce the same switch as the generator."""
    from_file = SimulationState.from_scenario(load_scenario(DEFAULT_SCENARIO_PATH))
    from_generator = SimulationState(build_synthetic_city(42).graph)

    results = []
    for state in (from_file, from_generator):
        before = state.route(ORIGIN, DESTINATION).to_dict()
        state.set_utilization(CONGESTED_EDGE, SEVERE_UTILIZATION)
        results.append((before, state.route(ORIGIN, DESTINATION).to_dict()))

    assert results[0] == results[1]
    assert results[0][1]["edge_ids"] == list(ALTERNATIVE_EDGES)


# --- restoring the original traffic restores the original route ---------------
def test_restoring_traffic_restores_original_route_and_cost(city):
    graph = city.graph
    before = astar(graph, ORIGIN, DESTINATION)

    previous_count, congested_count = _congest(graph)
    assert astar(graph, ORIGIN, DESTINATION).edge_ids == ALTERNATIVE_EDGES

    graph.set_vehicle_count(CONGESTED_EDGE, previous_count)

    restored = astar(graph, ORIGIN, DESTINATION)
    assert restored.edge_ids == before.edge_ids
    assert restored.total_cost == before.total_cost  # exact, not approximate
    assert restored.to_dict() == before.to_dict()
    assert congested_count != previous_count  # the round trip was not a no-op


def test_snapshot_restore_restores_original_route_and_cost(city):
    """Same round trip through SimulationState's snapshot/restore API."""
    state = SimulationState.from_scenario(city)
    snapshot = state.snapshot()
    before = state.route(ORIGIN, DESTINATION).to_dict()

    state.set_utilization(CONGESTED_EDGE, SEVERE_UTILIZATION)
    assert state.route(ORIGIN, DESTINATION).to_dict()["edge_ids"] == list(ALTERNATIVE_EDGES)
    assert state.snapshot().fingerprint() != snapshot.fingerprint()

    state.restore(snapshot)

    assert state.snapshot() == snapshot
    assert state.snapshot().fingerprint() == snapshot.fingerprint()
    assert state.route(ORIGIN, DESTINATION).to_dict() == before
    assert state.route(ORIGIN, DESTINATION, "dijkstra").to_dict()["edge_ids"] == list(BASELINE_EDGES)


# --- the congestion used stays inside the model's guarantees ------------------
def test_severe_congestion_is_valid_and_preserves_model_invariants(city):
    graph = city.graph
    _congest(graph)
    edge = graph.get_edge(CONGESTED_EDGE)

    assert isinstance(edge.current_vehicle_count, int) and edge.current_vehicle_count >= 0
    assert edge.utilization == pytest.approx(SEVERE_UTILIZATION)
    assert edge.is_overloaded
    for each in graph.edges():
        assert each.congestion_multiplier >= 1.0
        assert each.current_travel_time_min >= each.base_travel_time_min
