"""M2 demand generation: determinism, stable ids, valid OD pairs, time-of-day."""

import subprocess
import sys
import time

import pytest

from app.demand import (
    BASELINE_DEMAND_PROFILE,
    PEAK_HOUR_DEMAND_PROFILE,
    DemandGenerator,
    PlaceRole,
    compute_metrics,
    generate_demand,
)
from app.demand.config import DemandProfile, TimeBucket
from app.demand.generator import buckets_within_horizon
from app.errors import DemandGenerationError, DemandProfileError


# --- determinism -------------------------------------------------------------
def test_passenger_generation_is_deterministic(city_graph):
    a = generate_demand(city_graph, seed=42, passenger_count=500)
    b = generate_demand(city_graph, seed=42, passenger_count=500)
    assert [p.to_dict() for p in a.passengers] == [p.to_dict() for p in b.passengers]


def test_trip_generation_is_deterministic(city_graph):
    a = generate_demand(city_graph, seed=42, passenger_count=500)
    b = generate_demand(city_graph, seed=42, passenger_count=500)
    assert [t.to_dict() for t in a.trips] == [t.to_dict() for t in b.trips]


def test_matrix_metrics_and_fingerprint_are_deterministic(city_graph):
    runs = [generate_demand(city_graph, seed=42, passenger_count=400) for _ in range(3)]
    first = runs[0]
    for other in runs[1:]:
        assert other.matrix == first.matrix
        assert other.matrix.to_dict() == first.matrix.to_dict()
        assert compute_metrics(other).to_dict() == compute_metrics(first).to_dict()
        assert other.snapshot() == first.snapshot()
        assert other.snapshot().fingerprint() == first.snapshot().fingerprint()


def test_different_seeds_produce_different_demand(city_graph):
    a = generate_demand(city_graph, seed=42, passenger_count=400)
    b = generate_demand(city_graph, seed=43, passenger_count=400)
    assert a.snapshot().fingerprint() != b.snapshot().fingerprint()
    assert [t.to_dict() for t in a.trips] != [t.to_dict() for t in b.trips]


def test_generation_does_not_touch_global_random_state(city_graph):
    """The generator must use a local RNG, so the global stream is untouched."""
    import random
    random.seed(12345)
    expected = [random.random() for _ in range(3)]
    random.seed(12345)
    generate_demand(city_graph, seed=42, passenger_count=200)
    assert [random.random() for _ in range(3)] == expected


def test_determinism_across_separate_processes(city_graph):
    """Same inputs in a fresh interpreter must give the same fingerprint."""
    script = (
        "from app.network.synthetic_city import build_synthetic_city;"
        "from app.demand import generate_demand;"
        "d=generate_demand(build_synthetic_city(42).graph, seed=42, passenger_count=400);"
        "print(d.snapshot().fingerprint())"
    )
    outputs = set()
    for _ in range(2):
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
        outputs.add(result.stdout.strip())
    in_process = generate_demand(city_graph, seed=42, passenger_count=400).snapshot().fingerprint()
    assert len(outputs) == 1
    assert outputs.pop() == in_process


# --- stable ids --------------------------------------------------------------
def test_passenger_ids_are_stable_and_unique(city_graph):
    demand = generate_demand(city_graph, seed=42, passenger_count=250)
    ids = [p.passenger_id for p in demand.passengers]
    assert ids == [f"P{i:06d}" for i in range(250)]
    assert len(set(ids)) == 250


def test_trip_ids_are_stable_and_unique(city_graph):
    demand = generate_demand(city_graph, seed=42, passenger_count=250)
    ids = [t.trip_id for t in demand.trips]
    assert ids == [f"T{i:06d}" for i in range(250)]
    assert len(set(ids)) == 250


def test_passenger_ids_are_a_prefix_when_count_grows(city_graph):
    """A larger run must keep the smaller run's passenger ids and homes."""
    small = generate_demand(city_graph, seed=42, passenger_count=100)
    large = generate_demand(city_graph, seed=42, passenger_count=400)
    assert [p.to_dict() for p in large.passengers[:100]] == [p.to_dict() for p in small.passengers]


