"""Deterministic Demo Mode state machine.

The demo is *not* a recording and not a timer. It is a script of **inputs**
(start at a fixed state, inject traffic on a chosen edge, ask the orchestrator for
one cycle) driven over the real simulation, plus a set of **phase gates**. A phase
is only marked ``complete`` once the matching *derived* event
(see ``app.api.events``) has actually been produced from real simulation state.
If a gate is not met within its beat budget the phase is marked
``not_observed`` and the machine moves on - it never pretends.

Nothing here depends on wall-clock time, so ``reset -> run demo`` yields the same
phase order, events and final metrics every time, and pausing between beats cannot
change the outcome.
"""

from __future__ import annotations

from typing import Any

from app.api import events as ev
from app.fleet.models import TripStatus

DEMO_SCENARIO_ID = "DEMO_MORNING_RUSH"
DEMO_PODS = 40
DEMO_PASSENGERS = 600
DEMO_START_MIN = 360.0          # 06:00: the quiet minutes before the morning ramp

TICKS_PER_BEAT = 2
MAX_BEATS_PER_PHASE = 45
BASELINE_BEATS = 2              # quiet observation window before anything is injected
IMPACT_BEATS = 10               # a few more real beats so outcomes can settle
INJECT_UTILIZATION = 1.5        # scripted operator input: vehicles = 150% of capacity

PHASES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("BASELINE", "Baseline", ()),
    ("DEMAND_SURGE", "Demand Surge", (ev.DEMAND_SURGE,)),
    ("CONGESTION", "Congestion", (ev.CONGESTION_DETECTED,)),
    ("ROUTING", "Routing", (ev.ROUTE_ADAPTATION,)),
    ("SWARM", "Swarm", (ev.SWARM_FORMED,)),
    ("REBALANCING", "Rebalancing", (ev.DEMAND_IMBALANCE, ev.FLEET_REBALANCING)),
    ("GEMINI", "Gemini", (ev.GEMINI_PROPOSAL, ev.VALIDATION)),
    ("IMPACT", "Impact", ()),
)

# metric -> (label, polarity). polarity: -1 lower is better, +1 higher is better, 0 neutral
IMPACT_METRICS: tuple[tuple[str, str, int], ...] = (
    ("trips_served", "Trips served", 1),
    ("unserved_trips", "Unserved trips", -1),
    ("average_wait_min", "Average wait (min)", -1),
    ("average_completion_time_min", "Average completion time (min)", -1),
    ("final_total_deficit", "Forecast pod deficit", -1),
    ("deadhead_distance_km", "Deadhead distance (km)", -1),
    ("pod_distance_km", "Total pod distance (km)", -1),
    ("total_energy_kwh", "Energy used (kWh)", -1),
    ("road_occupancy_equiv_km", "Road occupancy (equiv. km)", -1),
    ("road_occupancy_saved_equiv_km", "Road occupancy saved (equiv. km)", 1),
    ("completed_repositions", "Completed repositions", 0),
    ("total_swarms_formed", "Swarms formed", 0),
)


