"""M4 compatibility rules and the deterministic formation algorithm."""

import pytest

from app.fleet import Pod, PodFleet, assign_trip
from app.swarm import (
    CORRIDOR_TOO_BRIEF,
    CORRIDOR_TOO_SMALL_A_SHARE,
    NOT_CO_LOCATED,
    NOT_ENOUGH_PODS,
    TOO_FEW_SHARED_EDGES,
    TOO_MANY_PODS,
    TOO_SHORT_SHARED_DISTANCE,
    SwarmConfig,
    are_compatible,
    build_corridor,
    check_group,
    corridor_metrics,
    plan_formation,
    remaining_distance_km,
    shared_edge_prefix,
    swarm_id_for,
)


def _assigned_pods(graph, destinations, *, party_size=1, capacity=4, origin="H0"):
    """Put one pod per destination at ``origin`` and assign it that trip."""
    from app.demand.models import TripRequest
    pods = [Pod(pod_id=f"POD{i:05d}", capacity=capacity, current_node_id=origin)
            for i in range(len(destinations))]
    fleet = PodFleet(graph, pods)
    for index, destination in enumerate(destinations):
        trip = TripRequest(trip_id=f"T{index:06d}", passenger_id=f"P{index:06d}",
                           origin_node_id=origin, destination_node_id=destination,
                           request_time_min=0.0, party_size=party_size,
                           trip_purpose="commute", time_bucket="morning_peak")
        assert assign_trip(fleet, graph, trip).is_assigned
    return fleet, fleet.pods()


# --- shared prefix ----------------------------------------------------------
def test_shared_prefix_finds_the_common_head():
    assert shared_edge_prefix([("A", "B", "C", "D", "E"), ("A", "B", "C", "D", "F")]) \
        == ("A", "B", "C", "D")


def test_shared_prefix_is_a_prefix_not_any_overlap():
    """Routes that cross the same edge later but not from the start share nothing."""
    assert shared_edge_prefix([("A", "B", "X"), ("C", "B", "X")]) == ()
    assert shared_edge_prefix([("A", "B"), ("B", "A")]) == ()


@pytest.mark.parametrize("sequences,expected", [
    ([], ()),
    ([("A", "B")], ("A", "B")),
    ([("A",), ("A", "B", "C")], ("A",)),
    ([("A", "B"), ("A", "B")], ("A", "B")),
    ([("A", "B"), ("A", "B"), ("A", "C")], ("A",)),
    ([("A",), ()], ()),
])
def test_shared_prefix_cases(sequences, expected):
    assert shared_edge_prefix(sequences) == expected


def test_corridor_metrics_use_the_networks_own_numbers(corridor_graph):
    distance, travel_time = corridor_metrics(corridor_graph, ("C1", "C2", "C3"))
    assert distance == pytest.approx(12.0)
    assert travel_time == pytest.approx(15.0)
    # And they follow congestion, because they read current_travel_time_min.
    corridor_graph.set_vehicle_count("C2", 3600 * 2)
    _, congested = corridor_metrics(corridor_graph, ("C1", "C2", "C3"))
    assert congested > travel_time


def test_build_corridor_describes_the_shared_run(corridor_graph):
    _, pods = _assigned_pods(corridor_graph, ["E", "F"])
    corridor = build_corridor(corridor_graph, pods)
    assert corridor.edge_ids == ("C1", "C2", "C3")
    assert corridor.origin_node_id == "H0"
    assert corridor.divergence_node_id == "H3"          # where the routes stop agreeing
    assert corridor.distance_km == pytest.approx(12.0)
    assert corridor.travel_time_min == pytest.approx(15.0)


def test_build_corridor_needs_two_pods_and_a_shared_head(corridor_graph):
    _, pods = _assigned_pods(corridor_graph, ["E"])
    assert build_corridor(corridor_graph, pods) is None


def test_remaining_distance_tracks_the_pods_own_route(corridor_graph):
    _, pods = _assigned_pods(corridor_graph, ["E", "F"])
    assert remaining_distance_km(corridor_graph, pods[0]) == pytest.approx(20.0)   # to E
    assert remaining_distance_km(corridor_graph, pods[1]) == pytest.approx(16.0)   # to F


# --- the five rules ---------------------------------------------------------
def test_compatible_pods_are_accepted(corridor_graph):
    _, pods = _assigned_pods(corridor_graph, ["E", "F"])
    result = check_group(corridor_graph, pods)
    assert result and result.is_compatible and result.reason is None
    assert result.corridor.edge_ids == ("C1", "C2", "C3")
    assert are_compatible(corridor_graph, pods)


