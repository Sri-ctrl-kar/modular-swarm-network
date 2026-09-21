"""M4 swarm models: validation, the state machine and snapshot fingerprints."""

import pytest

from app.errors import ModelValidationError, SwarmStateError
from app.swarm import (
    ALLOWED_SWARM_TRANSITIONS,
    MIN_SWARM_SIZE,
    SharedCorridor,
    Swarm,
    SwarmConfig,
    SwarmSnapshot,
    SwarmStatus,
)
from app.errors import SwarmConfigError


def _corridor(**overrides):
    kwargs = dict(edge_ids=("C1", "C2", "C3"), origin_node_id="H0", divergence_node_id="H3",
                  distance_km=12.0, travel_time_min=15.0)
    return SharedCorridor(**(kwargs | overrides))


def _swarm(**overrides):
    kwargs = dict(swarm_id="SW00001", pod_ids=("POD00000", "POD00001"),
                  leader_pod_id="POD00000", corridor=_corridor(), formation_time_min=10.0)
    return Swarm(**(kwargs | overrides))


# --- SharedCorridor ---------------------------------------------------------
def test_corridor_fields():
    corridor = _corridor()
    assert corridor.edge_count == 3
    assert corridor.origin_node_id == "H0" and corridor.divergence_node_id == "H3"
    assert corridor.to_dict()["edge_ids"] == ["C1", "C2", "C3"]


def test_corridor_requires_edges_and_rejects_repeats():
    with pytest.raises(ModelValidationError, match="at least one edge"):
        _corridor(edge_ids=())
    with pytest.raises(ModelValidationError, match="repeats an edge"):
        _corridor(edge_ids=("C1", "C1"))


@pytest.mark.parametrize("field,value", [
    ("distance_km", -1.0), ("distance_km", float("nan")), ("travel_time_min", -0.5),
    ("origin_node_id", ""), ("divergence_node_id", " H3 "), ("edge_ids", ("",)),
])
def test_corridor_validation(field, value):
    with pytest.raises(ModelValidationError):
        _corridor(**{field: value})


# --- Swarm size and membership ---------------------------------------------
def test_a_single_pod_is_not_a_swarm():
    assert MIN_SWARM_SIZE == 2
    with pytest.raises(ModelValidationError, match="a single pod is not a swarm"):
        _swarm(pod_ids=("POD00000",), leader_pod_id="POD00000")
    with pytest.raises(ModelValidationError, match="at least 2 pods"):
        _swarm(pod_ids=(), leader_pod_id="POD00000")


def test_pod_ids_are_stored_sorted_whatever_order_they_arrive_in():
    unsorted = _swarm(pod_ids=("POD00009", "POD00002", "POD00005"), leader_pod_id="POD00002")
    assert unsorted.pod_ids == ("POD00002", "POD00005", "POD00009")
    assert unsorted.size == 3
    # Membership equality does not depend on insertion order.
    other = _swarm(pod_ids=("POD00005", "POD00009", "POD00002"), leader_pod_id="POD00002")
    assert other.pod_ids == unsorted.pod_ids


def test_duplicate_members_rejected():
    with pytest.raises(ModelValidationError, match="lists a pod twice"):
        _swarm(pod_ids=("POD00000", "POD00000"))


def test_leader_must_be_a_member():
    with pytest.raises(ModelValidationError, match="is not a member"):
        _swarm(leader_pod_id="POD00099")


def test_swarm_defaults_position_to_the_corridor_origin():
    swarm = _swarm()
    assert swarm.current_node_id == "H0"
    assert swarm.corridor_edges_completed == 0 and not swarm.corridor_is_complete
    assert swarm.status is SwarmStatus.FORMING
    assert swarm.members_still_travelling == swarm.pod_ids


@pytest.mark.parametrize("field,value", [
    ("swarm_id", ""), ("formation_time_min", -1.0), ("corridor", "C1,C2"),
    ("corridor_edges_completed", -1), ("corridor_edges_completed", 99),
    ("shared_distance_km", -1.0), ("split_time_min", -2.0),
])
def test_swarm_validation(field, value):
    with pytest.raises(ModelValidationError):
        _swarm(**{field: value})


# --- state machine ----------------------------------------------------------
def test_state_machine_shape():
    assert {s.value for s in SwarmStatus} == {"forming", "active", "splitting", "completed"}
    assert set(ALLOWED_SWARM_TRANSITIONS) == set(SwarmStatus)
    assert ALLOWED_SWARM_TRANSITIONS[SwarmStatus.COMPLETED] == frozenset()


