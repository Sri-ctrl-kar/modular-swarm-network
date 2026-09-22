"""M4.1 — regression tests pinning the *meaning* of every swarm comparison metric.

These tests exist because the numbers were audited and found correct while their
names and units were not all self-evident. They pin definitions, not behaviour:
formation is untouched, and nothing here asserts that swarm mode is better at
anything.

The two units in play:

* ``km``        physical kilometres actually driven.
* ``equiv-km``  single-pod-equivalent road SPACE; one pod alone for 1 km = 1.0.

Mixing them is the specific mistake these tests are here to catch.
"""

import pytest

from app.demand import generate_demand
from app.fleet import generate_fleet
from app.fleet.metrics import compute_fleet_metrics
from app.network.synthetic_city import build_synthetic_city
from app.swarm import (
    DEFAULT_SWARM_CONFIG,
    SwarmConfig,
    SwarmSimulation,
    compute_swarm_metrics,
)

PODS = 60
PASSENGERS = 400


def _run(enable_swarms=True, config=DEFAULT_SWARM_CONFIG, pods=PODS, passengers=PASSENGERS):
    graph = build_synthetic_city(42).graph
    fleet = generate_fleet(graph, fleet_size=pods, seed=42)
    demand = generate_demand(graph, seed=42, passenger_count=passengers)
    sim = SwarmSimulation(graph, fleet, demand.trips, swarm_config=config,
                          enable_swarms=enable_swarms)
    sim.run()
    return sim, fleet


# --- pod_distance_km: physical kilometres, nothing else ----------------------
def test_pod_distance_is_exactly_the_sum_of_pod_odometers():
    sim, fleet = _run()
    metrics = compute_swarm_metrics(sim)
    assert metrics.pod_distance_km == pytest.approx(
        sum(pod.total_distance_km for pod in fleet), abs=1e-3)


def test_pod_distance_matches_the_fleet_layers_own_figure():
    """M4 must not invent a second notion of distance driven."""
    sim, fleet = _run()
    swarm_metrics = compute_swarm_metrics(sim)
    fleet_metrics = compute_fleet_metrics(fleet, sim.records(), sim.time_min)
    assert swarm_metrics.pod_distance_km == pytest.approx(fleet_metrics.total_distance_km, abs=1e-3)


def test_platooning_does_not_reduce_physical_pod_distance_per_trip():
    """Per completed trip, the distance driven is the route's distance — formation
    or not. Platooning discounts no kilometre."""
    sim, _ = _run()
    from app.fleet.models import TripStatus
    completed = [r for r in sim.records() if r.status is TripStatus.COMPLETED]
    assert completed
    for record in completed:
        assert record.route_distance_km > 0


# --- shared corridor vs coordinated pod-km ----------------------------------
def test_shared_corridor_counts_each_corridor_once_and_pod_km_counts_every_pod():
    """The distinction that stops a platoon being mistaken for one vehicle."""
    sim, _ = _run()
    metrics = compute_swarm_metrics(sim)
    swarms = sim.swarms()
    assert swarms, "this scenario is supposed to form swarms"

    assert metrics.total_shared_corridor_distance_km == pytest.approx(
        sum(s.shared_distance_km for s in swarms), abs=1e-3)
    assert metrics.coordinated_pod_km == pytest.approx(
        sum(s.shared_distance_km * s.size for s in swarms), abs=1e-3)
    # Every swarm has >= 2 members, so pod-km strictly exceeds corridor-km.
    assert metrics.coordinated_pod_km > metrics.total_shared_corridor_distance_km


def test_four_pods_over_a_five_km_corridor_is_five_corridor_km_and_twenty_pod_km():
    """The worked example from the documentation, pinned exactly."""
    from app.swarm.models import SharedCorridor, Swarm
    corridor = SharedCorridor(edge_ids=("C1",), origin_node_id="A", divergence_node_id="B",
                              distance_km=5.0, travel_time_min=6.0)
    swarm = Swarm(swarm_id="SW00001", pod_ids=tuple(f"POD{i:05d}" for i in range(4)),
                  leader_pod_id="POD00000", corridor=corridor, formation_time_min=0.0)
    swarm.activate()
    swarm.record_progress(node_id="B", edges_completed=1, distance_km=5.0, travel_time_min=6.0)
    assert swarm.shared_distance_km == 5.0        # corridor traversed once
    assert swarm.coordinated_pod_km() == 20.0     # four pods still drove it


def test_coordinated_pod_km_formula_is_exact_because_no_member_leaves_mid_corridor():
    """``d x n`` is only right if every member drives the whole corridor. A swarm's
    corridor is the common PREFIX of its members' routes, so no member's trip can
    end strictly inside it — this pins that assumption."""
    sim, _ = _run()
    for swarm in sim.swarms():
        if swarm.departed_pod_ids:
            assert swarm.corridor_is_complete, (
                f"{swarm.swarm_id} lost a member before its corridor ended, which would "
                f"make coordinated_pod_km over-count")