def test_rule_4_size(corridor_graph):
    _, pods = _assigned_pods(corridor_graph, ["E", "F", "E", "F", "E"])
    assert check_group(corridor_graph, pods[:1]).reason == NOT_ENOUGH_PODS
    assert check_group(corridor_graph, ()).reason == NOT_ENOUGH_PODS
    # Five pods against the default maximum of four.
    assert check_group(corridor_graph, pods).reason == TOO_MANY_PODS
    assert check_group(corridor_graph, pods, SwarmConfig(max_swarm_size=5)).is_compatible


def test_rule_1_co_location(corridor_graph):
    """Pods at different nodes never platoon — no teleporting into formation."""
    from app.demand.models import TripRequest
    fleet = PodFleet(corridor_graph, [
        Pod(pod_id="POD00000", capacity=4, current_node_id="H0"),
        Pod(pod_id="POD00001", capacity=4, current_node_id="H1"),
    ])
    for index, (origin, destination) in enumerate((("H0", "E"), ("H1", "F"))):
        trip = TripRequest(trip_id=f"T{index:06d}", passenger_id=f"P{index:06d}",
                           origin_node_id=origin, destination_node_id=destination,
                           request_time_min=0.0, party_size=1, trip_purpose="commute",
                           time_bucket="morning_peak")
        assert assign_trip(fleet, corridor_graph, trip).is_assigned
    assert check_group(corridor_graph, fleet.pods()).reason == NOT_CO_LOCATED


def test_rule_2_minimum_shared_edges(corridor_graph):
    _, pods = _assigned_pods(corridor_graph, ["E", "F"])
    strict = SwarmConfig(min_shared_edges=4)            # the shared run is only 3
    result = check_group(corridor_graph, pods, strict)
    assert not result and result.reason == TOO_FEW_SHARED_EDGES
    assert result.corridor is not None                  # the corridor is still reported


def test_rule_2_minimum_shared_distance(corridor_graph):
    _, pods = _assigned_pods(corridor_graph, ["E", "F"])
    strict = SwarmConfig(min_shared_distance_km=50.0)   # the shared run is 12 km
    assert check_group(corridor_graph, pods, strict).reason == TOO_SHORT_SHARED_DISTANCE


def test_rule_3_corridor_must_be_a_real_share_of_the_journey(corridor_graph):
    _, pods = _assigned_pods(corridor_graph, ["E", "F"])
    # 12 km shared of a 20 km and a 16 km journey: 0.60 and 0.75.
    assert check_group(corridor_graph, pods, SwarmConfig(min_shared_route_fraction=0.6)).is_compatible
    result = check_group(corridor_graph, pods, SwarmConfig(min_shared_route_fraction=0.65))
    assert not result and result.reason == CORRIDOR_TOO_SMALL_A_SHARE


def test_stability_window_rejects_a_corridor_that_would_end_at_once(corridor_graph):
    _, pods = _assigned_pods(corridor_graph, ["E", "F"])
    # The shared run takes 15 min; demand 20 and it is refused.
    assert check_group(corridor_graph, pods, SwarmConfig(min_formation_stability_min=15.0)).is_compatible
    result = check_group(corridor_graph, pods, SwarmConfig(min_formation_stability_min=20.0))
    assert not result and result.reason == CORRIDOR_TOO_BRIEF


def test_pods_going_the_same_place_are_compatible(corridor_graph):
    """Identical destinations share the whole route, which is the easiest case."""
    _, pods = _assigned_pods(corridor_graph, ["E", "E"])
    result = check_group(corridor_graph, pods)
    assert result.is_compatible
    assert result.corridor.edge_ids == ("C1", "C2", "C3", "HE", "ME")
    assert result.corridor.divergence_node_id == "E"


def test_incompatible_routes_are_rejected(corridor_graph):
    """Congest C3 and the E-bound pod takes the X route, sharing nothing with F."""
    corridor_graph.set_vehicle_count("C3", 3600 * 3)
    _, pods = _assigned_pods(corridor_graph, ["E", "F"])
    assert pods[0].remaining_edge_ids == ("X1", "X2")
    assert pods[1].remaining_edge_ids == ("C1", "C2", "C3", "HF")
    result = check_group(corridor_graph, pods)
    assert not result and result.reason == TOO_FEW_SHARED_EDGES
    assert result.corridor is None


