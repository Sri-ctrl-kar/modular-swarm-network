"""Bridge from trip requests to M1 routing.

This module calls ``app.routing.find_route`` and stores what comes back. It does
NOT reimplement, wrap or tune any routing logic, and routing never imports this
module — the dependency points one way only (demand -> routing).

Because a route is computed from the graph's CURRENT vehicle counts, routing the
same trip after traffic changes correctly yields the new cost. Nothing is cached:
10,000 trips route in well under a second (measured), so M1's deliberate
"no route caching" decision stands.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

from app.demand.models import TripRequest
from app.errors import NoRouteError
from app.models.route import Route
from app.network.graph import NetworkGraph
from app.routing import find_route

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoutedTrip:
    """One trip request plus the route found for it (or why none was found).

    ``free_flow_travel_time_min`` sums the route's *base* edge times, i.e. the
    same path with no congestion. Comparing it with the route's
    ``total_travel_time_min`` shows what current traffic costs this trip.
    """

    trip: TripRequest
    route: Route | None
    free_flow_travel_time_min: float | None = None
    unroutable_reason: str | None = None

    @property
    def is_routed(self) -> bool:
        return self.route is not None

    @property
    def passenger_km(self) -> float:
        """Route distance multiplied by party size (0 when unroutable)."""
        return 0.0 if self.route is None else self.route.total_distance_km * self.trip.party_size

    @property
    def congestion_delay_min(self) -> float:
        """Travel time above free flow on this route (0 when unroutable)."""
        if self.route is None or self.free_flow_travel_time_min is None:
            return 0.0
        return self.route.total_travel_time_min - self.free_flow_travel_time_min

    def to_dict(self) -> dict:
        return {
            "trip": self.trip.to_dict(),
            "route": None if self.route is None else self.route.to_dict(),
            "free_flow_travel_time_min": self.free_flow_travel_time_min,
            "unroutable_reason": self.unroutable_reason,
        }


def free_flow_time_min(graph: NetworkGraph, route: Route) -> float:
    """Travel time of a route's edges at free flow (base times, no congestion)."""
    return sum(graph.get_edge(edge_id).base_travel_time_min for edge_id in route.edge_ids)


def route_trip(graph: NetworkGraph, trip: TripRequest, algorithm: str = "astar") -> RoutedTrip:
    """Route one trip under the graph's current traffic.

    A genuinely unreachable destination (``NoRouteError``) is recorded on the
    result rather than raised, because a demand set may legitimately contain
    trips the network cannot serve. Every other error — an unknown node id, an
    unknown algorithm — still propagates: those are input bugs, not demand facts.
    """
    try:
        route = find_route(graph, trip.origin_node_id, trip.destination_node_id, algorithm)
    except NoRouteError as exc:
        logger.debug("trip %s unroutable: %s", trip.trip_id, exc)
        return RoutedTrip(trip=trip, route=None, unroutable_reason=str(exc))
    return RoutedTrip(trip=trip, route=route, free_flow_travel_time_min=free_flow_time_min(graph, route))


def route_trips(graph: NetworkGraph, trips: Iterable[TripRequest],
                algorithm: str = "astar") -> tuple[RoutedTrip, ...]:
    """Route every trip in order. Input order is preserved, so the result is as
    deterministic as the trips and the network state that produced it."""
    routed = tuple(route_trip(graph, trip, algorithm) for trip in trips)
    unroutable = sum(1 for item in routed if not item.is_routed)
    if unroutable:
        logger.info("%d of %d trips could not be routed", unroutable, len(routed))
    return routed
