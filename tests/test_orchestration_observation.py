"""M6: the observation layer — determinism, fingerprints, and what it refuses to expose."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from app.orchestration import (
    OrchestrationConfig,
    build_observation,
    canonical_json,
    fingerprint_of,
)


# --- determinism -------------------------------------------------------------
def test_the_same_state_gives_the_same_observation(ai_simulation):
    first = build_observation(ai_simulation)
    second = build_observation(ai_simulation)
    assert first.to_dict() == second.to_dict()
    assert first.fingerprint() == second.fingerprint()


def test_the_observation_is_byte_identical_across_repeated_builds(ai_simulation):
    renders = {build_observation(ai_simulation).to_json() for _ in range(5)}
    assert len(renders) == 1


def test_building_an_observation_changes_nothing(ai_simulation):
    """Read-only means read-only: the engine is untouched by being looked at."""
    before = ai_simulation.snapshot().fingerprint()
    traffic = {e.edge_id: e.current_vehicle_count for e in ai_simulation.graph.edges()}
    batteries = {p.pod_id: p.battery_percent for p in ai_simulation.fleet}

    for _ in range(3):
        build_observation(ai_simulation)

    assert ai_simulation.snapshot().fingerprint() == before
    assert {e.edge_id: e.current_vehicle_count for e in ai_simulation.graph.edges()} == traffic
    assert {p.pod_id: p.battery_percent for p in ai_simulation.fleet} == batteries


def test_the_fingerprint_moves_when_the_simulation_does(ai_simulation):
    before = build_observation(ai_simulation).fingerprint()
    ai_simulation.run(until_min=ai_simulation.time_min + 60.0)
    assert build_observation(ai_simulation).fingerprint() != before


def test_two_identical_runs_produce_the_same_fingerprint():
    from app.demand import generate_demand
    from app.fleet import generate_fleet
    from app.network.synthetic_city import build_synthetic_city
    from app.rebalancing import RebalancingSimulation

    fingerprints = set()
    for _ in range(2):
        graph = build_synthetic_city(42).graph
        fleet = generate_fleet(graph, fleet_size=25, seed=42)
        demand = generate_demand(graph, seed=42, passenger_count=150)
        simulation = RebalancingSimulation(graph, fleet, demand.trips)
        simulation.run(until_min=180.0)
        fingerprints.add(build_observation(simulation).fingerprint())
    assert len(fingerprints) == 1


def test_separate_processes_agree_under_different_hash_seeds():
    """Python's string hashing is randomised per process; the observation must not be."""
    script = (
        "from app.network.synthetic_city import build_synthetic_city;"
        "from app.demand import generate_demand;"
        "from app.fleet import generate_fleet;"
        "from app.rebalancing import RebalancingSimulation;"
        "from app.orchestration import build_observation;"
        "g=build_synthetic_city(42).graph;"
        "f=generate_fleet(g, fleet_size=25, seed=42);"
        "d=generate_demand(g, seed=42, passenger_count=150);"
        "s=RebalancingSimulation(g, f, d.trips);"
        "s.run(until_min=180.0);"
        "print(build_observation(s).fingerprint())"
    )
    outputs = set()
    for seed in ("0", "1", "2"):
        completed = subprocess.run([sys.executable, "-c", script], capture_output=True,
                                   text=True, env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"},
                                   cwd=str(__import__("app.config", fromlist=["x"]).PROJECT_ROOT))
        assert completed.returncode == 0, completed.stderr
        outputs.add(completed.stdout.strip())
    assert len(outputs) == 1, outputs


# --- what the observation may contain ----------------------------------------
_PLAIN = (str, int, float, bool, type(None))