def test_every_trip_links_to_a_generated_passenger(city_graph):
    demand = generate_demand(city_graph, seed=42, passenger_count=300)
    known = {p.passenger_id for p in demand.passengers}
    assert all(t.passenger_id in known for t in demand.trips)


def test_trips_per_passenger(city_graph):
    demand = generate_demand(city_graph, seed=42, passenger_count=50, trips_per_passenger=3)
    assert len(demand.passengers) == 50
    assert len(demand.trips) == 150
    counts = {}
    for trip in demand.trips:
        counts[trip.passenger_id] = counts.get(trip.passenger_id, 0) + 1
    assert set(counts.values()) == {3}


# --- valid, sane OD pairs ----------------------------------------------------
def test_all_origins_and_destinations_are_real_network_nodes(city_graph):
    demand = generate_demand(city_graph, seed=42, passenger_count=500)
    known = {n.node_id for n in city_graph.nodes()}
    assert {t.origin_node_id for t in demand.trips} <= known
    assert {t.destination_node_id for t in demand.trips} <= known
    assert all(city_graph.has_node(t.origin_node_id) for t in demand.trips)


def test_origin_never_equals_destination(city_graph):
    demand = generate_demand(city_graph, seed=42, passenger_count=2000)
    assert not [t for t in demand.trips if t.origin_node_id == t.destination_node_id]
    assert all(cell.origin != cell.destination for cell in demand.matrix.cells)


def test_party_sizes_come_from_the_configured_distribution(city_graph):
    demand = generate_demand(city_graph, seed=42, passenger_count=1000)
    allowed = {size for size, _ in BASELINE_DEMAND_PROFILE.party_size_distribution}
    sizes = {t.party_size for t in demand.trips}
    assert sizes <= allowed
    assert all(t.party_size >= 1 for t in demand.trips)
    assert len(sizes) > 1, "a 1000-trip run should exercise more than one party size"


def test_demand_is_not_uniform_across_nodes(city_graph):
    """Roles must matter: plain intersections should attract far less than hubs
    and workplaces. A uniform sampler would make these roughly equal."""
    demand = generate_demand(city_graph, seed=42, passenger_count=2000)
    attracted = dict(demand.matrix.demand_by_destination())
    generator = DemandGenerator(city_graph, BASELINE_DEMAND_PROFILE)

    through = sum(v for node, v in attracted.items() if generator.role_of(node) == PlaceRole.THROUGH)
    employment = sum(v for node, v in attracted.items() if generator.role_of(node) == PlaceRole.EMPLOYMENT)
    assert employment > through * 2


def test_totals_are_consistent_across_views(city_graph):
    demand = generate_demand(city_graph, seed=42, passenger_count=600)
    expected_volume = sum(t.party_size for t in demand.trips)

    assert demand.matrix.total_trips == len(demand.trips)
    assert demand.matrix.total_demand == expected_volume == demand.total_demand
    assert sum(v for _, v in demand.demand_by_time_bucket()) == expected_volume
    assert sum(v for _, v in demand.trips_by_time_bucket()) == len(demand.trips)
    assert sum(v for _, v in demand.matrix.demand_by_origin()) == expected_volume
    assert sum(v for _, v in demand.matrix.demand_by_destination()) == expected_volume

    metrics = compute_metrics(demand)
    assert metrics.total_passenger_volume == expected_volume
    assert metrics.total_trip_requests == len(demand.trips)
    assert metrics.total_passengers == len(demand.passengers)
    assert metrics.average_party_size == pytest.approx(expected_volume / len(demand.trips), abs=5e-4)


# --- time of day -------------------------------------------------------------
def test_request_times_fall_inside_their_reported_bucket(city_graph):
    demand = generate_demand(city_graph, seed=42, passenger_count=1500)
    for trip in demand.trips:
        bucket = BASELINE_DEMAND_PROFILE.bucket(trip.time_bucket)
        assert bucket.contains(trip.request_time_min), (trip.trip_id, trip.request_time_min)


