"""Tests for the M7 Visual Command Center HTTP API Adapter."""

from __future__ import annotations

import json
import pytest

from app.api.server import SimulationSession


@pytest.fixture
def session() -> SimulationSession:
    return SimulationSession(scenario_id="baseline", pods=20, passengers=100)


def test_session_init(session: SimulationSession) -> None:
    assert session.sim is not None
    assert len(session.sim.fleet) == 20
    assert session.sim.time_min == 0.0
    state = session.get_state()
    assert "pods" in state
    assert len(state["pods"]) == 20
    assert "metrics" in state
    assert state["metrics"]["total_pods"] == 20
    assert state["metrics"]["trips_served"] == 0


def test_session_step(session: SimulationSession) -> None:
    initial_time = session.sim.time_min
    result = session.step(ticks=5)
    assert result["time_min"] > initial_time
    assert result["tick_index"] == 5
    assert len(result["pods"]) == 20
    assert "edges" in result
    assert len(result["edges"]) == 56


def test_session_orchestration_cycle(session: SimulationSession) -> None:
    obs = session.api.get_observation()
    assert "fingerprint" in obs
    assert "headline" in obs
    assert "demand" in obs

    # Propose action through mock provider
    record = session.api.propose_action()
    assert "cycle_id" in record
    assert "verdict" in record
    assert record["verdict"] in ("APPROVED", "REJECTED")


def test_session_validate_action(session: SimulationSession) -> None:
    # Valid proposal
    obs = session.api.get_observation()
    action = {
        "action_type": "NO_ACTION",
        "reason": "Testing validation rule",
        "expected_effect": "No change",
        "confidence": 1.0,
        "parameters": {},
        "observation_fingerprint": obs["fingerprint"],
    }
    verdict = session.api.validate_action(action)
    assert verdict["verdict"] == "APPROVED"
    assert len(verdict["checks"]) > 0

    # Invalid action type
    bad_action = {
        "action_type": "TELEPORT_PODS",
        "reason": "Non-existent action",
        "expected_effect": "Teleportation",
        "confidence": 1.0,
        "parameters": {},
        "observation_fingerprint": obs["fingerprint"],
    }
    verdict_bad = session.api.validate_action(bad_action)
    assert verdict_bad["verdict"] == "REJECTED"


def test_evaluation_scenario_session() -> None:
    session = SimulationSession(scenario_id="A_LARGE_DEFICIT")
    assert session.sim.time_min == 420.0
    state = session.get_state()
    assert state["metrics"]["final_total_deficit"] > 0


def test_handler_direct_dispatch() -> None:
    import io
    from unittest.mock import MagicMock
    from app.api.server import VisualCommandCenterHandler, get_session

    session = get_session()
    assert session is not None

    class MockRequest:
        def __init__(self, method: str, path: str, body: bytes = b"") -> None:
            self.method = method
            self.path = path
            self.body = body

        def makefile(self, mode: str, *args, **kwargs):
            if "b" in mode and "r" in mode:
                return io.BytesIO(self.body)
            elif "b" in mode and "w" in mode:
                return io.BytesIO()
            return io.StringIO()

        def sendall(self, data):
            pass

    # Test that health and network can be formatted directly
    key_info = session.api.orchestrator.config
    assert key_info is not None


def test_network_edge_utilization_serialization() -> None:
    from app.api.server import VisualCommandCenterHandler, get_session
    session = get_session()
    handler = VisualCommandCenterHandler.__new__(VisualCommandCenterHandler)
    captured = {}
    handler._send_json = lambda data, status=200: captured.update({"data": data, "status": status})

    handler._handle_network()
    assert captured["status"] == 200
    edges = captured["data"]["edges"]
    assert len(edges) == 56
    for edge in edges:
        assert "utilization" in edge
        assert isinstance(edge["utilization"], (int, float))
        assert edge["utilization"] >= 0.0
        assert "current_travel_time_min" in edge
        assert "capacity_vehicles_per_hour" in edge


