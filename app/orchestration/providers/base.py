"""The provider interface: the one place the outside world can speak to this system.

A provider turns an observation into a *response*. It does not turn it into an
action, and it certainly does not turn it into an effect. What comes back is text
(and, when the provider can manage it, a structured payload); ``parse_action`` then
decides whether that text describes a well-formed proposal, and the validator
decides whether the proposal is permitted.

Keeping the interface this thin is what lets the deterministic engine run with no
provider at all. Nothing below M6 imports this module, M6 imports no SDK at module
scope, and ``MockProvider`` needs no network, no key and no third-party package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable

from app.orchestration.observation import OrchestrationObservation


@dataclass(frozen=True)
class ProviderResponse:
    """Exactly what a provider said, kept as data.

    ``payload`` is set when the provider returned a structured object (a
    schema-constrained response). ``raw_text`` is always set, because the audit
    record must be able to show what came back even when nothing usable did.

    Neither field is ever executed, imported, or used to name anything.
    """

    provider: str
    raw_text: str
    payload: Mapping[str, Any] | None = None
    model: str | None = None
    #: Wall-clock duration of the call. The one non-deterministic number in M6; it is
    #: reported as an operational measurement and never enters a fingerprint.
    latency_ms: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Audit-facing form. Deliberately excludes ``latency_ms`` (see above)."""
        return {
            "provider": self.provider,
            "model": self.model,
            "payload": dict(self.payload) if self.payload is not None else None,
            "metadata": dict(self.metadata),
        }


@runtime_checkable
class AIProvider(Protocol):
    """Anything that can look at an observation and say what it would do.

    Implementations must not mutate the observation, must not reach the simulation
    (they are never given it), and must not raise anything but
    ``app.errors.ProviderError`` for an operational failure.
    """

    name: str

    def propose_action(self, observation: OrchestrationObservation) -> ProviderResponse:
        ...

    def describe(self) -> Mapping[str, Any]:
        """Configuration facts safe to print: never a key, never a credential."""
        ...
