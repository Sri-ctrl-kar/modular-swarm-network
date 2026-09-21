# Modular Swarm Network — working notes for Claude Code

Read `README.md` first: it documents the architecture, the congestion formula, the
A* admissibility proof and the synthetic-city assumptions.

## Status

Milestones 1, 1.1, 2 and 3 are complete and green: 327 test functions /
480 parametrized cases, all passing.

* **M1** — deterministic city + network foundation.
* **M1.1** — `tests/test_route_switch_regression.py`: congestion can change the
  optimal route, and restoring traffic restores it.
* **M2** — `app/demand/`: deterministic synthetic passenger demand — passengers,
  trip requests, an OD matrix, time-of-day profiles and routing integration.
* **M3** — `app/fleet/`: deterministic pod fleet — pods, assignment, discrete-tick
  movement, an approximated battery model and fleet metrics. Pods are
  **independent**; there is no grouping.

Next milestone is **M4: swarm formation / platooning** — not started. Do not
implement swarm formation, platooning, magnetic linking, LLM integration or a
dashboard yet.

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
5. **Layering:** models ← network ← routing ← simulation ← demand ← fleet ← cli.
   Never import downward-to-upward; in particular routing must never import
   `app.demand`, and nothing below the fleet may import `app.fleet` (tests walk
   the lower layers to enforce both).
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
10. **No swarm concepts in M3.** `PodStatus` has exactly five members
   (idle, assigned, traveling, arrived, charging) and a test asserts that set.
   Grouping, platooning and magnetic linking belong to M4.

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

## When starting M4

Swarm formation consumes pods and their routes without modifying `app/fleet/`,
the same way the fleet consumes M2. Put it in a new package above the fleet with
its own tests. Grouping will need new pod states; add them to
`ALLOWED_TRANSITIONS` deliberately and update the test that pins the current set.
The pipeline is `demand → trips → routing → pod grouping → swarm formation`; M3
ends at independent pod movement.
