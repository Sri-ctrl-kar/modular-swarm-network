"""Typed exception hierarchy shared by every layer.

Callers (CLI, future swarm controller) can catch ``SwarmNetworkError`` for
anything this package raises deliberately, or a narrower subclass.
"""

from __future__ import annotations


class SwarmNetworkError(Exception):
    """Base class for all deliberate errors raised by this package."""


class ModelValidationError(SwarmNetworkError, ValueError):
    """A data model (Node, Edge, parameters, snapshot) received invalid values."""


class GraphError(SwarmNetworkError):
    """Base class for network-graph errors."""


class DuplicateNodeError(GraphError):
    """A node id (or node name) is already present in the graph."""


class DuplicateEdgeError(GraphError):
    """An edge id is already present in the graph."""


class NodeNotFoundError(GraphError):
    """A referenced node id/name does not exist in the graph."""


class EdgeNotFoundError(GraphError):
    """A referenced edge id does not exist in the graph."""


class GeometryValidationError(GraphError, ValueError):
    """An edge is physically inconsistent with its endpoint coordinates.

    Raised when road distance is shorter than the straight-line distance, or when
    the implied free-flow speed exceeds the network speed ceiling. Both checks are
    what make the A* heuristic provably admissible.
    """


class RoutingError(SwarmNetworkError):
    """Base class for routing errors."""


class NoRouteError(RoutingError):
    """Origin and destination exist but the destination is unreachable."""


class InvalidCostError(RoutingError, ValueError):
    """A cost function produced a non-finite, zero or negative edge cost."""


class ScenarioError(SwarmNetworkError):
    """A scenario file is missing, malformed, or semantically invalid."""


class DemandError(SwarmNetworkError):
    """Base class for passenger-demand (M2) errors."""


class DemandProfileError(DemandError, ValueError):
    """A demand profile / demand scenario file is malformed or inconsistent."""


class DemandGenerationError(DemandError):
    """Demand generation received inputs it cannot satisfy deterministically."""