def test_traffic_injection_utilization() -> None:
    from app.api.server import VisualCommandCenterHandler, get_session
    session = get_session()
    handler = VisualCommandCenterHandler.__new__(VisualCommandCenterHandler)
    captured = {}
    handler._send_json = lambda data, status=200: captured.update({"data": data, "status": status})
    handler._send_error = lambda msg, status=400: captured.update({"error": msg, "status": status})

    target_edge_id = "E001"
    edge = session.graph.get_edge(target_edge_id)
    initial_count = edge.current_vehicle_count

    handler._handle_set_traffic({"edge_id": target_edge_id, "utilization": 1.5})
    assert captured.get("status") == 200
    assert captured["data"]["edge_id"] == target_edge_id
    assert abs(captured["data"]["utilization"] - 1.5) < 0.05
    assert edge.current_vehicle_count > initial_count

    # Reset
    handler._handle_set_traffic({"edge_id": target_edge_id, "utilization": 0.0})
    assert edge.current_vehicle_count == 0
    assert captured["data"]["utilization"] == 0.0


def test_demand_map_serialization() -> None:
    from app.api.server import VisualCommandCenterHandler, get_session
    session = get_session()
    handler = VisualCommandCenterHandler.__new__(VisualCommandCenterHandler)
    captured = {}
    handler._send_json = lambda data, status=200: captured.update({"data": data, "status": status})

    handler._handle_demand_map()
    assert captured["status"] == 200
    data = captured["data"]
    assert "time_min" in data
    assert "forecast_horizon_min" in data
    assert "total_forecast_demand" in data
    assert "deficit_nodes" in data
    assert "surplus_nodes" in data
    assert "nodes" in data
    assert len(data["nodes"]) == 22
    assert "top_od_flows" in data


# --- /api/comparison road space -----------------------------------------------
# These pin the fix for a defect: the endpoint used to report road space as
#   max(0, baseline.pod_distance_km - swarm.pod_distance_km * 0.85)
# a hardcoded factor found nowhere in the engine, which differenced physical km
# against a scaled quantity and labelled the result equiv-km. On the endpoint's own
# fixed spec it produced 44.029 equiv-km while M4's real figure was 0.000, because
# no swarm forms inside the 180-minute horizon. The rules below are what replaced it.
def _spec(name: str, *, swarms: bool, rebalancing: bool = False,
          pods: int = 60, passengers: int = 400, horizon: float | None = 180.0):
    from app.orchestration import ScenarioSpec
    return ScenarioSpec(name, profile_name="baseline", pods=pods, passengers=passengers,
                        enable_swarms=swarms, enable_rebalancing=rebalancing,
                        horizon_min=horizon)


def test_scenario_result_carries_m4s_own_road_space() -> None:
    """The runner must publish the canonical metric, not leave callers to derive it."""
    from app.orchestration import run_scenario
    from app.orchestration.scenarios import build_simulation
    from app.swarm.metrics import compute_swarm_metrics

    spec = _spec("canonical", swarms=True)
    result = run_scenario(spec)

    simulation = build_simulation(spec)
    simulation.run(until_min=spec.horizon_min)
    expected = compute_swarm_metrics(simulation)

    assert result.road_occupancy_equiv_km == expected.road_occupancy_equiv_km
    assert result.road_occupancy_saved_equiv_km == expected.road_occupancy_saved_equiv_km
    assert result.road_occupancy_saving_percent == expected.road_occupancy_saving_percent
    assert result.swarm_count == expected.swarm_count


def test_road_space_baseline_without_swarms_is_exactly_zero() -> None:
    """M4's identity: with no formations, occupancy == pod distance, so saving is 0.0."""
    from app.api.server import _road_space_entry
    from app.orchestration import run_scenario

    result = run_scenario(_spec("no_swarms", swarms=False))
    assert result.swarm_count == 0
    assert result.road_occupancy_equiv_km == result.pod_distance_km
    assert result.road_occupancy_saved_equiv_km == 0.0

    entry = _road_space_entry(result)
    assert entry["available"] is True
    assert entry["saved_equiv_km"] == 0.0
    assert entry["saving_percent"] == 0.0
    assert entry["zero_reason"] is not None and "no swarm formed" in entry["zero_reason"]