def test_bucket_volumes_follow_the_configured_shares(city_graph):
    """Shares are probabilities, so allow sampling slack — but the ordering and
    rough magnitudes must follow the profile."""
    demand = generate_demand(city_graph, seed=42, passenger_count=4000)
    trips_by_bucket = dict(demand.trips_by_time_bucket())
    total = sum(trips_by_bucket.values())
    for bucket in BASELINE_DEMAND_PROFILE.buckets:
        observed = trips_by_bucket[bucket.name] / total
        assert observed == pytest.approx(bucket.trip_share, abs=0.03), bucket.name


def test_peaks_are_busier_than_midday_and_off_peak(city_graph):
    demand = generate_demand(city_graph, seed=42, passenger_count=3000)
    volume = dict(demand.demand_by_time_bucket())
    assert volume["morning_peak"] > volume["midday"] > volume["off_peak"]
    assert volume["evening_peak"] > volume["midday"]
    assert compute_metrics(demand).peak_bucket == "morning_peak"


def test_morning_sends_people_from_home_and_evening_brings_them_back(city_graph):
    """The direction of demand must reverse between the peaks."""
    demand = generate_demand(city_graph, seed=42, passenger_count=3000)
    generator = DemandGenerator(city_graph, BASELINE_DEMAND_PROFILE)

    def flow(bucket_name):
        out_of_home = into_home = 0
        for trip in demand.trips:
            if trip.time_bucket != bucket_name:
                continue
            if generator.role_of(trip.origin_node_id) == PlaceRole.RESIDENTIAL:
                out_of_home += trip.party_size
            if generator.role_of(trip.destination_node_id) == PlaceRole.RESIDENTIAL:
                into_home += trip.party_size
        return out_of_home, into_home

    morning_out, morning_in = flow("morning_peak")
    evening_out, evening_in = flow("evening_peak")
    assert morning_out > morning_in
    assert evening_in > evening_out


def test_commuters_anchor_on_their_home_node_during_the_peaks(city_graph):
    demand = generate_demand(city_graph, seed=42, passenger_count=1500)
    homes = {p.passenger_id: p.home_node_id for p in demand.passengers}
    commuters = {p.passenger_id for p in demand.passengers if p.is_commuter}

    morning = [t for t in demand.trips if t.time_bucket == "morning_peak" and t.passenger_id in commuters]
    evening = [t for t in demand.trips if t.time_bucket == "evening_peak" and t.passenger_id in commuters]
    assert morning and evening
    assert all(t.origin_node_id == homes[t.passenger_id] for t in morning)
    assert all(t.destination_node_id == homes[t.passenger_id] for t in evening)


def test_time_profile_itself_carries_no_randomness(city_graph):
    """Bucket windows and shares are fixed configuration, identical every run."""
    for _ in range(3):
        demand = generate_demand(city_graph, seed=7, passenger_count=50)
        assert BASELINE_DEMAND_PROFILE.bucket("morning_peak").windows == ((360.0, 600.0),)
        assert BASELINE_DEMAND_PROFILE.bucket("morning_peak").trip_share == 0.35
        assert set(dict(demand.trips_by_time_bucket())) <= set(BASELINE_DEMAND_PROFILE.bucket_names())


# --- baseline versus peak_hour ----------------------------------------------
def test_peak_hour_profile_concentrates_demand_far_more_than_baseline(city_graph):
    baseline = generate_demand(city_graph, seed=42, passenger_count=2000)
    peak = generate_demand(city_graph, seed=42, passenger_count=2000, profile=PEAK_HOUR_DEMAND_PROFILE)

    baseline_share = dict(baseline.trips_by_time_bucket())["morning_peak"] / len(baseline.trips)
    peak_share = dict(peak.trips_by_time_bucket())["morning_peak"] / len(peak.trips)

    assert baseline_share == pytest.approx(0.35, abs=0.03)
    assert peak_share > 0.80
    assert peak_share > baseline_share * 2
    assert baseline.profile_id == "baseline" and peak.profile_id == "peak_hour"
    assert baseline.snapshot().fingerprint() != peak.snapshot().fingerprint()


