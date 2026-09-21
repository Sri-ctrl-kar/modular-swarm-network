"""M2 demand scenario files, profile validation and the CLI command."""

import json

import pytest

from app.demand import (
    BASELINE_DEMAND_PATH,
    BASELINE_DEMAND_PROFILE,
    DEMAND_PROFILES,
    DEMAND_SCENARIO_PATHS,
    PEAK_HOUR_DEMAND_PATH,
    PEAK_HOUR_DEMAND_PROFILE,
    DemandProfile,
    PlaceRole,
    TimeBucket,
    dump_demand_profile,
    generate_demand,
    load_demand_profile,
    resolve_demand_profile,
)
from app.demand.config import DEMAND_SCHEMA_VERSION
from app.errors import DemandProfileError, SwarmNetworkError


# --- the committed scenario files --------------------------------------------
@pytest.mark.parametrize("profile,path", [
    (BASELINE_DEMAND_PROFILE, BASELINE_DEMAND_PATH),
    (PEAK_HOUR_DEMAND_PROFILE, PEAK_HOUR_DEMAND_PATH),
])
def test_committed_demand_scenario_is_byte_identical_to_its_profile(profile, path):
    """Mirrors M1's baseline.json rule: the JSON is an export, never hand-edited."""
    assert path.read_text(encoding="utf-8") == dump_demand_profile(profile)


@pytest.mark.parametrize("path", sorted(DEMAND_SCENARIO_PATHS.values()))
def test_demand_scenario_files_load_and_round_trip(path):
    profile = load_demand_profile(path)
    assert profile.to_dict() == json.loads(path.read_text(encoding="utf-8"))
    assert dump_demand_profile(profile) == path.read_text(encoding="utf-8")


@pytest.mark.parametrize("name,path", sorted(DEMAND_SCENARIO_PATHS.items()))
def test_loaded_profile_equals_the_builtin_profile(name, path):
    assert load_demand_profile(path) == DEMAND_PROFILES[name]


def test_loaded_profile_generates_identical_demand_to_the_builtin(city_graph):
    """Loading from disk must not change a single generated trip."""
    from_file = generate_demand(city_graph, seed=42, passenger_count=300,
                                profile=load_demand_profile(BASELINE_DEMAND_PATH))
    from_code = generate_demand(city_graph, seed=42, passenger_count=300,
                                profile=BASELINE_DEMAND_PROFILE)
    assert from_file.snapshot().fingerprint() == from_code.snapshot().fingerprint()
    assert [t.to_dict() for t in from_file.trips] == [t.to_dict() for t in from_code.trips]


def test_scenario_files_declare_synthetic_provenance():
    for path in DEMAND_SCENARIO_PATHS.values():
        data = json.loads(path.read_text(encoding="utf-8"))
        provenance = data["data_provenance"]
        assert "SYNTHETIC" in provenance
        assert "not calibrated" in provenance.lower()


def test_resolve_accepts_names_and_paths():
    assert resolve_demand_profile("baseline") is BASELINE_DEMAND_PROFILE
    assert resolve_demand_profile("peak_hour") is PEAK_HOUR_DEMAND_PROFILE
    assert resolve_demand_profile(str(BASELINE_DEMAND_PATH)) == BASELINE_DEMAND_PROFILE
    with pytest.raises(DemandProfileError, match="unknown demand profile"):
        resolve_demand_profile("rush_hour_2099")


def test_loading_a_missing_or_broken_file(tmp_path):
    with pytest.raises(DemandProfileError, match="cannot read"):
        load_demand_profile(tmp_path / "nope.json")
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(DemandProfileError, match="invalid JSON"):
        load_demand_profile(broken)


def test_demand_profile_errors_are_swarm_network_errors():
    """So the CLI's existing handler turns them into one clean line, exit code 2."""
    assert issubclass(DemandProfileError, SwarmNetworkError)


# --- profile validation ------------------------------------------------------
def _valid_kwargs(**overrides):
    kwargs = dict(
        profile_id="test",
        description="",
        buckets=(TimeBucket(name="all_day", windows=((0.0, 1440.0),), trip_share=1.0,
                            production={"mixed": 1.0}, attraction={"mixed": 1.0}),),
        party_size_distribution=((1, 1.0),),
        default_roles_by_node_type={"station": "mixed", "terminal": "hub", "intersection": "through"},
        home_role_weights={"mixed": 1.0},
    )
    return kwargs | overrides


