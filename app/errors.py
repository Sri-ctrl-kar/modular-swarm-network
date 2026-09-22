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


class FleetError(SwarmNetworkError):
    """Base class for pod-fleet (M3) errors."""


class FleetConfigError(FleetError, ValueError):
    """A fleet configuration or initialisation profile is malformed."""


class PodStateError(FleetError):
    """An illegal pod state transition or an operation invalid for a pod's state."""


class PodNotFoundError(FleetError):
    """A referenced pod id does not exist in the fleet."""


class DuplicatePodError(FleetError):
    """A pod id is already present in the fleet."""


class AssignmentError(FleetError):
    """A trip cannot be assigned to the requested pod."""


class SwarmError(SwarmNetworkError):
    """Base class for swarm/platoon (M4) errors."""


class SwarmConfigError(SwarmError, ValueError):
    """A swarm configuration or compatibility threshold is malformed."""


class SwarmStateError(SwarmError):
    """An illegal swarm state transition, or an operation invalid for that state."""


class SwarmNotFoundError(SwarmError):
    """A referenced swarm id does not exist."""


class IncompatibleSwarmError(SwarmError):
    """Pods were asked to form a swarm that the compatibility rules reject."""


class RebalancingError(SwarmNetworkError):
    """Base class for adaptive fleet-rebalancing (M5) errors."""


class RebalancingConfigError(RebalancingError, ValueError):
    """A rebalancing configuration or forecast weighting is malformed."""


class ForecastError(RebalancingError):
    """A demand forecast could not be produced from the inputs given."""


class RepositioningError(RebalancingError):
    """A repositioning move cannot be dispatched or executed as requested."""


class ActionValidationError(RebalancingError, ValueError):
    """A proposed action failed the deterministic validator at the M6 boundary."""


class OrchestrationError(SwarmNetworkError):
    """Base class for AI-orchestration (M6) errors."""


class OrchestrationConfigError(OrchestrationError, ValueError):
    """An orchestration configuration value is malformed or outside its bounds."""


class ActionSchemaError(OrchestrationError, ValueError):
    """An AI action does not satisfy the structured action schema."""


class ProviderError(OrchestrationError):
    """An AI provider could not be used, or failed while producing a proposal."""


class ProviderConfigurationError(ProviderError, ValueError):
    """A provider was requested without what it needs — a missing API key, say.

    The message names the *environment variable*, never its value.
    """


class ProviderResponseError(ProviderError):
    """A provider returned something that is not a parseable structured action."""


class ExecutionRefusedError(OrchestrationError):
    """Execution was attempted without an APPROVED validator verdict."""
