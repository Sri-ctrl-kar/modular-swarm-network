"""Immutable snapshot of dynamic network state (time + per-edge vehicle counts)."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any

from app.errors import ModelValidationError


@dataclass(frozen=True)
class NetworkSnapshot:
    time_min: float
    vehicle_counts: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        if isinstance(self.time_min, bool) or not isinstance(self.time_min, (int, float)) \
                or not math.isfinite(self.time_min) or self.time_min < 0:
            raise ModelValidationError(f"time_min must be a finite number >= 0, got {self.time_min!r}")
        seen: set[str] = set()
        for edge_id, count in self.vehicle_counts:
            if edge_id in seen:
                raise ModelValidationError(f"duplicate edge id in snapshot: {edge_id!r}")
            seen.add(edge_id)
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ModelValidationError(f"vehicle count for {edge_id!r} must be an int >= 0")

    def as_dict(self) -> dict[str, Any]:
        return {"time_min": self.time_min, "vehicle_counts": dict(self.vehicle_counts)}

    def fingerprint(self) -> str:
        """Stable SHA-256 of the canonical JSON form (for reproducibility checks)."""
        canonical = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
