"""M3 pod model: validation, the state machine, trip records and snapshots."""

import pytest

from app.errors import ModelValidationError, PodStateError
from app.fleet.models import (
    ALLOWED_TRANSITIONS,
    TIMED_OUT_PREFIX,
    FleetSnapshot,
    Pod,
    PodStatus,
    TripRecord,
    TripStatus,
)
from app.models.route import Route


def _pod(**overrides):
    kwargs = dict(pod_id="POD00000", capacity=4, current_node_id="market")
    return Pod(**(kwargs | overrides))


def _route(origin="A", destination="D", node_ids=("A", "B", "D"), edge_ids=("AB", "BD"),
           distance=4.0, time_min=6.0):
    return Route(origin=origin, destination=destination, node_ids=node_ids, edge_ids=edge_ids,
                 total_distance_km=distance, total_travel_time_min=time_min, total_cost=time_min,
                 cost_metric="travel_time_min", nodes_visited=3, algorithm="astar")


# --- validation --------------------------------------------------------------
def test_pod_defaults():
    pod = _pod()
    assert pod.status is PodStatus.IDLE
    assert pod.battery_percent == 100.0
    assert pod.available_seats == 4 and pod.occupied_seats == 0
    assert pod.assigned_trip_id is None and pod.route is None
    assert pod.route_progress == 0.0 and pod.remaining_edge_ids == ()
    assert pod.total_distance_km == 0.0 and pod.completed_trip_count == 0


@pytest.mark.parametrize("capacity", [0, -1, 1.5, "4", True, None])
def test_pod_rejects_invalid_capacity(capacity):
    with pytest.raises(ModelValidationError):
        _pod(capacity=capacity)


@pytest.mark.parametrize("battery", [-0.1, 100.1, float("nan"), float("inf"), "80", True])
def test_pod_rejects_invalid_battery(battery):
    with pytest.raises(ModelValidationError):
        _pod(battery_percent=battery)


def test_pod_accepts_battery_bounds():
    assert _pod(battery_percent=0.0).battery_percent == 0.0
    assert _pod(battery_percent=100.0).battery_percent == 100.0


@pytest.mark.parametrize("node_id", ["", "   ", " market ", None, 42])
def test_pod_rejects_invalid_node_id(node_id):
    with pytest.raises(ModelValidationError):
        _pod(current_node_id=node_id)


@pytest.mark.parametrize("pod_id", ["", "  ", " POD1 ", None])
def test_pod_rejects_invalid_pod_id(pod_id):
    with pytest.raises(ModelValidationError):
        _pod(pod_id=pod_id)


def test_pod_rejects_occupancy_above_capacity():
    with pytest.raises(ModelValidationError, match="exceeds capacity"):
        _pod(capacity=2, occupied_seats=3)


def test_pod_rejects_unknown_status_and_bad_route():
    with pytest.raises(ModelValidationError, match="status must be one of"):
        _pod(status="platooning")          # no swarm states exist in M3
    with pytest.raises(ModelValidationError, match="route must be a Route"):
        _pod(route="A->D")
    with pytest.raises(ModelValidationError, match="route_index"):
        _pod(route=_route(), route_index=5)


@pytest.mark.parametrize("value", [-1.0, float("nan"), "5"])
def test_pod_rejects_negative_counters(value):
    with pytest.raises(ModelValidationError):
        _pod(total_distance_km=value)


# --- state machine ----------------------------------------------------------
def test_state_machine_has_no_swarm_states():
    """M3 is explicitly pre-swarm: the enum must not grow platoon states yet."""
    assert {s.value for s in PodStatus} == {"idle", "assigned", "traveling", "arrived", "charging"}
    assert set(ALLOWED_TRANSITIONS) == set(PodStatus)