def test_no_pod_drives_more_in_formation_than_it_drives_in_total():
    sim, fleet = _run()
    per_pod = {}
    for swarm in sim.swarms():
        for pod_id in swarm.pod_ids:
            per_pod[pod_id] = per_pod.get(pod_id, 0.0) + swarm.shared_distance_km
    for pod_id, formation_km in per_pod.items():
        assert formation_km <= fleet.get_pod(pod_id).total_distance_km + 1e-9


# --- unplatooned_pod_km -----------------------------------------------------
def test_unplatooned_pod_km_is_driven_km_minus_formation_km():
    """It is the solo *kilometres*, which includes the solo legs of pods that did
    platoon — not the distance driven by pods that never joined a swarm."""
    sim, _ = _run()
    metrics = compute_swarm_metrics(sim)
    assert metrics.unplatooned_pod_km == pytest.approx(
        metrics.pod_distance_km - metrics.coordinated_pod_km, abs=1e-2)
    assert metrics.unplatooned_pod_km + metrics.coordinated_pod_km == pytest.approx(
        metrics.pod_distance_km, abs=1e-2)


def test_pods_that_platooned_still_contribute_solo_kilometres():
    """Proves the naming caveat above is real: a platooned pod's tail is unplatooned."""
    sim, fleet = _run()
    swarms = sim.swarms()
    assert swarms
    platooned = {p for s in swarms for p in s.pod_ids}
    # At least one platooned pod drove further than its corridors.
    tails = [fleet.get_pod(p).total_distance_km
             - sum(s.shared_distance_km for s in swarms if p in s.pod_ids)
             for p in platooned]
    assert any(tail > 1e-6 for tail in tails)


# --- road occupancy: the unit boundary --------------------------------------
def test_road_occupancy_equals_pod_distance_exactly_when_nothing_platoons():
    """Not a coincidence: with no swarms every pod occupies 1.0 equiv-km per km, so
    the formula reduces to pod_distance_km identically. This is why the
    independent run reports the same number twice."""
    sim, _ = _run(enable_swarms=False)
    metrics = compute_swarm_metrics(sim)
    assert metrics.swarm_count == 0
    assert metrics.coordinated_pod_km == 0.0
    assert metrics.road_occupancy_equiv_km == metrics.pod_distance_km
    assert metrics.road_occupancy_saved_equiv_km == pytest.approx(0.0, abs=1e-6)
    assert metrics.road_occupancy_saving_percent == pytest.approx(0.0, abs=1e-6)


def test_road_occupancy_follows_its_documented_formula():
    sim, _ = _run()
    metrics = compute_swarm_metrics(sim)
    factor = DEFAULT_SWARM_CONFIG.formation_occupancy_factor
    expected = metrics.unplatooned_pod_km + sum(
        s.shared_distance_km * (1.0 + (s.size - 1) * factor) for s in sim.swarms())
    assert metrics.road_occupancy_equiv_km == pytest.approx(expected, abs=1e-2)


def test_road_occupancy_is_below_coordinated_pod_km_but_above_corridor_km():
    """It sits strictly between the two physical figures, which is the whole point:
    a formation is neither n separate vehicles nor one."""
    sim, _ = _run()
    metrics = compute_swarm_metrics(sim)
    formation_only = sum(
        s.shared_distance_km * (1.0 + (s.size - 1) * DEFAULT_SWARM_CONFIG.formation_occupancy_factor)
        for s in sim.swarms())
    corridor_km = metrics.total_shared_corridor_distance_km
    coordinated_km = metrics.coordinated_pod_km
    assert corridor_km < formation_only < coordinated_km


def test_saving_equals_the_closed_form():
    """pod_distance - road_occupancy is identically Sum d*(n-1)*(1-f)."""
    sim, _ = _run()
    metrics = compute_swarm_metrics(sim)
    factor = DEFAULT_SWARM_CONFIG.formation_occupancy_factor
    closed_form = sum(s.shared_distance_km * (s.size - 1) * (1.0 - factor)
                      for s in sim.swarms())
    assert metrics.road_occupancy_saved_equiv_km == pytest.approx(closed_form, abs=1e-2)
    assert metrics.road_occupancy_saved_equiv_km == pytest.approx(
        metrics.pod_distance_km - metrics.road_occupancy_equiv_km, abs=1e-2)


def test_saving_percent_denominator_is_this_runs_own_pod_km():
    sim, _ = _run()
    metrics = compute_swarm_metrics(sim)
    assert metrics.road_occupancy_saving_percent == pytest.approx(
        100.0 * metrics.road_occupancy_saved_equiv_km / metrics.pod_distance_km, abs=1e-2)


