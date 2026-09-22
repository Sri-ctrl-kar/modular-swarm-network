"""M5 demand windows, the deterministic forecast and the spatial demand map."""

import pytest

from app.demand import BASELINE_DEMAND_PROFILE, generate_demand
from app.errors import ForecastError, RebalancingConfigError
from app.fleet import generate_fleet
from app.fleet.models import TripRecord, TripStatus
from app.fleet.simulation import FleetSimulation
from app.network.synthetic_city import build_synthetic_city
from app.rebalancing import (
    DEFAULT_REBALANCING_CONFIG,
    RebalancingConfig,
    build_demand_map,
    build_forecast,
    demand_in_window,
    demand_windows,
    profile_shares,
)


def _record(trip_id, origin, time_min, destination="airport", party_size=1):
    return TripRecord(trip_id=trip_id, party_size=party_size, origin_node_id=origin,
                      destination_node_id=destination, request_time_min=float(time_min))


def _run_to(minute, pods=100, passengers=1000):
    graph = build_synthetic_city(42).graph
    fleet = generate_fleet(graph, fleet_size=pods, seed=42)
    demand = generate_demand(graph, seed=42, passenger_count=passengers)
    sim = FleetSimulation(graph, fleet, demand.trips)
    sim.run(until_min=minute)
    return graph, fleet, sim


# --- windows -----------------------------------------------------------------
def test_windows_sit_around_now():
    windows = demand_windows(500.0)
    assert windows["recent"].start_min == 440.0 and windows["recent"].end_min == 500.0
    assert windows["current"].start_min == 485.0 and windows["current"].end_min == 500.0
    assert windows["near_future"].start_min == 500.0 and windows["near_future"].end_min == 530.0


def test_windows_are_clamped_at_zero():
    windows = demand_windows(5.0)
    assert windows["recent"].start_min == 0.0
    assert windows["current"].start_min == 0.0


def test_window_membership_is_half_open_except_current():
    windows = demand_windows(500.0)
    recent = windows["recent"]
    assert recent.contains(440.0) and recent.contains(499.9)
    assert not recent.contains(500.0)          # half-open at the end
    current = windows["current"]
    assert current.contains(500.0)             # a trip requested exactly now is current
    near = windows["near_future"]
    assert near.contains(500.0) and not near.contains(530.0)


@pytest.mark.parametrize("bad", [-1.0, "500", True, None])
def test_invalid_now_rejected(bad):
    with pytest.raises(ForecastError):
        demand_windows(bad)


def test_demand_in_window_counts_trips_not_passengers():
    """One pod serves one trip whatever the party size, so demand is in trips."""
    records = [_record("T1", "market", 100.0, party_size=4),
               _record("T2", "market", 110.0, party_size=1),
               _record("T3", "airport", 105.0, party_size=2)]
    counts = demand_in_window(records, demand_windows(120.0)["recent"])
    assert counts == {"market": 2, "airport": 1}


def test_demand_in_window_counts_unserved_trips_too():
    """Demand is demand: a trip nobody served still happened."""
    served = _record("T1", "market", 100.0)
    served.status = TripStatus.COMPLETED
    failed = _record("T2", "market", 101.0)
    failed.status = TripStatus.FAILED
    counts = demand_in_window([served, failed], demand_windows(120.0)["recent"])
    assert counts == {"market": 2}


# --- the forecast is not clairvoyant ----------------------------------------
def test_forecast_ignores_trips_that_have_not_been_requested_yet():
    """The decisive property: adding future demand must not move the forecast."""
    graph = build_synthetic_city(42).graph
    history = [_record(f"T{i}", "market", 100.0 + i) for i in range(10)]
    future = [_record(f"F{i}", "airport", 500.0 + i) for i in range(50)]

    without_future = build_forecast(graph, history, 150.0)
    with_future = build_forecast(graph, history + future, 150.0)
    assert with_future.to_dict() == without_future.to_dict()


def test_forecast_refuses_to_extrapolate_from_thin_history():
    graph = build_synthetic_city(42).graph
    records = [_record("T1", "market", 0.5)]
    early = build_forecast(graph, records, 2.0)
    assert not early.has_sufficient_history
    assert early.upcoming_bucket == "insufficient_history"
    assert early.total_forecast_trips == 0.0

    later = build_forecast(graph, records, 30.0)
    assert later.has_sufficient_history


def test_history_threshold_is_configurable():
    graph = build_synthetic_city(42).graph
    records = [_record("T1", "market", 1.0)]
    config = RebalancingConfig(min_history_min=0.0)
    assert build_forecast(graph, records, 5.0, config).has_sufficient_history


