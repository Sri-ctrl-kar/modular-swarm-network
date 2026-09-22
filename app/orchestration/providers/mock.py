"""Providers that need no network: a deterministic stand-in, and a scripted one.

*** ``MockProvider`` is a fixed arithmetic rule, not a model. It exists so the whole
    orchestration path can be tested without an API key, and it is not a prediction
    of what Gemini would choose. ***

THE MOCK'S RULES, IN ORDER
--------------------------
1. Forecast deficit below ``mock_min_deficit_to_propose``  -> ``NO_ACTION``
2. No eligible pod can move                      -> ``NO_ACTION``
3. Deadhead already above ``mock_max_deadhead_share_percent`` of all driving
                                                 -> ``NO_ACTION``
4. Otherwise                                     -> ``REQUEST_REBALANCING`` toward the
   worst deficit nodes, for ``ceil(total_deficit)`` pods, clamped to the configured
   bound and to the pods actually eligible.

Rule 3 is the one worth reading twice: a real shortfall is *not* on its own a reason
to move pods. If the run has already spent half its kilometres driving empty, another
empty kilometre is a poor way to buy a trip, and the mock declines. The rules read
only the observation — plain values — exactly as a live provider does.

``confidence`` is a fixed expression of the deficit, written out below. It is a
number the proposal carries, **not** a probability that the proposal is right, and
nothing in the validator ever consults it.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from app.errors import ProviderError
from app.orchestration.actions import AIActionType
from app.orchestration.config import DEFAULT_ORCHESTRATION_CONFIG, OrchestrationConfig
from app.orchestration.observation import OrchestrationObservation, canonical_json


class MockProvider:
    """A deterministic rule-based provider. Same observation in, same proposal out."""

    name = "mock"

    def __init__(self, config: OrchestrationConfig = DEFAULT_ORCHESTRATION_CONFIG) -> None:
        self._config = config

    def describe(self) -> Mapping[str, Any]:
        return {
            "provider": self.name,
            "model": None,
            "requires_api_key": False,
            "deterministic": True,
            "note": "Fixed rules, no network, no model. Stands in for an LLM in tests "
                    "and demos; says nothing about what a live model would choose.",
            "rules": {
                "min_deficit_to_propose": self._config.mock_min_deficit_to_propose,
                "max_deadhead_share_percent": self._config.mock_max_deadhead_share_percent,
            },
        }

    def propose_action(self, observation: OrchestrationObservation):
        from app.orchestration.providers.base import ProviderResponse

        payload = self.decide(observation)
        return ProviderResponse(provider=self.name, raw_text=canonical_json(payload),
                                payload=payload, model=None)

    # --- the rules ---------------------------------------------------------------
    def decide(self, observation: OrchestrationObservation) -> dict[str, Any]:
        """The proposal, as the plain dict a schema-constrained model would return."""
        config = self._config
        fingerprint = observation.fingerprint()
        deficit = float(observation.demand["total_deficit"])
        eligible = int(observation.fleet["idle_eligible_pods"])
        deadhead_share = observation.metrics["rebalancing"]["deadhead_share_percent"]
        nodes = observation.deficit_node_ids()[:config.max_target_nodes]

        if deficit < config.mock_min_deficit_to_propose or not nodes:
            return self._no_action(
                fingerprint,
                f"Forecast deficit is {deficit:.4f} pods across "
                f"{observation.demand['deficit_node_count']} node(s), below the "
                f"{config.mock_min_deficit_to_propose:g} threshold worth acting on. "
                f"Repositioning would spend empty kilometres for no shortfall.")

        if eligible < config.min_pod_count:
            return self._no_action(
                fingerprint,
                f"A forecast deficit of {deficit:.4f} pods exists, but no pod is "
                f"eligible to move: every pod is busy, charging, or coordinating in an "
                f"active swarm.")

        if deadhead_share is not None and deadhead_share > config.mock_max_deadhead_share_percent:
            return self._no_action(
                fingerprint,
                f"Forecast deficit is {deficit:.4f} pods, but {deadhead_share}% of all "
                f"driving is already empty, above the "
                f"{config.mock_max_deadhead_share_percent:g}% the rules allow. The cost "
                f"of chasing this shortfall outweighs the service it would buy.")

        pod_count = max(config.min_pod_count,
                        min(int(math.ceil(deficit)), config.max_pod_count, eligible))
        # A fixed expression of the shortfall. Not a belief, not a probability.
        confidence = round(min(0.95, 0.5 + deficit / 40.0), 4)
        return {
            "action_type": AIActionType.REQUEST_REBALANCING.value,
            "parameters": {"target_nodes": list(nodes), "pod_count": pod_count},
            "reason": (f"Forecast deficit of {deficit:.4f} pods across "
                       f"{observation.demand['deficit_node_count']} node(s), worst at "
                       f"{nodes[0]}, against {eligible} eligible pod(s)."),
            "expected_effect": (f"Ask the deterministic rebalancer to run a cycle toward "
                                f"{', '.join(nodes)}. The engine decides which pods, if "
                                f"any, actually move."),
            "confidence": confidence,
            "observation_fingerprint": fingerprint,
        }

    @staticmethod
    def _no_action(fingerprint: str, reason: str) -> dict[str, Any]:
        return {
            "action_type": AIActionType.NO_ACTION.value,
            "parameters": {},
            "reason": reason,
            "expected_effect": "Nothing moves; no deadhead kilometres are spent.",
            "confidence": 0.9,
            "observation_fingerprint": fingerprint,
        }


class ScriptedProvider:
    """Returns prepared responses in order, or fails on cue. For tests only.

    This is how the malformed-response and provider-failure paths are exercised
    without pretending a real provider misbehaved.
    """

    name = "scripted"

    def __init__(self, responses, *, model: str | None = None) -> None:
        self._responses = list(responses)
        self._index = 0
        self._model = model

    def describe(self) -> Mapping[str, Any]:
        return {"provider": self.name, "model": self._model, "requires_api_key": False,
                "deterministic": True, "scripted_responses": len(self._responses),
                "note": "A test double. Replays prepared responses; contacts nothing."}

    def propose_action(self, observation: OrchestrationObservation):
        from app.orchestration.providers.base import ProviderResponse

        if self._index >= len(self._responses):
            raise ProviderError("scripted provider has no responses left")
        item = self._responses[self._index]
        self._index += 1
        if isinstance(item, Exception):
            raise item
        if isinstance(item, str):
            return ProviderResponse(provider=self.name, raw_text=item, payload=None,
                                    model=self._model)
        return ProviderResponse(provider=self.name, raw_text=canonical_json(item),
                                payload=item, model=self._model)