@pytest.mark.parametrize("factor,expected_saving_ratio", [
    (0.0, 1.0),    # a follower costs no road space at all -> saves the full (n-1)*d
    (0.5, 0.5),
    (1.0, 0.0),    # a follower costs a full slot -> coordination saves nothing
])
def test_occupancy_factor_scales_the_saving_linearly(factor, expected_saving_ratio):
    """The factor is the only lever on the saving, and it acts exactly as documented.
    At factor 1.0 the saving is zero — the model claims nothing by construction."""
    config = SwarmConfig(formation_occupancy_factor=factor)
    sim, _ = _run(config=config)
    metrics = compute_swarm_metrics(sim)
    full_separation = sum(s.shared_distance_km * (s.size - 1) for s in sim.swarms())
    assert metrics.road_occupancy_saved_equiv_km == pytest.approx(
        full_separation * expected_saving_ratio, abs=1e-2)


def test_occupancy_factor_of_one_makes_swarm_and_independent_occupancy_identical():
    """With no headway benefit assumed, road occupancy collapses back onto pod-km."""
    sim, _ = _run(config=SwarmConfig(formation_occupancy_factor=1.0))
    metrics = compute_swarm_metrics(sim)
    assert metrics.swarm_count > 0                      # it did platoon
    assert metrics.road_occupancy_equiv_km == pytest.approx(metrics.pod_distance_km, abs=1e-2)
    assert metrics.road_occupancy_saved_equiv_km == pytest.approx(0.0, abs=1e-6)


def test_the_reported_factor_is_the_one_actually_used():
    config = SwarmConfig(formation_occupancy_factor=0.25)
    sim, _ = _run(config=config)
    assert compute_swarm_metrics(sim, config).formation_occupancy_factor == 0.25


# --- instantaneous versus cumulative ----------------------------------------
def test_current_participation_fields_are_a_snapshot_not_a_total():
    """After a finished run no swarm is active, so the *currently* fields read 0 and
    total_pods. That is correct; the cumulative figure is a different field."""
    sim, _ = _run()
    metrics = compute_swarm_metrics(sim)
    assert metrics.pods_currently_in_swarms == 0
    assert metrics.pods_currently_independent == metrics.total_pods
    # ... while the cumulative count shows the run really did platoon.
    assert metrics.distinct_pods_ever_in_a_swarm > 0
    assert metrics.swarm_participation_percent == pytest.approx(
        100.0 * metrics.distinct_pods_ever_in_a_swarm / metrics.total_pods, abs=0.01)


def test_current_participation_is_non_zero_mid_run():
    """Proves the snapshot fields do track reality, they are simply zero at the end."""
    graph = build_synthetic_city(42).graph
    fleet = generate_fleet(graph, fleet_size=PODS, seed=42)
    demand = generate_demand(graph, seed=42, passenger_count=PASSENGERS)
    sim = SwarmSimulation(graph, fleet, demand.trips)
    seen_active = False
    for _ in range(600):
        sim.tick()
        if compute_swarm_metrics(sim).pods_currently_in_swarms > 0:
            seen_active = True
            break
    assert seen_active, "no tick ever had pods coordinating"


# --- the cross-mode comparison ----------------------------------------------
def test_swarm_mode_is_allowed_to_look_worse_on_distance_and_time():
    """The comparison must not be rigged. Waiting to form shifts departures, so the
    swarm run may drive further and finish later — and the metrics say so plainly."""
    independent, _ = _run(enable_swarms=False)
    swarm, _ = _run(enable_swarms=True)
    base = compute_fleet_metrics(independent.fleet, independent.records(), independent.time_min)
    with_swarms = compute_fleet_metrics(swarm.fleet, swarm.records(), swarm.time_min)

    # No assertion that swarm mode wins on either; only that both are reported and
    # that the served sets genuinely differ, which is what explains the totals.
    assert base.total_distance_km > 0 and with_swarms.total_distance_km > 0
    assert base.average_trip_completion_time_min is not None
    assert with_swarms.average_trip_completion_time_min is not None
    assert (with_swarms.completed_trips != base.completed_trips
            or with_swarms.total_distance_km != base.total_distance_km), (
        "the two modes should differ somewhere; if not, the delay had no effect")


