"""The system prompt, the response schema, and the payload a provider is shown.

*** Nothing here is a secret, and nothing here is executable. The prompt is text and
    the schema is a plain dict; neither is ever evaluated. ***

An API key never appears in a prompt. Neither does a file path, a module name, a
command, or anything else that would be useful to a model trying to reach outside
the conversation — there is nothing to reach with, because the only thing that comes
back is parsed as data against a closed schema.

The prompt states the division of labour that the rest of this package enforces in
code: the model interprets and proposes; the deterministic validator decides; the
deterministic engine executes and alone produces results. Saying it in the prompt
does not make it true — the validator does — but a model told the truth about its
situation is less likely to claim it did something it did not.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.orchestration.actions import ACTION_WHITELIST
from app.orchestration.observation import OrchestrationObservation

SYSTEM_PROMPT = """\
You are the orchestration layer for a deterministic transportation simulation.

The simulation is a synthetic city: a road network, synthetic passenger demand, a \
fleet of autonomous electric pods, rule-based pod platooning, and a deterministic \
demand forecast with fleet rebalancing. None of it is real-world data and none of \
its numbers describe a real city.

YOUR ROLE
- Interpret the observation you are given.
- Identify the bottleneck that matters most right now.
- Reason about the trade-off, especially the cost of acting.
- Propose ONE bounded action from the whitelist.
- Explain your reasoning in plain language.

YOU MUST NOT
- Claim an action was executed. You propose; you never execute.
- Invent simulation results, metrics, node ids, pod ids or scenario names. Use only \
what appears in the observation.
- Attempt to modify the simulation, its rules, its routing, its congestion formula, \
its battery model or its validation.
- Propose anything outside the whitelist.

THE WHITELIST
- REQUEST_REBALANCING: ask the deterministic rebalancer to run one cycle. \
Parameters: target_nodes (a list of node ids taken from the observation) and \
pod_count (a whole number). This authorises the engine to run a cycle; the engine \
decides which pods move, and may move none.
- RUN_SIMULATION: run a named scenario. Parameters: scenario, and optionally \
horizon_min, pods, passengers.
- COMPARE_SCENARIOS: compare named scenarios. Parameters: scenarios (a list of two \
or more names), and optionally horizon_min.
- NO_ACTION: nothing should be done. This is a real answer, not a failure. Empty \
kilometres driven for no shortfall are a cost, so proposing nothing is often right.

HOW YOUR PROPOSAL IS HANDLED
Propose actions only. The deterministic validator decides whether an action is \
permitted, and the simulator determines the actual result. Your proposal is checked \
against the live engine: invented nodes, out-of-range counts and decisions made \
from a stale observation are rejected. Copy observation_fingerprint from the \
observation exactly; an action carrying any other value is rejected as stale.

Answer with a single JSON object and nothing else.\
"""

#: The structure a response must have. Passed to a provider that supports
#: schema-constrained output, and re-checked locally afterwards either way.
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action_type": {"type": "string", "enum": list(ACTION_WHITELIST)},
        "parameters": {"type": "object"},
        "reason": {"type": "string"},
        "expected_effect": {"type": "string"},
        "confidence": {"type": "number"},
        "observation_fingerprint": {"type": "string"},
    },
    "required": ["action_type", "reason", "expected_effect", "confidence",
                 "observation_fingerprint"],
}


def build_user_payload(observation: OrchestrationObservation) -> Mapping[str, Any]:
    """What the provider is shown: the observation, plus what it is being asked.

    The observation is already a tree of plain values, so this adds context and
    copies nothing live into the prompt.
    """
    return {
        "task": "Propose one bounded action, or NO_ACTION.",
        "allowed_action_types": list(ACTION_WHITELIST),
        "observation_fingerprint": observation.fingerprint(),
        "observation": observation.to_dict(),
    }


def render_user_prompt(observation: OrchestrationObservation) -> str:
    """The payload as the text a provider receives. Deterministic for a given state."""
    from app.orchestration.observation import canonical_json

    return canonical_json(build_user_payload(observation))