# --- forecast composition ----------------------------------------------------
def test_forecast_is_the_documented_weighted_sum():
    graph = build_synthetic_city(42).graph
    records = [_record(f"T{i}", "market", 100.0 + i) for i in range(12)]
    forecast = build_forecast(graph, records, 160.0)

    for node in forecast.nodes:
        assert node.forecast_trips == pytest.approx(
            node.recent_component + node.profile_component, abs=1e-4)
    market = forecast.node("market")
    assert market.recent_trips == 12
    assert market.recent_component > 0
    assert forecast.recent_weight + forecast.profile_weight == pytest.approx(1.0)


def test_recent_component_scales_with_observed_demand():
    graph = build_synthetic_city(42).graph
    few = [_record(f"T{i}", "market", 100.0 + i) for i in range(4)]
    many = [_record(f"T{i}", "market", 100.0 + i * 0.5) for i in range(40)]
    assert (build_forecast(graph, many, 160.0).node("market").recent_component
            > build_forecast(graph, few, 160.0).node("market").recent_component)


def test_profile_weighting_can_be_turned_off():
    graph = build_synthetic_city(42).graph
    records = [_record(f"T{i}", "market", 100.0 + i) for i in range(12)]
    only_recent = RebalancingConfig(forecast_recent_weight=1.0, forecast_profile_weight=0.0)
    forecast = build_forecast(graph, records, 160.0, only_recent)
    assert all(node.profile_component == 0.0 for node in forecast.nodes)
    # Nodes with no recent demand then forecast nothing at all.
    assert forecast.node("airport").forecast_trips == 0.0


def test_profile_shares_sum_to_one_and_follow_roles():
    graph = build_synthetic_city(42).graph
    shares = profile_shares(graph, BASELINE_DEMAND_PROFILE, "morning_peak")
    assert sum(shares.values()) == pytest.approx(1.0)
    # Residential nodes produce most of the morning, junctions almost nothing.
    assert shares["residential_north"] > shares["jct_northgate"]


def test_forecast_looks_at_the_upcoming_bucket_not_the_current_one():
    """This is what makes prepositioning possible rather than merely reactive."""
    graph = build_synthetic_city(42).graph
    records = [_record(f"T{i}", "market", 580.0 + i * 0.5) for i in range(20)]
    # 09:55 + a 30 min horizon lands in midday, not the morning peak.
    forecast = build_forecast(graph, records, 595.0)
    assert forecast.upcoming_bucket == "midday"


def test_forecast_determinism():
    graph, _, sim = _run_to(520.0)
    runs = [build_forecast(graph, sim.records(), sim.time_min).to_dict() for _ in range(3)]
    assert all(run == runs[0] for run in runs)


def test_forecast_nodes_are_sorted_and_cover_the_network():
    graph, _, sim = _run_to(520.0)
    forecast = build_forecast(graph, sim.records(), sim.time_min)
    ids = [node.node_id for node in forecast.nodes]
    assert ids == sorted(ids)
    assert set(ids) == {n.node_id for n in graph.nodes()}


def test_unknown_node_lookup_raises():
    graph, _, sim = _run_to(520.0)
    forecast = build_forecast(graph, sim.records(), sim.time_min)
    with pytest.raises(ForecastError):
        forecast.forecast_for("atlantis")


# --- the spatial demand map --------------------------------------------------
def test_demand_map_columns_follow_their_definitions():
    graph, fleet, sim = _run_to(520.0)
    demand_map = build_demand_map(graph, fleet, sim.records(), sim.time_min)

    for row in demand_map.rows:
        assert row.balance == pytest.approx(row.available_pods - row.forecast_demand, abs=1e-3)
        assert row.deficit == pytest.approx(max(0.0, -row.balance), abs=1e-3)
        assert row.surplus == pytest.approx(max(0.0, row.balance), abs=1e-3)
        assert not (row.is_deficit and row.is_surplus)
        if row.available_pods == 0:
            assert row.demand_supply_ratio is None      # undefined, not infinity
        else:
            assert row.demand_supply_ratio == pytest.approx(
                row.forecast_demand / row.available_pods, abs=1e-3)


