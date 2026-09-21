"""Assumptions of the SYNTHETIC passenger-demand model (M2).

*** ALL DEMAND DATA IS SYNTHETIC. This is a scenario model for simulation and is
    NOT calibrated to real-world city mobility data. ***

Every number the demand generator uses lives here and is mirrored in
``scenarios/demand_*.json``, exactly as ``app/config.py`` is mirrored in
``scenarios/baseline.json``. No magic numbers belong in the generator.

WHY THIS LAYER DEFINES ITS OWN NODE ROLES
-----------------------------------------
M1's ``Node`` only distinguishes ``intersection`` / ``station`` / ``terminal``,
which cannot express "residential" versus "employment". Rather than change the
M1 model (which would alter ``scenarios/baseline.json``), the demand layer keeps
its own ``PlaceRole`` classification as demand configuration:

    role(node) = node_roles[node_id]  if listed,
                 else default_roles_by_node_type[node.node_type]

So M1 stays untouched and the mapping is an explicit, reviewable assumption.

HOW A TRIP IS DRAWN (all weights below, no hidden rules)
--------------------------------------------------------
1. A time bucket is drawn from the buckets' ``trip_share`` weights.
2. A request time is drawn uniformly inside that bucket's window(s), windows
   weighted by their length.
3. Origin weight      = production[role(origin)]
   Destination weight = attraction[role(dest)] / (1 + straight_line_km) ** gravity_distance_exponent
   The distance term is a plain gravity-style decay: nearby destinations are
   likelier. Straight-line km is used for WEIGHTING ONLY — travel cost always
   comes from the network (see M1's routing).
4. Commuters anchor on their home node: in the morning peak the home node IS the
   origin; in the evening peak it IS the destination. Everyone else samples both.
5. The origin is removed from the destination candidate set, so origin never
   equals destination by construction (no rejection sampling, no retry loop).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from app.errors import DemandProfileError

DEMAND_SCHEMA_VERSION = 1
DEFAULT_DEMAND_SEED = 42
MINUTES_PER_DAY = 1440

DEMAND_PROVENANCE = (
    "SYNTHETIC — passengers, trips, party sizes and time-of-day weights are invented "
    "for prototyping. This is a synthetic demand model for simulation and is not "
    "calibrated to real-world city mobility data."
)

# Tolerance when checking that shares / distributions sum to 1.
SHARE_TOLERANCE = 1e-9


class PlaceRole(str, Enum):
    """What a node means for demand. A demand-layer concept, not an M1 node type."""

    RESIDENTIAL = "residential"   # trips start here in the morning, end here in the evening
    EMPLOYMENT = "employment"     # workplaces / campuses: strong daytime attraction
    HUB = "hub"                   # terminals and interchange stations: high attraction all day
    MIXED = "mixed"               # markets, riverside: moderate both ways
    THROUGH = "through"           # plain intersections: people pass through, few start/end here


class TripPurpose(str, Enum):
    COMMUTE = "commute"
    OTHER = "other"


def _check_number(value: Any, label: str, *, minimum: float | None = None,
                  maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise DemandProfileError(f"{label} must be a finite number, got {value!r}")
    if minimum is not None and value < minimum:
        raise DemandProfileError(f"{label} must be >= {minimum}, got {value!r}")
    if maximum is not None and value > maximum:
        raise DemandProfileError(f"{label} must be <= {maximum}, got {value!r}")
    return float(value)


def _check_weights(raw: Mapping[Any, Any], label: str) -> dict[PlaceRole, float]:
    """Every PlaceRole gets a weight; missing roles default to 0.0."""
    if not isinstance(raw, Mapping):
        raise DemandProfileError(f"{label} must be a mapping, got {type(raw).__name__}")
    weights = {role: 0.0 for role in PlaceRole}
    for key, value in raw.items():
        try:
            role = PlaceRole(key)
        except ValueError:
            allowed = ", ".join(r.value for r in PlaceRole)
            raise DemandProfileError(f"{label}: unknown role {key!r} (allowed: {allowed})") from None
        weights[role] = _check_number(value, f"{label}[{role.value}]", minimum=0.0)
    if sum(weights.values()) <= 0.0:
        raise DemandProfileError(f"{label} must have at least one positive weight")
    return weights


@dataclass(frozen=True)
class TimeBucket:
    """One time-of-day band: when it happens, how much of the day's demand it
    carries, and which roles produce / attract trips during it."""

    name: str
    windows: tuple[tuple[float, float], ...]
    trip_share: float
    production: Mapping[PlaceRole, float]
    attraction: Mapping[PlaceRole, float]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise DemandProfileError(f"time bucket name must be a non-empty string, got {self.name!r}")
        if not self.windows:
            raise DemandProfileError(f"bucket {self.name!r} must have at least one window")
        windows = []
        for start, end in self.windows:
            start = _check_number(start, f"bucket {self.name!r} window start", minimum=0.0,
                                  maximum=MINUTES_PER_DAY)
            end = _check_number(end, f"bucket {self.name!r} window end", minimum=0.0,
                                maximum=MINUTES_PER_DAY)
            if end <= start:
                raise DemandProfileError(f"bucket {self.name!r}: window end must be > start, got [{start}, {end})")
            windows.append((start, end))
        object.__setattr__(self, "windows", tuple(windows))
        object.__setattr__(self, "trip_share",
                           _check_number(self.trip_share, f"bucket {self.name!r} trip_share", minimum=0.0))
        object.__setattr__(self, "production", _check_weights(self.production, f"bucket {self.name!r} production"))
        object.__setattr__(self, "attraction", _check_weights(self.attraction, f"bucket {self.name!r} attraction"))

    @property
    def total_window_min(self) -> float:
        return sum(end - start for start, end in self.windows)

    def contains(self, time_min: float) -> bool:
        return any(start <= time_min < end for start, end in self.windows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "windows": [[start, end] for start, end in self.windows],
            "trip_share": self.trip_share,
            "production": {role.value: w for role, w in self.production.items()},
            "attraction": {role.value: w for role, w in self.attraction.items()},
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TimeBucket":
        required = {"name", "windows", "trip_share", "production", "attraction"}
        missing = required - set(data)
        if missing:
            raise DemandProfileError(f"time bucket is missing fields: {sorted(missing)}")
        windows = data["windows"]
        if not isinstance(windows, (list, tuple)):
            raise DemandProfileError("bucket windows must be a list")
        parsed = []
        for window in windows:
            if not isinstance(window, (list, tuple)) or len(window) != 2:
                raise DemandProfileError(f"each window must be [start_min, end_min], got {window!r}")
            parsed.append((window[0], window[1]))
        return cls(name=data["name"], windows=tuple(parsed), trip_share=data["trip_share"],
                   production=data["production"], attraction=data["attraction"])


@dataclass(frozen=True)
class DemandProfile:
    """A complete, human-readable demand assumption set."""

    profile_id: str
    description: str
    buckets: tuple[TimeBucket, ...]
    party_size_distribution: tuple[tuple[int, float], ...]
    node_roles: Mapping[str, PlaceRole] = field(default_factory=dict)
    default_roles_by_node_type: Mapping[str, PlaceRole] = field(default_factory=dict)
    home_role_weights: Mapping[PlaceRole, float] = field(default_factory=dict)
    commuter_share: float = 0.6
    gravity_distance_exponent: float = 1.0
    data_provenance: str = DEMAND_PROVENANCE

    def __post_init__(self) -> None:
        if not isinstance(self.profile_id, str) or not self.profile_id.strip():
            raise DemandProfileError(f"profile_id must be a non-empty string, got {self.profile_id!r}")
        if not self.buckets:
            raise DemandProfileError("a demand profile needs at least one time bucket")
        names = [b.name for b in self.buckets]
        if len(set(names)) != len(names):
            raise DemandProfileError(f"duplicate time bucket names: {names}")
        share_total = sum(b.trip_share for b in self.buckets)
        if abs(share_total - 1.0) > 1e-6:
            raise DemandProfileError(f"bucket trip_share values must sum to 1.0, got {share_total!r}")

        parties = []
        for size, weight in self.party_size_distribution:
            if isinstance(size, bool) or not isinstance(size, int) or size < 1:
                raise DemandProfileError(f"party size must be an int >= 1, got {size!r}")
            parties.append((size, _check_number(weight, f"party size {size} weight", minimum=0.0)))
        if not parties:
            raise DemandProfileError("party_size_distribution must not be empty")
        if len({size for size, _ in parties}) != len(parties):
            raise DemandProfileError("duplicate party sizes in party_size_distribution")
        if sum(weight for _, weight in parties) <= 0:
            raise DemandProfileError("party_size_distribution needs a positive weight")
        object.__setattr__(self, "party_size_distribution", tuple(sorted(parties)))

        object.__setattr__(self, "node_roles",
                           {str(k): PlaceRole(v) for k, v in dict(self.node_roles).items()})
        object.__setattr__(self, "default_roles_by_node_type",
                           {str(k): PlaceRole(v) for k, v in dict(self.default_roles_by_node_type).items()})
        object.__setattr__(self, "home_role_weights",
                           _check_weights(self.home_role_weights or {PlaceRole.RESIDENTIAL: 1.0},
                                          "home_role_weights"))
        object.__setattr__(self, "commuter_share",
                           _check_number(self.commuter_share, "commuter_share", minimum=0.0, maximum=1.0))
        object.__setattr__(self, "gravity_distance_exponent",
                           _check_number(self.gravity_distance_exponent, "gravity_distance_exponent", minimum=0.0))

    # --- lookups ---------------------------------------------------------------
    def role_for(self, node_id: str, node_type: str) -> PlaceRole:
        """Explicit per-node role wins; otherwise fall back on the node type."""
        if node_id in self.node_roles:
            return self.node_roles[node_id]
        node_type = getattr(node_type, "value", node_type)
        if node_type in self.default_roles_by_node_type:
            return self.default_roles_by_node_type[node_type]
        raise DemandProfileError(
            f"no demand role for node {node_id!r} of type {node_type!r}; "
            f"add it to node_roles or default_roles_by_node_type"
        )

    def bucket(self, name: str) -> TimeBucket:
        for candidate in self.buckets:
            if candidate.name == name:
                return candidate
        raise DemandProfileError(f"unknown time bucket {name!r}; "
                                 f"known: {sorted(b.name for b in self.buckets)}")

    def bucket_for_time(self, time_min: float) -> TimeBucket:
        for candidate in self.buckets:
            if candidate.contains(time_min):
                return candidate
        raise DemandProfileError(f"no time bucket covers minute {time_min!r}")

    def bucket_names(self) -> tuple[str, ...]:
        return tuple(b.name for b in self.buckets)

    # --- serialisation ---------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": DEMAND_SCHEMA_VERSION,
            "profile_id": self.profile_id,
            "description": self.description,
            "data_provenance": self.data_provenance,
            "assumptions": {
                "commuter_share": self.commuter_share,
                "gravity_distance_exponent": self.gravity_distance_exponent,
                "gravity_note": "destination weight = attraction / (1 + straight_line_km) ** exponent; "
                                "straight-line km is used for weighting only, never as travel cost",
                "party_size_distribution": {str(size): weight for size, weight in self.party_size_distribution},
                "home_role_weights": {role.value: w for role, w in self.home_role_weights.items()},
                "default_roles_by_node_type": {k: v.value for k, v in self.default_roles_by_node_type.items()},
                "node_roles": {k: v.value for k, v in sorted(self.node_roles.items())},
            },
            "time_buckets": [b.to_dict() for b in self.buckets],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DemandProfile":
        if not isinstance(data, Mapping):
            raise DemandProfileError("demand profile root must be a JSON object")
        required = ("schema_version", "profile_id", "data_provenance", "assumptions", "time_buckets")
        missing = [key for key in required if key not in data]
        if missing:
            raise DemandProfileError(f"demand profile is missing keys: {missing}")
        if data["schema_version"] != DEMAND_SCHEMA_VERSION:
            raise DemandProfileError(f"unsupported demand schema_version {data['schema_version']!r} "
                                     f"(expected {DEMAND_SCHEMA_VERSION})")
        assumptions = data["assumptions"]
        if not isinstance(assumptions, Mapping):
            raise DemandProfileError("assumptions must be a JSON object")
        try:
            parties = tuple((int(size), weight)
                            for size, weight in assumptions["party_size_distribution"].items())
            return cls(
                profile_id=str(data["profile_id"]),
                description=str(data.get("description", "")),
                data_provenance=str(data["data_provenance"]),
                buckets=tuple(TimeBucket.from_dict(b) for b in data["time_buckets"]),
                party_size_distribution=parties,
                node_roles=assumptions.get("node_roles", {}),
                default_roles_by_node_type=assumptions.get("default_roles_by_node_type", {}),
                home_role_weights=assumptions.get("home_role_weights", {}),
                commuter_share=assumptions["commuter_share"],
                gravity_distance_exponent=assumptions["gravity_distance_exponent"],
            )
        except DemandProfileError:
            raise
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            raise DemandProfileError(f"malformed demand profile: {exc!r}") from exc


# ---------------------------------------------------------------------------
# The synthetic city's role map. Node ids come from app/network/synthetic_city.py.
# These are invented judgements about what each place is for, nothing more.
# ---------------------------------------------------------------------------
SYNTHETIC_CITY_NODE_ROLES: dict[str, PlaceRole] = {
    "residential_north": PlaceRole.RESIDENTIAL,
    "residential_south": PlaceRole.RESIDENTIAL,
    "riverside": PlaceRole.RESIDENTIAL,
    "university": PlaceRole.EMPLOYMENT,
    "tech_park": PlaceRole.EMPLOYMENT,
    "industrial_zone": PlaceRole.EMPLOYMENT,
    "market": PlaceRole.MIXED,
    "north_station": PlaceRole.HUB,
    "central_station": PlaceRole.HUB,
    "east_hub": PlaceRole.HUB,
    "west_hub": PlaceRole.HUB,
    "airport": PlaceRole.HUB,
}

# Any node not named above falls back on its M1 node type.
DEFAULT_ROLES_BY_NODE_TYPE: dict[str, PlaceRole] = {
    "station": PlaceRole.MIXED,
    "terminal": PlaceRole.HUB,
    "intersection": PlaceRole.THROUGH,
}

# Where passengers live. Residential first, a little from mixed areas.
HOME_ROLE_WEIGHTS: dict[PlaceRole, float] = {
    PlaceRole.RESIDENTIAL: 8.0,
    PlaceRole.MIXED: 2.0,
    PlaceRole.HUB: 1.0,
}

PARTY_SIZE_DISTRIBUTION: tuple[tuple[int, float], ...] = ((1, 0.62), (2, 0.22), (3, 0.10), (4, 0.06))

_MORNING = TimeBucket(
    name="morning_peak",
    windows=((360.0, 600.0),),     # 06:00–10:00
    trip_share=0.35,
    production={"residential": 6.0, "mixed": 1.5, "hub": 1.0, "employment": 0.6, "through": 0.2},
    attraction={"employment": 5.0, "hub": 3.0, "mixed": 1.5, "residential": 0.4, "through": 0.2},
)
_MIDDAY = TimeBucket(
    name="midday",
    windows=((600.0, 960.0),),     # 10:00–16:00
    trip_share=0.22,
    production={"mixed": 2.0, "residential": 1.5, "employment": 1.5, "hub": 1.5, "through": 0.3},
    attraction={"mixed": 2.5, "hub": 2.0, "employment": 1.5, "residential": 1.2, "through": 0.3},
)
_EVENING = TimeBucket(
    name="evening_peak",
    windows=((960.0, 1200.0),),    # 16:00–20:00
    trip_share=0.31,
    production={"employment": 5.0, "hub": 3.0, "mixed": 1.8, "residential": 0.5, "through": 0.2},
    attraction={"residential": 6.0, "mixed": 1.5, "hub": 1.2, "employment": 0.5, "through": 0.2},
)
_OFF_PEAK = TimeBucket(
    name="off_peak",
    windows=((1200.0, 1440.0), (0.0, 360.0)),   # 20:00–24:00 and 00:00–06:00
    trip_share=0.12,
    production={"mixed": 1.5, "residential": 1.5, "hub": 1.2, "employment": 0.8, "through": 0.3},
    attraction={"mixed": 1.8, "residential": 1.5, "hub": 1.5, "employment": 0.6, "through": 0.3},
)

BASELINE_DEMAND_PROFILE = DemandProfile(
    profile_id="baseline",
    description="Whole-day synthetic demand: morning and evening peaks around a quieter "
                "midday and a low off-peak night.",
    buckets=(_MORNING, _MIDDAY, _EVENING, _OFF_PEAK),
    party_size_distribution=PARTY_SIZE_DISTRIBUTION,
    node_roles=SYNTHETIC_CITY_NODE_ROLES,
    default_roles_by_node_type=DEFAULT_ROLES_BY_NODE_TYPE,
    home_role_weights=HOME_ROLE_WEIGHTS,
    commuter_share=0.6,
    gravity_distance_exponent=1.0,
)

PEAK_HOUR_DEMAND_PROFILE = DemandProfile(
    profile_id="peak_hour",
    description="Morning rush concentrated into 07:00-09:00: nearly all trips are commuters "
                "leaving home for work or a hub. Same city, much sharper demand.",
    buckets=(
        TimeBucket(
            name="morning_peak",
            windows=((420.0, 540.0),),          # 07:00–09:00 only
            trip_share=0.86,
            # Sharper than baseline: residential origins dominate, employment/hub absorb.
            production={"residential": 9.0, "mixed": 1.2, "hub": 0.8, "employment": 0.3, "through": 0.1},
            attraction={"employment": 7.0, "hub": 4.0, "mixed": 1.0, "residential": 0.2, "through": 0.1},
        ),
        TimeBucket(
            name="midday",
            windows=((540.0, 960.0),),
            trip_share=0.09,
            production={"mixed": 2.0, "employment": 1.5, "hub": 1.5, "residential": 1.0, "through": 0.3},
            attraction={"mixed": 2.5, "hub": 2.0, "employment": 1.5, "residential": 1.0, "through": 0.3},
        ),
        TimeBucket(
            name="off_peak",
            windows=((960.0, 1440.0), (0.0, 420.0)),
            trip_share=0.05,
            production={"mixed": 1.5, "residential": 1.5, "hub": 1.2, "employment": 0.8, "through": 0.3},
            attraction={"mixed": 1.8, "residential": 1.5, "hub": 1.5, "employment": 0.6, "through": 0.3},
        ),
    ),
    party_size_distribution=PARTY_SIZE_DISTRIBUTION,
    node_roles=SYNTHETIC_CITY_NODE_ROLES,
    default_roles_by_node_type=DEFAULT_ROLES_BY_NODE_TYPE,
    home_role_weights=HOME_ROLE_WEIGHTS,
    commuter_share=0.9,
    gravity_distance_exponent=1.0,
)

DEMAND_PROFILES: dict[str, DemandProfile] = {
    BASELINE_DEMAND_PROFILE.profile_id: BASELINE_DEMAND_PROFILE,
    PEAK_HOUR_DEMAND_PROFILE.profile_id: PEAK_HOUR_DEMAND_PROFILE,
}
