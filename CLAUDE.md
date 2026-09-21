# Modular Swarm Network — working notes for Claude Code

Read `README.md` first: it documents the architecture, the congestion formula, the
A* admissibility proof and the synthetic-city assumptions.

## Status

Milestones 1, 1.1, 2, 3 and 4 are complete and green: 428 test functions /
614 parametrized cases, all passing.

* **M1** — deterministic city + network foundation.
* **M1.1** — `tests/test_route_switch_regression.py`: congestion can change the
  optimal route, and restoring traffic restores it.
* **M2** — `app/demand/`: deterministic synthetic passenger demand — passengers,
  trip requests, an OD matrix, time-of-day profiles and routing integration.
* **M3** — `app/fleet/`: deterministic pod fleet — pods, assignment, discrete-tick
  movement, an approximated battery model and fleet metrics. Pods are
  **independent**; grouping lives in M4.
* **M4** — `app/swarm/`: deterministic rule-based swarm formation, platoon
  movement, splitting at divergence, swarm metrics, an independent-vs-swarm
  comparison, and a **rebalancing hook only**.

Next milestone is **M5: adaptive fleet rebalancing** — not started. Do not
implement adaptive rebalancing, magnetic linking, LLM integration or a dashboard
yet.

## Commands

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # pytest only; runtime is stdlib-only
pytest -q
python -m app.cli.main route --from "North Station" --to "Airport" --algorithm both
python -m app.cli.main congestion-demo --from "North Station" --to "Airport"
python -m app.cli.main info
python -m app.cli.main export-scenario --seed 42 --output scenarios/baseline.json
python -m app.cli.main demand-demo --profile baseline --passengers 1000
python -m app.cli.main demand-demo --profile peak_hour --passengers 2000 --no-routing
python -m app.cli.main fleet-demo --pods 100 --passengers 1000
python -m app.cli.main swarm-demo --pods 100 --passengers 1000
```

## Invariants — do not break these

1. **Stdlib only at runtime.** No Gemini/OpenAI/Anthropic SDKs, no Streamlit,
   FastAPI, NetworkX, requests, DB or external APIs. pytest is test-only.
2. **Determinism.** Same seed → byte-identical scenario JSON; same input →
   identical `Route`. Tie-breaking is by node id; adjacency keeps insertion
   order. `scenarios/baseline.json` must stay byte-identical to
   `generate_synthetic_city_dict(42)` (a test enforces this) — regenerate it
   with `export-scenario` rather than hand-editing. The same applies to
   `scenarios/demand_*.json`, which are exports of the profiles in
   `app/demand/config.py`: edit the config and re-export, never the JSON.
   Demand generation and fleet initialisation use a local `random.Random(seed)`
   and never the global `random` module; every weighted draw walks a key-sorted
   list so results never depend on dict or graph insertion order. The fleet tick
   loop contains **no RNG at all** — fleet init is M3's only seeded step. Trips
   are processed in `trip_id` order and pods in `pod_id` order.
3. **A\* heuristic stays admissible.** It relies on two graph rules enforced in
   `NetworkGraph.add_edge`: road distance ≥ straight-line distance, and
   free-flow speed ≤ `NETWORK_MAX_SPEED_KMH`. Congestion multipliers are always
   ≥ 1. Changing any of these means redoing the proof in `app/routing/costs.py`.
4. **Assumptions live in `app/config.py`** and the scenario file, not inside
   algorithms. No magic numbers in routing or graph code.
5. **Layering:** models ← network ← routing ← simulation ← demand ← fleet ←
   swarm ← cli. Never import downward-to-upward: nothing below a layer may import
   `app.demand`, `app.fleet` or `app.swarm` (tests walk the lower layers to
   enforce all three).
6. **Typed errors only** (`app/errors.py`); the CLI turns them into one clean
   line on stderr with exit code 1 (no route) or 2 (invalid input).
7. **Synthetic data must stay labelled as synthetic.** No real-world
   performance claims. Demand carries the stronger wording it needs: it is a
   synthetic model for simulation and is **not calibrated to real-world city
   mobility data**. The fleet's battery and charging behaviour must stay labelled
   an **approximation, not a physically calibrated model**.
8. **Demand does not reach down.** `app/demand/` consumes `SimulationState`,
   `NetworkGraph` and `find_route` as they are. It must not reimplement routing,
   cache routes, or change the congestion model or M1's `Node`/`Edge`. The
   demand layer's `PlaceRole` classification exists precisely so that adding
   land-use information does not require touching M1.
9. **The fleet does not reach down either.** `app/fleet/` uses M2's
   `TripRequest` and `route_trip` and M1's edge costs unchanged. It defines **no
   second cost model and no second congestion model**: travel time comes from
   `edge.current_travel_time_min` and energy scales by
   `edge.congestion_multiplier`. Edge costs are sampled when a pod **enters** an
   edge and are fixed for that traversal — both directions of that rule are
   pinned by tests, so do not "improve" it without updating them.
10. **`PodStatus` still has exactly five members** (idle, assigned, traveling,
   arrived, charging) and a test asserts that set. M4 deliberately added no pod
   state: swarm membership lives in `app/swarm/`, which is why M1-M3 needed no
   edit for it. Keep it that way unless there is a reason M4 did not have.
11. **The swarm layer reaches down too, and no further.** `app/swarm/` uses M3's
   `advance_pod`, M2's routes and M1's edge costs unchanged; it defines **no
   third cost or congestion model**. Formation is rule-based — explicit numeric
   thresholds, no AI/LLM, no scoring model, no randomness. Platooning must never
   grant a distance, time or energy discount: the coordination benefit is a
   **road-space estimate** resting on `formation_occupancy_factor`, and no
   aerodynamic, fuel or emissions saving may be claimed anywhere.
12. **Rebalancing stays a hook.** `app/swarm/rebalancing.py` plans and returns
   `RepositionRequest`s; nothing in M4 executes them, and `plan()` must stay
   read-only. Adaptive rebalancing is M5.

## The M2 demand layer

`app/demand/` sits on top of `SimulationState` and is organised as:

| Module | Holds |
|---|---|
| `config.py` | every demand assumption: `PlaceRole`, `TimeBucket`, `DemandProfile`, the baseline and peak_hour profiles |
| `models.py` | `Passenger`, `TripRequest`, `ODCell`, `DemandMatrix`, `DemandSnapshot` |
| `generator.py` | `DemandGenerator` / `generate_demand`, the seeded draw and `buckets_within_horizon` |
| `trip_routing.py` | `RoutedTrip`, `route_trip(s)` — calls M1 routing, stores the result |
| `metrics.py` | `DemandMetrics`, `compute_metrics` |
| `profile_io.py` | load / dump / resolve demand profiles |

The RNG consumption order is fixed and documented in `generator.py`; changing it
changes every generated scenario, so treat it as part of the contract.

## The M3 fleet layer

`app/fleet/` sits on top of `app/demand/` and is organised as:

| Module | Holds |
|---|---|
| `config.py` | every fleet assumption: capacity, tick length, the energy model, charging thresholds, `depot_role_weights` |
| `models.py` | `Pod`, `PodStatus`, `ALLOWED_TRANSITIONS`, `TripRecord`, `TripStatus`, `FleetSnapshot` |
| `pod_fleet.py` | `PodFleet` — membership, pod_id-ordered listing, status counts |
| `generator.py` | `generate_fleet`, `pod_id_for`, `depot_weights` (seeded placement) |
| `assignment.py` | `assign_trip` and the eligibility policy (lowest eligible `pod_id`) |
| `movement.py` | `advance_pod`, `start_pod_travel`, `edge_sample` |
| `simulation.py` | `FleetSimulation` — the fixed 9-step tick order |
| `metrics.py` | `FleetMetrics`, `compute_fleet_metrics` |

The tick order in `simulation.py` is part of the behavioural contract, as is the
"sampled at entry" rule in `movement.py`. Two deliberate simplifications to know
before changing anything: a pod is only eligible for trips starting at the node
it already occupies (**no repositioning**), and `max_trip_wait_min` is what makes
a run terminate instead of waiting forever.

## The M4 swarm layer

`app/swarm/` sits on top of `app/fleet/` and is organised as:

| Module | Holds |
|---|---|
| `config.py` | every threshold: the five compatibility rules, the two timing windows, `formation_occupancy_factor` |
| `models.py` | `SharedCorridor`, `Swarm`, `SwarmStatus`, `ALLOWED_SWARM_TRANSITIONS`, `SwarmSnapshot` |
| `compatibility.py` | `shared_edge_prefix`, `build_corridor`, `check_group` — the single implementation of the rules |
| `formation.py` | `plan_formation` (pure planning), `build_swarms`, `swarm_id_for` |
| `movement.py` | `advance_swarm` — lockstep platoon movement over M3's `advance_pod` |
| `simulation.py` | `SwarmSimulation`, a `FleetSimulation` subclass overriding two steps |
| `metrics.py` | `SwarmMetrics`, `compute_swarm_metrics`, `compare_modes` |
| `rebalancing.py` | `RepositionRequest`, `FleetRebalancer`, `SurplusDeficitRebalancer` — hook only |

`SwarmSimulation` extends `FleetSimulation` and overrides exactly two steps —
`_depart_assigned_pods` (splitting + formation + the formation delay) and
`_advance_travelling_pods` (platoon movement) — so M3's charging, assignment,
records, timeout and battery model are inherited untouched. `enable_swarms=False`
reproduces M3 exactly, and a test pins that against `FleetSimulation` itself;
keep it true, because the baseline comparison depends on it.

Two contracts to know before changing anything: edge costs are sampled when a pod
**enters** an edge (inherited from M3), and `advance_swarm` steps to edge
boundaries so a formation stops exactly at its divergence node instead of
overrunning it.

The dominant limitation is still M3's: pods only platoon when already co-located,
so on the seed-42 city just 28 swarms form, nearly all of size 2.
`max_formation_delay_min` is the one threshold that moves that number much.

## When starting M5

Adaptive rebalancing is the obvious next step, and M4 already defines its
interface: implement `FleetRebalancer.plan` properly and give something the right
to *execute* the requests. That execution is new behaviour — a pod driving empty —
so it needs its own state or trip representation, its own tests, and honest
accounting for the empty kilometres it adds. Do not change `app/swarm/` to do it;
build above it as every milestone so far has.