def test_compatibility_is_pure(corridor_graph):
    """Checking must not move pods, change the network, or vary between calls."""
    fleet, pods = _assigned_pods(corridor_graph, ["E", "F"])
    before_pods = [p.to_dict() for p in fleet]
    before_traffic = {e.edge_id: e.current_vehicle_count for e in corridor_graph.edges()}
    first = check_group(corridor_graph, pods)
    second = check_group(corridor_graph, pods)
    assert first == second
    assert [p.to_dict() for p in fleet] == before_pods
    assert {e.edge_id: e.current_vehicle_count for e in corridor_graph.edges()} == before_traffic


# --- formation algorithm ----------------------------------------------------
def test_formation_groups_compatible_pods_and_picks_the_lowest_id_as_leader(corridor_graph):
    _, pods = _assigned_pods(corridor_graph, ["E", "F", "E"])
    plan = plan_formation(corridor_graph, pods)
    assert len(plan.groups) == 1
    group = plan.groups[0]
    assert group.pod_ids == ("POD00000", "POD00001", "POD00002")
    assert group.leader_pod_id == "POD00000"
    assert group.corridor.edge_ids == ("C1", "C2", "C3")
    assert plan.independent_pod_ids == ()


def test_formation_respects_max_swarm_size_and_leaves_the_rest(corridor_graph):
    _, pods = _assigned_pods(corridor_graph, ["E"] * 6)
    plan = plan_formation(corridor_graph, pods, SwarmConfig(max_swarm_size=4))
    sizes = sorted(group.size for group in plan.groups)
    assert max(sizes) <= 4
    assert sum(sizes) + len(plan.independent_pod_ids) == 6
    # A busy node can produce more than one swarm.
    assert len(plan.groups) == 2 and sizes == [2, 4]
    assert plan.groups[0].pod_ids == ("POD00000", "POD00001", "POD00002", "POD00003")
    assert plan.groups[1].pod_ids == ("POD00004", "POD00005")


def test_a_lone_pod_stays_independent(corridor_graph):
    _, pods = _assigned_pods(corridor_graph, ["E"])
    plan = plan_formation(corridor_graph, pods)
    assert plan.groups == ()
    assert plan.independent_pod_ids == ("POD00000",)


def test_no_compatible_pods_leaves_everyone_independent(corridor_graph):
    corridor_graph.set_vehicle_count("C3", 3600 * 3)
    _, pods = _assigned_pods(corridor_graph, ["E", "F"])
    plan = plan_formation(corridor_graph, pods)
    assert plan.groups == ()
    assert plan.independent_pod_ids == ("POD00000", "POD00001")


def test_formation_is_deterministic_and_ignores_input_order(corridor_graph):
    _, pods = _assigned_pods(corridor_graph, ["E", "F", "E", "F"])
    forward = plan_formation(corridor_graph, pods)
    backward = plan_formation(corridor_graph, list(reversed(pods)))
    repeated = plan_formation(corridor_graph, pods)
    assert forward.to_dict() == backward.to_dict() == repeated.to_dict()


def test_formation_is_pure(corridor_graph):
    fleet, pods = _assigned_pods(corridor_graph, ["E", "F"])
    before = [p.to_dict() for p in fleet]
    plan_formation(corridor_graph, pods)
    assert [p.to_dict() for p in fleet] == before      # planning moves nothing


def test_pods_at_different_nodes_form_separate_swarms(corridor_graph):
    """Two nodes, two swarms; nodes are walked in sorted order."""
    from app.demand.models import TripRequest
    pods = [Pod(pod_id=f"POD{i:05d}", capacity=4,
                current_node_id="H0" if i < 2 else "H1") for i in range(4)]
    fleet = PodFleet(corridor_graph, pods)
    for index, pod in enumerate(pods):
        trip = TripRequest(trip_id=f"T{index:06d}", passenger_id=f"P{index:06d}",
                           origin_node_id=pod.current_node_id,
                           destination_node_id="E" if index % 2 == 0 else "F",
                           request_time_min=0.0, party_size=1, trip_purpose="commute",
                           time_bucket="morning_peak")
        assert assign_trip(fleet, corridor_graph, trip).is_assigned

    plan = plan_formation(corridor_graph, fleet.pods(), SwarmConfig(min_shared_distance_km=2.0,
                                                                   min_shared_route_fraction=0.3))
    assert len(plan.groups) == 2
    assert plan.groups[0].corridor.origin_node_id == "H0"
    assert plan.groups[1].corridor.origin_node_id == "H1"


def test_swarm_ids_are_stable_and_sortable():
    assert swarm_id_for(1) == "SW00001"
    assert swarm_id_for(28) == "SW00028"
    assert sorted([swarm_id_for(10), swarm_id_for(2)]) == ["SW00002", "SW00010"]
    for bad in (0, -1, 1.5, "1", True):
        with pytest.raises(ValueError):
            swarm_id_for(bad)