def test_happy_path_lifecycle():
    swarm = _swarm()
    swarm.activate()
    assert swarm.is_active
    swarm.record_progress(node_id="H3", edges_completed=3, distance_km=12.0, travel_time_min=15.0)
    assert swarm.corridor_is_complete
    swarm.begin_split(time_min=25.0)
    assert swarm.status is SwarmStatus.SPLITTING and swarm.split_time_min == 25.0
    swarm.complete()
    assert swarm.is_finished
    assert swarm.duration_min(now_min=99.0) == pytest.approx(15.0)   # 25 - 10, not 99 - 10


@pytest.mark.parametrize("start,target", [
    (SwarmStatus.FORMING, SwarmStatus.SPLITTING),
    (SwarmStatus.ACTIVE, SwarmStatus.FORMING),
    (SwarmStatus.SPLITTING, SwarmStatus.ACTIVE),
    (SwarmStatus.COMPLETED, SwarmStatus.ACTIVE),
    (SwarmStatus.COMPLETED, SwarmStatus.SPLITTING),
])
def test_illegal_transitions_rejected(start, target):
    swarm = _swarm(status=start)
    with pytest.raises(SwarmStateError, match="illegal transition"):
        swarm.set_status(target)
    assert swarm.status is start


def test_same_status_and_unknown_status_rejected():
    swarm = _swarm()
    with pytest.raises(SwarmStateError, match="already forming"):
        swarm.set_status(SwarmStatus.FORMING)
    with pytest.raises(SwarmStateError, match="unknown swarm status"):
        swarm.set_status("magnetically_linked")      # M4 has no such state


def test_progress_cannot_go_backwards_or_past_the_corridor():
    swarm = _swarm()
    swarm.activate()
    swarm.record_progress(node_id="H2", edges_completed=2, distance_km=8.0, travel_time_min=10.0)
    with pytest.raises(ModelValidationError, match="cannot go backwards"):
        swarm.record_progress(node_id="H1", edges_completed=1, distance_km=4.0, travel_time_min=5.0)
    with pytest.raises(ModelValidationError, match="exceeds the corridor"):
        swarm.record_progress(node_id="H3", edges_completed=9, distance_km=12.0, travel_time_min=15.0)


# --- capacity and pod-km ----------------------------------------------------
def test_capacity_is_the_sum_of_pod_capacities():
    """A swarm's seats are its pods' seats added up — it is not one cabin."""
    swarm = _swarm(pod_ids=("POD00000", "POD00001", "POD00002"), leader_pod_id="POD00000")
    assert swarm.capacity([4, 4, 4]) == 12
    assert swarm.capacity([4, 6, 2]) == 12
    with pytest.raises(ModelValidationError, match="capacities"):
        swarm.capacity([4, 4])


def test_coordinated_pod_km_does_not_pretend_pods_merge():
    """Four pods over a 5 km corridor is 20 pod-km and 5 corridor-km."""
    swarm = _swarm(pod_ids=tuple(f"POD{i:05d}" for i in range(4)), leader_pod_id="POD00000",
                   corridor=_corridor(edge_ids=("C1",), distance_km=5.0, travel_time_min=6.0,
                                      divergence_node_id="H1"))
    swarm.activate()
    swarm.record_progress(node_id="H1", edges_completed=1, distance_km=5.0, travel_time_min=6.0)
    assert swarm.shared_distance_km == 5.0        # corridor traversed once
    assert swarm.coordinated_pod_km() == 20.0     # but four pods still drove it
    assert swarm.size == 4


# --- membership changes -----------------------------------------------------
def test_members_can_leave_early():
    swarm = _swarm(pod_ids=("POD00000", "POD00001", "POD00002"), leader_pod_id="POD00000")
    swarm.mark_departed("POD00001")
    assert swarm.departed_pod_ids == ("POD00001",)
    assert swarm.members_still_travelling == ("POD00000", "POD00002")
    swarm.mark_departed("POD00001")               # idempotent
    assert swarm.departed_pod_ids == ("POD00001",)
    with pytest.raises(ModelValidationError, match="not in swarm"):
        swarm.mark_departed("POD00099")


def test_successors_are_recorded_sorted_and_deduplicated():
    swarm = _swarm()
    swarm.add_successor("SW00005")
    swarm.add_successor("SW00003")
    swarm.add_successor("SW00005")
    assert swarm.successor_swarm_ids == ("SW00003", "SW00005")


