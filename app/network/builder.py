"""Scenario (JSON) <-> NetworkGraph conversion.

The scenario file is the single human-readable source of every assumption:
provenance, seed, congestion parameters, speed ceiling, nodes and edges.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import SCENARIO_SCHEMA_VERSION, CongestionParameters
from app.errors import ScenarioError, SwarmNetworkError
from app.models.edge import Edge
from app.models.node import Node
from app.network.graph import NetworkGraph

logger = logging.getLogger(__name__)

REQUIRED_KEYS = ("schema_version", "scenario_id", "data_provenance", "assumptions", "simulation", "nodes", "edges")


@dataclass(frozen=True)
class ScenarioMetadata:
    scenario_id: str
    description: str
    data_provenance: str
    seed: int | None
    start_time_min: float
    congestion: CongestionParameters
    max_speed_kmh: float
    extra_assumptions: dict[str, Any] = field(default_factory=dict)


@dataclass
class LoadedScenario:
    metadata: ScenarioMetadata
    graph: NetworkGraph


def scenario_from_dict(data: dict[str, Any]) -> LoadedScenario:
    if not isinstance(data, dict):
        raise ScenarioError("scenario root must be a JSON object")
    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        raise ScenarioError(f"scenario is missing keys: {missing}")
    if data["schema_version"] != SCENARIO_SCHEMA_VERSION:
        raise ScenarioError(f"unsupported schema_version {data['schema_version']!r} "
                            f"(expected {SCENARIO_SCHEMA_VERSION})")
    assumptions = data["assumptions"]
    try:
        congestion = CongestionParameters.from_dict(assumptions["congestion_model"]["parameters"])
        max_speed = assumptions["network_max_speed_kmh"]
        metadata = ScenarioMetadata(
            scenario_id=str(data["scenario_id"]),
            description=str(data.get("description", "")),
            data_provenance=str(data["data_provenance"]),
            seed=data.get("seed"),
            start_time_min=float(data["simulation"]["start_time_min"]),
            congestion=congestion,
            max_speed_kmh=max_speed,
            extra_assumptions={k: v for k, v in assumptions.items()
                               if k not in ("congestion_model", "network_max_speed_kmh")},
        )
        graph = NetworkGraph(max_speed_kmh=max_speed)
        for index, raw in enumerate(data["nodes"]):
            _with_context(f"nodes[{index}]", lambda: graph.add_node(Node.from_dict(raw)))
        for index, raw in enumerate(data["edges"]):
            _with_context(f"edges[{index}]", lambda: graph.add_edge(Edge.from_dict(raw, congestion)))
    except ScenarioError:
        raise
    except (KeyError, TypeError) as exc:
        raise ScenarioError(f"malformed scenario: {exc!r}") from exc
    except SwarmNetworkError as exc:
        raise ScenarioError(str(exc)) from exc
    logger.info("loaded scenario %s: %d nodes, %d edges",
                metadata.scenario_id, graph.node_count(), graph.edge_count())
    return LoadedScenario(metadata=metadata, graph=graph)


def _with_context(where: str, action) -> None:
    try:
        action()
    except (SwarmNetworkError, KeyError, TypeError) as exc:
        raise ScenarioError(f"{where}: {exc}") from exc


def load_scenario(path: str | Path) -> LoadedScenario:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ScenarioError(f"cannot read scenario file {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ScenarioError(f"invalid JSON in {path}: {exc}") from exc
    return scenario_from_dict(data)


def dump_scenario(data: dict[str, Any]) -> str:
    """Canonical, deterministic JSON text for a scenario dict."""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def graph_to_edge_and_node_dicts(graph: NetworkGraph) -> tuple[list[dict], list[dict]]:
    return [n.to_dict() for n in graph.nodes()], [e.to_dict() for e in graph.edges()]