class DemoRunner:
    """Drives one demo over a :class:`SimulationSession` (duck-typed)."""

    def __init__(self, session: Any) -> None:
        self._s = session
        self.status = "idle"            # idle | running | paused | finished
        self.beat = 0
        self.ticks = 0
        self._index = 0                 # index into PHASES of the current phase
        self._entered = False
        self._beats_in_phase = 0
        self._phase_status = {pid: "pending" for pid, _, _ in PHASES}
        self._evidence: dict[str, str | None] = {pid: None for pid, _, _ in PHASES}
        self._seen: dict[str, str] = {}          # event kind -> first event id
        self._baseline: dict[str, Any] | None = None
        self.impact: dict[str, Any] | None = None
        self.injected_edge: str | None = None

    # ------------------------------------------------------------ lifecycle
    def begin(self) -> list[dict[str, Any]]:
        self._baseline = dict(self._s.metrics())
        self.status = "running"
        self._phase_status["BASELINE"] = "active"
        self._entered = True
        e = self._s.deriver.emit(
            ev.DEMO_INPUT,
            f"DEMO MODE started from the deterministic initial state at minute "
            f"{self._s.sim.time_min:.0f} ({DEMO_PODS} pods, {DEMO_PASSENGERS} passengers)",
            severity="info", source="demo", scenario_id=DEMO_SCENARIO_ID)
        return [e]

    def pause(self) -> None:
        if self.status == "running":
            self.status = "paused"

    def resume(self) -> None:
        if self.status == "paused":
            self.status = "running"

    # ---------------------------------------------------------------- beats
    def advance(self) -> list[dict[str, Any]]:
        """Run one beat. No-op unless ``running``. Returns the new events."""
        if self.status != "running":
            return []
        new: list[dict[str, Any]] = []
        pid = PHASES[self._index][0]
        if not self._entered:
            new += self._enter(pid)
        for _ in range(TICKS_PER_BEAT):
            if not self._s.sim.has_pending_work:
                break
            self._s.sim.tick()
            self.ticks += 1
            new += self._s.deriver.observe()
        self.beat += 1
        self._beats_in_phase += 1
        self._record(new)
        new += self._evaluate()
        return new

    def _record(self, events: list[dict[str, Any]]) -> None:
        for e in events:
            self._seen.setdefault(e["kind"], e["id"])

    def _enter(self, pid: str) -> list[dict[str, Any]]:
        self._entered = True
        self._phase_status[pid] = "active"
        new: list[dict[str, Any]] = []
        if pid == "CONGESTION":
            new += self._inject_congestion()
        elif pid == "GEMINI":
            new += self._run_gemini_cycle()
        self._record(new)
        return new

    def _evaluate(self) -> list[dict[str, Any]]:
        """Complete the current phase if its gate is met; cascade through satisfied ones."""
        new: list[dict[str, Any]] = []
        while self._index < len(PHASES):
            pid, label, kinds = PHASES[self._index]
            if pid == "BASELINE":
                met = self._beats_in_phase >= BASELINE_BEATS
            elif pid == "IMPACT":
                met = self._beats_in_phase >= IMPACT_BEATS
            else:
                met = all(k in self._seen for k in kinds)
            if met:
                self._phase_status[pid] = "complete"
                self._evidence[pid] = (self._seen.get(kinds[-1]) if kinds else None)
                if pid == "IMPACT":
                    self.impact = self._impact_summary()
                    self.status = "finished"
                    self._index += 1
                    break
            elif self._beats_in_phase >= MAX_BEATS_PER_PHASE or not self._s.sim.has_pending_work:
                self._phase_status[pid] = "not_observed"
                if pid == "IMPACT":
                    self.impact = self._impact_summary()
                    self.status = "finished"
                    self._index += 1
                    break
            else:
                break
            self._index += 1
            self._beats_in_phase = 0
            self._entered = False
            nxt = PHASES[self._index][0]
            self._phase_status[nxt] = "active"
            # Run the next phase's entry action immediately so its input is visible
            # in the same beat (and so a gate that is already met can cascade).
            new += self._enter(nxt)
            new += self._s.deriver.observe()
            self._record(new)
        return new

    # ------------------------------------------------- scripted inputs
    def _inject_congestion(self) -> list[dict[str, Any]]:
        """Scripted operator input: load one edge. Chosen from real pending trips."""
        sim, graph = self._s.sim, self._s.graph
        counts: dict[str, int] = {}
        upcoming = sorted(
            (r for r in sim.records_with_status(TripStatus.PENDING)
             if r.request_time_min <= sim.time_min + 45.0),
            key=lambda r: (r.request_time_min, r.trip_id))[:80]
        for r in upcoming:
            for eid in self._s.deriver._free_flow_edges(r.origin_node_id, r.destination_node_id):
                counts[eid] = counts.get(eid, 0) + 1
        if counts:
            edge_id = sorted(counts, key=lambda e: (-counts[e], e))[0]
        else:  # pragma: no cover - fallback if nothing is queued
            edge_id = sorted(graph.edges(), key=lambda e: (-e.utilization, e.edge_id))[0].edge_id
        edge = graph.get_edge(edge_id)
        count = int(round(edge.capacity_vehicles_per_hour * INJECT_UTILIZATION))
        edge.set_vehicle_count(count)
        self.injected_edge = edge_id
        inject = self._s.deriver.emit(
            ev.DEMO_INPUT,
            f"Scripted input: loaded edge {edge_id} to {INJECT_UTILIZATION:.0%} of capacity "
            f"(it lies on {counts.get(edge_id, 0)} upcoming free-flow trip routes)",
            severity="info", source="demo", edge_id=edge_id,
            vehicle_count=count, upcoming_routes_on_edge=counts.get(edge_id, 0))
        return [inject] + self._s.deriver.observe()

    def _run_gemini_cycle(self) -> list[dict[str, Any]]:
        _, events = self._s.run_orchestration_cycle(advance_min=None)
        return events

    # -------------------------------------------------------------- output
    def _impact_summary(self) -> dict[str, Any]:
        assert self._baseline is not None
        final = self._s.metrics()
        rows = []
        for key, label, polarity in IMPACT_METRICS:
            start, end = self._baseline.get(key), final.get(key)
            delta = None if start is None or end is None else round(end - start, 4)
            if delta is None:
                verdict = "n/a"
            elif delta == 0 or polarity == 0:
                verdict = "unchanged" if delta == 0 else "neutral"
            else:
                verdict = "improved" if delta * polarity > 0 else "worsened"
            rows.append({"metric": key, "label": label, "start": start, "end": end,
                         "delta": delta, "verdict": verdict})
        kinds: dict[str, int] = {}
        for e in self._s.deriver.events:
            kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
        return {
            "window": {"start_min": DEMO_START_MIN,
                       "end_min": round(self._s.sim.time_min, 2)},
            "basis": ("Start vs end of the demo window on the same deterministic "
                      "simulation. This is NOT a counterfactual and NOT a road-space "
                      "reduction claim; worsened metrics are shown as-is."),
            "metrics": rows,
            "event_counts": dict(sorted(kinds.items())),
            "injected_edge": self.injected_edge,
        }

    def to_dict(self) -> dict[str, Any]:
        cur = PHASES[self._index] if self._index < len(PHASES) else None
        return {
            "active": self.status in ("running", "paused", "finished"),
            "label": "DEMO MODE",
            "status": self.status,
            "scenario_id": DEMO_SCENARIO_ID,
            "beat": self.beat,
            "ticks": self.ticks,
            "current_phase": cur[0] if cur and self.status != "finished" else None,
            "current_phase_label": cur[1] if cur and self.status != "finished" else "Complete",
            "phases": [
                {"id": pid, "label": label, "status": self._phase_status[pid],
                 "evidence_event_id": self._evidence[pid]}
                for pid, label, _ in PHASES
            ],
            "provider": ev.provider_label(self._s.provider_type),
            "impact": self.impact,
        }
