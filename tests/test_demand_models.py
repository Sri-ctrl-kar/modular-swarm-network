"""M2 demand models: validation, OD aggregation and snapshot fingerprints."""

import pytest

from app.demand.models import DemandMatrix, DemandSnapshot, ODCell, Passenger, TripRequest
from app.errors import ModelValidationError, NodeNotFoundError

NODES = ("a", "b", "c")


def _trip(trip_id="T000000", passenger_id="P000000", origin="a", destination="b",
          time_min=480.0, party_size=1, purpose="commute", bucket="morning_peak"):
    return TripRequest(trip_id=trip_id, passenger_id=passenger_id, origin_node_id=origin,
                       destination_node_id=destination, request_time_min=time_min,
                       party_size=party_size, trip_purpose=purpose, time_bucket=bucket)


# --- Passenger ---------------------------------------------------------------
def test_passenger_fields_and_commuter_flag():
    p = Passenger(passenger_id="P000001", home_node_id="a", purpose="commute")
    assert p.is_commuter
    assert Passenger(passenger_id="P2", home_node_id="a", purpose="other").is_commuter is False
    assert p.to_dict() == {"passenger_id": "P000001", "home_node_id": "a", "purpose": "commute"}


@pytest.mark.parametrize("field,value", [
    ("passenger_id", ""), ("passenger_id", "  "), ("passenger_id", None),
    ("home_node_id", ""), ("purpose", ""), ("passenger_id", " P1 "),
])
def test_passenger_rejects_bad_text(field, value):
    kwargs = {"passenger_id": "P1", "home_node_id": "a", "purpose": "commute"} | {field: value}
    with pytest.raises(ModelValidationError):
        Passenger(**kwargs)


# --- TripRequest -------------------------------------------------------------
def test_trip_request_fields():
    trip = _trip(party_size=3, time_min=481.5)
    assert trip.od_pair == ("a", "b")
    assert trip.party_size == 3
    assert trip.request_time_min == pytest.approx(481.5)
    assert trip.to_dict()["time_bucket"] == "morning_peak"


def test_trip_request_rejects_origin_equal_to_destination():
    with pytest.raises(ModelValidationError, match="origin and destination must differ"):
        _trip(origin="a", destination="a")


@pytest.mark.parametrize("party_size", [0, -1, 1.5, "2", True, None])
def test_trip_request_rejects_invalid_party_size(party_size):
    with pytest.raises(ModelValidationError):
        _trip(party_size=party_size)


@pytest.mark.parametrize("time_min", [-1.0, float("nan"), float("inf"), "480", True])
def test_trip_request_rejects_invalid_request_time(time_min):
    with pytest.raises(ModelValidationError):
        _trip(time_min=time_min)


def test_trip_request_accepts_zero_request_time():
    assert _trip(time_min=0.0).request_time_min == 0.0


# --- ODCell ------------------------------------------------------------------
def test_od_cell_rejects_fewer_passengers_than_trips():
    with pytest.raises(ModelValidationError, match="cannot be fewer than trips"):
        ODCell(origin="a", destination="b", trips=3, passengers=2)


def test_od_cell_allows_equal_passengers_and_trips():
    assert ODCell(origin="a", destination="b", trips=3, passengers=3).passengers == 3


# --- DemandMatrix aggregation ------------------------------------------------
def test_matrix_aggregates_trips_and_party_sizes():
    trips = [
        _trip("T0", "P0", "a", "b", party_size=2),
        _trip("T1", "P1", "a", "b", party_size=1),
        _trip("T2", "P2", "b", "c", party_size=4),
    ]
    matrix = DemandMatrix.from_trips(trips, NODES)

    assert matrix.total_trips == 3
    assert matrix.total_demand == 7               # 2 + 1 + 4
    assert matrix.demand_for("a", "b") == 3       # party sizes summed, not trips counted
    assert matrix.trips_for("a", "b") == 2
    assert matrix.demand_for("b", "c") == 4
    assert matrix.pair_count == 2


def test_matrix_returns_zero_for_pairs_without_demand():
    matrix = DemandMatrix.from_trips([_trip()], NODES)
    assert matrix.demand_for("c", "a") == 0       # valid nodes, no trips
    assert matrix.trips_for("c", "a") == 0
    assert matrix.cell("c", "a") is None


def test_matrix_is_sorted_and_order_independent():
    trips = [_trip("T0", "P0", "b", "c"), _trip("T1", "P1", "a", "b"), _trip("T2", "P2", "a", "c")]
    forward = DemandMatrix.from_trips(trips, NODES)
    reverse = DemandMatrix.from_trips(list(reversed(trips)), NODES)

    assert [(c.origin, c.destination) for c in forward.cells] == [("a", "b"), ("a", "c"), ("b", "c")]
    assert forward == reverse
    assert forward.to_dict() == reverse.to_dict()


