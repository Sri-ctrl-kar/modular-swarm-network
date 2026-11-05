"""The pure-Python API boundary a future UI will sit on.

*** No HTTP, no framework, no server. Six methods, plain dicts in and out. ***

A front end — whatever it eventually is — needs exactly these operations:

    get_observation()   what the city looks like now
    propose_action()    ask the provider for a proposal (the only external call)
    validate_action()   have the deterministic validator rule on one
    execute_action()    run an approved action through the engine
    get_result()        the last cycle's outcome
    get_audit_log()     the whole trail

Each returns JSON-serialisable plain values, so an HTTP adapter over this would be a
thin translation layer with no logic of its own — which is the point of putting the
boundary here rather than in a web framework. A caller of this class never receives
a graph, a fleet, a pod or a simulation, and ``validate_action`` accepts a plain
dict, so a UI can post one straight from a form without constructing engine types.

The same rules apply as everywhere else in M6: a proposal supplied by a caller is
untrusted, is parsed against the schema, and is validated against the live engine
before anything runs.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.orchestration.actions import parse_action
from app.orchestration.audit import AuditLog
from app.orchestration.config import DEFAULT_ORCHESTRATION_CONFIG, OrchestrationConfig
from app.orchestration.metrics import compute_orchestration_metrics
from app.orchestration.orchestrator import CycleResult, Orchestrator
from app.orchestration.validator import Verdict


class OrchestrationAPI:
    """A thin, stable façade over the orchestration layer. Holds no state but the log."""

    def __init__(self, provider, simulation, *,
                 config: OrchestrationConfig = DEFAULT_ORCHESTRATION_CONFIG,
                 orchestrator: Orchestrator | None = None) -> None:
        self._orchestrator = orchestrator or Orchestrator(
            provider, simulation=simulation, config=config)
        self._last: CycleResult | None = None

    @property
    def orchestrator(self) -> Orchestrator:
        return self._orchestrator

    # --- GET OBSERVATION ---------------------------------------------------------
    def get_observation(self) -> dict[str, Any]:
        observation = self._orchestrator.observe()
        data = observation.to_dict()
        data["fingerprint"] = observation.fingerprint()
        data["headline"] = observation.headline()
        return data

    # --- PROPOSE ACTION ----------------------------------------------------------
    def propose_action(self, *, advance_min: float | None = None) -> dict[str, Any]:
        """Run one full cycle and return its audit record.

        A UI button maps to exactly this: one observation, one provider call, one
        validated decision, one recorded outcome.
        """
        self._last = self._orchestrator.run_cycle(advance_min=advance_min)
        return self._last.record.to_dict()

    # --- VALIDATE ACTION ---------------------------------------------------------
    def validate_action(self, action: Mapping[str, Any] | Any) -> dict[str, Any]:
        """Rule on a proposal supplied by the caller. Executes nothing, ever."""
        parsed = parse_action(action, config=self._orchestrator.config)
        if not parsed.is_valid:
            return {"verdict": Verdict.REJECTED.value, "reason_code": parsed.error_code,
                    "detail": parsed.error_detail, "checks": [],
                    "observed_fingerprint": None}
        verdict = self._orchestrator.validator.validate(
            parsed.action, simulation=self._orchestrator.simulation)
        return verdict.to_dict()

    # --- EXECUTE ACTION ----------------------------------------------------------
    def execute_action(self, action: Mapping[str, Any] | Any) -> dict[str, Any]:
        """Validate a caller-supplied proposal and, only if approved, run it."""
        parsed = parse_action(action, config=self._orchestrator.config)
        if not parsed.is_valid:
            return {"status": "REJECTED", "reason_code": parsed.error_code,
                    "detail": parsed.error_detail, "execution": None}
        verdict = self._orchestrator.validator.validate(
            parsed.action, simulation=self._orchestrator.simulation)
        if not verdict.approved:
            return {"status": "REJECTED", "reason_code": verdict.reason_code,
                    "detail": verdict.detail, "execution": None}
        execution = self._orchestrator.executor.execute(
            verdict, simulation=self._orchestrator.simulation)
        return {"status": execution.status.value, "reason_code": None,
                "detail": execution.detail, "execution": execution.to_dict()}

    # --- GET RESULT --------------------------------------------------------------
    def get_result(self) -> dict[str, Any] | None:
        """The last cycle's record, or ``None`` if no cycle has run."""
        return self._last.record.to_dict() if self._last is not None else None

    # --- GET AUDIT LOG -----------------------------------------------------------
    def get_audit_log(self) -> dict[str, Any]:
        log: AuditLog = self._orchestrator.audit_log
        data = log.to_dict()
        data["fingerprint"] = log.fingerprint()
        data["metrics"] = compute_orchestration_metrics(log).to_dict()
        return data
