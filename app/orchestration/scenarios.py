"""Named deterministic scenarios, and the evaluation set the orchestrator is judged on.

*** Every number an action produces comes from the M1-M5 engine. M6 chooses which
    run to ask for; it never computes a result itself. ***

A ``ScenarioSpec`` is a complete, reproducible description of a run: city seed,
demand profile, fleet size, passenger count, seeds and horizon. Running the same
spec twice gives the same fleet fingerprint, in the same process or another one —
``run_scenario`` is a thin composition of existing entry points
(``build_synthetic_city``, ``generate_fleet``, ``generate_demand``,
``RebalancingSimulation``) and adds no simulation logic of its own.

THE EVALUATION SET
------------------
Three fixed situations, built to exercise a decision rather than to flatter one:

``A_LARGE_DEFICIT``   demand concentrated where the pods are not. Rebalancing is the
                      obvious consideration.
``B_NO_DEFICIT``      supply comfortably ahead of forecast demand. ``NO_ACTION`` is
                      the correct consideration; acting would spend deadhead
                      kilometres for nothing.
``C_EXPENSIVE``       a real deficit, but the surplus pods are a long way from it and
                      the run has already spent heavily on deadhead. A smaller
                      bounded action, or none, is the defensible consideration.

``expected_consideration`` is written down for each, and it is exactly that — what a
reasonable operator would *consider*. It is asserted only against the mock provider,
whose rules are fixed and reviewable. A live model is recorded and read, never
asserted: a test suite that demands a particular sentence from a language model is
testing the weather.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.demand.generator import generate_demand
from app.demand.profile_io import resolve_demand_profile
from app.errors import OrchestrationConfigError
from app.fleet.config import DEFAULT_FLEET_CONFIG, FleetConfig
from app.fleet.generator import generate_fleet
from app.fleet.metrics import compute_fleet_metrics
from app.network.synthetic_city import build_synthetic_city
from app.rebalancing.config import DEFAULT_REBALANCING_CONFIG, RebalancingConfig
from app.rebalancing.metrics import compute_rebalancing_metrics
from app.rebalancing.simulation import RebalancingSimulation

DEFAULT_CITY_SEED = 42
DEFAULT_FLEET_SEED = 42
DEFAULT_DEMAND_SEED = 42


@dataclass(frozen=True)
class ScenarioSpec:
    """Everything needed to reproduce a run, and nothing that varies between runs."""

    scenario_id: str
    profile_name: str = "baseline"
    pods: int = 60
    passengers: int = 400
    city_seed: int = DEFAULT_CITY_SEED
    fleet_seed: int = DEFAULT_FLEET_SEED
    demand_seed: int = DEFAULT_DEMAND_SEED
    horizon_min: float | None = None
    enable_swarms: bool = True
    enable_rebalancing: bool = True
    algorithm: str = "astar"

    def __post_init__(self) -> None:
        for name in ("pods", "passengers"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise OrchestrationConfigError(f"{name} must be a positive integer, "
                                               f"got {value!r}")
        if self.horizon_min is not None and (isinstance(self.horizon_min, bool)
                                             or not isinstance(self.horizon_min, (int, float))
                                             or self.horizon_min <= 0):
            raise OrchestrationConfigError(
                f"horizon_min must be a positive number or None, got {self.horizon_min!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "profile_name": self.profile_name,
            "pods": self.pods,
            "passengers": self.passengers,
            "city_seed": self.city_seed,
            "fleet_seed": self.fleet_seed,
            "demand_seed": self.demand_seed,
            "horizon_min": self.horizon_min,
            "enable_swarms": self.enable_swarms,
            "enable_rebalancing": self.enable_rebalancing,
            "algorithm": self.algorithm,
        }


def build_simulation(spec: ScenarioSpec, fleet_config: FleetConfig = DEFAULT_FLEET_CONFIG,
                     rebalancing_config: RebalancingConfig = DEFAULT_REBALANCING_CONFIG,
                     ) -> RebalancingSimulation:
    """A fresh, unrun simulation for ``spec``. Composition only; no new mechanics."""
    profile = resolve_demand_profile(spec.profile_name)
    graph = build_synthetic_city(spec.city_seed).graph
    fleet = generate_fleet(graph, fleet_size=spec.pods, seed=spec.fleet_seed,
                           config=fleet_config, profile=profile)
    demand = generate_demand(graph, seed=spec.demand_seed, passenger_count=spec.passengers,
                             profile=profile)
    return RebalancingSimulation(
        graph, fleet, demand.trips, fleet_config, rebalancing_config=rebalancing_config,
        profile=profile, enable_swarms=spec.enable_swarms,
        enable_rebalancing=spec.enable_rebalancing, algorithm=spec.algorithm)


@dataclass(frozen=True)
class ScenarioResult:
    """One deterministic run's outcome, as plain values."""

    spec: ScenarioSpec
    end_time_min: float
    fleet_fingerprint: str
    trips_served: int
    unserved_trips: int
    average_wait_min: float | None
    average_completion_time_min: float | None
    pod_distance_km: float
    deadhead_distance_km: float
    total_energy_kwh: float
    completed_repositions: int
    final_total_deficit: float

    def to_dict(self) -> dict[str, Any]:
        data = {name: getattr(self, name) for name in self.__dataclass_fields__
                if name != "spec"}
        data["spec"] = self.spec.to_dict()
        return data