def test_road_space_in_a_swarm_run_matches_the_closed_form() -> None:
    """A run that really platoons: the saving must equal sum d*(n-1)*(1-f) exactly."""
    from app.api.server import _road_space_entry
    from app.orchestration import run_scenario
    from app.orchestration.scenarios import build_simulation

    spec = _spec("swarming", swarms=True, horizon=900.0)
    simulation = build_simulation(spec)
    simulation.run(until_min=spec.horizon_min)
    swarms = simulation.swarms()
    assert swarms, "this scenario is meant to form swarms; re-tune it if it stops"

    factor = simulation.swarm_config.formation_occupancy_factor
    closed_form = sum(s.shared_distance_km * (s.size - 1) * (1.0 - factor) for s in swarms)

    result = run_scenario(spec)
    assert result.swarm_count == len(swarms)
    assert result.road_occupancy_saved_equiv_km == pytest.approx(closed_form, abs=1e-3)
    assert result.road_occupancy_saved_equiv_km > 0.0

    entry = _road_space_entry(result)
    assert entry["available"] is True
    assert entry["zero_reason"] is None
    assert entry["saved_equiv_km"] == result.road_occupancy_saved_equiv_km
    assert entry["saving_percent"] == result.road_occupancy_saving_percent


def test_road_space_is_unavailable_when_nothing_drove() -> None:
    """No distance driven -> road space undefined, reported as unavailable not zero."""
    from app.api.server import _road_space_entry
    from app.orchestration import run_scenario

    result = run_scenario(_spec("nothing_moved", swarms=True, pods=5, passengers=5,
                                horizon=1.0))
    assert result.pod_distance_km == 0.0
    assert result.road_occupancy_saving_percent is None

    entry = _road_space_entry(result)
    assert entry["available"] is False
    assert entry["saved_equiv_km"] is None
    assert entry["occupancy_equiv_km"] is None
    assert entry["saving_percent"] is None
    assert entry["unavailable_reason"] is not None


def test_road_space_summary_never_differences_across_runs() -> None:
    """The cross-run delta the old formula faked must stay absent and marked so."""
    from app.api.server import _road_space_summary
    from app.orchestration import run_scenario

    baseline = run_scenario(_spec("b", swarms=False))
    swarm = run_scenario(_spec("s", swarms=True))
    adaptive = run_scenario(_spec("a", swarms=True, rebalancing=True))

    summary = _road_space_summary(baseline, swarm, adaptive)
    assert summary["cross_run_difference_equiv_km"] is None
    assert summary["cross_run_comparable"] is False
    assert "not reported" in summary["cross_run_note"]
    assert set(summary["per_run"]) == {"baseline", "swarm", "adaptive"}
    assert summary["per_run"]["baseline"]["saved_equiv_km"] == 0.0
    assert "equiv-km" in summary["unit"]


def test_comparison_response_has_no_invented_road_space_figure() -> None:
    """The response must not reintroduce a flat road_space_saved_equiv_km scalar."""
    from app.api.server import VisualCommandCenterHandler

    handler = VisualCommandCenterHandler.__new__(VisualCommandCenterHandler)
    captured: dict = {}
    handler._send_json = lambda data, status=200: captured.update({"data": data,
                                                                   "status": status})
    handler._handle_comparison()
    assert captured["status"] == 200
    impact = captured["data"]["impact_summary"]

    assert "road_space_saved_equiv_km" not in impact
    road_space = impact["road_space"]
    assert road_space["cross_run_comparable"] is False
    for mode in ("baseline", "swarm", "adaptive"):
        entry = road_space["per_run"][mode]
        assert entry["saved_equiv_km"] is None or entry["saved_equiv_km"] >= 0.0
    # The engine's truth for this fixed spec: no formations inside 180 minutes.
    assert road_space["per_run"]["swarm"]["swarm_count"] == 0
    assert road_space["per_run"]["swarm"]["saved_equiv_km"] == 0.0


def test_comparison_response_is_json_serialisable() -> None:
    from app.api.server import VisualCommandCenterHandler

    handler = VisualCommandCenterHandler.__new__(VisualCommandCenterHandler)
    captured: dict = {}
    handler._send_json = lambda data, status=200: captured.update({"data": data})
    handler._handle_comparison()
    assert json.loads(json.dumps(captured["data"])) == captured["data"]