def test_demand_map_finds_the_real_imbalance():
    """Mid-morning on the seed-42 city, residential nodes are short and workplaces
    are over-supplied — the M3 limitation this milestone exists to address."""
    graph, fleet, sim = _run_to(520.0)
    demand_map = build_demand_map(graph, fleet, sim.records(), sim.time_min)

    deficits = {row.node_id for row in demand_map.deficit_rows()}
    surpluses = {row.node_id for row in demand_map.surplus_rows()}
    assert "residential_south" in deficits
    assert demand_map.total_deficit > 0 and demand_map.total_surplus > 0
    assert deficits.isdisjoint(surpluses)


def test_deficit_and_surplus_rows_are_deterministically_ordered():
    graph, fleet, sim = _run_to(520.0)
    demand_map = build_demand_map(graph, fleet, sim.records(), sim.time_min)
    deficits = demand_map.deficit_rows()
    assert [(-r.deficit, r.node_id) for r in deficits] == sorted(
        (-r.deficit, r.node_id) for r in deficits)
    surpluses = demand_map.surplus_rows()
    assert [(-r.surplus, r.node_id) for r in surpluses] == sorted(
        (-r.surplus, r.node_id) for r in surpluses)


def test_actuals_are_evaluation_only_and_cannot_change_a_decision():
    """Switching the evaluation column on must not move a forecast or a deficit."""
    graph, fleet, sim = _run_to(520.0)
    without = build_demand_map(graph, fleet, sim.records(), sim.time_min, include_actuals=False)
    with_actuals = build_demand_map(graph, fleet, sim.records(), sim.time_min,
                                    include_actuals=True)

    assert without.forecast.to_dict() == with_actuals.forecast.to_dict()
    assert without.total_deficit == with_actuals.total_deficit
    assert [r.deficit for r in without.rows] == [r.deficit for r in with_actuals.rows]
    assert all(r.actual_near_future_demand is None for r in without.rows)
    assert any(r.actual_near_future_demand is not None for r in with_actuals.rows)


def test_demand_map_determinism():
    graph, fleet, sim = _run_to(520.0)
    runs = [build_demand_map(graph, fleet, sim.records(), sim.time_min,
                             include_actuals=True).to_dict() for _ in range(3)]
    assert all(run == runs[0] for run in runs)


def test_demand_map_is_json_serialisable():
    import json
    graph, fleet, sim = _run_to(520.0)
    data = build_demand_map(graph, fleet, sim.records(), sim.time_min,
                            include_actuals=True).to_dict()
    assert json.loads(json.dumps(data)) == data


def test_available_pods_excludes_busy_pods():
    """Only pods free to be moved are counted as supply."""
    graph, fleet, sim = _run_to(520.0)
    demand_map = build_demand_map(graph, fleet, sim.records(), sim.time_min)
    counted = sum(row.available_pods for row in demand_map.rows)
    idle = sum(1 for pod in fleet if pod.is_idle)
    assert counted == idle <= len(fleet)


def test_unknown_node_row_raises():
    graph, fleet, sim = _run_to(520.0)
    with pytest.raises(KeyError):
        build_demand_map(graph, fleet, sim.records(), sim.time_min).row("atlantis")


# --- config validation -------------------------------------------------------
@pytest.mark.parametrize("field,value", [
    ("current_window_min", 0.0), ("forecast_horizon_min", -1.0),
    ("forecast_recent_window_min", 0.0), ("rebalance_interval_min", 0.0),
    ("min_deficit_to_act", -1.0), ("max_repositions_per_cycle", -1),
    ("max_repositions_per_cycle", 1.5), ("max_concurrent_repositions", -1),
    ("max_reposition_distance_km", 0.0), ("reposition_battery_reserve_percent", 101.0),
    ("min_history_min", -1.0), ("max_surplus_before_drain", -1.0),
])
def test_invalid_rebalancing_config_rejected(field, value):
    with pytest.raises(RebalancingConfigError):
        RebalancingConfig(**{field: value})


def test_forecast_weights_must_sum_to_one():
    with pytest.raises(RebalancingConfigError, match="must be 1.0"):
        RebalancingConfig(forecast_recent_weight=0.7, forecast_profile_weight=0.7)
    assert RebalancingConfig(forecast_recent_weight=0.2, forecast_profile_weight=0.8)


def test_config_declares_no_ml_and_no_optimality():
    data = DEFAULT_REBALANCING_CONFIG.to_dict()
    assert "no machine learning" in data["data_provenance"]
    assert "no LLM" in data["data_provenance"]
    assert "not" in data["data_provenance"] and "optimal" in data["data_provenance"]
    assert "never future trips" in data["forecast"]["note"]
    assert "not a global optimum" in data["rebalancer"]["note"]