def run_scenario(spec: ScenarioSpec, *, max_ticks: int = 100_000,
                 fleet_config: FleetConfig = DEFAULT_FLEET_CONFIG,
                 rebalancing_config: RebalancingConfig = DEFAULT_REBALANCING_CONFIG,
                 ) -> ScenarioResult:
    """Run ``spec`` to completion (or to its horizon) and report the engine's numbers."""
    simulation = build_simulation(spec, fleet_config, rebalancing_config)
    simulation.run(max_ticks=max_ticks, until_min=spec.horizon_min)
    fleet_metrics = compute_fleet_metrics(simulation.fleet, simulation.records(),
                                          simulation.time_min)
    rebalancing_metrics = compute_rebalancing_metrics(simulation)
    return ScenarioResult(
        spec=spec,
        end_time_min=round(simulation.time_min, 4),
        fleet_fingerprint=simulation.snapshot().fingerprint(),
        trips_served=rebalancing_metrics.trips_served,
        unserved_trips=rebalancing_metrics.unserved_trips,
        average_wait_min=rebalancing_metrics.average_wait_min,
        average_completion_time_min=rebalancing_metrics.average_completion_time_min,
        pod_distance_km=rebalancing_metrics.pod_distance_km,
        deadhead_distance_km=rebalancing_metrics.reposition_distance_km,
        total_energy_kwh=fleet_metrics.total_energy_kwh,
        completed_repositions=rebalancing_metrics.completed_repositions,
        final_total_deficit=rebalancing_metrics.final_total_deficit,
    )


# ---------------------------------------------------------------------------
# The evaluation set
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EvaluationScenario:
    """A fixed situation, plus what a reasonable operator would consider doing.

    ``expected_consideration`` is a note for a reader and an assertion target for the
    **mock** provider only. Nothing here asserts what a live model must say.
    """

    name: str
    description: str
    expected_consideration: str
    build: Callable[[], RebalancingSimulation]
    warm_up_min: float

    def prepared(self) -> RebalancingSimulation:
        """The simulation advanced to the minute the situation is posed at."""
        simulation = self.build()
        if self.warm_up_min > 0:
            simulation.run(until_min=self.warm_up_min)
        return simulation

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description,
                "expected_consideration": self.expected_consideration,
                "warm_up_min": self.warm_up_min}


def _scenario_a() -> RebalancingSimulation:
    """A 20-pod fleet against 800 passengers: demand plainly outruns the fleet."""
    return build_simulation(ScenarioSpec(
        scenario_id="A_LARGE_DEFICIT", profile_name="baseline", pods=20, passengers=800))


def _scenario_b() -> RebalancingSimulation:
    """A 120-pod fleet against 40 passengers: supply far ahead of forecast demand."""
    return build_simulation(ScenarioSpec(
        scenario_id="B_NO_DEFICIT", profile_name="baseline", pods=120, passengers=40))


def _scenario_c() -> RebalancingSimulation:
    """A 110-pod fleet mid-evening: a real deficit, but half the driving is already empty."""
    return build_simulation(ScenarioSpec(
        scenario_id="C_EXPENSIVE", profile_name="baseline", pods=110, passengers=250))


EVALUATION_SCENARIOS: tuple[EvaluationScenario, ...] = (
    EvaluationScenario(
        name="A_LARGE_DEFICIT",
        description="20 pods against 800 passengers, at minute 420. The engine "
                    "forecasts roughly 26 pods short across a dozen nodes while only "
                    "about a fifth of driving so far has been empty, so moving pods "
                    "toward the shortfall is the obvious thing to weigh.",
        expected_consideration="REQUEST_REBALANCING",
        build=_scenario_a,
        warm_up_min=420.0,
    ),
    EvaluationScenario(
        name="B_NO_DEFICIT",
        description="120 pods against 40 passengers, at minute 240. Forecast demand is "
                    "met everywhere — the total deficit is zero — so there is nothing "
                    "for a repositioning move to fix. (This fleet is far too large for "
                    "its demand, and around 90% of its driving is already empty; the "
                    "deficit is what settles the decision, but the waste is real and "
                    "is reported rather than hidden.)",
        expected_consideration="NO_ACTION",
        build=_scenario_b,
        warm_up_min=240.0,
    ),
    EvaluationScenario(
        name="C_EXPENSIVE",
        description="110 pods against 250 passengers, at minute 560. There is a "
                    "genuine forecast deficit of about 4 pods, but close to half of "
                    "every kilometre driven so far has been deadhead. Chasing this "
                    "shortfall costs more empty driving than the service it buys, so a "
                    "smaller action — or none — is the defensible call.",
        expected_consideration="NO_ACTION or a smaller bounded action",
        build=_scenario_c,
        warm_up_min=560.0,
    ),
)


def evaluation_scenario(name: str) -> EvaluationScenario:
    for scenario in EVALUATION_SCENARIOS:
        if scenario.name == name:
            return scenario
    raise OrchestrationConfigError(
        f"unknown evaluation scenario {name!r}; known: "
        f"{[s.name for s in EVALUATION_SCENARIOS]}")
