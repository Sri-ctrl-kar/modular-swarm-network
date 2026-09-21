"""Milestone 4: deterministic swarm formation and platooning.

*** M4 uses deterministic RULE-BASED swarm formation. No AI/LLM is involved. ***

*** A swarm is a coordination layer over individual autonomous pods, not a
    fictional single vehicle. *** Every pod keeps its own id, battery, passengers
    and route; the swarm records the corridor they cover together.

    individual pods -> compatible routes -> swarm formation -> platoon travel
    -> route divergence -> swarm split -> individual pods

This package sits above M3's fleet and is imported by none of the layers below.
``SwarmSimulation`` extends ``FleetSimulation``, so M1-M3 needed no edit: pods
keep M3's five-member ``PodStatus`` and swarm membership lives here.

Fleet rebalancing is a HOOK only — see ``app/swarm/rebalancing.py``. Actual
adaptive rebalancing belongs to M5.
"""

from __future__ import annotations

from app.swarm.compatibility import (
    CORRIDOR_TOO_BRIEF,
    CORRIDOR_TOO_SMALL_A_SHARE,
    NOT_CO_LOCATED,
    NOT_ENOUGH_PODS,
    TOO_FEW_SHARED_EDGES,
    TOO_MANY_PODS,
    TOO_SHORT_SHARED_DISTANCE,
    CompatibilityResult,
    are_compatible,
    build_corridor,
    check_group,
    corridor_metrics,
    remaining_distance_km,
    shared_edge_prefix,
)
from app.swarm.config import (
    DEFAULT_SWARM_CONFIG,
    MIN_SWARM_SIZE,
    SWARM_PROVENANCE,
    SwarmConfig,
)
from app.swarm.formation import (
    CandidateGroup,
    FormationResult,
    build_swarms,
    plan_formation,
    swarm_id_for,
)
from app.swarm.metrics import ModeComparison, SwarmMetrics, compare_modes, compute_swarm_metrics
from app.swarm.models import (
    ALLOWED_SWARM_TRANSITIONS,
    SharedCorridor,
    Swarm,
    SwarmSnapshot,
    SwarmStatus,
)
from app.swarm.movement import (
    SwarmMovementResult,
    advance_swarm,
    corridor_progress,
    pods_are_synchronised,
)
from app.swarm.rebalancing import (
    REASON_UNSERVED_DEMAND,
    FleetRebalancer,
    RepositionRequest,
    SurplusDeficitRebalancer,
)
from app.swarm.simulation import SwarmSimulation, SwarmTickReport

__all__ = [
    "ALLOWED_SWARM_TRANSITIONS", "CORRIDOR_TOO_BRIEF", "CORRIDOR_TOO_SMALL_A_SHARE",
    "CandidateGroup", "CompatibilityResult", "DEFAULT_SWARM_CONFIG", "FleetRebalancer",
    "FormationResult", "MIN_SWARM_SIZE", "ModeComparison", "NOT_CO_LOCATED", "NOT_ENOUGH_PODS",
    "REASON_UNSERVED_DEMAND", "RepositionRequest", "SWARM_PROVENANCE", "SharedCorridor",
    "SurplusDeficitRebalancer", "Swarm", "SwarmConfig", "SwarmMetrics", "SwarmMovementResult",
    "SwarmSimulation", "SwarmSnapshot", "SwarmStatus", "SwarmTickReport",
    "TOO_FEW_SHARED_EDGES", "TOO_MANY_PODS", "TOO_SHORT_SHARED_DISTANCE", "advance_swarm",
    "are_compatible", "build_corridor", "build_swarms", "check_group", "compare_modes",
    "compute_swarm_metrics", "corridor_metrics", "corridor_progress", "plan_formation",
    "pods_are_synchronised", "remaining_distance_km", "shared_edge_prefix", "swarm_id_for",
]
