"""Assumptions of the AI orchestration layer (M6).

*** Gemini is an orchestrator, not the simulation engine. ***

Every bound the orchestration layer enforces lives here, as in every layer below.
There are no magic numbers in the validator, the providers or the executor.

WHY EVERY NUMBER HERE IS A LIMIT, NOT A TUNING KNOB
---------------------------------------------------
The engine below is deterministic and already validated. The only new risk M6
introduces is an *untrusted* proposal arriving from outside the process. So every
value in this file answers one question: **what is the worst thing an approved
proposal could do, and where does it stop?**

``max_pod_count``              a proposal cannot ask for the whole fleet at once.
``max_target_nodes``           a proposal cannot name an unbounded node list.
``max_horizon_min``            a proposal cannot ask for an unbounded simulation.
``max_simulation_pods``        a proposal cannot ask for an unbounded fleet.
``max_simulation_passengers``  a proposal cannot ask for unbounded demand.
``max_scenarios_per_comparison``  a comparison cannot fan out without limit.
``max_actions_per_cycle``      one cycle executes at most this many actions.
``max_cycles``                 a caller cannot start an unbounded orchestration run.
``provider_max_attempts``      there are no automatic infinite retries.

NO SECRETS LIVE HERE
--------------------
``api_key_env_var`` is the *name* of an environment variable. The key itself is
never stored in this config, never written to an audit record, never logged and
never placed in a prompt.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from app.errors import OrchestrationConfigError

ORCHESTRATION_SCHEMA_VERSION = 1

ORCHESTRATION_PROVENANCE = (
    "SYNTHETIC — the orchestration bounds, the mock provider's rules and the prompt are "
    "invented project assumptions. Gemini (or the mock) only *proposes*: every action is "
    "checked by a deterministic validator and executed by the deterministic M1-M5 engine, "
    "which alone decides what actually happens. No AI output is ever executed as code."
)

#: Why there is no "AI accuracy" figure anywhere in this layer. Quoted in metrics.
NO_ACCURACY_NOTE = (
    "There is deliberately no 'AI accuracy' metric. Accuracy would need a ground-truth "
    "best action, and none exists: the rebalancing heuristic itself is not claimed to be "
    "optimal. What is measured instead is process — how many proposals were generated, "
    "accepted, rejected, stale or malformed — and, separately, the deterministic outcome "
    "the engine produced. Comparing decisions means comparing those engine outcomes."
)


def _check_number(value: Any, label: str, *, minimum: float | None = None,
                  maximum: float | None = None, allow_equal_min: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise OrchestrationConfigError(f"{label} must be a finite number, got {value!r}")
    if minimum is not None:
        if allow_equal_min and value < minimum:
            raise OrchestrationConfigError(f"{label} must be >= {minimum}, got {value!r}")
        if not allow_equal_min and value <= minimum:
            raise OrchestrationConfigError(f"{label} must be > {minimum}, got {value!r}")
    if maximum is not None and value > maximum:
        raise OrchestrationConfigError(f"{label} must be <= {maximum}, got {value!r}")
    return float(value)


def _check_int(value: Any, label: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise OrchestrationConfigError(f"{label} must be an integer, got {value!r}")
    if value < minimum:
        raise OrchestrationConfigError(f"{label} must be >= {minimum}, got {value!r}")
    return value


def _check_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OrchestrationConfigError(f"{label} must be a non-empty string, got {value!r}")
    return value


@dataclass(frozen=True)
class OrchestrationConfig:
    """Every bound an approved AI proposal is held to."""

    # --- the bounded loop -------------------------------------------------------
    max_actions_per_cycle: int = 1
    max_cycles: int = 20

    # --- REQUEST_REBALANCING ----------------------------------------------------
    max_target_nodes: int = 10
    min_pod_count: int = 1
    max_pod_count: int = 25

    # --- RUN_SIMULATION ---------------------------------------------------------
    min_horizon_min: float = 1.0
    max_horizon_min: float = 1440.0
    max_simulation_pods: int = 500
    max_simulation_passengers: int = 5000

    # --- COMPARE_SCENARIOS ------------------------------------------------------
    min_scenarios_per_comparison: int = 2
    max_scenarios_per_comparison: int = 4

    # --- untrusted text ---------------------------------------------------------
    # Free-form AI text is stored and printed, never executed. It is capped and
    # stripped of control characters so a provider cannot flood a log or smuggle
    # terminal escapes through a reason string.
    max_reason_chars: int = 600
    max_expected_effect_chars: int = 600
    max_raw_response_chars: int = 4000

    # --- observation ------------------------------------------------------------
    top_nodes: int = 5
    top_edges: int = 5
    top_swarms: int = 5

    # --- staleness --------------------------------------------------------------
    #: An approved action is tied to the state it was reasoned from. NO_ACTION is
    #: exempt because doing nothing is safe in any state.
    require_fresh_observation: bool = True

    # --- providers --------------------------------------------------------------
    api_key_env_var: str = "GEMINI_API_KEY"
    model_env_var: str = "GEMINI_MODEL"
    default_gemini_model: str = "gemini-2.5-flash"
    provider_timeout_s: float = 30.0
    #: 1 means "call once, never retry". Deliberately not a retry loop.
    provider_max_attempts: int = 1

    # --- the mock provider's fixed rules ---------------------------------------
    # These make the mock's behaviour reproducible and reviewable. They are a stand-in
    # for a model's judgement, not a prediction of what Gemini would do.
    mock_min_deficit_to_propose: float = 1.0
    mock_max_deadhead_share_percent: float = 40.0

    data_provenance: str = ORCHESTRATION_PROVENANCE

    def __post_init__(self) -> None:
        for name, minimum in (("max_actions_per_cycle", 1), ("max_cycles", 1),
                              ("max_target_nodes", 1), ("min_pod_count", 1),
                              ("max_pod_count", 1), ("max_simulation_pods", 1),
                              ("max_simulation_passengers", 1),
                              ("min_scenarios_per_comparison", 2),
                              ("max_scenarios_per_comparison", 2),
                              ("max_reason_chars", 1), ("max_expected_effect_chars", 1),
                              ("max_raw_response_chars", 1), ("top_nodes", 1),
                              ("top_edges", 1), ("top_swarms", 1),
                              ("provider_max_attempts", 1)):
            object.__setattr__(self, name, _check_int(getattr(self, name), name, minimum=minimum))
        for name in ("min_horizon_min", "max_horizon_min", "provider_timeout_s"):
            object.__setattr__(self, name, _check_number(getattr(self, name), name,
                                                         minimum=0.0, allow_equal_min=False))
        for name in ("mock_min_deficit_to_propose", "mock_max_deadhead_share_percent"):
            object.__setattr__(self, name, _check_number(getattr(self, name), name, minimum=0.0))
        for name in ("api_key_env_var", "model_env_var", "default_gemini_model"):
            object.__setattr__(self, name, _check_text(getattr(self, name), name))
        if not isinstance(self.require_fresh_observation, bool):
            raise OrchestrationConfigError(
                f"require_fresh_observation must be a bool, got {self.require_fresh_observation!r}")
        if self.min_pod_count > self.max_pod_count:
            raise OrchestrationConfigError(
                f"min_pod_count ({self.min_pod_count}) must be <= max_pod_count "
                f"({self.max_pod_count})")
        if self.min_horizon_min > self.max_horizon_min:
            raise OrchestrationConfigError(
                f"min_horizon_min ({self.min_horizon_min}) must be <= max_horizon_min "
                f"({self.max_horizon_min})")
        if self.min_scenarios_per_comparison > self.max_scenarios_per_comparison:
            raise OrchestrationConfigError(
                f"min_scenarios_per_comparison ({self.min_scenarios_per_comparison}) must be "
                f"<= max_scenarios_per_comparison ({self.max_scenarios_per_comparison})")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ORCHESTRATION_SCHEMA_VERSION,
            "data_provenance": self.data_provenance,
            "loop": {
                "max_actions_per_cycle": self.max_actions_per_cycle,
                "max_cycles": self.max_cycles,
                "note": "The caller drives the loop. Nothing here runs autonomously.",
            },
            "action_bounds": {
                "max_target_nodes": self.max_target_nodes,
                "min_pod_count": self.min_pod_count,
                "max_pod_count": self.max_pod_count,
                "min_horizon_min": self.min_horizon_min,
                "max_horizon_min": self.max_horizon_min,
                "max_simulation_pods": self.max_simulation_pods,
                "max_simulation_passengers": self.max_simulation_passengers,
                "min_scenarios_per_comparison": self.min_scenarios_per_comparison,
                "max_scenarios_per_comparison": self.max_scenarios_per_comparison,
            },
            "staleness": {
                "require_fresh_observation": self.require_fresh_observation,
                "note": "An action carries the fingerprint of the observation it was "
                        "reasoned from; the validator rejects it against any other state.",
            },
            "providers": {
                "api_key_env_var": self.api_key_env_var,
                "model_env_var": self.model_env_var,
                "default_gemini_model": self.default_gemini_model,
                "provider_timeout_s": self.provider_timeout_s,
                "provider_max_attempts": self.provider_max_attempts,
                "note": "Only the variable NAMES appear here. No key is stored, logged "
                        "or placed in a prompt.",
            },
            "mock_provider": {
                "mock_min_deficit_to_propose": self.mock_min_deficit_to_propose,
                "mock_max_deadhead_share_percent": self.mock_max_deadhead_share_percent,
                "note": "Fixed arithmetic rules, not a model. They stand in for an LLM in "
                        "tests and say nothing about what Gemini would choose.",
            },
            "metrics_note": NO_ACCURACY_NOTE,
        }


DEFAULT_ORCHESTRATION_CONFIG = OrchestrationConfig()
