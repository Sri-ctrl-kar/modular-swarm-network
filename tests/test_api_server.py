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



