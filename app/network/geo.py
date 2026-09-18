"""Geographic utilities.

IMPORTANT DISTINCTION
---------------------
* Geographic (straight-line) distance: the great-circle distance between two
  coordinates, computed here with the Haversine formula. It ignores roads.
* Road distance: ``Edge.distance_km``, the length a vehicle actually drives.

Road distance is always >= geographic distance (enforced by the graph). Routing
uses geographic distance ONLY as a lower bound for the A* heuristic, never as a
travel cost.
"""

from __future__ import annotations

import math

from app.config import EARTH_RADIUS_KM
from app.models.node import Node


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float,
                 radius_km: float = EARTH_RADIUS_KM) -> float:
    """Great-circle distance in km between two (lat, lon) points in degrees."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    a = min(1.0, max(0.0, a))  # clamp float noise
    return 2.0 * radius_km * math.asin(math.sqrt(a))


def node_distance_km(a: Node, b: Node) -> float:
    """Straight-line (geographic) distance between two nodes."""
    return haversine_km(a.latitude, a.longitude, b.latitude, b.longitude)