@pytest.mark.parametrize("start,target", [
    (PodStatus.IDLE, PodStatus.TRAVELING),
    (PodStatus.IDLE, PodStatus.ARRIVED),
    (PodStatus.TRAVELING, PodStatus.IDLE),
    (PodStatus.TRAVELING, PodStatus.CHARGING),
    (PodStatus.CHARGING, PodStatus.ASSIGNED),
    (PodStatus.ARRIVED, PodStatus.TRAVELING),
])
def test_illegal_transitions_are_rejected(start, target):
    pod = _pod(status=start)
    with pytest.raises(PodStateError, match="illegal transition"):
        pod.set_status(target)
    assert pod.status is start          # unchanged after a rejected transition


def test_transition_to_same_status_is_rejected():
    pod = _pod()
    with pytest.raises(PodStateError, match="already idle"):
        pod.set_status(PodStatus.IDLE)


def test_unknown_status_transition_is_rejected():
    with pytest.raises(PodStateError, match="unknown pod status"):
        _pod().set_status("swarming")


def test_full_happy_path_lifecycle():
    pod = _pod(current_node_id="A")
    route = _route()

    pod.assign("T000001", route, party_size=2)
    assert pod.status is PodStatus.ASSIGNED
    assert pod.assigned_trip_id == "T000001"
    assert pod.occupied_seats == 2 and pod.available_seats == 2
    assert pod.route is route and pod.remaining_edge_ids == ("AB", "BD")

    pod.start_travel("AB", 3.0, 2.0, 0.36)
    assert pod.status is PodStatus.TRAVELING and pod.current_edge_id == "AB"

    assert pod.advance_on_edge(3.0) == pytest.approx(3.0)
    assert pod.edge_is_complete
    assert pod.complete_edge("B") == (2.0, 0.36)
    assert pod.current_node_id == "B" and pod.route_index == 1
    assert pod.route_progress == pytest.approx(0.5)
    assert not pod.route_is_complete

    pod.enter_edge("BD", 3.0, 2.0, 0.36)
    pod.advance_on_edge(3.0)
    pod.complete_edge("D")
    assert pod.route_is_complete and pod.current_node_id == "D"

    pod.arrive()
    assert pod.status is PodStatus.ARRIVED and pod.completed_trip_count == 1

    assert pod.release() == "T000001"
    assert pod.status is PodStatus.IDLE
    assert pod.assigned_trip_id is None and pod.route is None and pod.occupied_seats == 0
    assert pod.total_distance_km == pytest.approx(4.0)
    assert pod.total_energy_kwh == pytest.approx(0.72)
    assert pod.total_travel_time_min == pytest.approx(6.0)


# --- capacity and occupancy -------------------------------------------------
def test_can_accept_respects_capacity_and_state():
    pod = _pod(capacity=4, current_node_id="A")
    assert pod.can_accept(4) and not pod.can_accept(5)
    pod.assign("T1", _route(), party_size=4)
    assert not pod.can_accept(1)        # no longer idle and no seats left


def test_assign_rejects_oversized_party():
    pod = _pod(capacity=2, current_node_id="A")
    with pytest.raises(PodStateError, match="exceeds .* available seats"):
        pod.assign("T1", _route(), party_size=3)
    assert pod.status is PodStatus.IDLE and pod.assigned_trip_id is None


def test_assign_requires_idle_pod_and_matching_origin():
    pod = _pod(current_node_id="A")
    pod.assign("T1", _route(), party_size=1)
    with pytest.raises(PodStateError, match="must be idle"):
        pod.assign("T2", _route(), party_size=1)

    elsewhere = _pod(current_node_id="C")
    with pytest.raises(PodStateError, match="route starts at"):
        elsewhere.assign("T3", _route(), party_size=1)


@pytest.mark.parametrize("party", [0, -1, 1.5, True])
def test_assign_rejects_invalid_party_size(party):
    with pytest.raises(ModelValidationError):
        _pod(current_node_id="A").assign("T1", _route(), party_size=party)


def test_assign_rejects_non_route():
    with pytest.raises(ModelValidationError, match="route must be a Route"):
        _pod(current_node_id="A").assign("T1", "A->D", party_size=1)


