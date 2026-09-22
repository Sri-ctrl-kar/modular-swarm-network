"""Milestone 6: AI orchestration and validated AI control.

*** Gemini is an orchestrator, not the simulation engine. ***

    AI PROPOSES -> VALIDATOR CHECKS -> SIMULATION EXECUTES -> RESULTS RETURN
                -> AI INTERPRETS

This package sits **above** everything. It contains no routing, no congestion model,
no demand model, no battery model, no rebalancing heuristic and no simulation metric:
every number it reports *about the city* comes from M1-M5, which remain the
deterministic source of truth and were left unchanged by this milestone. The only
figures M6 computes itself are counts of its own decision process — proposals
generated, accepted, rejected, stale, malformed — and they are kept strictly apart
from the engine's outcomes (see ``metrics.py``).

What M6 adds is a way to let something outside the process — a language model, or a
fixed rule standing in for one — *suggest* what the engine should do next, without
ever being able to do it:

* ``observation.py`` — the only thing the AI sees: frozen plain values, no live
  objects, a stable SHA-256 fingerprint.
* ``actions.py`` — a closed four-member whitelist and a strict schema. AI output is
  parsed as data and never executed, imported or shelled out.
* ``validator.py`` — a deterministic check against the live engine, including
  ``REJECT_STALE_OBSERVATION`` so a decision made about one city state cannot be
  applied to another.
* ``execution.py`` — approved actions mapped onto existing M5/M3 entry points. No
  second implementation of anything.
* ``providers/`` — a mock provider (no key, no network, deterministic) and an
  optional Gemini provider whose SDK is imported lazily, so the runtime stays
  standard-library-only unless live mode is explicitly requested.
* ``audit.py`` — what was seen, proposed, decided and actually done, per cycle.
* ``api.py`` — the pure-Python boundary a future UI will sit on. No HTTP here.

The simulation remains fully usable with no provider at all: if Gemini is missing,
misconfigured, slow or wrong, the engine is unaffected and the mock path still works.
"""

from __future__ import annotations

from app.orchestration.actions import (
    ACTION_WHITELIST,
    AI_OUTPUT_INVALID,
    AIAction,
    AIActionType,
    ParsedAction,
    no_action,
    parse_action,
)
from app.orchestration.api import OrchestrationAPI
from app.orchestration.audit import AuditLog, OrchestrationRecord, cycle_id_for
from app.orchestration.config import (
    DEFAULT_ORCHESTRATION_CONFIG,
    NO_ACCURACY_NOTE,
    ORCHESTRATION_PROVENANCE,
    OrchestrationConfig,
)
from app.orchestration.execution import (
    ActionExecutor,
    ExecutionResult,
    ExecutionStatus,
)
from app.orchestration.metrics import (
    OrchestrationMetrics,
    compute_orchestration_metrics,
)
from app.orchestration.observation import (
    OrchestrationObservation,
    build_observation,
    canonical_json,
    fingerprint_of,
)
from app.orchestration.orchestrator import CycleResult, Orchestrator
from app.orchestration.prompt import RESPONSE_SCHEMA, SYSTEM_PROMPT, build_user_payload
from app.orchestration.providers import (
    AIProvider,
    GeminiProvider,
    MockProvider,
    ProviderResponse,
    ScriptedProvider,
    api_key_status,
)
from app.orchestration.scenarios import (
    EVALUATION_SCENARIOS,
    EvaluationScenario,
    ScenarioResult,
    ScenarioSpec,
    build_simulation,
    evaluation_scenario,
    run_scenario,
)
from app.orchestration.validator import (
    ALLOWED_PARAMETERS,
    REJECT_STALE_OBSERVATION,
    OrchestrationValidator,
    ValidationVerdict,
    Verdict,
)

__all__ = [
    "ACTION_WHITELIST", "AIAction", "AIActionType", "AIProvider", "AI_OUTPUT_INVALID",
    "ALLOWED_PARAMETERS", "ActionExecutor", "AuditLog", "CycleResult",
    "DEFAULT_ORCHESTRATION_CONFIG", "EVALUATION_SCENARIOS", "EvaluationScenario",
    "ExecutionResult", "ExecutionStatus", "GeminiProvider", "MockProvider",
    "NO_ACCURACY_NOTE", "ORCHESTRATION_PROVENANCE", "OrchestrationAPI",
    "OrchestrationConfig", "OrchestrationMetrics", "OrchestrationObservation",
    "OrchestrationRecord", "OrchestrationValidator", "Orchestrator", "ParsedAction",
    "ProviderResponse", "REJECT_STALE_OBSERVATION", "RESPONSE_SCHEMA", "SYSTEM_PROMPT",
    "ScenarioResult", "ScenarioSpec", "ScriptedProvider", "ValidationVerdict", "Verdict",
    "api_key_status", "build_observation", "build_simulation", "build_user_payload",
    "canonical_json", "compute_orchestration_metrics", "cycle_id_for",
    "evaluation_scenario", "fingerprint_of", "no_action", "parse_action", "run_scenario",
]
