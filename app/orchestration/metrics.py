"""Decision-process metrics for the orchestration layer.

*** There is deliberately no "AI accuracy" figure here. ***

Accuracy needs a ground truth, and there is none: the rebalancing heuristic the AI
is proposing to invoke is itself explicitly not claimed to be optimal, so there is no
"right answer" for a proposal to match. Inventing a percentage anyway would be the
one genuinely dishonest number this project could produce.

What is measured instead is **process** — how the loop behaved — and it is kept
strictly separate from **outcome**, which only the deterministic engine produces:

    process   proposals generated, accepted, rejected, stale, malformed;
              provider failures; execution failures; which action types were chosen
    outcome   trips served, deadhead kilometres, deficit — engine numbers, taken from
              the audit records' metrics deltas, never from anything the AI said

Comparing two orchestration policies means comparing their engine outcomes on the
same deterministic scenario, with the methodology stated. It does not mean scoring
the proposals.

``average_latency_ms`` is wall-clock and therefore not deterministic. It is reported
because it is operationally useful, and it is flagged everywhere it appears.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from app.orchestration.audit import AuditLog, OrchestrationRecord
from app.orchestration.config import NO_ACCURACY_NOTE
from app.orchestration.execution import ExecutionStatus
from app.orchestration.validator import REJECT_STALE_OBSERVATION, Verdict


@dataclass(frozen=True)
class OrchestrationMetrics:
    """How the loop behaved. Not how good the decisions were — see the module note."""

    cycles: int
    proposals_generated: int
    proposals_accepted: int
    proposals_rejected: int
    stale_proposals: int
    invalid_proposals: int
    provider_failures: int
    successful_actions: int
    execution_failures: int
    no_action_count: int
    action_type_counts: tuple[tuple[str, int], ...]
    rejection_reason_counts: tuple[tuple[str, int], ...]
    average_latency_ms: float | None
    accuracy_note: str = NO_ACCURACY_NOTE

    @property
    def acceptance_rate(self) -> float | None:
        """Accepted over generated. ``None`` when nothing parsed — undefined, not zero."""
        if self.proposals_generated == 0:
            return None
        return round(self.proposals_accepted / self.proposals_generated, 4)

    def to_dict(self) -> dict[str, Any]:
        data = {name: getattr(self, name) for name in self.__dataclass_fields__}
        data["action_type_counts"] = dict(self.action_type_counts)
        data["rejection_reason_counts"] = dict(self.rejection_reason_counts)
        data["acceptance_rate"] = self.acceptance_rate
        data["latency_note"] = ("average_latency_ms is a wall-clock measurement and is "
                                "not deterministic; it is excluded from every record "
                                "fingerprint.")
        return data


def compute_orchestration_metrics(log: AuditLog | Iterable[OrchestrationRecord]
                                  ) -> OrchestrationMetrics:
    """Summarise an audit trail. Reads records only; touches no simulation."""
    records = tuple(log.records() if isinstance(log, AuditLog) else log)

    action_counts: dict[str, int] = {}
    rejection_counts: dict[str, int] = {}
    latencies = [r.latency_ms for r in records if r.latency_ms is not None]

    generated = accepted = rejected = stale = invalid = provider_failures = 0
    successful = failures = no_action = 0

    for record in records:
        if record.provider_error is not None:
            provider_failures += 1
            continue
        if record.proposal is None:
            invalid += 1
            continue
        generated += 1
        action_type = str(record.proposal["action_type"])
        action_counts[action_type] = action_counts.get(action_type, 0) + 1
        if record.verdict == Verdict.APPROVED.value:
            accepted += 1
        else:
            rejected += 1
            code = record.reason_code or "UNKNOWN"
            rejection_counts[code] = rejection_counts.get(code, 0) + 1
            if code == REJECT_STALE_OBSERVATION:
                stale += 1
        if record.execution is not None:
            status = record.execution.get("status")
            if status == ExecutionStatus.EXECUTED.value:
                successful += 1
            elif status == ExecutionStatus.FAILED.value:
                failures += 1
            elif status == ExecutionStatus.SKIPPED.value:
                no_action += 1

    return OrchestrationMetrics(
        cycles=len(records),
        proposals_generated=generated,
        proposals_accepted=accepted,
        proposals_rejected=rejected,
        stale_proposals=stale,
        invalid_proposals=invalid,
        provider_failures=provider_failures,
        successful_actions=successful,
        execution_failures=failures,
        no_action_count=no_action,
        action_type_counts=tuple(sorted(action_counts.items())),
        rejection_reason_counts=tuple(sorted(rejection_counts.items())),
        average_latency_ms=(round(sum(latencies) / len(latencies), 3) if latencies else None),
    )
