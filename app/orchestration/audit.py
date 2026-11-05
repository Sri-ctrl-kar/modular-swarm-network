"""The audit trail: what was seen, what was proposed, why it was allowed, what happened.

*** Nothing in an audit record is a secret. No API key, no credential, no provider
    configuration beyond a model name. ***

One ``OrchestrationRecord`` per cycle, appended in order, never rewritten. Between
them the records answer the four questions that make an AI-in-the-loop system
reviewable at all:

    "What did the model see?"       -> ``observation_fingerprint`` + ``observed_problem``
    "What did it propose?"          -> ``proposal`` (and ``raw_response`` when the
                                       proposal could not be parsed)
    "Why was it accepted/rejected?" -> ``verdict``, ``reason_code``, ``checks``
    "What actually happened?"       -> ``execution``, ``simulation_fingerprint_after``,
                                       ``metrics_delta``

The model's ``expected_effect`` and the engine's ``metrics_delta`` are both stored,
side by side and never merged. That is deliberate: what a model said it expected and
what the deterministic engine did are different kinds of statement, and collapsing
them is exactly how a system starts reporting an AI's intentions as results.

DETERMINISM
-----------
``to_dict()`` contains no wall-clock time. Call latency is real and useful, so it is
kept — on ``timing()``, outside the deterministic record — and never fingerprinted.
Two identical cycles therefore produce byte-identical records.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping

from app.orchestration.observation import fingerprint_of


def cycle_id_for(index: int) -> str:
    """Positional, zero-padded, stable across processes. Never a Python ``hash()``."""
    return f"CY{index:05d}"


@dataclass(frozen=True)
class OrchestrationRecord:
    """One complete pass of propose -> validate -> execute, as plain values."""

    cycle_id: str
    provider: str
    model: str | None
    observation_fingerprint: str
    observed_problem: str
    proposal: Mapping[str, Any] | None = None
    parse_error_code: str | None = None
    parse_error_detail: str | None = None
    raw_response: str = ""
    provider_error: str | None = None
    verdict: str = "REJECTED"
    reason_code: str | None = None
    verdict_detail: str | None = None
    checks: tuple[tuple[str, bool], ...] = ()
    execution: Mapping[str, Any] | None = None
    simulation_fingerprint_before: str | None = None
    simulation_fingerprint_after: str | None = None
    metrics_delta: Mapping[str, Any] = field(default_factory=dict)
    #: Wall-clock; excluded from ``to_dict`` so replays compare exactly.
    latency_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """The deterministic record. No wall-clock, no secrets, no live objects."""
        return {
            "cycle_id": self.cycle_id,
            "provider": self.provider,
            "model": self.model,
            "observation_fingerprint": self.observation_fingerprint,
            "observed_problem": self.observed_problem,
            "proposal": dict(self.proposal) if self.proposal is not None else None,
            "parse_error_code": self.parse_error_code,
            "parse_error_detail": self.parse_error_detail,
            "raw_response": self.raw_response,
            "provider_error": self.provider_error,
            "verdict": self.verdict,
            "reason_code": self.reason_code,
            "verdict_detail": self.verdict_detail,
            "checks": [{"check": name, "passed": passed} for name, passed in self.checks],
            "execution": dict(self.execution) if self.execution is not None else None,
            "simulation_fingerprint_before": self.simulation_fingerprint_before,
            "simulation_fingerprint_after": self.simulation_fingerprint_after,
            "metrics_delta": dict(self.metrics_delta),
        }

    def timing(self) -> dict[str, Any]:
        """The non-deterministic part, kept apart on purpose."""
        return {"cycle_id": self.cycle_id, "latency_ms": self.latency_ms,
                "note": "Wall-clock measurement. Excluded from the deterministic record "
                        "and from every fingerprint."}

    def fingerprint(self) -> str:
        return fingerprint_of(self.to_dict())

    def explanation(self) -> str:
        """The cycle as a reviewer reads it: problem, proposal, decision, outcome."""
        lines = [
            f"{self.cycle_id}  provider={self.provider}"
            + (f" model={self.model}" if self.model else ""),
            f"  OBSERVED    {self.observed_problem}",
        ]
        if self.provider_error:
            lines.append(f"  PROVIDER    failed: {self.provider_error}")
        elif self.proposal is None:
            lines.append(f"  PROPOSAL    unusable: {self.parse_error_code} "
                         f"({self.parse_error_detail})")
        else:
            lines.append(f"  PROPOSAL    {self.proposal['action_type']} "
                         f"(confidence {self.proposal['confidence']})")
            lines.append(f"  REASONING   {self.proposal['reason']}")
            lines.append(f"  AI EXPECTS  {self.proposal['expected_effect']}")
        lines.append(f"  VALIDATOR   {self.verdict}"
                     + (f" — {self.reason_code}: {self.verdict_detail}"
                        if self.reason_code else ""))
        if self.execution is not None:
            lines.append(f"  ENGINE DID  {self.execution['status']}: "
                         f"{self.execution['detail']}")
        else:
            lines.append("  ENGINE DID  nothing — the action never reached execution.")
        return "\n".join(lines)


class AuditLog:
    """An append-only list of records, in cycle order."""

    def __init__(self) -> None:
        self._records: list[OrchestrationRecord] = []

    def __len__(self) -> int:
        return len(self._records)

    def __iter__(self) -> Iterator[OrchestrationRecord]:
        return iter(self._records)

    def append(self, record: OrchestrationRecord) -> OrchestrationRecord:
        if not isinstance(record, OrchestrationRecord):
            raise TypeError(f"expected an OrchestrationRecord, got {type(record).__name__}")
        self._records.append(record)
        return record

    def records(self) -> tuple[OrchestrationRecord, ...]:
        return tuple(self._records)

    def next_cycle_id(self) -> str:
        return cycle_id_for(len(self._records) + 1)

    def to_dict(self) -> dict[str, Any]:
        return {"cycle_count": len(self._records),
                "records": [record.to_dict() for record in self._records]}

    def fingerprint(self) -> str:
        """Identity of the whole trail. Identical for two identical runs."""
        return fingerprint_of(self.to_dict())

    def explanation(self) -> str:
        return "\n\n".join(record.explanation() for record in self._records)

    def timings(self) -> tuple[dict[str, Any], ...]:
        return tuple(record.timing() for record in self._records)
