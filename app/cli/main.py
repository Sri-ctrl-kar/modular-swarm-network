"""CLI for Milestones 1, 2, 3 and 4.

Examples:
    python -m app.cli.main route --from "North Station" --to "Airport"
    python -m app.cli.main route --from north_station --to airport --algorithm both
    python -m app.cli.main route --from "North Station" --to "Airport" --traffic E007=4000
    python -m app.cli.main congestion-demo --from "North Station" --to "Airport"
    python -m app.cli.main info
    python -m app.cli.main export-scenario --seed 42 --output scenarios/baseline.json
    python -m app.cli.main demand-demo --profile baseline --passengers 1000
    python -m app.cli.main demand-demo --profile peak_hour --passengers 2000 --no-routing
    python -m app.cli.main fleet-demo --pods 100 --passengers 1000
    python -m app.cli.main swarm-demo --pods 100 --passengers 1000

Results go to stdout; logs and errors go to stderr. Exit codes: 0 ok,
1 no route, 2 invalid input / scenario error.
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
from dataclasses import replace
from pathlib import Path
from typing import Sequence

from app.config import DEFAULT_SCENARIO_PATH, DEFAULT_SEED
from app.demand import (
    DEFAULT_DEMAND_SEED,
    buckets_within_horizon,
    compute_metrics,
    generate_demand,
    resolve_demand_profile,
    route_trips,
)
from app.errors import ModelValidationError, NoRouteError, SwarmNetworkError
from app.fleet import (
    DEFAULT_FLEET_CONFIG,
    DEFAULT_FLEET_SEED,
    FleetSimulation,
    compute_fleet_metrics,
    generate_fleet,
)
from app.models.route import Route
from app.network.builder import LoadedScenario, dump_scenario, load_scenario
from app.network.synthetic_city import build_synthetic_city, generate_synthetic_city_dict
from app.routing import ALGORITHM_LABELS, find_route
from app.simulation.state import SimulationState
from app.swarm import (
    DEFAULT_SWARM_CONFIG,
    SurplusDeficitRebalancer,
    SwarmSimulation,
    compute_swarm_metrics,
)

BANNER = "MODULAR SWARM NETWORK\n====================="


def _load(args: argparse.Namespace) -> LoadedScenario:
    if args.seed is not None:
        return build_synthetic_city(args.seed)
    return load_scenario(args.scenario)


def _parse_traffic(items: Sequence[str]) -> list[tuple[str, int]]:
    parsed = []
    for item in items:
        edge_id, sep, raw = item.partition("=")
        if not sep or not edge_id.strip():
            raise ModelValidationError(f"--traffic expects EDGE_ID=COUNT, got {item!r}")
        try:
            parsed.append((edge_id.strip(), int(raw)))
        except ValueError:
            raise ModelValidationError(f"--traffic count must be an integer, got {raw!r}") from None
    return parsed


def format_route(route: Route, state: SimulationState) -> str:
    graph = state.graph
    lines = [
        "Route",
        f"Origin: {graph.get_node(route.origin).name}",
        f"Destination: {graph.get_node(route.destination).name}",
        "",
        f"Algorithm: {ALGORITHM_LABELS.get(route.algorithm, route.algorithm)}",
        f"Total distance: {route.total_distance_km:.2f} km",
        f"Travel time: {route.total_travel_time_min:.2f} minutes",
        f"Nodes visited: {route.nodes_visited}",
        "",
        "Path:",
        graph.get_node(route.node_ids[0]).name,
    ]
    for edge_id, node_id in zip(route.edge_ids, route.node_ids[1:]):
        e = graph.get_edge(edge_id)
        lines.append(
            f"    ↓  {edge_id} {e.road_type.value}, {e.distance_km:.2f} km, "
            f"{e.current_travel_time_min:.2f} min (load {e.utilization:.0%})"
        )
        lines.append(graph.get_node(node_id).name)
    return "\n".join(lines)


def _header(scenario: LoadedScenario, state: SimulationState) -> str:
    return (f"{BANNER}\n\nScenario: {scenario.metadata.scenario_id} "
            f"[SYNTHETIC DATA]  sim time: {state.time_min:.1f} min\n")


def cmd_route(args: argparse.Namespace) -> int:
    scenario = _load(args)
    state = SimulationState.from_scenario(scenario)
    if args.zero_traffic:
        state.clear_traffic()
    for edge_id, count in _parse_traffic(args.traffic):
        state.set_vehicle_count(edge_id, count)
    origin = state.graph.resolve_node(args.origin).node_id
    destination = state.graph.resolve_node(args.destination).node_id

    algorithms = ["astar", "dijkstra"] if args.algorithm == "both" else [args.algorithm]
    routes = [find_route(state.graph, origin, destination, a) for a in algorithms]
    print(_header(scenario, state))
    print("\n\n".join(format_route(r, state) for r in routes))
    if len(routes) == 2:
        match = math.isclose(routes[0].total_cost, routes[1].total_cost, rel_tol=1e-12, abs_tol=1e-9)
        print(f"\nOptimal cost match (A* vs Dijkstra): {'YES' if match else 'NO'}")
        return 0 if match else 3
    return 0


def cmd_congestion_demo(args: argparse.Namespace) -> int:
    scenario = _load(args)
    state = SimulationState.from_scenario(scenario)
    origin = state.graph.resolve_node(args.origin).node_id
    destination = state.graph.resolve_node(args.destination).node_id
    before = find_route(state.graph, origin, destination, args.algorithm)
    print(_header(scenario, state))
    print("BEFORE congestion\n-----------------")
    print(format_route(before, state))
    for edge_id in before.edge_ids:
        state.set_utilization(edge_id, args.utilization)
    print(f"\nApplied utilization {args.utilization:.0%} to every edge of the original route: "
          f"{', '.join(before.edge_ids) or '(none)'}\n")
    after = find_route(state.graph, origin, destination, args.algorithm)
    print("AFTER congestion\n----------------")
    print(format_route(after, state))
    changed = after.edge_ids != before.edge_ids
    print(f"\nRoute changed: {'YES' if changed else 'NO (no faster alternative exists)'}")
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    scenario = _load(args)
    graph = scenario.graph
    stats = graph.statistics()
    report = graph.validate_connectivity()
    print(BANNER + "\n")
    print(f"Scenario: {scenario.metadata.scenario_id}")
    print(f"Provenance: {scenario.metadata.data_provenance}")
    print(f"Seed: {scenario.metadata.seed}")
    for key, value in stats.items():
        print(f"{key}: {value}")
    print(f"strongly_connected: {report.is_strongly_connected}")
    print(f"weakly_connected: {report.is_weakly_connected}")
    print("\nNodes:")
    for node in graph.nodes():
        print(f"  {node.node_id:<20} {node.name:<22} {node.node_type.value}")
    return 0


def cmd_demand_demo(args: argparse.Namespace) -> int:
    """Generate synthetic demand and summarise it (M2). No vehicles are simulated."""
    scenario = _load(args)
    state = SimulationState.from_scenario(scenario)
    profile = resolve_demand_profile(args.profile)
    demand = generate_demand(
        state.graph,
        seed=args.demand_seed,
        passenger_count=args.passengers,
        profile=profile,
        trips_per_passenger=args.trips_per_passenger,
        horizon_min=args.horizon_min,
    )
    routed = None if args.no_routing else route_trips(state.graph, demand.trips, args.algorithm)
    metrics = compute_metrics(demand, routed, top_n=args.top)
    snapshot = demand.snapshot()
    graph = state.graph

    print(_header(scenario, state))
    print("Demand [SYNTHETIC — not calibrated to real-world mobility data]")
    print("-------------------------------------------------------------")
    print(f"Demand profile: {profile.profile_id} (seed {demand.seed})")
    print(f"  {profile.description}")
    print(f"Passengers: {metrics.total_passengers}")
    print(f"Trip requests: {metrics.total_trip_requests}")
    print(f"Total passenger demand: {metrics.total_passenger_volume}")
    print(f"Average party size: {metrics.average_party_size}")
    print(f"Unique origins / destinations: {metrics.unique_origins} / {metrics.unique_destinations}")
    print(f"OD pairs with demand: {metrics.od_pairs_used}")

    print(f"\nTop {args.top} OD pairs (by passengers):")
    for origin, destination, passengers in metrics.top_od_pairs:
        trips = demand.matrix.trips_for(origin, destination)
        print(f"  {graph.get_node(origin).name:<20} -> {graph.get_node(destination).name:<20} "
              f"{passengers:>5} passengers  ({trips} trips)")

    horizon_note = "" if args.horizon_min is None else f", clipped to the first {args.horizon_min:g} min"
    print(f"\nDemand by time bucket (passengers{horizon_note}):")
    trips_by_bucket = dict(metrics.trips_by_time_bucket)
    # The buckets the generator actually drew from, so --horizon-min is reported honestly.
    effective = {b.name: b for b in buckets_within_horizon(profile, args.horizon_min)}
    for name, passengers in metrics.demand_by_time_bucket:
        windows = " ".join(f"{int(s)//60:02d}:{int(s)%60:02d}-{int(e)//60:02d}:{int(e)%60:02d}"
                           for s, e in effective[name].windows)
        marker = "  <- peak" if name == metrics.peak_bucket else ""
        print(f"  {name:<14} {passengers:>6} passengers  {trips_by_bucket.get(name, 0):>6} trips  "
              f"[{windows}]{marker}")

    if routed is None:
        print("\nRouting: skipped (--no-routing)")
    else:
        print(f"\nRouting ({ALGORITHM_LABELS.get(args.algorithm, args.algorithm)}, current traffic):")
        print(f"  Routed trips: {metrics.routed_trips}")
        print(f"  Unroutable trips: {metrics.unroutable_trips}")
        print(f"  Average route distance: {metrics.average_route_distance_km} km")
        print(f"  Average travel time: {metrics.average_route_travel_time_min} min")
        print(f"  Average free-flow time: {metrics.average_free_flow_travel_time_min} min")

    print(f"\nDemand fingerprint: {snapshot.fingerprint()}")
    return 0


def cmd_fleet_demo(args: argparse.Namespace) -> int:
    """Run the pod fleet against generated demand (M3).

    Pods are independent vehicles here: swarm/platoon formation is deferred to M4.
    """
    scenario = _load(args)
    state = SimulationState.from_scenario(scenario)
    graph = state.graph
    profile = resolve_demand_profile(args.profile)
    config = DEFAULT_FLEET_CONFIG
    if args.capacity is not None:
        config = replace(config, default_pod_capacity=args.capacity)
    if args.tick_minutes is not None:
        config = replace(config, tick_minutes=args.tick_minutes)

    demand = generate_demand(graph, seed=args.demand_seed, passenger_count=args.passengers,
                             profile=profile)
    fleet = generate_fleet(graph, fleet_size=args.pods, seed=args.fleet_seed,
                           config=config, profile=profile)
    simulation = FleetSimulation(graph, fleet, demand.trips, config,
                                 algorithm=args.algorithm)
    run = simulation.run(max_ticks=args.max_ticks)
    metrics = compute_fleet_metrics(fleet, simulation.records(), simulation.time_min)

    print(_header(scenario, state))
    print("Pod fleet [SYNTHETIC — approximated energy model, not physics]")
    print("-------------------------------------------------------------")
    print("Pods are independent in M3; swarm/platoon formation is deferred to M4.")
    print(f"\nFleet: {metrics.total_pods} pods (seed {args.fleet_seed})")
    print(f"Capacity: {config.default_pod_capacity} seats per pod "
          f"({metrics.total_capacity} seats total)")
    print(f"Demand profile: {profile.profile_id} (seed {args.demand_seed})")
    print(f"Trips: {metrics.total_trips}")
    print(f"Simulated: {run.ticks} ticks of {config.tick_minutes:g} min "
          f"-> minute {run.end_time_min:g} ({run.stopped_because})")

    print("\nTrips")
    print(f"  Assigned and completed: {metrics.completed_trips}"
          f"  ({'n/a' if metrics.completion_rate is None else format(metrics.completion_rate, '.1%')})")
    print(f"  Unassigned (no pod at origin in time): {metrics.unassigned_trips}")
    print(f"  Unroutable (no path exists): {metrics.unroutable_trips}")
    print(f"  Average wait before pickup: {metrics.average_trip_wait_time_min} min")
    print(f"  Average completion time: {metrics.average_trip_completion_time_min} min")

    print("\nFleet activity")
    print(f"  Passengers carried: {metrics.total_passengers_carried}")
    print(f"  Average occupancy: {metrics.average_passenger_occupancy} of "
          f"{config.default_pod_capacity} seats"
          f"  ({'n/a' if metrics.average_occupancy_rate is None else format(metrics.average_occupancy_rate, '.1%')})")
    print(f"  Average pod utilization (driving time): "
          f"{'n/a' if metrics.average_pod_utilization is None else format(metrics.average_pod_utilization, '.1%')}")
    print(f"  Distance travelled: {metrics.total_distance_km} km")
    print(f"  Driving time: {metrics.total_travel_time_min} min")

    print("\nEnergy (approximation)")
    print(f"  Consumed: {metrics.total_energy_kwh} kWh")
    print(f"  Average: {metrics.average_energy_per_km} kWh/km")
    batteries = [pod.battery_percent for pod in fleet.pods()]
    if batteries:
        print(f"  Battery: min {min(batteries):.1f}%  mean {sum(batteries) / len(batteries):.1f}%")

    print("\nFinal fleet state")
    for status, count in sorted(fleet.count_by_status().items()):
        print(f"  {status:<10} {count}")

    print(f"\nFleet fingerprint: {simulation.snapshot().fingerprint()}")
    return 0


def cmd_swarm_demo(args: argparse.Namespace) -> int:
    """Form platoons from compatible pod journeys and compare against M3 (M4).

    Formation is deterministic and rule-based — no AI/LLM is involved. A swarm is
    a coordination layer over individual pods, not a single vehicle.
    """
    profile = resolve_demand_profile(args.profile)
    fleet_config = DEFAULT_FLEET_CONFIG
    if args.capacity is not None:
        fleet_config = replace(fleet_config, default_pod_capacity=args.capacity)
    swarm_config = DEFAULT_SWARM_CONFIG
    if args.formation_delay is not None:
        swarm_config = replace(swarm_config, max_formation_delay_min=args.formation_delay)
    if args.max_swarm_size is not None:
        swarm_config = replace(swarm_config, max_swarm_size=args.max_swarm_size)

    # Both runs must start from an untouched network and fleet, so build each
    # from scratch: only the swarm switch differs.
    def fresh():
        scenario = _load(args)
        state = SimulationState.from_scenario(scenario)
        demand = generate_demand(state.graph, seed=args.demand_seed,
                                 passenger_count=args.passengers, profile=profile)
        fleet = generate_fleet(state.graph, fleet_size=args.pods, seed=args.fleet_seed,
                               config=fleet_config, profile=profile)
        return scenario, state, demand, fleet

    scenario, state, demand, _ = fresh()
    runs = {}
    for enabled in (False, True):
        _, run_state, run_demand, run_fleet = fresh()
        simulation = SwarmSimulation(run_state.graph, run_fleet, run_demand.trips, fleet_config,
                                     swarm_config=swarm_config, enable_swarms=enabled,
                                     algorithm=args.algorithm)
        report = simulation.run(max_ticks=args.max_ticks)
        runs[enabled] = (simulation, run_fleet, report,
                         compute_fleet_metrics(run_fleet, simulation.records(), simulation.time_min),
                         compute_swarm_metrics(simulation, swarm_config))

    swarm_sim, swarm_fleet, swarm_report, swarm_fleet_m, swarm_m = runs[True]
    base_sim, base_fleet, base_report, base_fleet_m, base_m = runs[False]

    print(_header(scenario, state))
    print("Swarm formation and platooning [SYNTHETIC — deterministic, rule-based]")
    print("---------------------------------------------------------------------")
    print("A swarm is a coordination layer over individual pods, not one vehicle.")
    print("Pods keep their own passengers, battery and route. No AI/LLM is involved.")

    print(f"\nFleet: {swarm_fleet_m.total_pods} pods x {fleet_config.default_pod_capacity} seats"
          f"   Demand: {profile.profile_id}, {swarm_fleet_m.total_trips} trips")
    print(f"Active trips served: {swarm_fleet_m.completed_trips}")
    print(f"Thresholds: >= {swarm_config.min_shared_edges} shared edges, "
          f">= {swarm_config.min_shared_distance_km:g} km, "
          f">= {swarm_config.min_shared_route_fraction:.0%} of each journey, "
          f"<= {swarm_config.max_swarm_size} pods, "
          f"wait <= {swarm_config.max_formation_delay_min:g} min")

    print("\nSwarms")
    print(f"  Swarms formed: {swarm_m.swarm_count}")
    print(f"  Splits at divergence: {swarm_m.split_count}")
    print(f"  Average size: {swarm_m.average_swarm_size} pods   Largest: {swarm_m.max_swarm_size} pods")
    print(f"  Pods that platooned at least once: {swarm_m.distinct_pods_ever_in_a_swarm}"
          f" of {swarm_m.total_pods} ({swarm_m.swarm_participation_percent}%)")
    print(f"  Average shared corridor: {swarm_m.average_shared_corridor_distance_km} km "
          f"over {swarm_m.average_shared_corridor_edges} edges")
    print(f"  Total shared corridor: {swarm_m.total_shared_corridor_distance_km} km")
    print(f"  Average time in formation: {swarm_m.average_swarm_duration_min} min")

    if swarm_sim.swarms():
        print(f"\nExample swarms (of {swarm_m.swarm_count})")
        for swarm in swarm_sim.swarms()[:args.examples]:
            names = " ".join(swarm.pod_ids)
            print(f"  {swarm.swarm_id}  {names}  (leader {swarm.leader_pod_id})")
            print(f"    corridor {' -> '.join(swarm.corridor.edge_ids)}"
                  f"  {swarm.corridor.distance_km:.2f} km")
            print(f"    from {state.graph.get_node(swarm.corridor.origin_node_id).name}"
                  f" to {state.graph.get_node(swarm.corridor.divergence_node_id).name}"
                  f"  then each pod continues alone")

    print("\nIndependent (M3) vs swarm (M4) — same network, demand, fleet, seed and horizon")
    print(f"  {'metric':<34}{'independent':>13}{'swarm':>13}")
    rows = [
        ("pods that platooned", base_m.distinct_pods_ever_in_a_swarm, swarm_m.distinct_pods_ever_in_a_swarm),
        ("swarms formed", base_m.swarm_count, swarm_m.swarm_count),
        ("trips completed", base_fleet_m.completed_trips, swarm_fleet_m.completed_trips),
        ("pod distance driven (km)", base_fleet_m.total_distance_km, swarm_fleet_m.total_distance_km),
        ("energy used (kWh)", base_fleet_m.total_energy_kwh, swarm_fleet_m.total_energy_kwh),
        ("road occupancy (km, est.)", base_m.road_occupancy_km, swarm_m.road_occupancy_km),
        ("shared corridor (km)", base_m.total_shared_corridor_distance_km,
         swarm_m.total_shared_corridor_distance_km),
        ("avg wait before pickup (min)", base_fleet_m.average_trip_wait_time_min,
         swarm_fleet_m.average_trip_wait_time_min),
        ("avg completion time (min)", base_fleet_m.average_trip_completion_time_min,
         swarm_fleet_m.average_trip_completion_time_min),
    ]
    for label, left, right in rows:
        print(f"  {label:<34}{left!s:>13}{right!s:>13}")

    print(f"\n  Estimated road space freed by coordination: "
          f"{swarm_m.coordination_benefit_km} km "
          f"({swarm_m.road_occupancy_saving_percent}% of pod-km)")
    print(f"  Assumption: a following pod in formation needs "
          f"{swarm_config.formation_occupancy_factor:.0%} of an independent pod's road space.")
    print("  This is a ROAD-SPACE estimate only. Pod distance, travel time and energy are")
    print("  unchanged by platooning above — no fuel, energy or emissions saving is claimed.")

    requests = SurplusDeficitRebalancer(max_requests=args.rebalance_preview).plan(
        swarm_fleet, swarm_sim.records(), swarm_sim.graph)
    print(f"\nRebalancing hook: {len(requests)} reposition request(s) planned, none executed.")
    for request in requests:
        print(f"  {request.request_id}  {request.pod_id}  "
              f"{state.graph.get_node(request.from_node_id).name} -> "
              f"{state.graph.get_node(request.to_node_id).name}  (unserved: {request.priority})")
    print("  M4 exposes the interface only; adaptive rebalancing belongs to M5.")

    print(f"\nSwarm fingerprint: {swarm_sim.swarm_snapshot().fingerprint()}")
    print(f"Fleet fingerprint: {swarm_sim.snapshot().fingerprint()}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    text = dump_scenario(generate_synthetic_city_dict(args.seed))
    if args.output == "-":
        sys.stdout.write(text)
    else:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"wrote {args.output}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.cli.main", description="Modular Swarm Network — M1 + M2 + M3 + M4 CLI")
    parser.add_argument("--log-level", default="WARNING",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="log level (logs go to stderr)")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_source(p: argparse.ArgumentParser) -> None:
        group = p.add_mutually_exclusive_group()
        group.add_argument("--scenario", default=str(DEFAULT_SCENARIO_PATH), help="scenario JSON path")
        group.add_argument("--seed", type=int, default=None, help="generate synthetic city from seed instead")

    route = sub.add_parser("route", help="compute the fastest route")
    route.add_argument("--from", dest="origin", required=True, help="origin node name or id")
    route.add_argument("--to", dest="destination", required=True, help="destination node name or id")
    route.add_argument("--algorithm", choices=["astar", "dijkstra", "both"], default="astar")
    route.add_argument("--traffic", action="append", default=[], metavar="EDGE_ID=COUNT",
                       help="override an edge's vehicle count (repeatable)")
    route.add_argument("--zero-traffic", action="store_true", help="start from free-flow conditions")
    add_source(route)
    route.set_defaults(func=cmd_route)

    demo = sub.add_parser("congestion-demo", help="congest the best route and re-route")
    demo.add_argument("--from", dest="origin", required=True)
    demo.add_argument("--to", dest="destination", required=True)
    demo.add_argument("--algorithm", choices=["astar", "dijkstra"], default="astar")
    demo.add_argument("--utilization", type=float, default=3.0)
    add_source(demo)
    demo.set_defaults(func=cmd_congestion_demo)

    info = sub.add_parser("info", help="network statistics")
    add_source(info)
    info.set_defaults(func=cmd_info)

    demand = sub.add_parser("demand-demo", help="generate synthetic passenger demand and summarise it")
    demand.add_argument("--profile", default="baseline",
                        help="built-in profile name (baseline, peak_hour) or a demand JSON path")
    demand.add_argument("--passengers", type=int, default=1000, help="number of synthetic passengers")
    demand.add_argument("--trips-per-passenger", type=int, default=1)
    demand.add_argument("--demand-seed", type=int, default=DEFAULT_DEMAND_SEED,
                        help="seed for demand generation (independent of the city seed)")
    demand.add_argument("--horizon-min", type=float, default=None,
                        help="clip demand to the first N minutes of the day")
    demand.add_argument("--algorithm", choices=["astar", "dijkstra"], default="astar")
    demand.add_argument("--no-routing", action="store_true", help="skip routing the generated trips")
    demand.add_argument("--top", type=int, default=5, help="how many OD pairs to list")
    add_source(demand)
    demand.set_defaults(func=cmd_demand_demo)

    fleet = sub.add_parser("fleet-demo", help="run the pod fleet against generated demand")
    fleet.add_argument("--pods", type=int, default=100, help="fleet size")
    fleet.add_argument("--capacity", type=int, default=None, help="seats per pod")
    fleet.add_argument("--passengers", type=int, default=1000, help="synthetic passengers to generate")
    fleet.add_argument("--profile", default="baseline",
                       help="demand profile name (baseline, peak_hour) or a demand JSON path")
    fleet.add_argument("--fleet-seed", type=int, default=DEFAULT_FLEET_SEED)
    fleet.add_argument("--demand-seed", type=int, default=DEFAULT_DEMAND_SEED)
    fleet.add_argument("--tick-minutes", type=float, default=None, help="simulation tick length")
    fleet.add_argument("--max-ticks", type=int, default=100_000)
    fleet.add_argument("--algorithm", choices=["astar", "dijkstra"], default="astar")
    add_source(fleet)
    fleet.set_defaults(func=cmd_fleet_demo)

    swarm = sub.add_parser("swarm-demo", help="form platoons and compare with independent pods")
    swarm.add_argument("--pods", type=int, default=100, help="fleet size")
    swarm.add_argument("--capacity", type=int, default=None, help="seats per pod")
    swarm.add_argument("--passengers", type=int, default=1000)
    swarm.add_argument("--profile", default="baseline",
                       help="demand profile name (baseline, peak_hour) or a demand JSON path")
    swarm.add_argument("--fleet-seed", type=int, default=DEFAULT_FLEET_SEED)
    swarm.add_argument("--demand-seed", type=int, default=DEFAULT_DEMAND_SEED)
    swarm.add_argument("--formation-delay", type=float, default=None,
                       help="minutes a pod waits at its origin for compatible partners")
    swarm.add_argument("--max-swarm-size", type=int, default=None)
    swarm.add_argument("--examples", type=int, default=3, help="how many example swarms to print")
    swarm.add_argument("--rebalance-preview", type=int, default=3,
                       help="how many planned reposition requests to show")
    swarm.add_argument("--max-ticks", type=int, default=100_000)
    swarm.add_argument("--algorithm", choices=["astar", "dijkstra"], default="astar")
    add_source(swarm)
    swarm.set_defaults(func=cmd_swarm_demo)

    export = sub.add_parser("export-scenario", help="write the synthetic city as scenario JSON")
    export.add_argument("--seed", type=int, default=DEFAULT_SEED)
    export.add_argument("--output", default="-", help="file path or '-' for stdout")
    export.set_defaults(func=cmd_export)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level, stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        return args.func(args)
    except NoRouteError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except SwarmNetworkError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except BrokenPipeError:  # e.g. output piped into `head`
        sys.stderr.close()
        return 0


if __name__ == "__main__":
    sys.exit(main())