def test_matrix_origins_destinations_and_totals_by_node():
    trips = [_trip("T0", "P0", "a", "b", party_size=2), _trip("T1", "P1", "c", "b", party_size=3)]
    matrix = DemandMatrix.from_trips(trips, NODES)
    assert matrix.origins() == ("a", "c")
    assert matrix.destinations() == ("b",)
    assert matrix.demand_by_origin() == (("a", 2), ("c", 3))
    assert matrix.demand_by_destination() == (("b", 5),)


def test_top_pairs_is_deterministic_on_ties():
    """Equal volumes must break on origin then destination, not insertion order."""
    trips = [_trip("T0", "P0", "c", "a"), _trip("T1", "P1", "a", "b"), _trip("T2", "P2", "b", "c")]
    matrix = DemandMatrix.from_trips(trips, NODES)
    assert [(c.origin, c.destination) for c in matrix.top_pairs(3)] == [("a", "b"), ("b", "c"), ("c", "a")]
    assert matrix.top_pairs(0) == ()
    assert len(matrix.top_pairs(99)) == 3


def test_matrix_rejects_unknown_nodes_in_cells():
    with pytest.raises(NodeNotFoundError, match="origin"):
        DemandMatrix(node_ids=NODES, cells=(ODCell("zzz", "b", 1, 1),))
    with pytest.raises(NodeNotFoundError, match="destination"):
        DemandMatrix(node_ids=NODES, cells=(ODCell("a", "zzz", 1, 1),))


@pytest.mark.parametrize("method", ["demand_for", "trips_for", "cell"])
def test_matrix_queries_reject_unknown_nodes(method):
    matrix = DemandMatrix.from_trips([_trip()], NODES)
    with pytest.raises(NodeNotFoundError):
        getattr(matrix, method)("a", "nowhere")
    with pytest.raises(NodeNotFoundError):
        getattr(matrix, method)("nowhere", "a")


def test_matrix_rejects_self_pairs_unless_configured():
    with pytest.raises(ModelValidationError, match="self-pair"):
        DemandMatrix(node_ids=NODES, cells=(ODCell("a", "a", 1, 1),))
    kept = DemandMatrix(node_ids=NODES, cells=(ODCell("a", "a", 1, 2),), allow_self_pairs=True)
    assert kept.demand_for("a", "a") == 2


def test_matrix_rejects_duplicate_cells_and_empty_node_list():
    with pytest.raises(ModelValidationError, match="duplicate OD cell"):
        DemandMatrix(node_ids=NODES, cells=(ODCell("a", "b", 1, 1), ODCell("a", "b", 2, 2)))
    with pytest.raises(ModelValidationError, match="at least one node id"):
        DemandMatrix(node_ids=(), cells=())


def test_empty_matrix_is_valid_and_zero():
    matrix = DemandMatrix.from_trips([], NODES)
    assert matrix.total_trips == 0 and matrix.total_demand == 0
    assert matrix.cells == () and matrix.origins() == () and matrix.top_pairs() == ()


# --- DemandSnapshot ----------------------------------------------------------
def _snapshot(**overrides):
    kwargs = dict(profile_id="baseline", seed=42, passenger_count=2, trip_count=2, total_demand=3,
                  od_cells=(("a", "b", 1, 2), ("b", "c", 1, 1)),
                  bucket_totals=(("morning_peak", 2), ("midday", 1)))
    return DemandSnapshot(**(kwargs | overrides))


def test_snapshot_fingerprint_is_stable_and_order_independent():
    a = _snapshot()
    b = _snapshot(od_cells=(("b", "c", 1, 1), ("a", "b", 1, 2)),
                  bucket_totals=(("midday", 1), ("morning_peak", 2)))
    assert a == b
    assert a.fingerprint() == b.fingerprint()
    assert a.fingerprint() == _snapshot().fingerprint()      # repeatable
    assert len(a.fingerprint()) == 64


def test_snapshot_fingerprint_changes_with_content():
    base = _snapshot().fingerprint()
    assert _snapshot(seed=43).fingerprint() != base
    assert _snapshot(total_demand=4).fingerprint() != base
    assert _snapshot(profile_id="peak_hour").fingerprint() != base
    assert _snapshot(od_cells=(("a", "b", 1, 3), ("b", "c", 1, 1))).fingerprint() != base


def test_snapshot_rejects_duplicates_and_bad_values():
    with pytest.raises(ModelValidationError, match="duplicate OD pair"):
        _snapshot(od_cells=(("a", "b", 1, 1), ("a", "b", 1, 1)))
    with pytest.raises(ModelValidationError, match="duplicate time bucket"):
        _snapshot(bucket_totals=(("midday", 1), ("midday", 2)))
    with pytest.raises(ModelValidationError):
        _snapshot(seed="42")
    with pytest.raises(ModelValidationError):
        _snapshot(passenger_count=-1)


def test_snapshot_as_dict_round_trips_through_json():
    import json
    snapshot = _snapshot()
    assert json.loads(json.dumps(snapshot.as_dict())) == snapshot.as_dict()
