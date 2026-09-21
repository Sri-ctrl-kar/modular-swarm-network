"""Assumptions of the deterministic swarm / platooning model (M4).

*** M4 uses deterministic RULE-BASED swarm formation. No AI/LLM is involved. ***

Every threshold lives here and nowhere else, mirroring ``app/config.py`` (network),
``app/demand/config.py`` (demand) and ``app/fleet/config.py`` (fleet). Formation
is decided by explicit numeric comparisons, never by a fuzzy or learned score.

A SWARM IS A COORDINATION LAYER, NOT A VEHICLE
----------------------------------------------
Pods in a swarm stay individually identifiable: each keeps its own id, battery,
passengers and route. The swarm records that they traverse a shared corridor
together. Four pods in a platoon are still four pods — see ``ROAD OCCUPANCY``
below for exactly what is and is not claimed to improve.

THE FIVE COMPATIBILITY RULES (all must hold; see ``app/swarm/compatibility.py``)
-------------------------------------------------------------------------------
1. Co-location — every candidate sits at the same node, ready to enter the same
   next edge. Pods are never teleported together to form a platoon.
2. Shared corridor length — the common prefix of the candidates' remaining routes
   is at least ``min_shared_edges`` edges AND ``min_shared_distance_km`` long.
3. Bounded divergence — that shared corridor is at least
   ``min_shared_route_fraction`` of *every* member's remaining journey, so pods
   do not platoon for a trivial leg of an otherwise unrelated trip.
4. Size — at most ``max_swarm_size`` pods, and at least 2 (one pod is not a swarm).
5. State — every candidate is a pod that is free to join: it holds a trip and a
   route and is not already in another swarm.

Plus two timing thresholds:

* ``max_formation_delay_min`` — how long an assigned pod will wait at its origin
  for compatible partners before giving up and departing alone. This is the only
  behavioural difference from M3's immediate departure, and it applies in swarm
  mode only, so the baseline comparison stays honest. It is also by far the most
  influential threshold: on the seed-42 city with 100 pods and 1,000 trips,
  raising it from 3 to 5 to 10 to 15 minutes takes the number of swarms from 15
  to 28 to 36 to 47, while the other thresholds barely move the count. The
  default of 5 is a compromise — longer waits platoon more pods but delay
  arrivals, and past about 15 minutes completions start to fall.
* ``min_formation_stability_min`` — a swarm only forms if its shared corridor is
  worth at least this many minutes of travel. This is what stops formation and
  splitting from oscillating: a group that would disband almost immediately is
  never formed in the first place.

ROAD OCCUPANCY (an explicit assumption, NOT a physics or fuel claim)
-------------------------------------------------------------------
Platooning grants **no discount** on distance, travel time or energy. A pod in a
formation drives the same edges at the same M1 edge costs and pays the same M3
battery cost as it would alone, metre for metre — a test asserts that a platooned
pod and a solo pod covering the same corridor consume exactly the same.

Be careful reading fleet *totals* across the two modes: they are not equal, and
that is not a platooning effect. Waiting up to ``max_formation_delay_min`` shifts
departures, so a slightly different set of trips gets served, and the totals move
with the served set. Compare per-trip figures, not aggregates, when you want to
check the cost model.

What coordination changes is modelled road *space*. A pod following in formation
keeps a shorter headway, so it is charged ``formation_occupancy_factor`` of the
road space an independent pod would need:

    road_occupancy_km = pod_km_outside_formation
                      + Σ over swarms [ corridor_km × (1 + (n - 1) × factor) ]

The factor is a **project assumption about headway**, not a measured
aerodynamic, fuel or emissions saving. No such saving is claimed anywhere.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from app.errors import SwarmConfigError

SWARM_SCHEMA_VERSION = 1

SWARM_PROVENANCE = (
    "SYNTHETIC — swarm formation thresholds, the stability window and the road-occupancy "
    "factor are invented project assumptions. Formation is deterministic and rule-based; "
    "no AI/LLM is involved. No aerodynamic, fuel or emissions saving is claimed."
)


def _check_number(value: Any, label: str, *, minimum: float | None = None,
                  maximum: float | None = None, allow_equal_min: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SwarmConfigError(f"{label} must be a finite number, got {value!r}")
    if minimum is not None:
        if allow_equal_min and value < minimum:
            raise SwarmConfigError(f"{label} must be >= {minimum}, got {value!r}")
        if not allow_equal_min and value <= minimum:
            raise SwarmConfigError(f"{label} must be > {minimum}, got {value!r}")
    if maximum is not None and value > maximum:
        raise SwarmConfigError(f"{label} must be <= {maximum}, got {value!r}")
    return float(value)


#: A swarm needs at least two pods. One pod travelling alone is not a swarm.
MIN_SWARM_SIZE = 2


@dataclass(frozen=True)
class SwarmConfig:
    """Every tunable threshold of swarm formation and platoon accounting."""

    # Rule 2 — how much corridor is worth coordinating over.
    min_shared_edges: int = 2
    min_shared_distance_km: float = 3.0
    # Rule 3 — the corridor must be this fraction of each member's remaining trip.
    min_shared_route_fraction: float = 0.4
    # Rule 4 — group size.
    max_swarm_size: int = 4
    # Timing.
    max_formation_delay_min: float = 5.0
    min_formation_stability_min: float = 2.0
    # Road-space accounting (see module docstring — an assumption, not physics).
    formation_occupancy_factor: float = 0.4

    data_provenance: str = SWARM_PROVENANCE

    def __post_init__(self) -> None:
        if isinstance(self.min_shared_edges, bool) or not isinstance(self.min_shared_edges, int) \
                or self.min_shared_edges < 1:
            raise SwarmConfigError(f"min_shared_edges must be an int >= 1, got {self.min_shared_edges!r}")
        if isinstance(self.max_swarm_size, bool) or not isinstance(self.max_swarm_size, int) \
                or self.max_swarm_size < MIN_SWARM_SIZE:
            raise SwarmConfigError(
                f"max_swarm_size must be an int >= {MIN_SWARM_SIZE}, got {self.max_swarm_size!r}"
            )
        object.__setattr__(self, "min_shared_distance_km",
                           _check_number(self.min_shared_distance_km, "min_shared_distance_km",
                                         minimum=0.0, allow_equal_min=False))
        object.__setattr__(self, "min_shared_route_fraction",
                           _check_number(self.min_shared_route_fraction, "min_shared_route_fraction",
                                         minimum=0.0, maximum=1.0))
        object.__setattr__(self, "max_formation_delay_min",
                           _check_number(self.max_formation_delay_min, "max_formation_delay_min",
                                         minimum=0.0))
        object.__setattr__(self, "min_formation_stability_min",
                           _check_number(self.min_formation_stability_min,
                                         "min_formation_stability_min", minimum=0.0))
        object.__setattr__(self, "formation_occupancy_factor",
                           _check_number(self.formation_occupancy_factor,
                                         "formation_occupancy_factor", minimum=0.0, maximum=1.0))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SWARM_SCHEMA_VERSION,
            "data_provenance": self.data_provenance,
            "compatibility": {
                "min_swarm_size": MIN_SWARM_SIZE,
                "max_swarm_size": self.max_swarm_size,
                "min_shared_edges": self.min_shared_edges,
                "min_shared_distance_km": self.min_shared_distance_km,
                "min_shared_route_fraction": self.min_shared_route_fraction,
                "note": "All rules are explicit numeric comparisons; no AI/LLM is involved.",
            },
            "timing": {
                "max_formation_delay_min": self.max_formation_delay_min,
                "min_formation_stability_min": self.min_formation_stability_min,
            },
            "road_occupancy": {
                "formula": "pod_km_outside_formation + sum(corridor_km * (1 + (n-1) * factor))",
                "formation_occupancy_factor": self.formation_occupancy_factor,
                "note": "A headway assumption about road SPACE. Platooning changes no pod's "
                        "distance, travel time or energy here, and no aerodynamic, fuel or "
                        "emissions saving is claimed.",
            },
        }


DEFAULT_SWARM_CONFIG = SwarmConfig()
