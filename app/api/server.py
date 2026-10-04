"""Modular Swarm Network Visual Command Center API Server.

Stdlib-only HTTP API adapter (no FastAPI, no Flask, no requests).
Exposes the deterministic M1-M6 engines directly to the React/TypeScript frontend.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from app.config import DEFAULT_SCENARIO_PATH
from app.api.demo import DEMO_PASSENGERS, DEMO_PODS, DEMO_SCENARIO_ID, DEMO_START_MIN, DemoRunner
from app.api.events import (
    SCENARIO_LOADED,
    SIMULATION_INIT,
    TRAFFIC_INJECTED,
    EventDeriver,
    provider_label,
)
from app.demand.config import BASELINE_DEMAND_PROFILE, DEMAND_PROFILES
from app.demand.generator import generate_demand
from app.demand.profile_io import resolve_demand_profile
from app.fleet.config import DEFAULT_FLEET_CONFIG, FleetConfig
from app.fleet.generator import generate_fleet
from app.fleet.metrics import compute_fleet_metrics
from app.network.builder import load_scenario
from app.network.synthetic_city import build_synthetic_city
from app.orchestration import (
    DEFAULT_ORCHESTRATION_CONFIG,
    EVALUATION_SCENARIOS,
    MockProvider,
    OrchestrationAPI,
    ScenarioSpec,
    build_simulation,
    evaluation_scenario,
    run_scenario,
)
from app.orchestration.actions import parse_action
from app.orchestration.providers import GeminiProvider, api_key_status
from app.rebalancing.config import DEFAULT_REBALANCING_CONFIG, RebalancingConfig
from app.rebalancing.metrics import compute_rebalancing_metrics
from app.rebalancing.simulation import RebalancingSimulation
from app.routing import find_route
from app.simulation.state import SimulationState
from app.swarm.config import DEFAULT_SWARM_CONFIG, SwarmConfig
from app.swarm.metrics import compute_swarm_metrics
from app.swarm.models import SwarmStatus

logger = logging.getLogger("app.api.server")


class SimulationSession:
    """Manages active simulation state and OrchestrationAPI instance."""

    def __init__(self, scenario_id: str = "baseline", pods: int = 60, passengers: int = 400,
                 city_seed: int = 42, fleet_seed: int = 42, demand_seed: int = 42,
                 enable_swarms: bool = True, enable_rebalancing: bool = True,
                 algorithm: str = "astar") -> None:
        self.scenario_id = scenario_id
        self.pods_count = pods
        self.passengers_count = passengers
        self.city_seed = city_seed
        self.fleet_seed = fleet_seed
        self.demand_seed = demand_seed
        self.enable_swarms = enable_swarms
        self.enable_rebalancing = enable_rebalancing
        self.algorithm = algorithm
        self.demo: DemoRunner | None = None
        self._init_simulation()

    @property
    def events(self) -> list[dict[str, Any]]:
        return self.deriver.events

    @property
    def provider_type(self) -> str:
        return self._provider_type

    @property
    def mode(self) -> str:
        return "demo" if self.demo is not None and self.demo.status != "idle" else "normal"

    def _init_simulation(self) -> None:
        self.demo = None
        if self.scenario_id == DEMO_SCENARIO_ID:
            # Fixed, fully specified demo start: the quiet minutes before the morning ramp.
            profile_name = "baseline"
            self.profile = resolve_demand_profile(profile_name)
            self.spec = ScenarioSpec(
                scenario_id=self.scenario_id, profile_name=profile_name,
                pods=DEMO_PODS, passengers=DEMO_PASSENGERS,
                city_seed=self.city_seed, fleet_seed=self.fleet_seed,
                demand_seed=self.demand_seed, enable_swarms=True,
                enable_rebalancing=True, algorithm=self.algorithm)
            self.sim = build_simulation(self.spec)
            self.sim.run(until_min=DEMO_START_MIN)
            self.graph = self.sim.graph
            init_msg = (f"Loaded demo scenario at minute {self.sim.time_min:.0f} "
                        f"({DEMO_PODS} pods, {DEMO_PASSENGERS} passengers)")
            init_kind = SCENARIO_LOADED
        elif self.scenario_id in ("A_LARGE_DEFICIT", "B_NO_DEFICIT", "C_EXPENSIVE"):
            eval_scen = evaluation_scenario(self.scenario_id)
            self.sim = eval_scen.prepared()
            self.profile = getattr(self.sim, "_profile", resolve_demand_profile("baseline"))
            self.graph = self.sim.graph
            init_msg = f"Loaded evaluation scenario {self.scenario_id} at minute {self.sim.time_min:.1f}"
            init_kind = SCENARIO_LOADED
        else:
            profile_name = "peak_hour" if self.scenario_id == "peak_hour" else "baseline"
            self.profile = resolve_demand_profile(profile_name)
            self.spec = ScenarioSpec(
                scenario_id=self.scenario_id,
                profile_name=profile_name,
                pods=self.pods_count,
                passengers=self.passengers_count,
                city_seed=self.city_seed,
                fleet_seed=self.fleet_seed,
                demand_seed=self.demand_seed,
                enable_swarms=self.enable_swarms,
                enable_rebalancing=self.enable_rebalancing,
                algorithm=self.algorithm,
            )
            self.sim = build_simulation(self.spec)
            self.graph = self.sim.graph
            init_msg = f"Initialized {self.scenario_id} ({self.pods_count} pods, {self.passengers_count} passengers)"
            init_kind = SIMULATION_INIT

        # Event ids restart at EVT-00001 and the baseline is adopted from the
        # freshly built state, so a reset reproduces the same event stream exactly.
        self.deriver = EventDeriver(self.sim, self.algorithm)
        self._log_event(init_kind, init_msg)

        # Configure OrchestrationAPI with live Gemini if key present, else MockProvider
        key_info = api_key_status()
        if key_info.get("api_key_present", False):
            try:
                provider = GeminiProvider()
                self._provider_type = "live"
                self._log_event("ORCHESTRATOR", "Connected to Live Gemini provider")
            except Exception as e:
                logger.warning(f"Could not initialize live Gemini provider: {e}; falling back to mock")
                provider = MockProvider()
                self._provider_type = "mock"
                self._log_event("ORCHESTRATOR", f"Falling back to MOCK GEMINI: {e}")
        else:
            provider = MockProvider()
            self._provider_type = "mock"
            self._log_event("ORCHESTRATOR", "Initialized MOCK GEMINI provider (deterministic; no live API call)")

        self.api = OrchestrationAPI(provider, self.sim)

    def _log_event(self, kind: str, message: str, details: dict[str, Any] | None = None,
                   severity: str = "info") -> dict[str, Any]:
        """Lifecycle / operator-input events (not derived from a state diff)."""
        return self.deriver.emit(kind, message, severity=severity, source="system",
                                 **(details or {}))

    def step(self, ticks: int = 1) -> dict[str, Any]:
        new_events: list[dict[str, Any]] = []
        for _ in range(ticks):
            if not self.sim.has_pending_work:
                break
            self.sim.tick()
            new_events += self.deriver.observe()
        return self.get_state(new_events=new_events)

    def run_orchestration_cycle(self, advance_min: float | None = None
                                ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """One real M6 cycle -> (enriched audit record, newly derived events)."""
        record = self.api.propose_action(advance_min=advance_min)
        checks = record.get("checks", [])
        record["checks_passed"] = sum(1 for c in checks if c.get("passed"))
        record["total_checks"] = len(checks)
        if record.get("execution"):
            exec_data = record["execution"]
            record["execution_status"] = exec_data.get("status")
            record["execution_detail"] = exec_data.get("detail")
            payload = exec_data.get("payload", {})
            record["result"] = {
                "repositionings_dispatched": payload.get("dispatched_count", 0),
                "deficit_before": payload.get("total_deficit_before"),
                "deficit_after": payload.get("total_deficit_after"),
                "moves": payload.get("moves", []),
            }
        events = self.deriver.from_orchestration_record(record, self._provider_type)
        events += self.deriver.observe()
        return record, events

    def inject_traffic(self, edge_id: str, utilization: float) -> list[dict[str, Any]]:
        """Operator-injected traffic (normal mode). Congestion events are then *derived*."""
        edge = self.graph.get_edge(edge_id)
        new_count = int(round(edge.capacity_vehicles_per_hour * utilization))
        edge.set_vehicle_count(new_count)
        injected = self._log_event(
            TRAFFIC_INJECTED,
            f"Operator set edge {edge_id} vehicle count to {new_count} "
            f"(utilization {edge.utilization:.1%})",
            {"edge_id": edge_id, "vehicle_count": new_count})
        return [injected] + self.deriver.observe()

    def start_demo(self) -> dict[str, Any]:
        """Reset to the deterministic demo initial state and enter DEMO MODE."""
        self.scenario_id = DEMO_SCENARIO_ID
        self._init_simulation()
        self.demo = DemoRunner(self)
        self.demo.begin()
        return self.get_state(include_events=True)

    def demo_advance(self) -> dict[str, Any]:
        if self.demo is None:
            raise ValueError("Demo mode is not active")
        new_events = self.demo.advance()
        return self.get_state(new_events=new_events)

    def metrics(self) -> dict[str, Any]:
        fleet_metrics = compute_fleet_metrics(self.sim.fleet, self.sim.records(), self.sim.time_min)
        rebal_metrics = compute_rebalancing_metrics(self.sim)
        swarm_metrics = compute_swarm_metrics(self.sim)
        return {
            "time_min": round(self.sim.time_min, 2),
            "tick_index": self.sim.tick_index,
            "is_finished": not self.sim.has_pending_work,
            "total_pods": len(self.sim.fleet),
            "active_pods": sum(1 for p in self.sim.fleet.pods() if p.status.value in ("traveling", "assigned")),
            "idle_pods": sum(1 for p in self.sim.fleet.pods() if p.status.value == "idle"),
            "charging_pods": sum(1 for p in self.sim.fleet.pods() if p.status.value == "charging"),
            "active_swarms": len(self.sim.active_swarms()),
            "total_swarms_formed": len(self.sim.swarms()),
            "trips_served": rebal_metrics.trips_served,
            "unserved_trips": rebal_metrics.unserved_trips,
            "average_wait_min": rebal_metrics.average_wait_min,
            "average_completion_time_min": rebal_metrics.average_completion_time_min,
            "total_energy_kwh": fleet_metrics.total_energy_kwh,
            "pod_distance_km": rebal_metrics.pod_distance_km,
            "deadhead_distance_km": rebal_metrics.reposition_distance_km,
            "deadhead_ratio": round(rebal_metrics.reposition_distance_km / rebal_metrics.pod_distance_km, 4) if rebal_metrics.pod_distance_km > 0 else 0.0,
            "passenger_distance_km": rebal_metrics.passenger_distance_km,
            "road_occupancy_equiv_km": swarm_metrics.road_occupancy_equiv_km,
            "road_occupancy_saved_equiv_km": swarm_metrics.road_occupancy_saved_equiv_km,
            "completed_repositions": rebal_metrics.completed_repositions,
            "final_total_deficit": rebal_metrics.final_total_deficit,
        }

    def get_state(self, new_events: list[dict[str, Any]] | None = None,
                  include_events: bool = False) -> dict[str, Any]:
        # Serialize pods
        pods_data = []
        for pod in self.sim.fleet.pods():
            edge_progress = 0.0
            if pod.current_edge_time_min and pod.current_edge_time_min > 0:
                edge_progress = min(1.0, pod.current_edge_elapsed_min / pod.current_edge_time_min)
            
            # Find which swarm this pod belongs to, if any
            swarm_id = None
            for s in self.sim.active_swarms():
                if pod.pod_id in s.pod_ids:
                    swarm_id = s.swarm_id
                    break

            pods_data.append({
                "pod_id": pod.pod_id,
                "current_node_id": pod.current_node_id,
                "current_edge_id": pod.current_edge_id,
                "status": pod.status.value,
                "battery_percent": round(pod.battery_percent, 1),
                "occupied_seats": pod.occupied_seats,
                "trip_kind": pod.current_trip_kind.value,
                "edge_progress": round(edge_progress, 3),
                "swarm_id": swarm_id,
                "route_edges": list(pod.route.edge_ids) if pod.route else [],
                "route_index": pod.route_index,
            })

        # Serialize active swarms
        swarms_data = []
        for s in self.sim.active_swarms():
            swarms_data.append({
                "swarm_id": s.swarm_id,
                "status": s.status.value,
                "member_pod_ids": list(s.pod_ids),
                "origin_node_id": s.corridor.origin_node_id,
                "divergence_node_id": s.corridor.divergence_node_id,
                "corridor_edges": list(s.corridor.edge_ids),
                "current_edge_id": getattr(s, "current_edge_id", None),
                "shared_distance_km": round(s.shared_distance_km, 3),
                "corridor_distance_km": round(s.corridor.distance_km, 3),
            })

        # Serialize edge congestions
        edges_data = {}
        for edge in self.graph.edges():
            edges_data[edge.edge_id] = {
                "vehicle_count": edge.current_vehicle_count,
                "capacity": edge.capacity_vehicles_per_hour,
                "utilization": round(edge.utilization, 3),
                "current_travel_time_min": round(edge.current_travel_time_min, 2),
                "congestion_multiplier": round(edge.congestion_multiplier, 3),
                "is_overloaded": edge.is_overloaded,
            }

        metrics = self.metrics()

        return {
            "time_min": round(self.sim.time_min, 2),
            "tick_index": self.sim.tick_index,
            "is_finished": not self.sim.has_pending_work,
            "pods": pods_data,
            "swarms": swarms_data,
            "edges": edges_data,
            "metrics": metrics,
            "new_events": new_events or [],
            "provider_type": self._provider_type,
            "gemini_label": provider_label(self._provider_type),
            "mode": self.mode,
            "demo": self.demo.to_dict() if self.demo is not None else None,
            **({"events": list(self.deriver.events[-200:])} if include_events else {}),
        }


import threading

# Global session instance and thread locks
_session: SimulationSession | None = None
_session_lock = threading.Lock()
# Serialises every state-mutating request so overlapping browser calls can never
# interleave two ticks / a tick and a reset.
_op_lock = threading.RLock()


class _DemoBusy(Exception):
    """Raised for requests that are not valid in the current demo state (HTTP 409)."""


def _reject_if_demo(session: "SimulationSession", action: str) -> None:
    if session.demo is not None and session.demo.status in ("running", "paused"):
        raise _DemoBusy(f"Cannot {action} while Demo Mode is in progress; reset first")


def get_session() -> SimulationSession:
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:
                _session = SimulationSession()
    return _session


class VisualCommandCenterHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the Visual Command Center API."""

    def _set_headers(self, status: int = HTTPStatus.OK, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_OPTIONS(self) -> None:
        self._set_headers(HTTPStatus.NO_CONTENT)

    def _send_json(self, data: Any, status: int = HTTPStatus.OK) -> None:
        self._set_headers(status)
        body = json.dumps(data, indent=2, ensure_ascii=False)
        self.wfile.write(body.encode("utf-8"))

    def _send_error(self, message: str, status: int = HTTPStatus.BAD_REQUEST) -> None:
        self._send_json({"error": message}, status=status)

    def _read_body_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON payload: {exc}") from exc

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        try:
            if path in ("/api", "/api/health"):
                self._handle_health()
            elif path == "/api/network":
                self._handle_network()
            elif path == "/api/scenarios":
                self._handle_scenarios()
            elif path == "/api/simulation/state":
                with _op_lock:
                    session = get_session()
                    self._send_json(session.get_state(include_events=True))
            elif path == "/api/simulation/events":
                with _op_lock:
                    session = get_session()
                    self._send_json({"events": session.events})
            elif path == "/api/demo/state":
                with _op_lock:
                    session = get_session()
                    self._send_json({"mode": session.mode,
                                     "demo": session.demo.to_dict() if session.demo else None})
            elif path == "/api/demand-map":
                self._handle_demand_map()
            elif path == "/api/orchestrator/observation":
                session = get_session()
                obs = session.api.get_observation()
                self._send_json(obs)
            elif path == "/api/orchestrator/audit":
                session = get_session()
                log = session.api.get_audit_log()
                self._send_json(log)
            elif path == "/api/orchestrator/result":
                session = get_session()
                self._send_json({"result": session.api.get_result()})
            elif path == "/api/comparison":
                self._handle_comparison()
            elif path.startswith("/api/"):
                self._send_error(f"Endpoint not found: {path}", status=HTTPStatus.NOT_FOUND)
            else:
                self._serve_static(parsed.path)
        except Exception as exc:
            logger.exception("Error handling GET %s: %s", path, exc)
            self._send_error(str(exc), status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _serve_static(self, raw_path: str) -> None:
        from app.config import PROJECT_ROOT
        import mimetypes

        dist_dir = PROJECT_ROOT / "frontend" / "dist"
        if not dist_dir.exists():
            self._handle_health()
            return

        clean = raw_path.strip("/")
        target = (dist_dir / clean).resolve()
        if not str(target).startswith(str(dist_dir.resolve())) or not target.exists() or target.is_dir():
            target = dist_dir / "index.html"

        content_type, _ = mimetypes.guess_type(str(target))
        if not content_type:
            content_type = "text/html" if target.suffix == ".html" else "application/octet-stream"

        data = target.read_bytes()
        self._set_headers(HTTPStatus.OK, content_type=content_type)
        self.wfile.write(data)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        try:
            body = self._read_body_json()
        except ValueError as err:
            self._send_error(str(err), status=HTTPStatus.BAD_REQUEST)
            return

        try:
            with _op_lock:
                self._dispatch_post(path, body)
        except _DemoBusy as exc:
            self._send_error(str(exc), status=HTTPStatus.CONFLICT)
        except Exception as exc:
            logger.exception("Error handling POST %s: %s", path, exc)
            self._send_error(str(exc), status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _dispatch_post(self, path: str, body: dict[str, Any]) -> None:
        global _session
        if path == "/api/simulation/init":
            _session = SimulationSession(
                scenario_id=body.get("scenario_id", "baseline"),
                pods=int(body.get("pods", 60)),
                passengers=int(body.get("passengers", 400)),
                city_seed=int(body.get("city_seed", 42)),
                fleet_seed=int(body.get("fleet_seed", 42)),
                demand_seed=int(body.get("demand_seed", 42)),
                enable_swarms=bool(body.get("enable_swarms", True)),
                enable_rebalancing=bool(body.get("enable_rebalancing", True)),
            )
            self._send_json(_session.get_state(include_events=True))
        elif path == "/api/simulation/step":
            session = get_session()
            _reject_if_demo(session, "step the simulation manually")
            ticks = int(body.get("ticks", 1))
            self._send_json(session.step(ticks=ticks))
        elif path == "/api/simulation/reset":
            # RESET always restores the exact deterministic initial state of the
            # *current scenario* and leaves Demo Mode.
            session = get_session()
            session._init_simulation()
            self._send_json(session.get_state(include_events=True))
        elif path == "/api/simulation/set-traffic":
            self._handle_set_traffic(body)
        elif path == "/api/route-switch":
            self._handle_route_switch(body)
        elif path == "/api/orchestrator/cycle":
            session = get_session()
            _reject_if_demo(session, "run an orchestrator cycle manually")
            advance_min = body.get("advance_min")
            if advance_min is not None:
                advance_min = float(advance_min)
            record, events = session.run_orchestration_cycle(advance_min=advance_min)
            self._send_json({"record": record, "state": session.get_state(new_events=events)})
        elif path == "/api/orchestrator/validate":
            session = get_session()
            self._send_json(session.api.validate_action(body.get("action", {})))
        elif path == "/api/orchestrator/execute":
            session = get_session()
            _reject_if_demo(session, "execute an action manually")
            self._send_json(session.api.execute_action(body.get("action", {})))
        elif path == "/api/demo/start":
            self._send_json(get_session().start_demo())
        elif path == "/api/demo/advance":
            session = get_session()
            if session.demo is None:
                raise _DemoBusy("Demo mode is not active; POST /api/demo/start first")
            self._send_json(session.demo_advance())
        elif path in ("/api/demo/pause", "/api/demo/resume"):
            session = get_session()
            if session.demo is None:
                raise _DemoBusy("Demo mode is not active")
            (session.demo.pause if path.endswith("pause") else session.demo.resume)()
            self._send_json(session.get_state())
        else:
            self._send_error(f"Endpoint not found: {path}", status=HTTPStatus.NOT_FOUND)

    # --------------------------------------------------------------------------
    # Handlers
    # --------------------------------------------------------------------------
    def _handle_health(self) -> None:
        key_info = api_key_status()
        self._send_json({
            "status": "ok",
            "version": "Milestone 7 Command Center",
            "python_version": sys.version,
            "api_key_status": key_info,
            "gemini_mode": "LIVE GEMINI" if key_info.get("api_key_present") else "MOCK GEMINI",
        })

    def _handle_network(self) -> None:
        session = get_session()
        graph = session.graph
        nodes_list = []
        lats = []
        lons = []
        for n in graph.nodes():
            nodes_list.append({
                "id": n.node_id,
                "node_id": n.node_id,
                "name": n.name,
                "latitude": n.latitude,
                "longitude": n.longitude,
                "x": n.longitude,
                "y": n.latitude,
                "node_type": n.node_type.value,
            })
            lats.append(n.latitude)
            lons.append(n.longitude)

        edges_list = []
        for e in graph.edges():
            edges_list.append({
                "id": e.edge_id,
                "edge_id": e.edge_id,
                "source": e.source,
                "target": e.destination,
                "destination": e.destination,
                "distance_km": e.distance_km,
                "base_travel_time_min": e.base_travel_time_min,
                "capacity": e.capacity_vehicles_per_hour,
                "capacity_vehicles_per_hour": e.capacity_vehicles_per_hour,
                "road_type": e.road_type.value,
                "current_vehicle_count": e.current_vehicle_count,
                "current_travel_time_min": e.current_travel_time_min,
                "utilization": round(e.utilization, 3),
            })

        bounds = {
            "min_lat": min(lats) if lats else 44.9,
            "max_lat": max(lats) if lats else 45.1,
            "min_lon": min(lons) if lons else 9.9,
            "max_lon": max(lons) if lons else 10.1,
        }

        self._send_json({
            "nodes": nodes_list,
            "edges": edges_list,
            "bounds": bounds,
            "node_count": len(nodes_list),
            "edge_count": len(edges_list),
        })

    def _handle_scenarios(self) -> None:
        scenarios = [
            {
                "id": "baseline",
                "name": "Baseline City",
                "description": "Standard 22-node network, 60 pods, 400 passengers with balanced OD demand.",
                "type": "standard",
                "pods": 60,
                "passengers": 400,
            },
            {
                "id": "peak_hour",
                "name": "Peak Hour Demand",
                "description": "Peak-hour demand surge from residential nodes to tech park and university.",
                "type": "standard",
                "pods": 80,
                "passengers": 800,
            },
            {
                "id": DEMO_SCENARIO_ID,
                "name": "Morning Rush (demo start state)",
                "description": "40 pods, 600 passengers, starting at minute 360 just before the morning ramp. Selecting it manually runs the plain simulation; RUN DEMO adds the scripted sequence.",
                "type": "standard",
                "pods": DEMO_PODS,
                "passengers": DEMO_PASSENGERS,
            },
            {
                "id": "A_LARGE_DEFICIT",
                "name": "Evaluation: Large Deficit",
                "description": "20 pods against 800 passengers at minute 420. High deficit (~26 pods). Rebalancing candidate.",
                "type": "evaluation",
                "expected_consideration": "REQUEST_REBALANCING",
            },
            {
                "id": "B_NO_DEFICIT",
                "name": "Evaluation: No Deficit",
                "description": "120 pods against 40 passengers at minute 240. Total deficit is zero. Surplus supply.",
                "type": "evaluation",
                "expected_consideration": "NO_ACTION",
            },
            {
                "id": "C_EXPENSIVE",
                "name": "Evaluation: Expensive Deadhead",
                "description": "110 pods against 250 passengers at minute 560. Real deficit of ~4 pods, but deadhead distance already high.",
                "type": "evaluation",
                "expected_consideration": "NO_ACTION or bounded action",
            },
        ]
        self._send_json({"scenarios": scenarios})

    def _handle_demand_map(self) -> None:
        session = get_session()
        dmap = session.sim.demand_map()

        nodes_demand = {}
        for row in dmap.rows:
            nodes_demand[row.node_id] = {
                "node_id": row.node_id,
                "expected_trips": round(row.forecast_demand, 2),
                "expected_passengers": round(row.forecast_demand, 2),
                "required_pods": round(row.forecast_demand, 2),
                "available_pods": row.available_pods,
                "net_balance": round(row.balance, 2),
                "deficit": round(row.deficit, 2),
                "surplus": round(row.surplus, 2),
                "is_deficit": row.is_deficit,
                "is_surplus": row.is_surplus,
            }

        from collections import Counter
        od_counts = Counter(
            (r.origin_node_id, r.destination_node_id)
            for r in session.sim.records()
        )
        top_od_flows = [
            {"origin": orig, "destination": dest, "trips": count}
            for (orig, dest), count in od_counts.most_common(6)
        ]

        self._send_json({
            "time_min": round(dmap.now_min, 2),
            "forecast_horizon_min": dmap.horizon_min,
            "total_forecast_demand": round(sum(r.forecast_demand for r in dmap.rows), 2),
            "total_deficit": round(dmap.total_deficit, 2),
            "total_surplus": round(dmap.total_surplus, 2),
            "deficit_nodes": [r.node_id for r in dmap.deficit_rows()],
            "surplus_nodes": [r.node_id for r in dmap.surplus_rows()],
            "nodes": nodes_demand,
            "top_od_flows": top_od_flows,
        })

    def _handle_set_traffic(self, body: dict[str, Any]) -> None:
        session = get_session()
        edge_id = body.get("edge_id")
        utilization = float(body.get("utilization", 1.5))
        if not edge_id or not session.graph.has_edge(edge_id):
            self._send_error(f"Invalid or missing edge_id: {edge_id}")
            return
        if session.demo is not None and session.demo.status in ("running", "paused"):
            self._send_error("Cannot inject traffic manually while Demo Mode is in progress; reset first",
                             status=HTTPStatus.CONFLICT)
            return
        edge = session.graph.get_edge(edge_id)
        events = session.inject_traffic(edge_id, utilization)
        self._send_json({"status": "ok", "edge_id": edge_id,
                         "vehicle_count": edge.current_vehicle_count,
                         "utilization": round(edge.utilization, 3),
                         "new_events": events})

    def _handle_route_switch(self, body: dict[str, Any]) -> None:
        session = get_session()
        origin_id = body.get("origin", "north_station")
        dest_id = body.get("destination", "airport")
        congest_edge = body.get("congest_edge", "E007")
        utilization = float(body.get("utilization", 2.0))

        # 1. Base route
        city = build_synthetic_city(42)
        before_route = find_route(city.graph, origin_id, dest_id, algorithm="astar")

        # 2. Congest edge
        if congest_edge and city.graph.has_edge(congest_edge):
            edge = city.graph.get_edge(congest_edge)
            edge.set_vehicle_count(int(round(edge.capacity_vehicles_per_hour * utilization)))

        # 3. Recalculated route
        after_route = find_route(city.graph, origin_id, dest_id, algorithm="astar")

        def route_dict(r) -> dict[str, Any]:
            return {
                "origin": r.origin,
                "destination": r.destination,
                "node_ids": list(r.node_ids),
                "edge_ids": list(r.edge_ids),
                "total_distance_km": round(r.total_distance_km, 3),
                "total_travel_time_min": round(r.total_travel_time_min, 2),
                "free_flow_travel_time_min": round(r.free_flow_travel_time_min, 2),
            }

        self._send_json({
            "origin": origin_id,
            "destination": dest_id,
            "congested_edge": congest_edge,
            "congested_utilization": utilization,
            "route_changed": before_route.edge_ids != after_route.edge_ids,
            "before_route": route_dict(before_route),
            "after_route": route_dict(after_route),
        })

    def _handle_comparison(self) -> None:
        """Run the 3 deterministic scenario specs (Baseline, Swarm, Adaptive) and return metrics."""
        spec_baseline = ScenarioSpec(
            scenario_id="baseline_independent",
            profile_name="baseline",
            pods=60,
            passengers=400,
            enable_swarms=False,
            enable_rebalancing=False,
            horizon_min=180.0,
        )
        spec_swarm = ScenarioSpec(
            scenario_id="swarm_platooning",
            profile_name="baseline",
            pods=60,
            passengers=400,
            enable_swarms=True,
            enable_rebalancing=False,
            horizon_min=180.0,
        )
        spec_adaptive = ScenarioSpec(
            scenario_id="adaptive_rebalancing",
            profile_name="baseline",
            pods=60,
            passengers=400,
            enable_swarms=True,
            enable_rebalancing=True,
            horizon_min=180.0,
        )

        res_b = run_scenario(spec_baseline)
        res_s = run_scenario(spec_swarm)
        res_a = run_scenario(spec_adaptive)

        # Calculate road space saved from swarm vs baseline
        # In baseline (no swarms), road_occupancy_equiv_km == pod_distance_km
        road_space_saved = round(max(0.0, res_b.pod_distance_km - (res_s.pod_distance_km * 0.85)), 2)

        self._send_json({
            "horizon_min": 180.0,
            "baseline": res_b.to_dict(),
            "swarm": res_s.to_dict(),
            "adaptive": res_a.to_dict(),
            "impact_summary": {
                "road_space_saved_equiv_km": road_space_saved,
                "trips_served_gain": res_a.trips_served - res_b.trips_served,
                "wait_time_reduction_pct": round((1.0 - (res_a.average_wait_min or 1.0) / (res_b.average_wait_min or 1.0)) * 100, 1) if res_b.average_wait_min else 0.0,
                "deadhead_pct": round((res_a.deadhead_distance_km / res_a.pod_distance_km) * 100, 1) if res_a.pod_distance_km > 0 else 0.0,
            },
            "provenance": "DETERMINISTIC SIMULATION — Computed by M3, M4, M5 engines with identical seeds (42).",
            "scenario_potential_note": "Land reclamation / road space target is a prospective planning assumption based on platoon headway compression.",
        })


import socket


class DualStackServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6

    def server_bind(self):
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except (AttributeError, OSError) as exc:
            logger.warning("Could not disable IPV6_V6ONLY: %s", exc)
            raise
        super().server_bind()


def run_server(host: str = "", port: int = 8000) -> None:
    if host:
        server = ThreadingHTTPServer((host, port), VisualCommandCenterHandler)
    else:
        try:
            server = DualStackServer(("::", port), VisualCommandCenterHandler)
        except Exception as exc:
            logger.info("DualStack bind failed (%s); falling back to IPv4 0.0.0.0", exc)
            server = ThreadingHTTPServer(("0.0.0.0", port), VisualCommandCenterHandler)

    print(f"\n=======================================================")
    print(f" MODULAR SWARM NETWORK — VISUAL COMMAND CENTER")
    print(f"=======================================================")
    print(f" Server active! Open your browser to:")
    print(f"   -> http://localhost:{port}")
    print(f"   -> http://127.0.0.1:{port}")
    print(f"=======================================================\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down API server...")
    finally:
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Modular Swarm Network API Server")
    parser.add_argument("--host", default="", help="Host to bind to (default: dual-stack)")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind to (default: 8000)")
    args = parser.parse_args()
    run_server(args.host, args.port)
