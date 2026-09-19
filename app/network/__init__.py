"""Graph structure, geography and scenario construction."""

from app.network.builder import LoadedScenario, ScenarioMetadata, load_scenario, scenario_from_dict
from app.network.geo import haversine_km, node_distance_km
from app.network.graph import ConnectivityReport, NetworkGraph
from app.network.synthetic_city import build_synthetic_city, generate_synthetic_city_dict

__all__ = [
    "LoadedScenario", "ScenarioMetadata", "load_scenario", "scenario_from_dict",
    "haversine_km", "node_distance_km", "ConnectivityReport", "NetworkGraph",
    "build_synthetic_city", "generate_synthetic_city_dict",
]
