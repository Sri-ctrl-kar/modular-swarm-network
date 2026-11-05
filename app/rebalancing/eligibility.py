"""Which pods may be repositioned, and why the others may not.

**A passenger's pod is never taken.** The rules below are deliberately
conservative: an ineligible pod is left alone even when moving it would help the
forecast, because interrupting service to improve a statistic is exactly the kind
of thing this layer must not do.

A pod is eligible only when **all** of these hold:

1. it is ``IDLE`` — so it is not ASSIGNED, TRAVELING or ARRIVED, and therefore not
   in passenger service and not already repositioning;
2. it is not ``CHARGING`` (implied by 1, and asserted separately so the intent is
   explicit rather than incidental);
3. it is not a member of an **active swarm** (M4 owns that pod's coordination);
4. its battery covers the move's estimated energy plus
   ``reposition_battery_reserve_percent``, so it arrives able to work.

Rules 1–3 are checked here. Rule 4 needs a route, so it is applied by the planner
once it knows where the pod would go; ``has_battery_for`` is the shared predicate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from app.fleet.config import DEFAULT_FLEET_CONFIG, FleetConfig
from app.fleet.models import Pod, PodStatus
from app.fleet.pod_fleet import PodFleet
from app.rebalancing.config import DEFAULT_REBALANCING_CONFIG, RebalancingConfig

#: Machine-readable reasons a pod was not eligible.
NOT_IDLE = "not_idle"
IS_CHARGING = "is_charging"
IN_ACTIVE_SWARM = "in_active_swarm"
INSUFFICIENT_BATTERY = "insufficient_battery"


@dataclass(frozen=True)
class EligibilityResult:
    pod_id: str
    is_eligible: bool
    reason: str | None = None

    def __bool__(self) -> bool:
        return self.is_eligible


def check_pod(pod: Pod, *, in_active_swarm: bool) -> EligibilityResult:
    """Apply rules 1-3 to one pod. Rule 4 (battery) needs a route, so not here."""
    if pod.status is PodStatus.CHARGING:
        return EligibilityResult(pod.pod_id, False, IS_CHARGING)
    if pod.status is not PodStatus.IDLE:
        # Covers ASSIGNED / TRAVELING / ARRIVED: in passenger service, or already
        # repositioning. Either way the pod is busy and is left alone.
        return EligibilityResult(pod.pod_id, False, NOT_IDLE)
    if in_active_swarm:
        return EligibilityResult(pod.pod_id, False, IN_ACTIVE_SWARM)
    return EligibilityResult(pod.pod_id, True)


def eligible_pods(fleet: PodFleet,
                  in_active_swarm: Callable[[str], bool] | None = None) -> tuple[Pod, ...]:
    """Every pod that passes rules 1-3, in ``pod_id`` order.

    ``in_active_swarm`` is injected rather than imported so this module does not
    depend on the swarm simulation; the M5 simulation passes its own lookup.
    """
    predicate = in_active_swarm or (lambda pod_id: False)
    return tuple(pod for pod in fleet.pods()
                 if check_pod(pod, in_active_swarm=predicate(pod.pod_id)))


def eligible_pods_by_node(fleet: PodFleet,
                          in_active_swarm: Callable[[str], bool] | None = None
                          ) -> dict[str, tuple[str, ...]]:
    """{node_id: pod ids eligible to be repositioned from it}, ids sorted."""
    by_node: dict[str, list[str]] = {}
    for pod in eligible_pods(fleet, in_active_swarm):
        by_node.setdefault(pod.current_node_id, []).append(pod.pod_id)
    return {node: tuple(sorted(pods)) for node, pods in sorted(by_node.items())}


def required_battery_percent(energy_kwh: float, fleet_config: FleetConfig = DEFAULT_FLEET_CONFIG,
                             config: RebalancingConfig = DEFAULT_REBALANCING_CONFIG) -> float:
    """Battery a move needs: its own energy, plus the reserve, as a percentage.

    Uses M3's battery model unchanged — there is no second energy model here.
    """
    return energy_kwh * fleet_config.percent_per_kwh + config.reposition_battery_reserve_percent


def has_battery_for(pod: Pod, energy_kwh: float,
                    fleet_config: FleetConfig = DEFAULT_FLEET_CONFIG,
                    config: RebalancingConfig = DEFAULT_REBALANCING_CONFIG) -> bool:
    """Rule 4. A pod short of energy + reserve is refused, never sent and stranded."""
    return pod.battery_percent >= required_battery_percent(energy_kwh, fleet_config, config)


def ineligibility_reasons(fleet: PodFleet,
                          in_active_swarm: Callable[[str], bool] | None = None
                          ) -> tuple[tuple[str, str], ...]:
    """(pod_id, reason) for every pod that failed rules 1-3, for reporting."""
    predicate = in_active_swarm or (lambda pod_id: False)
    rows = []
    for pod in fleet.pods():
        result = check_pod(pod, in_active_swarm=predicate(pod.pod_id))
        if not result:
            rows.append((pod.pod_id, result.reason))
    return tuple(rows)