def test_profile_shares_must_sum_to_one():
    buckets = (
        TimeBucket(name="a", windows=((0.0, 720.0),), trip_share=0.5,
                   production={"mixed": 1.0}, attraction={"mixed": 1.0}),
        TimeBucket(name="b", windows=((720.0, 1440.0),), trip_share=0.2,
                   production={"mixed": 1.0}, attraction={"mixed": 1.0}),
    )
    with pytest.raises(DemandProfileError, match="must sum to 1.0"):
        DemandProfile(**_valid_kwargs(buckets=buckets))


def test_profile_rejects_duplicate_bucket_names():
    bucket = TimeBucket(name="dup", windows=((0.0, 720.0),), trip_share=0.5,
                        production={"mixed": 1.0}, attraction={"mixed": 1.0})
    other = TimeBucket(name="dup", windows=((720.0, 1440.0),), trip_share=0.5,
                       production={"mixed": 1.0}, attraction={"mixed": 1.0})
    with pytest.raises(DemandProfileError, match="duplicate time bucket names"):
        DemandProfile(**_valid_kwargs(buckets=(bucket, other)))


def test_profile_requires_at_least_one_bucket():
    with pytest.raises(DemandProfileError, match="at least one time bucket"):
        DemandProfile(**_valid_kwargs(buckets=()))


@pytest.mark.parametrize("parties", [
    ((0, 1.0),), ((-1, 1.0),), ((1.5, 1.0),), (), ((1, -1.0),), ((1, 0.0),), ((1, 0.5), (1, 0.5)),
])
def test_profile_rejects_bad_party_size_distribution(parties):
    with pytest.raises(DemandProfileError):
        DemandProfile(**_valid_kwargs(party_size_distribution=parties))


@pytest.mark.parametrize("share", [-0.1, 1.1, "half", float("nan")])
def test_profile_rejects_bad_commuter_share(share):
    with pytest.raises(DemandProfileError):
        DemandProfile(**_valid_kwargs(commuter_share=share))


@pytest.mark.parametrize("exponent", [-1.0, float("inf"), "one"])
def test_profile_rejects_bad_gravity_exponent(exponent):
    with pytest.raises(DemandProfileError):
        DemandProfile(**_valid_kwargs(gravity_distance_exponent=exponent))


@pytest.mark.parametrize("windows", [
    ((600.0, 600.0),), ((600.0, 500.0),), ((-10.0, 600.0),), ((0.0, 1500.0),), (),
])
def test_time_bucket_rejects_bad_windows(windows):
    with pytest.raises(DemandProfileError):
        TimeBucket(name="b", windows=windows, trip_share=1.0,
                   production={"mixed": 1.0}, attraction={"mixed": 1.0})


def test_time_bucket_rejects_unknown_role_and_all_zero_weights():
    with pytest.raises(DemandProfileError, match="unknown role"):
        TimeBucket(name="b", windows=((0.0, 60.0),), trip_share=1.0,
                   production={"spaceport": 1.0}, attraction={"mixed": 1.0})
    with pytest.raises(DemandProfileError, match="at least one positive weight"):
        TimeBucket(name="b", windows=((0.0, 60.0),), trip_share=1.0,
                   production={"mixed": 0.0}, attraction={"mixed": 1.0})


def test_time_bucket_fills_missing_roles_with_zero():
    bucket = TimeBucket(name="b", windows=((0.0, 60.0),), trip_share=1.0,
                        production={"mixed": 1.0}, attraction={"hub": 2.0})
    assert bucket.production[PlaceRole.THROUGH] == 0.0
    assert set(bucket.production) == set(PlaceRole)
    assert bucket.total_window_min == 60.0


def test_multi_window_bucket_reports_total_length_and_membership():
    bucket = TimeBucket(name="night", windows=((1200.0, 1440.0), (0.0, 360.0)), trip_share=1.0,
                        production={"mixed": 1.0}, attraction={"mixed": 1.0})
    assert bucket.total_window_min == 600.0
    assert bucket.contains(1300.0) and bucket.contains(10.0)
    assert not bucket.contains(700.0)
    assert not bucket.contains(1440.0)      # half-open at the end