# --- movement guards --------------------------------------------------------
def test_cannot_move_without_being_assigned_and_travelling():
    pod = _pod(current_node_id="A")
    with pytest.raises(PodStateError, match="must be assigned"):
        pod.start_travel("AB", 3.0, 2.0, 0.36)
    with pytest.raises(PodStateError, match="not travelling"):
        pod.advance_on_edge(1.0)
    with pytest.raises(PodStateError, match="no edge to complete"):
        pod.complete_edge("B")


def test_entering_the_wrong_edge_is_rejected():
    pod = _pod(current_node_id="A")
    pod.assign("T1", _route(), party_size=1)
    with pytest.raises(PodStateError, match="next route edge is"):
        pod.start_travel("BD", 3.0, 2.0, 0.36)      # BD is second, not first


def test_completing_an_unfinished_edge_is_rejected():
    pod = _pod(current_node_id="A")
    pod.assign("T1", _route(), party_size=1)
    pod.start_travel("AB", 3.0, 2.0, 0.36)
    pod.advance_on_edge(1.0)
    with pytest.raises(PodStateError, match="is not finished"):
        pod.complete_edge("B")


def test_advance_never_overshoots_the_edge():
    pod = _pod(current_node_id="A")
    pod.assign("T1", _route(), party_size=1)
    pod.start_travel("AB", 3.0, 2.0, 0.36)
    assert pod.advance_on_edge(10.0) == pytest.approx(3.0)      # only 3 min were available
    assert pod.current_edge_elapsed_min == pytest.approx(3.0)
    assert pod.total_travel_time_min == pytest.approx(3.0)


def test_arrive_requires_the_whole_route():
    pod = _pod(current_node_id="A")
    pod.assign("T1", _route(), party_size=1)
    pod.start_travel("AB", 3.0, 2.0, 0.36)
    pod.advance_on_edge(3.0)
    pod.complete_edge("B")
    with pytest.raises(PodStateError, match="route edge"):
        pod.arrive()


def test_release_is_only_valid_from_arrived_or_assigned():
    idle = _pod()
    with pytest.raises(PodStateError, match="cannot be released"):
        idle.release()

    cancelled = _pod(current_node_id="A")
    cancelled.assign("T1", _route(), party_size=2)
    assert cancelled.release() == "T1"       # an assigned pod may be stood down
    assert cancelled.status is PodStatus.IDLE and cancelled.occupied_seats == 0


# --- battery ----------------------------------------------------------------
def test_discharge_clamps_at_zero_and_never_goes_negative():
    pod = _pod(battery_percent=10.0)
    assert pod.discharge(4.0) == pytest.approx(4.0)
    assert pod.battery_percent == pytest.approx(6.0)
    assert pod.discharge(999.0) == pytest.approx(6.0)     # only what was left
    assert pod.battery_percent == 0.0
    assert pod.discharge(5.0) == 0.0
    assert pod.battery_percent == 0.0


def test_charge_clamps_at_one_hundred():
    pod = _pod(battery_percent=98.0)
    assert pod.charge(1.0) == pytest.approx(1.0)
    assert pod.charge(50.0) == pytest.approx(1.0)
    assert pod.battery_percent == 100.0


@pytest.mark.parametrize("value", [-1.0, float("nan"), "5"])
def test_battery_changes_reject_bad_values(value):
    with pytest.raises(ModelValidationError):
        _pod().discharge(value)
    with pytest.raises(ModelValidationError):
        _pod().charge(value)


def test_charging_cycle():
    pod = _pod(battery_percent=5.0)
    pod.begin_charging()
    assert pod.status is PodStatus.CHARGING
    pod.charge(40.0)
    pod.finish_charging()
    assert pod.status is PodStatus.IDLE and pod.battery_percent == pytest.approx(45.0)
    with pytest.raises(PodStateError, match="not charging"):
        pod.finish_charging()


def test_charging_pod_cannot_be_assigned():
    pod = _pod(current_node_id="A", battery_percent=5.0)
    pod.begin_charging()
    assert not pod.can_accept(1)
    with pytest.raises(PodStateError, match="must be idle"):
        pod.assign("T1", _route(), party_size=1)