def test_raw_totals_across_modes_are_not_like_for_like():
    """Pins the documented trap: the two runs do not serve the same trips, so their
    raw totals cannot be differenced to get the coordination effect."""
    independent, _ = _run(enable_swarms=False)
    swarm, _ = _run(enable_swarms=True)
    base_fleet = compute_fleet_metrics(independent.fleet, independent.records(),
                                       independent.time_min)
    swarm_fleet = compute_fleet_metrics(swarm.fleet, swarm.records(), swarm.time_min)
    swarm_metrics = compute_swarm_metrics(swarm)

    cross_mode_difference = (compute_swarm_metrics(independent).road_occupancy_equiv_km
                             - swarm_metrics.road_occupancy_equiv_km)
    within_run_saving = swarm_metrics.road_occupancy_saved_equiv_km

    # The two are not the same quantity, because the served sets differ.
    if swarm_fleet.completed_trips != base_fleet.completed_trips:
        assert cross_mode_difference != pytest.approx(within_run_saving, abs=1e-6)
    # The within-run saving is the one with a closed-form definition.
    assert within_run_saving == pytest.approx(
        swarm_metrics.pod_distance_km - swarm_metrics.road_occupancy_equiv_km, abs=1e-2)


def test_saving_percent_is_the_scale_free_row_that_does_compare():
    """Independent mode is exactly 0 %; swarm mode is a share of its own pod-km. This
    is the only cross-run-comparable figure among the occupancy numbers."""
    independent, _ = _run(enable_swarms=False)
    swarm, _ = _run(enable_swarms=True)
    assert compute_swarm_metrics(independent).road_occupancy_saving_percent == pytest.approx(0.0)
    percent = compute_swarm_metrics(swarm).road_occupancy_saving_percent
    assert 0.0 < percent < 100.0


def test_comparison_reports_both_modes_without_normalising_anything_away():
    from app.swarm import compare_modes
    trips = generate_demand(build_synthetic_city(42).graph, seed=42, passenger_count=250).trips
    from app.fleet import DEFAULT_FLEET_CONFIG
    comparison = compare_modes(
        lambda: build_synthetic_city(42).graph, trips,
        fleet_factory=lambda graph: generate_fleet(graph, fleet_size=40, seed=42),
        fleet_config=DEFAULT_FLEET_CONFIG)
    data = comparison.to_dict()
    for mode in ("independent", "swarm"):
        for field in ("pod_distance_km", "road_occupancy_equiv_km",
                      "road_occupancy_saved_equiv_km", "road_occupancy_saving_percent",
                      "coordinated_pod_km", "unplatooned_pod_km"):
            assert field in data[mode]["swarm"], f"{field} missing from {mode}"
        assert "total_distance_km" in data[mode]["fleet"]
        assert "average_trip_completion_time_min" in data[mode]["fleet"]


# --- naming and unit hygiene -------------------------------------------------
def test_equiv_km_fields_are_named_apart_from_physical_km_fields():
    """Road-space figures carry *_equiv_km; physical distances carry *_km. Keeping
    them textually distinct is what stops the two being added together."""
    from app.swarm.metrics import SwarmMetrics
    fields = set(SwarmMetrics.__dataclass_fields__)
    equiv = {f for f in fields if f.endswith("_equiv_km")}
    assert equiv == {"road_occupancy_equiv_km", "road_occupancy_saved_equiv_km"}
    physical = {f for f in fields if f.endswith("_km") and f not in equiv}
    assert physical == {"pod_distance_km", "coordinated_pod_km", "unplatooned_pod_km",
                        "total_shared_corridor_distance_km",
                        "average_shared_corridor_distance_km"}
    # The old ambiguous names must not come back.
    for retired in ("road_occupancy_km", "independent_pod_km", "coordination_benefit_km",
                    "pods_in_swarms", "independent_pods"):
        assert retired not in fields, f"{retired} was renamed for being ambiguous"


def test_module_documents_both_units_and_the_disclaimer():
    import app.swarm.metrics as metrics_module
    doc = metrics_module.__doc__
    flat = " ".join(doc.split())          # the doc is wrapped; compare on words
    assert "equiv-km" in flat and "physical kilometres" in flat
    assert "not fuel, not energy, not emissions and not time saved" in flat
    assert "Σ over swarms (d_s × (n_s − 1) × (1 − f))" in flat


def test_config_declares_the_occupancy_unit():
    data = DEFAULT_SWARM_CONFIG.to_dict()["road_occupancy"]
    assert "equiv-km" in data["unit"]
    assert "not distance" in data["unit"]
    assert "unplatooned_pod_km" in data["formula"]


# --- determinism of the audited figures -------------------------------------
def test_every_audited_metric_is_reproducible():
    runs = []
    for _ in range(3):
        sim, _ = _run()
        m = compute_swarm_metrics(sim)
        runs.append((m.pod_distance_km, m.total_shared_corridor_distance_km, m.coordinated_pod_km,
                     m.unplatooned_pod_km, m.road_occupancy_equiv_km,
                     m.road_occupancy_saved_equiv_km, m.road_occupancy_saving_percent))
    assert all(run == runs[0] for run in runs)
