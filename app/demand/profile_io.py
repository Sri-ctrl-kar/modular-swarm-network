"""Demand profile (demand scenario) file I/O.

A demand scenario file is the human-readable source of every demand assumption,
exactly as ``scenarios/baseline.json`` is for the network. The shipped files are
exports of the profiles in ``app/demand/config.py``, and a test asserts they stay
byte-identical to those profiles — so edit the config and re-export rather than
hand-editing the JSON.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.config import PROJECT_ROOT
from app.demand.config import DEMAND_PROFILES, DemandProfile
from app.errors import DemandProfileError

DEMAND_SCENARIO_DIR = PROJECT_ROOT / "scenarios"
BASELINE_DEMAND_PATH = DEMAND_SCENARIO_DIR / "demand_baseline.json"
PEAK_HOUR_DEMAND_PATH = DEMAND_SCENARIO_DIR / "demand_peak_hour.json"

DEMAND_SCENARIO_PATHS = {
    "baseline": BASELINE_DEMAND_PATH,
    "peak_hour": PEAK_HOUR_DEMAND_PATH,
}


def dump_demand_profile(profile: DemandProfile) -> str:
    """Canonical, deterministic JSON text for a demand profile."""
    if not isinstance(profile, DemandProfile):
        raise DemandProfileError(f"expected a DemandProfile, got {type(profile).__name__}")
    return json.dumps(profile.to_dict(), indent=2, ensure_ascii=False) + "\n"


def load_demand_profile(path: str | Path) -> DemandProfile:
    """Read and validate a demand profile from a JSON file."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DemandProfileError(f"cannot read demand profile {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DemandProfileError(f"invalid JSON in {path}: {exc}") from exc
    return DemandProfile.from_dict(data)


def resolve_demand_profile(reference: str) -> DemandProfile:
    """Accept a built-in profile name ('baseline', 'peak_hour') or a file path."""
    if reference in DEMAND_PROFILES:
        return DEMAND_PROFILES[reference]
    path = Path(reference)
    if path.exists():
        return load_demand_profile(path)
    raise DemandProfileError(
        f"unknown demand profile {reference!r}; use a file path or one of "
        f"{sorted(DEMAND_PROFILES)}"
    )
