"""CLI for Milestone 1.

Examples:
    python -m app.cli.main route --from "North Station" --to "Airport"
    python -m app.cli.main route --from north_station --to airport --algorithm both
    python -m app.cli.main route --from "North Station" --to "Airport" --traffic E007=4000
    python -m app.cli.main congestion-demo --from "North Station" --to "Airport"
    python -m app.cli.main info
    python -m app.cli.main export-scenario --seed 42 --output scenarios/baseline.json

Results go to stdout; logs and errors go to stderr. Exit codes: 0 ok,
1 no route, 2 invalid input / scenario error.
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
from pathlib import Path
from typing import Sequence

from app.config import DEFAULT_SCENARIO_PATH, DEFAULT_SEED
from app.errors import ModelValidationError, NoRouteError, SwarmNetworkError
from app.models.route import Route
from app.network.builder import LoadedScenario, dump_scenario, load_scenario
from app.network.synthetic_city import build_synthetic_city, generate_synthetic_city_dict
from app.routing import ALGORITHM_LABELS, find_route
from app.simulation.state import SimulationState

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


def cmd_export(args: argparse.Namespace) -> int:
    text = dump_scenario(generate_synthetic_city_dict(args.seed))
    if args.output == "-":
        sys.stdout.write(text)
    else:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"wrote {args.output}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.cli.main", description="Modular Swarm Network — M1 CLI")
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
