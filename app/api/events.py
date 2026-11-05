"""Deterministic event derivation for the Visual Command Center.

Every event produced here is a *difference between two real states* of the
M1-M6 engines (or a field of a real M6 audit record). Nothing is emitted because
a timer elapsed, and nothing is invented: if the simulation did not change in a
relevant way between two ``observe`` calls, ``observe`` returns ``[]``.

The deriver is read-only with respect to the simulation. It stores only what it
needs to detect a *transition* (previous swarm statuses, previous reposition ids,
which edges are currently over the congestion threshold, ...).

Event ids are ``EVT-00001, EVT-00002, ...`` in emission order, so the same
sequence of simulation states always yields byte-identical events.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from app.routing import find_route
from app.routing.costs import custom_cost_model
from app.swarm.models import SwarmStatus

# --- thresholds (documented, not tuned to make a demo look good) ---------------
CONGESTION_THRESHOLD = 0.80      # edge utilization at/above which an edge is congested
CONGESTION_CLEAR = 0.70          # must fall below this before the edge can alert again
SURGE_MIN_ABS_TRIPS = 2.0        # forecast trips in the horizon must rise by at least this ...
SURGE_MIN_REL = 0.50             # ... and by at least this fraction of the previous forecast
IMBALANCE_MIN_DEFICIT = 1.0      # pods short at a node before it counts as imbalanced

# Event kinds -----------------------------------------------------------------
DEMAND_SURGE = "DEMAND_SURGE"
CONGESTION_DETECTED = "CONGESTION_DETECTED"
ROUTE_ADAPTATION = "ROUTE_ADAPTATION"
SWARM_FORMED = "SWARM_FORMED"
SWARM_SPLIT = "SWARM_SPLIT"
DEMAND_IMBALANCE = "DEMAND_IMBALANCE"
FLEET_REBALANCING = "FLEET_REBALANCING"
GEMINI_PROPOSAL = "GEMINI_PROPOSAL"
VALIDATION = "VALIDATION"
EXECUTION = "EXECUTION"
# Lifecycle / operator inputs (not derived from a state diff; clearly labelled)
SIMULATION_INIT = "SIMULATION_INIT"
SCENARIO_LOADED = "SCENARIO_LOADED"
TRAFFIC_INJECTED = "TRAFFIC_INJECTED"
DEMO_INPUT = "DEMO_INPUT"

DERIVED_KINDS = (
    DEMAND_SURGE, CONGESTION_DETECTED, ROUTE_ADAPTATION, SWARM_FORMED, SWARM_SPLIT,
    DEMAND_IMBALANCE, FLEET_REBALANCING, GEMINI_PROPOSAL, VALIDATION, EXECUTION,
)

_FREE_FLOW = custom_cost_model("free_flow_min", lambda edge: edge.base_travel_time_min)


def provider_label(provider_type: str) -> str:
    return "LIVE GEMINI" if provider_type == "live" else "MOCK GEMINI"


class EventDeriver:
    """Turns successive real simulation states into a deterministic event stream."""

    def __init__(self, simulation: Any, algorithm: str = "astar") -> None:
        self._sim = simulation
        self._graph = simulation.graph
        self._algorithm = algorithm
        self._counter = 0
        self.events: list[dict[str, Any]] = []
        self._new: list[dict[str, Any]] = []
        self._swarm_status: dict[str, str] = {}
        self._reposition_ids: set[str] = set()
        self._congested: set[str] = set()
        self._pod_route: dict[str, tuple[str, ...]] = {}
        self._free_flow_cache: dict[tuple[str, str], tuple[str, ...]] = {}
        self._cycle_count = 0
        self._prev_forecast: dict[str, float] | None = None
        self._deficit_nodes: set[str] = set()
        self.prime()

    # ------------------------------------------------------------------ basics
    def prime(self) -> None:
        """Adopt the simulation's *current* state as the baseline. Emits nothing."""
        sim = self._sim
        self._swarm_status = {s.swarm_id: s.status.value for s in sim.swarms()}
        self._reposition_ids = {a.reposition_id for a in sim.repositions()}
        self._congested = {e.edge_id for e in self._graph.edges()
                           if e.utilization >= CONGESTION_THRESHOLD}
        self._pod_route = {p.pod_id: self._route_edges(p) for p in sim.fleet.pods()}
        self._cycle_count = len(sim.deficit_history())
        rows = sim.demand_map().rows
        self._prev_forecast = {r.node_id: r.forecast_demand for r in rows}
        self._deficit_nodes = {r.node_id for r in rows if r.deficit >= IMBALANCE_MIN_DEFICIT}

    def emit(self, kind: str, message: str, *, severity: str = "info",
             source: str = "simulation", **details: Any) -> dict[str, Any]:
        self._counter += 1
        event = {
            "id": f"EVT-{self._counter:05d}",
            "time_min": round(self._sim.time_min, 2),
            "kind": kind,
            "message": message,
            "severity": severity,
            "source": source,
            "details": details,
        }
        self.events.append(event)
        self._new.append(event)
        if len(self.events) > 500:
            self.events = self.events[-500:]
        return event

    def _name(self, node_id: str) -> str:
        try:
            return self._graph.get_node(node_id).name
        except Exception:  # pragma: no cover - defensive
            return node_id

    @staticmethod
    def _route_edges(pod: Any) -> tuple[str, ...]:
        return tuple(pod.route.edge_ids) if getattr(pod, "route", None) else ()

    # ----------------------------------------------------------- state diffing
    def observe(self) -> list[dict[str, Any]]:
        """Compare the simulation against the last observation; return new events."""
        self._new = []
        self._observe_swarms()
        self._observe_repositions()
        self._observe_congestion()
        self._observe_routes()
        self._observe_demand()
        return list(self._new)

    def _observe_swarms(self) -> None:
        for swarm in self._sim.swarms():
            sid, status = swarm.swarm_id, swarm.status.value
            previous = self._swarm_status.get(sid)
            if previous is None:
                pods = ", ".join(swarm.pod_ids)
                self.emit(
                    SWARM_FORMED,
                    f"Swarm {sid} formed with {len(swarm.pod_ids)} pods on "
                    f"{self._name(swarm.corridor.origin_node_id)} → "
                    f"{self._name(swarm.corridor.divergence_node_id)}",
                    severity="success", swarm_id=sid, pod_ids=list(swarm.pod_ids),
                    size=len(swarm.pod_ids), origin=swarm.corridor.origin_node_id,
                    divergence=swarm.corridor.divergence_node_id, pods_label=pods)
                previous = SwarmStatus.FORMING.value
            if status != previous and status in (SwarmStatus.SPLITTING.value,
                                                 SwarmStatus.COMPLETED.value):
                if previous not in (SwarmStatus.SPLITTING.value, SwarmStatus.COMPLETED.value):
                    self.emit(
                        SWARM_SPLIT,
                        f"Swarm {sid} reached divergence at "
                        f"{self._name(swarm.corridor.divergence_node_id)}; members separate "
                        f"toward their own destinations",
                        severity="info", swarm_id=sid, pod_ids=list(swarm.pod_ids),
                        divergence=swarm.corridor.divergence_node_id)
            self._swarm_status[sid] = status

    def _observe_repositions(self) -> None:
        for assignment in self._sim.repositions():
            rid = assignment.reposition_id
            if rid in self._reposition_ids:
                continue
            self._reposition_ids.add(rid)
            self.emit(
                FLEET_REBALANCING,
                f"{assignment.pod_id} repositioning "
                f"{self._name(assignment.origin_node_id)} → "
                f"{self._name(assignment.target_node_id)} "
                f"({assignment.estimated_distance_km:.1f} km, empty)",
                severity="info", reposition_id=rid, pod_id=assignment.pod_id,
                source_node=assignment.origin_node_id, target_node=assignment.target_node_id,
                estimated_distance_km=round(assignment.estimated_distance_km, 3),
                target_deficit=round(assignment.target_deficit_at_dispatch, 2))

    def _observe_congestion(self) -> None:
        for edge in self._graph.edges():
            u = edge.utilization
            if edge.edge_id not in self._congested:
                if u >= CONGESTION_THRESHOLD:
                    self._congested.add(edge.edge_id)
                    self.emit(
                        CONGESTION_DETECTED,
                        f"Edge {edge.edge_id} ({self._name(edge.source)} → "
                        f"{self._name(edge.destination)}) utilization {u:.0%} crossed the "
                        f"{CONGESTION_THRESHOLD:.0%} threshold",
                        severity="warning", edge_id=edge.edge_id, utilization=round(u, 3),
                        threshold=CONGESTION_THRESHOLD,
                        travel_time_min=round(edge.current_travel_time_min, 2),
                        free_flow_min=round(edge.base_travel_time_min, 2))
            elif u < CONGESTION_CLEAR:
                self._congested.discard(edge.edge_id)

    def _free_flow_edges(self, origin: str, destination: str) -> tuple[str, ...]:
        key = (origin, destination)
        if key not in self._free_flow_cache:
            route = find_route(self._graph, origin, destination,
                               algorithm=self._algorithm, cost_model=_FREE_FLOW)
            self._free_flow_cache[key] = tuple(route.edge_ids)
        return self._free_flow_cache[key]

    def _observe_routes(self) -> None:
        for pod in self._sim.fleet.pods():
            edges = self._route_edges(pod)
            if edges == self._pod_route.get(pod.pod_id, ()):
                continue
            self._pod_route[pod.pod_id] = edges
            if not edges:
                continue
            route = pod.route
            free_flow = self._free_flow_edges(route.origin, route.destination)
            if free_flow == edges:
                continue
            # A route counts as an *adaptation* only if the free-flow route it
            # avoided really contains an edge that is congested right now.
            avoided = [eid for eid in free_flow if eid not in edges
                       and self._graph.get_edge(eid).utilization >= CONGESTION_THRESHOLD]
            if not avoided:
                continue
            self.emit(
                ROUTE_ADAPTATION,
                f"{pod.pod_id} routed {self._name(route.origin)} → "
                f"{self._name(route.destination)} around congested "
                f"{', '.join(avoided)} (free-flow path avoided)",
                severity="info", pod_id=pod.pod_id, avoided_edges=avoided,
                route_edges=list(edges), free_flow_edges=list(free_flow),
                origin=route.origin, destination=route.destination)

    def _observe_demand(self) -> None:
        cycles = len(self._sim.deficit_history())
        if cycles == self._cycle_count:
            return
        self._cycle_count = cycles
        rows = self._sim.demand_map().rows
        forecast = {r.node_id: r.forecast_demand for r in rows}
        previous = self._prev_forecast or {}
        for row in rows:
            before = previous.get(row.node_id, 0.0)
            rise = row.forecast_demand - before
            if rise >= SURGE_MIN_ABS_TRIPS and rise >= SURGE_MIN_REL * max(before, 1e-9):
                self.emit(
                    DEMAND_SURGE,
                    f"{self._name(row.node_id)} demand surge: forecast "
                    f"{before:.1f} → {row.forecast_demand:.1f} trips in the next "
                    f"{self._sim.demand_map().horizon_min:.0f} min",
                    severity="warning", node_id=row.node_id, zone=self._name(row.node_id),
                    forecast_before=round(before, 2),
                    forecast_after=round(row.forecast_demand, 2))
        self._prev_forecast = forecast
        deficit_now = {r.node_id: r.deficit for r in rows if r.deficit >= IMBALANCE_MIN_DEFICIT}
        new_nodes = sorted(set(deficit_now) - self._deficit_nodes,
                           key=lambda n: (-deficit_now[n], n))
        if new_nodes:
            top = ", ".join(f"{self._name(n)} (-{deficit_now[n]:.1f})" for n in new_nodes[:3])
            extra = f" +{len(new_nodes) - 3} more" if len(new_nodes) > 3 else ""
            self.emit(
                DEMAND_IMBALANCE,
                f"Forecast pod deficit at {top}{extra}; total deficit "
                f"{sum(deficit_now.values()):.1f} pods",
                severity="warning", nodes=new_nodes,
                deficits={n: round(deficit_now[n], 2) for n in new_nodes},
                total_deficit=round(sum(deficit_now.values()), 2))
        self._deficit_nodes = set(deficit_now)

    # ------------------------------------------------------ M6 audit record
    def from_orchestration_record(self, record: Mapping[str, Any],
                                  provider_type: str) -> list[dict[str, Any]]:
        """Events for one M6 cycle, taken straight from its audit record."""
        self._new = []
        label = provider_label(provider_type)
        cycle = record.get("cycle_id")
        proposal = record.get("proposal")
        if proposal is not None:
            params = proposal.get("parameters") or {}
            self.emit(
                GEMINI_PROPOSAL,
                f"{label} requested {proposal.get('action_type')}: "
                f"{str(proposal.get('reason', ''))[:110]} "
                f"(a request — the deterministic engine decides)",
                severity="info", source="orchestrator", provider=label, cycle_id=cycle,
                action_type=proposal.get("action_type"), parameters=dict(params),
                confidence=proposal.get("confidence"))
        else:
            self.emit(
                GEMINI_PROPOSAL,
                f"{label} produced no usable proposal "
                f"({record.get('provider_error') or record.get('parse_error_code')})",
                severity="warning", source="orchestrator", provider=label, cycle_id=cycle)
        checks = record.get("checks") or []
        passed = sum(1 for c in checks if c.get("passed"))
        approved = record.get("verdict") == "APPROVED"
        if approved:
            text = f"✓ Validator approved ({passed}/{len(checks)} checks passed)"
        else:
            text = (f"✗ Validator rejected: {record.get('reason_code')} — "
                    f"{record.get('verdict_detail')}")
        self.emit(VALIDATION, text, severity="success" if approved else "error",
                  source="orchestrator", cycle_id=cycle, verdict=record.get("verdict"),
                  reason_code=record.get("reason_code"), checks_passed=passed,
                  checks_total=len(checks))
        execution = record.get("execution")
        if execution is not None:
            status = execution.get("status")
            self.emit(
                EXECUTION, f"Engine {status}: {execution.get('detail')}",
                severity="success" if status == "EXECUTED" else (
                    "error" if status == "FAILED" else "info"),
                source="orchestrator", cycle_id=cycle, status=status)
        return list(self._new)
