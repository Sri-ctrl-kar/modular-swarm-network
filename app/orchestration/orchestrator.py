"""The bounded closed loop: observe, propose, validate, execute, record.

*** There is no autonomous loop here. ``run_cycle`` runs exactly one cycle and
    returns; ``run_cycles(n)`` runs n of them and stops. The caller decides when the
    orchestrator gets to think, and ``max_cycles`` caps even that. ***

ONE CYCLE
---------
    1. build the observation            (deterministic, read-only, plain values)
    2. ask the provider for a proposal  (the only external call; one attempt)
    3. parse it against the schema      (data only; never executed)
    4. validate it against the engine   (deterministic; stale proposals rejected)
    5. execute it, if approved          (through existing M5/M3 entry points)
    6. optionally advance the engine    (the caller's choice, not the AI's)
    7. record everything

Every stage can end the cycle: a provider failure, an unparseable response and a
rejected proposal each produce a complete audit record and no execution at all. The
engine is never left in a state that depends on the AI having behaved.

WHY STEP 6 EXISTS
-----------------
Dispatching a repositioning move changes nothing this instant — the pod departs on
the next tick and arrives minutes later. So measuring "what the action did" needs the
engine to be advanced. ``advance_min`` does that, and it is the *caller's* decision,
recorded as such: the AI cannot advance the clock, and an action's metrics delta
covers whatever window the caller chose. With ``advance_min=None`` the delta covers
the instant of execution only, which for a dispatch is usually all zeros — reported
as it falls rather than dressed up.

The delta is **a window, not an attribution.** During those minutes the engine also
runs its own rebalancing cycles, serves whatever trips arrive and charges whatever
pods need it. Almost none of what changes is the AI's doing, the detail string says
so, and nothing in this package claims otherwise.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from app.errors import ProviderError
from app.orchestration.actions import AIActionType, ParsedAction, parse_action
from app.orchestration.audit import AuditLog, OrchestrationRecord
from app.orchestration.config import DEFAULT_ORCHESTRATION_CONFIG, OrchestrationConfig
from app.orchestration.execution import ActionExecutor, ExecutionResult, ExecutionStatus
from app.orchestration.metrics import compute_orchestration_metrics
from app.orchestration.observation import OrchestrationObservation, build_observation
from app.orchestration.validator import (
    OrchestrationValidator,
    ValidationVerdict,
    Verdict,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CycleResult:
    """One cycle, as returned to the caller. The audit record is the durable copy."""

    observation: OrchestrationObservation
    parsed: ParsedAction | None
    verdict: ValidationVerdict | None
    execution: ExecutionResult | None
    record: OrchestrationRecord
    provider_error: str | None = None

    @property
    def approved(self) -> bool:
        return self.verdict is not None and self.verdict.approved

    @property
    def executed(self) -> bool:
        return self.execution is not None and self.execution.executed

    def to_dict(self) -> dict[str, Any]:
        return self.record.to_dict()


class Orchestrator:
    """Runs bounded orchestration cycles over a deterministic simulation.

    The provider is handed an observation and nothing else: no graph, no fleet, no
    pod, no simulation, no callable into the engine. Everything it says comes back
    as data and is checked before anything happens.
    """

    def __init__(self, provider, *, simulation=None,
                 config: OrchestrationConfig = DEFAULT_ORCHESTRATION_CONFIG,
                 validator: OrchestrationValidator | None = None,
                 executor: ActionExecutor | None = None,
                 audit_log: AuditLog | None = None) -> None:
        self._provider = provider
        self._simulation = simulation
        self._config = config
        self._validator = validator or OrchestrationValidator(config)
        self._executor = executor or ActionExecutor(config)
        self._audit = audit_log or AuditLog()

    # --- read-only accessors -----------------------------------------------------
    @property
    def config(self) -> OrchestrationConfig:
        return self._config

    @property
    def provider_name(self) -> str:
        return getattr(self._provider, "name", type(self._provider).__name__)

    @property
    def audit_log(self) -> AuditLog:
        return self._audit

    @property
    def validator(self) -> OrchestrationValidator:
        return self._validator

    @property
    def executor(self) -> ActionExecutor:
        return self._executor

    @property
    def simulation(self):
        """The engine this orchestrator acts on. Never handed to a provider."""
        return self._simulation

    def observe(self, *, simulation=None) -> OrchestrationObservation:
        target = simulation if simulation is not None else self._simulation
        if target is None:
            raise ValueError("no simulation to observe")
        return build_observation(target, self._config)

    def metrics(self):
        return compute_orchestration_metrics(self._audit)

    # --- the loop ----------------------------------------------------------------
    def run_cycle(self, *, simulation=None, advance_min: float | None = None) -> CycleResult:
        """One cycle. Returns when it is done; starts nothing in the background."""
        target = simulation if simulation is not None else self._simulation
        observation = self.observe(simulation=target)
        cycle_id = self._audit.next_cycle_id()
        model = self._describe_model()

        # --- 2. the provider ------------------------------------------------------
        started = time.perf_counter()
        try:
            response = self._provider.propose_action(observation)
        except ProviderError as exc:
            latency = round((time.perf_counter() - started) * 1000.0, 3)
            logger.warning("%s: provider failed: %s", cycle_id, exc)
            record = self._audit.append(OrchestrationRecord(
                cycle_id=cycle_id, provider=self.provider_name, model=model,
                observation_fingerprint=observation.fingerprint(),
                observed_problem=observation.headline(),
                provider_error=f"{type(exc).__name__}: {exc}",
                verdict=Verdict.REJECTED.value, reason_code="PROVIDER_FAILED",
                verdict_detail="no proposal was produced, so nothing was validated",
                latency_ms=latency))
            return CycleResult(observation, None, None, None, record,
                               provider_error=str(exc))
        latency = response.latency_ms
        if latency is None:
            latency = round((time.perf_counter() - started) * 1000.0, 3)

        # --- 3. parse -------------------------------------------------------------
        payload = response.payload if response.payload is not None else response.raw_text
        parsed = parse_action(payload, raw_text=response.raw_text, config=self._config)
        if not parsed.is_valid:
            logger.warning("%s: unusable provider output: %s", cycle_id, parsed.error_detail)
            record = self._audit.append(OrchestrationRecord(
                cycle_id=cycle_id, provider=self.provider_name, model=response.model or model,
                observation_fingerprint=observation.fingerprint(),
                observed_problem=observation.headline(),
                parse_error_code=parsed.error_code, parse_error_detail=parsed.error_detail,
                raw_response=parsed.raw_text, verdict=Verdict.REJECTED.value,
                reason_code=parsed.error_code,
                verdict_detail="the response did not fit the action schema, so no action "
                               "was formed and nothing was executed",
                latency_ms=latency))
            return CycleResult(observation, parsed, None, None, record)

        # --- 4. validate ----------------------------------------------------------
        verdict = self._validator.validate(parsed.action, simulation=target,
                                           observation=observation)
        if not verdict.approved:
            logger.info("%s: %s rejected (%s)", cycle_id,
                        parsed.action.action_type.value, verdict.reason_code)
            record = self._audit.append(OrchestrationRecord(
                cycle_id=cycle_id, provider=self.provider_name, model=response.model or model,
                observation_fingerprint=observation.fingerprint(),
                observed_problem=observation.headline(),
                proposal=parsed.action.to_dict(), raw_response=parsed.raw_text,
                verdict=verdict.verdict.value, reason_code=verdict.reason_code,
                verdict_detail=verdict.detail, checks=verdict.checks,
                latency_ms=latency))
            return CycleResult(observation, parsed, verdict, None, record)

        # --- 5. execute -----------------------------------------------------------
        execution = self._executor.execute(verdict, simulation=target)

        # --- 6. advance, if the caller asked ---------------------------------------
        execution = self._advance_and_remeasure(execution, target, advance_min)

        # --- 7. record ------------------------------------------------------------
        record = self._audit.append(OrchestrationRecord(
            cycle_id=cycle_id, provider=self.provider_name, model=response.model or model,
            observation_fingerprint=observation.fingerprint(),
            observed_problem=observation.headline(),
            proposal=parsed.action.to_dict(), raw_response=parsed.raw_text,
            verdict=verdict.verdict.value, reason_code=verdict.reason_code,
            verdict_detail=verdict.detail, checks=verdict.checks,
            execution=execution.to_dict(),
            simulation_fingerprint_before=execution.fingerprint_before,
            simulation_fingerprint_after=execution.fingerprint_after,
            metrics_delta=execution.metrics_delta(), latency_ms=latency))
        return CycleResult(observation, parsed, verdict, execution, record)

    def run_cycles(self, count: int, *, simulation=None,
                   advance_min: float | None = None) -> tuple[CycleResult, ...]:
        """Run ``count`` cycles, one after another, and stop. Never more than ``max_cycles``."""
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ValueError(f"count must be a positive integer, got {count!r}")
        if count > self._config.max_cycles:
            raise ValueError(
                f"count={count} exceeds max_cycles={self._config.max_cycles}; the loop is "
                f"bounded on purpose")
        return tuple(self.run_cycle(simulation=simulation, advance_min=advance_min)
                     for _ in range(count))

    # --- helpers -----------------------------------------------------------------
    def _describe_model(self) -> str | None:
        describe = getattr(self._provider, "describe", None)
        if describe is None:
            return None
        try:
            return describe().get("model")
        except Exception:  # a provider's own description must never break a cycle
            return None

    def _advance_and_remeasure(self, execution: ExecutionResult, simulation,
                               advance_min: float | None) -> ExecutionResult:
        """Let the engine run on, so a dispatched move has time to become a result."""
        if advance_min is None or simulation is None or \
                execution.status is not ExecutionStatus.EXECUTED or \
                execution.action_type is not AIActionType.REQUEST_REBALANCING:
            return execution
        from dataclasses import replace

        from app.orchestration.execution import tracked_metrics

        simulation.run(until_min=simulation.time_min + float(advance_min))
        detail = (f"{execution.detail} The caller then advanced the engine "
                  f"{float(advance_min):g} minutes, so the metrics delta below covers "
                  f"that window — the whole window, including the engine's own "
                  f"rebalancing cycles and ordinary service. It is not an attribution "
                  f"of the delta to this action.")
        return replace(execution, detail=detail, metrics_after=tracked_metrics(simulation),
                       fingerprint_after=simulation.snapshot().fingerprint())