# --- TripRecord -------------------------------------------------------------
def test_trip_record_timings_and_resolution():
    rec = TripRecord(trip_id="T1", party_size=2, origin_node_id="a", destination_node_id="b",
                     request_time_min=100.0)
    assert rec.status is TripStatus.PENDING
    assert not rec.is_resolved
    assert rec.waiting_time_min is None and rec.completion_time_min is None

    rec.assigned_time_min = 103.0
    rec.completed_time_min = 120.0
    rec.status = TripStatus.COMPLETED
    assert rec.waiting_time_min == pytest.approx(3.0)
    assert rec.completion_time_min == pytest.approx(20.0)
    assert rec.is_resolved


def test_failed_trip_record_is_resolved_and_keeps_its_reason():
    rec = TripRecord(trip_id="T1", party_size=1, origin_node_id="a", destination_node_id="b",
                     request_time_min=0.0, status=TripStatus.FAILED,
                     failure_reason=f"{TIMED_OUT_PREFIX} 60 min of the request")
    assert rec.is_resolved
    assert rec.to_dict()["failure_reason"].startswith(TIMED_OUT_PREFIX)


@pytest.mark.parametrize("field,value", [
    ("party_size", 0), ("party_size", "2"), ("trip_id", ""),
    ("request_time_min", -1.0), ("origin_node_id", " a "),
])
def test_trip_record_validation(field, value):
    kwargs = dict(trip_id="T1", party_size=1, origin_node_id="a", destination_node_id="b",
                  request_time_min=0.0) | {field: value}
    with pytest.raises(ModelValidationError):
        TripRecord(**kwargs)


# --- FleetSnapshot ----------------------------------------------------------
def _snapshot(**overrides):
    kwargs = dict(time_min=480.0,
                  pods=(("POD00000", "idle", "market", 91.5, 0, 2),
                        ("POD00001", "traveling", "east_hub", 40.25, 3, 1)),
                  trip_status_counts=(("completed", 3), ("pending", 1)))
    return FleetSnapshot(**(kwargs | overrides))


def test_snapshot_fingerprint_is_stable_and_order_independent():
    a = _snapshot()
    b = _snapshot(pods=(("POD00001", "traveling", "east_hub", 40.25, 3, 1),
                        ("POD00000", "idle", "market", 91.5, 0, 2)),
                  trip_status_counts=(("pending", 1), ("completed", 3)))
    assert a == b and a.fingerprint() == b.fingerprint()
    assert a.fingerprint() == _snapshot().fingerprint()
    assert len(a.fingerprint()) == 64


def test_snapshot_fingerprint_reflects_content():
    base = _snapshot().fingerprint()
    assert _snapshot(time_min=481.0).fingerprint() != base
    assert _snapshot(trip_status_counts=(("completed", 4),)).fingerprint() != base
    assert _snapshot(pods=(("POD00000", "idle", "market", 91.5, 0, 2),
                           ("POD00001", "traveling", "east_hub", 40.26, 3, 1))).fingerprint() != base


def test_snapshot_rounds_battery_so_float_noise_cannot_leak_in():
    noisy = _snapshot(pods=(("POD00000", "idle", "market", 91.5 + 1e-12, 0, 2),
                            ("POD00001", "traveling", "east_hub", 40.25, 3, 1)))
    assert noisy.fingerprint() == _snapshot().fingerprint()


def test_snapshot_rejects_duplicates_and_bad_values():
    with pytest.raises(ModelValidationError, match="duplicate pod id"):
        _snapshot(pods=(("POD00000", "idle", "a", 50.0, 0, 0),
                        ("POD00000", "idle", "b", 50.0, 0, 0)))
    with pytest.raises(ModelValidationError, match="duplicate trip status"):
        _snapshot(trip_status_counts=(("completed", 1), ("completed", 2)))
    with pytest.raises(ModelValidationError):
        _snapshot(time_min=-1.0)
    with pytest.raises(ModelValidationError):
        _snapshot(pods=(("POD00000", "idle", "a", 101.0, 0, 0),))


def test_snapshot_json_round_trip():
    import json
    snapshot = _snapshot()
    assert json.loads(json.dumps(snapshot.as_dict())) == snapshot.as_dict()