def test_to_dict_exposes_both_corridor_and_pod_km():
    swarm = _swarm()
    swarm.activate()
    swarm.record_progress(node_id="H3", edges_completed=3, distance_km=12.0, travel_time_min=15.0)
    data = swarm.to_dict()
    assert data["shared_distance_km"] == 12.0
    assert data["coordinated_pod_km"] == 24.0     # 2 pods
    assert data["corridor"]["edge_ids"] == ["C1", "C2", "C3"]
    assert data["size"] == 2 and data["leader_pod_id"] == "POD00000"


# --- SwarmSnapshot ----------------------------------------------------------
def _snapshot(**overrides):
    kwargs = dict(time_min=30.0,
                  swarms=(("SW00001", "active", "POD00000|POD00001", "H2", 8.0, 2),
                          ("SW00002", "completed", "POD00004|POD00007", "H3", 12.0, 3)),
                  status_counts=(("active", 1), ("completed", 1)),
                  formation_count=2, split_count=1)
    return SwarmSnapshot(**(kwargs | overrides))


def test_snapshot_fingerprint_is_stable_and_order_independent():
    a = _snapshot()
    b = _snapshot(swarms=(("SW00002", "completed", "POD00004|POD00007", "H3", 12.0, 3),
                          ("SW00001", "active", "POD00000|POD00001", "H2", 8.0, 2)),
                  status_counts=(("completed", 1), ("active", 1)))
    assert a == b and a.fingerprint() == b.fingerprint()
    assert a.fingerprint() == _snapshot().fingerprint()
    assert len(a.fingerprint()) == 64


def test_snapshot_fingerprint_reflects_content():
    base = _snapshot().fingerprint()
    assert _snapshot(time_min=31.0).fingerprint() != base
    assert _snapshot(formation_count=3).fingerprint() != base
    assert _snapshot(split_count=2).fingerprint() != base
    assert _snapshot(swarms=(("SW00001", "splitting", "POD00000|POD00001", "H2", 8.0, 2),
                             ("SW00002", "completed", "POD00004|POD00007", "H3", 12.0, 3))
                     ).fingerprint() != base


def test_snapshot_rounds_distance_so_float_noise_cannot_leak_in():
    noisy = _snapshot(swarms=(("SW00001", "active", "POD00000|POD00001", "H2", 8.0 + 1e-12, 2),
                              ("SW00002", "completed", "POD00004|POD00007", "H3", 12.0, 3)))
    assert noisy.fingerprint() == _snapshot().fingerprint()


def test_snapshot_rejects_duplicates_and_bad_values():
    with pytest.raises(ModelValidationError, match="duplicate swarm id"):
        _snapshot(swarms=(("SW00001", "active", "a", "H1", 1.0, 1),
                          ("SW00001", "active", "b", "H1", 1.0, 1)))
    with pytest.raises(ModelValidationError, match="duplicate swarm status"):
        _snapshot(status_counts=(("active", 1), ("active", 2)))
    with pytest.raises(ModelValidationError):
        _snapshot(time_min=-1.0)
    with pytest.raises(ModelValidationError):
        _snapshot(formation_count=-1)


def test_snapshot_json_round_trip():
    import json
    snapshot = _snapshot()
    assert json.loads(json.dumps(snapshot.as_dict())) == snapshot.as_dict()


# --- SwarmConfig ------------------------------------------------------------
@pytest.mark.parametrize("field,value", [
    ("min_shared_edges", 0), ("min_shared_edges", 1.5),
    ("max_swarm_size", 1), ("max_swarm_size", 2.5),
    ("min_shared_distance_km", 0.0), ("min_shared_distance_km", -1.0),
    ("min_shared_route_fraction", -0.1), ("min_shared_route_fraction", 1.1),
    ("max_formation_delay_min", -1.0), ("min_formation_stability_min", -1.0),
    ("formation_occupancy_factor", -0.1), ("formation_occupancy_factor", 1.1),
])
def test_invalid_swarm_config_rejected(field, value):
    with pytest.raises(SwarmConfigError):
        SwarmConfig(**{field: value})


def test_config_documents_that_no_ai_is_involved_and_no_fuel_saving_claimed():
    data = SwarmConfig().to_dict()
    assert "no AI/LLM is involved" in data["compatibility"]["note"]
    assert "no AI/LLM is involved" in data["data_provenance"]
    note = data["road_occupancy"]["note"].lower()
    assert "aerodynamic" in note and "fuel" in note and "emissions" in note
    assert "road space" in note