def test_peak_hour_demand_is_more_spatially_concentrated(city_graph):
    """A sharper commute should use fewer OD pairs and lean harder on the top ones."""
    baseline = generate_demand(city_graph, seed=42, passenger_count=2000)
    peak = generate_demand(city_graph, seed=42, passenger_count=2000, profile=PEAK_HOUR_DEMAND_PROFILE)

    assert peak.matrix.pair_count < baseline.matrix.pair_count
    top_share = lambda d: sum(c.passengers for c in d.matrix.top_pairs(5)) / d.matrix.total_demand
    assert top_share(peak) > top_share(baseline)


def test_peak_hour_trips_are_confined_to_its_narrow_window(city_graph):
    peak = generate_demand(city_graph, seed=42, passenger_count=1000, profile=PEAK_HOUR_DEMAND_PROFILE)
    morning = [t for t in peak.trips if t.time_bucket == "morning_peak"]
    assert morning
    assert all(420.0 <= t.request_time_min < 540.0 for t in morning)


# --- horizon ----------------------------------------------------------------
def test_horizon_limits_demand_to_the_simulated_window(city_graph):
    demand = generate_demand(city_graph, seed=42, passenger_count=600, horizon_min=600.0)
    assert all(t.request_time_min < 600.0 for t in demand.trips)
    assert "evening_peak" not in dict(demand.trips_by_time_bucket())


def test_horizon_renormalises_shares_and_clips_windows():
    """Windows are half-open [start, end), so a bucket starting exactly at the
    horizon contributes nothing: midday begins at minute 600 and drops out."""
    buckets = buckets_within_horizon(BASELINE_DEMAND_PROFILE, 600.0)
    assert sum(b.trip_share for b in buckets) == pytest.approx(1.0)
    assert {b.name for b in buckets} == {"morning_peak", "off_peak"}

    # A horizon one minute later does keep midday, clipped to a single minute.
    later = {b.name: b for b in buckets_within_horizon(BASELINE_DEMAND_PROFILE, 601.0)}
    assert later["midday"].windows == ((600.0, 601.0),)
    assert later["off_peak"].windows == ((0.0, 360.0),)      # the 20:00-24:00 window is gone

    assert BASELINE_DEMAND_PROFILE.bucket("midday").windows == ((600.0, 960.0),)   # profile untouched
    assert buckets_within_horizon(BASELINE_DEMAND_PROFILE, None) is BASELINE_DEMAND_PROFILE.buckets


def test_horizon_is_deterministic(city_graph):
    a = generate_demand(city_graph, seed=42, passenger_count=300, horizon_min=540.0)
    b = generate_demand(city_graph, seed=42, passenger_count=300, horizon_min=540.0)
    assert a.snapshot().fingerprint() == b.snapshot().fingerprint()


@pytest.mark.parametrize("horizon", [0, -5, float("nan"), float("inf"), "600", True])
def test_invalid_horizon_rejected(city_graph, horizon):
    with pytest.raises(DemandGenerationError):
        generate_demand(city_graph, seed=42, passenger_count=10, horizon_min=horizon)


def test_horizon_before_every_bucket_is_rejected():
    profile = DemandProfile(
        profile_id="late_only",
        description="Starts at 10:00.",
        buckets=(TimeBucket(name="midday", windows=((600.0, 960.0),), trip_share=1.0,
                            production={"mixed": 1.0}, attraction={"mixed": 1.0}),),
        party_size_distribution=((1, 1.0),),
        default_roles_by_node_type={"station": "mixed", "terminal": "mixed", "intersection": "mixed"},
        home_role_weights={"mixed": 1.0},
    )
    with pytest.raises(DemandGenerationError, match="starts before minute"):
        buckets_within_horizon(profile, 300.0)