def _assert_plain(value, path="observation"):
    if isinstance(value, _PLAIN):
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_plain(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            assert isinstance(key, str), f"{path} has a non-string key {key!r}"
            _assert_plain(item, f"{path}.{key}")
        return
    raise AssertionError(f"{path} is a {type(value).__name__}, not a plain value")


def test_the_observation_holds_plain_values_only(ai_observation):
    _assert_plain(ai_observation.to_dict())


def test_no_live_engine_object_is_reachable(ai_observation):
    """No graph, fleet, pod, swarm or simulation may be reachable from an observation."""
    from app.fleet.models import Pod
    from app.fleet.pod_fleet import PodFleet
    from app.network.graph import NetworkGraph
    from app.rebalancing.simulation import RebalancingSimulation
    from app.swarm.models import Swarm

    forbidden = (NetworkGraph, PodFleet, Pod, Swarm, RebalancingSimulation)

    def walk(value):
        assert not isinstance(value, forbidden), f"a {type(value).__name__} escaped"
        assert not callable(value), f"a callable escaped: {value!r}"
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    walk(ai_observation.to_dict())


def test_the_observation_survives_a_json_round_trip(ai_observation):
    data = ai_observation.to_dict()
    assert json.loads(json.dumps(data)) == data


def test_the_fingerprint_is_the_sha256_of_the_canonical_form(ai_observation):
    assert ai_observation.fingerprint() == fingerprint_of(ai_observation.to_dict())
    assert ai_observation.to_json() == canonical_json(ai_observation.to_dict())
    assert len(ai_observation.fingerprint()) == 64


def test_no_wall_clock_leaks_into_the_observation(ai_simulation):
    """The only clock is the simulation's own. Real time must not appear."""
    import time

    first = build_observation(ai_simulation).to_dict()
    time.sleep(0.01)
    assert build_observation(ai_simulation).to_dict() == first
    assert first["time_min"] == pytest.approx(ai_simulation.time_min)


# --- content ------------------------------------------------------------------
def test_every_engine_layer_is_represented(ai_observation):
    data = ai_observation.to_dict()
    assert set(data) == {"time_min", "network", "demand", "fleet", "swarm", "forecast",
                         "rebalancing", "metrics", "engine_fingerprints", "provenance"}
    assert set(data["metrics"]) == {"fleet", "swarm", "rebalancing"}


def test_the_battery_distribution_accounts_for_every_pod(ai_observation, ai_simulation):
    buckets = ai_observation.fleet["battery_distribution_percent_buckets"]
    assert sum(buckets.values()) == len(ai_simulation.fleet)
    assert list(buckets) == sorted(buckets)


def test_eligible_pods_agree_with_the_engines_own_rule(ai_observation, ai_simulation):
    from app.rebalancing.eligibility import eligible_pods

    def in_swarm(pod_id):
        return ai_simulation.swarm_of_pod(pod_id) is not None

    assert ai_observation.fleet["idle_eligible_pods"] == \
        len(eligible_pods(ai_simulation.fleet, in_swarm))


def test_deficit_nodes_are_ordered_worst_first(ai_observation):
    deficits = [row["deficit"] for row in ai_observation.demand["top_deficit_nodes"]]
    assert deficits == sorted(deficits, reverse=True)


def test_the_top_n_setting_bounds_every_listing(ai_simulation):
    narrow = build_observation(ai_simulation, OrchestrationConfig(top_nodes=2, top_edges=2,
                                                                  top_swarms=2))
    assert len(narrow.demand["top_deficit_nodes"]) <= 2
    assert len(narrow.network["most_congested_edges"]) <= 2
    assert len(narrow.fleet["pods_at_busiest_nodes"]) <= 2


def test_congested_edges_are_ordered_by_load(ai_observation):
    loads = [edge["utilization_percent"]
             for edge in ai_observation.network["most_congested_edges"]]
    assert loads == sorted(loads, reverse=True)


def test_the_headline_names_the_problem(ai_observation):
    headline = ai_observation.headline()
    assert str(ai_observation.time_min) in headline or "minute" in headline
    if ai_observation.demand["total_deficit"] > 0:
        assert ai_observation.deficit_node_ids()[0] in headline


def test_the_provenance_marks_the_data_synthetic(ai_observation):
    assert "SYNTHETIC" in ai_observation.provenance
    assert "orchestrator" in ai_observation.provenance or \
           "propose" in ai_observation.provenance


def test_the_observation_is_frozen(ai_observation):
    with pytest.raises(Exception):
        ai_observation.time_min = 0.0


def test_canonical_json_rejects_non_finite_numbers():
    with pytest.raises(ValueError):
        canonical_json({"value": float("inf")})