def test_profile_from_dict_rejects_bad_schema_and_missing_keys():
    good = BASELINE_DEMAND_PROFILE.to_dict()
    with pytest.raises(DemandProfileError, match="unsupported demand schema_version"):
        DemandProfile.from_dict(good | {"schema_version": DEMAND_SCHEMA_VERSION + 1})
    with pytest.raises(DemandProfileError, match="missing keys"):
        DemandProfile.from_dict({"schema_version": DEMAND_SCHEMA_VERSION})
    with pytest.raises(DemandProfileError, match="root must be"):
        DemandProfile.from_dict([1, 2, 3])


def test_bucket_lookup_errors_are_typed():
    with pytest.raises(DemandProfileError, match="unknown time bucket"):
        BASELINE_DEMAND_PROFILE.bucket("lunchtime")
    with pytest.raises(DemandProfileError, match="no time bucket covers"):
        BASELINE_DEMAND_PROFILE.bucket_for_time(5000.0)


def test_role_lookup_prefers_explicit_node_roles():
    profile = BASELINE_DEMAND_PROFILE
    assert profile.role_for("residential_north", "station") == PlaceRole.RESIDENTIAL
    assert profile.role_for("airport", "terminal") == PlaceRole.HUB
    # not named explicitly -> falls back on the M1 node type
    assert profile.role_for("jct_northgate", "intersection") == PlaceRole.THROUGH
    assert profile.role_for("some_new_station", "station") == PlaceRole.MIXED


def test_baseline_and_peak_profiles_cover_the_expected_buckets():
    assert BASELINE_DEMAND_PROFILE.bucket_names() == ("morning_peak", "midday", "evening_peak", "off_peak")
    assert PEAK_HOUR_DEMAND_PROFILE.bucket_names() == ("morning_peak", "midday", "off_peak")
    # Every minute of the day belongs to exactly one bucket in each profile.
    for profile in (BASELINE_DEMAND_PROFILE, PEAK_HOUR_DEMAND_PROFILE):
        for minute in range(0, 1440, 7):
            covering = [b.name for b in profile.buckets if b.contains(float(minute))]
            assert len(covering) == 1, (profile.profile_id, minute, covering)


# --- CLI ---------------------------------------------------------------------
def test_demand_demo_cli_runs_and_reports(capsys):
    from app.cli.main import main
    assert main(["demand-demo", "--passengers", "200"]) == 0
    out = capsys.readouterr().out
    assert "SYNTHETIC" in out
    assert "Passengers: 200" in out
    assert "Total passenger demand:" in out
    assert "Top 5 OD pairs" in out
    assert "Demand by time bucket" in out
    assert "Routed trips: 200" in out
    assert "Demand fingerprint:" in out


def test_demand_demo_cli_is_reproducible(capsys):
    from app.cli.main import main

    def fingerprint():
        main(["demand-demo", "--passengers", "200", "--profile", "peak_hour"])
        for line in capsys.readouterr().out.splitlines():
            if line.startswith("Demand fingerprint:"):
                return line.split(":", 1)[1].strip()
        raise AssertionError("no fingerprint in output")

    assert fingerprint() == fingerprint()


def test_demand_demo_cli_options(capsys):
    from app.cli.main import main
    assert main(["demand-demo", "--passengers", "50", "--no-routing", "--top", "3",
                 "--horizon-min", "600"]) == 0
    out = capsys.readouterr().out
    assert "Routing: skipped" in out
    assert "Top 3 OD pairs" in out
    assert "clipped to the first 600 min" in out
    assert "evening_peak" not in out


def test_demand_demo_cli_rejects_unknown_profile(capsys):
    from app.cli.main import main
    assert main(["demand-demo", "--profile", "nonexistent"]) == 2      # invalid input
    assert "unknown demand profile" in capsys.readouterr().err


def test_existing_cli_commands_still_work(capsys):
    """M2 must not disturb the M1 CLI."""
    from app.cli.main import main
    assert main(["route", "--from", "North Station", "--to", "Airport", "--algorithm", "both"]) == 0
    assert "Optimal cost match (A* vs Dijkstra): YES" in capsys.readouterr().out
    assert main(["info"]) == 0
    assert "node_count: 22" in capsys.readouterr().out
    assert main(["congestion-demo", "--from", "North Station", "--to", "Airport"]) == 0
    assert "Route changed:" in capsys.readouterr().out