# --- invalid input and small/empty edge cases -------------------------------
@pytest.mark.parametrize("count", [-1, 1.5, "10", True, None])
def test_invalid_passenger_count_rejected(city_graph, count):
    with pytest.raises(DemandGenerationError):
        generate_demand(city_graph, seed=42, passenger_count=count)


@pytest.mark.parametrize("seed", [1.5, "42", True, None])
def test_invalid_seed_rejected(city_graph, seed):
    with pytest.raises(DemandGenerationError):
        generate_demand(city_graph, seed=seed, passenger_count=10)


@pytest.mark.parametrize("trips_per", [0, -1, 1.5, True])
def test_invalid_trips_per_passenger_rejected(city_graph, trips_per):
    with pytest.raises(DemandGenerationError):
        generate_demand(city_graph, seed=42, passenger_count=10, trips_per_passenger=trips_per)


def test_zero_passengers_produces_empty_but_valid_demand(city_graph):
    demand = generate_demand(city_graph, seed=42, passenger_count=0)
    assert demand.passengers == () and demand.trips == ()
    assert demand.matrix.total_demand == 0 and demand.matrix.cells == ()
    assert demand.demand_by_time_bucket() == ()

    metrics = compute_metrics(demand)
    assert metrics.total_passengers == 0 and metrics.total_passenger_volume == 0
    assert metrics.average_party_size is None      # no fake 0.0 average
    assert metrics.peak_bucket is None
    assert demand.snapshot().fingerprint() == generate_demand(
        city_graph, seed=42, passenger_count=0).snapshot().fingerprint()


def test_single_passenger_demand(city_graph):
    demand = generate_demand(city_graph, seed=42, passenger_count=1)
    assert len(demand.passengers) == 1 and len(demand.trips) == 1
    assert demand.matrix.pair_count == 1
    assert demand.matrix.total_demand == demand.trips[0].party_size


def test_two_node_graph_generates_the_only_possible_pair(tiny_graph, tiny_profile):
    demand = generate_demand(tiny_graph, seed=42, passenger_count=20, profile=tiny_profile)
    assert {t.od_pair for t in demand.trips} <= {("home", "work"), ("work", "home")}
    assert all(t.origin_node_id != t.destination_node_id for t in demand.trips)


def test_single_node_graph_cannot_carry_demand(make_node):
    from app.network.graph import NetworkGraph
    graph = NetworkGraph()
    graph.add_node(make_node("only"))
    with pytest.raises(DemandGenerationError, match="at least 2 nodes"):
        DemandGenerator(graph, BASELINE_DEMAND_PROFILE)


def test_node_without_a_role_is_rejected(city_graph):
    """A profile that cannot classify a node must fail loudly, not silently
    weight it as zero."""
    profile = DemandProfile(
        profile_id="incomplete",
        description="No rule for intersections.",
        buckets=(TimeBucket(name="midday", windows=((600.0, 960.0),), trip_share=1.0,
                            production={"mixed": 1.0}, attraction={"mixed": 1.0}),),
        party_size_distribution=((1, 1.0),),
        default_roles_by_node_type={"station": "mixed", "terminal": "hub"},
        home_role_weights={"mixed": 1.0},
    )
    with pytest.raises(DemandProfileError, match="no demand role for node"):
        DemandGenerator(city_graph, profile)


def test_generator_rejects_a_non_profile(city_graph):
    with pytest.raises(DemandProfileError):
        DemandGenerator(city_graph, profile={"profile_id": "nope"})


# --- performance ------------------------------------------------------------
def test_ten_thousand_passengers_generate_quickly(city_graph):
    """Requirement: comfortably handle 10,000 passengers with plain stdlib."""
    started = time.perf_counter()
    demand = generate_demand(city_graph, seed=42, passenger_count=10_000)
    elapsed = time.perf_counter() - started

    assert len(demand.passengers) == 10_000 and len(demand.trips) == 10_000
    assert demand.matrix.total_trips == 10_000
    assert elapsed < 10.0, f"10k passengers took {elapsed:.2f}s"
