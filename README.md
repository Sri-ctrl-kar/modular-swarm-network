# Modular Swarm Network — Milestone 1

**Deterministic city + network simulation foundation.**

> ⚠️ **All data in this milestone is SYNTHETIC.** The city, coordinates, distances,
> capacities and traffic counts are invented. Nothing here claims real-world
> transportation performance.

## 1. What M1 does

M1 answers one question reliably and reproducibly:

> *Given an origin and a destination, what is the current fastest route through the city network?*

It provides validated node/edge models, a directed weighted graph with dynamic
(traffic-dependent) costs, a transparent congestion model, Dijkstra and A*
routing with a proven-admissible heuristic, a seeded synthetic city, a
human-readable scenario file, a minimal simulation state, and a CLI.

## 2. What M1 deliberately does NOT do

No passengers or demand (M2), no pods, platooning or splitting, no swarm
controller, no Gemini / LLM / agents, no demand prediction, no dashboard or
animation, no impact comparison, no route caching, no real map data.
The runtime uses **only the Python standard library** and works fully offline.

## 3. Architecture

```
                 ┌────────────────────────────┐
                 │  app/cli/main.py  (argparse)│   stdout = results, stderr = logs/errors
                 └──────────────┬─────────────┘
                                │
          ┌─────────────────────▼─────────────────────┐
          │ app/simulation/state.py  SimulationState   │  clock + traffic + snapshots
          └───────┬───────────────────────────┬───────┘
                  │                           │
   ┌──────────────▼─────────────┐   ┌─────────▼──────────────────────┐
   │ app/routing/               │   │ app/network/                   │
   │  dijkstra.py   astar.py    │──▶│  graph.py   NetworkGraph       │
   │  costs.py  (cost models,   │   │  geo.py     Haversine          │
   │   heuristics, proof)       │   │  builder.py JSON ⇄ graph       │
   └──────────────┬─────────────┘   │  synthetic_city.py (seeded)    │
                  │                 └─────────┬──────────────────────┘
          ┌───────▼───────────────────────────▼───────┐
          │ app/models/  Node · Edge · Route ·         │   pure, validated dataclasses
          │              NetworkSnapshot               │
          └───────────────────┬───────────────────────┘
                              │
          ┌───────────────────▼───────────────────────┐
          │ app/config.py (all assumptions)            │
          │ app/errors.py (typed exceptions)           │
          └────────────────────────────────────────────┘
   scenarios/baseline.json  ──(builder)──▶  NetworkGraph
```

Dependencies point downward only. Future layers (demand, pods, swarm
controller) should sit above `SimulationState` and consume it without changes
below.

## 4. Data models

| Model | Key fields | Validation |
|---|---|---|
| `Node` (frozen) | `node_id`, `name`, `latitude`, `longitude`, `node_type` ∈ {intersection, station, terminal} | non-empty id/name, finite lat ∈ [-90, 90], lon ∈ [-180, 180], known type |
| `Edge` | `edge_id`, `source`, `destination`, `distance_km`, `base_travel_time_min`, `capacity_vehicles_per_hour`, `current_vehicle_count`, `road_type` ∈ {local, arterial, trunk} | positive finite distance/time, integer capacity > 0, integer count ≥ 0, no self-loops |
| `Route` (frozen) | `origin`, `destination`, `node_ids`, `edge_ids`, `total_distance_km`, `total_travel_time_min`, `total_cost`, `cost_metric`, `nodes_visited`, `algorithm` | path consistency, non-negative totals |
| `NetworkSnapshot` (frozen) | `time_min`, `vehicle_counts` | stable SHA-256 `fingerprint()` |

Only `current_vehicle_count` is mutable, via `Edge.set_vehicle_count()`.
The graph additionally enforces two **geometry rules** on every edge:
road distance ≥ straight-line distance, and free-flow speed ≤ the network
ceiling (100 km/h). Node names must be unique (case-insensitive) so the CLI can
resolve them unambiguously.

## 5. Routing algorithms

Both use a binary heap and adjacency lists (O((V+E) log V)) and return the same
`Route` type. Heap ties break on node id, and adjacency preserves insertion
order, so results are deterministic. `nodes_visited` = nodes settled/closed.

**A\* heuristic:** `h(n) = haversine_km(n, goal) × 60 / 100 × 0.999` minutes.

*Admissibility proof:* for every edge, `current_time ≥ base_time ≥ road_km·60/V ≥
straight_km·60/V` (congestion multiplier ≥ 1; the two geometry rules). Since
great-circle distance obeys the triangle inequality, `h(u) ≤ cost(u,v) + h(v)`
(consistency), which implies `h` never overestimates. The 0.999 factor absorbs
floating-point noise. The distance cost model uses the same argument; custom
cost functions get the always-safe zero heuristic.

Errors are typed: `NodeNotFoundError`, `NoRouteError`, `InvalidCostError`
(zero/negative/NaN/∞ weights are rejected at search time as well as at model
construction).

**Geographic vs road distance:** Haversine distance is used *only* as a lower
bound for A*. Travel cost always comes from the edge's own road distance/time.

## 6. Congestion model

```
u = current_vehicle_count / capacity_vehicles_per_hour
if u <= 1:  m = 1 + α·u^β                     α = 0.15, β = 4   (classic BPR values)
if u >  1:  m = 1 + α + s·(u − 1)             s = 2.0          (project assumption)
current_travel_time = base_travel_time × m
```

Continuous at u = 1, monotonic, always ≥ 1 (so travel time is never negative
and never below free flow). Examples: u = 0.5 → ×1.009; u = 1 → ×1.15;
u = 1.5 → ×2.15; u = 3 → ×5.15. The overload slope is **not** empirically
calibrated. Parameters live in `app/config.py` and in the scenario file.

## 7. Synthetic city assumptions

22 nodes (9 stations, 3 terminals, 10 intersections) and 56 directed edges
(28 two-way links): an east–west **trunk** corridor
West Hub → Central Station → East Hub → Airport Interchange → Airport, arterial
alternatives (e.g. North Station → Ring Road North → Tech Park → Eastern Bypass → Airport),
and local streets. The network is strongly connected and not a grid.

Generation (`app/network/synthetic_city.py`, seed 42 by default): hand-authored
km layout around an arbitrary anchor (45.0°N, 10.0°E — not a real city);
seeded ±0.3 km jitter; road distance = straight-line × seeded detour factor
(local 1.30–1.45, arterial 1.20–1.30, trunk 1.05–1.12), rounded up; base time
= distance ÷ nominal speed (30 / 50 / 80 km/h), rounded up; capacity
600 / 1500 / 3600 veh/h; baseline counts at a seeded 15–55 % utilization.

`scenarios/baseline.json` is the exported result, including every assumption.
A test verifies it is byte-identical to the generator's output.

## 8. How to run

Python 3.11+. No installation is needed for the runtime.

```bash
python -m app.cli.main route --from "North Station" --to "Airport"
python -m app.cli.main route --from north_station --to airport --algorithm both
python -m app.cli.main route --from "North Station" --to "Airport" --traffic E009=4000 --traffic E011=3000
python -m app.cli.main route --from Market --to "Tech Park" --zero-traffic
python -m app.cli.main congestion-demo --from "North Station" --to "Airport" --utilization 3
python -m app.cli.main info
python -m app.cli.main route --from Market --to Airport --seed 7          # different synthetic city
python -m app.cli.main export-scenario --seed 42 --output scenarios/baseline.json
python -m app.cli.main --log-level INFO route --from Market --to Airport  # logs to stderr
```

Exit codes: `0` success, `1` no route, `2` invalid input or scenario,
`3` A*/Dijkstra cost mismatch (should never happen).

## 9. How to run tests

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest -q
```

64 test functions (105 cases with parametrization) across models, network,
routing, determinism and edge cases. Highlights: A* and Dijkstra costs match for
**all 462 ordered node pairs**, and again under random congestion; the heuristic is
checked for admissibility against true costs from every node to every goal.

## 10. Known limitations

* Synthetic data only; no calibration against real traffic.
* Vehicle counts are static inputs — there is no traffic propagation, queueing,
  signal timing, turn penalties or time-of-day variation.
* Travel time is computed at query time; a long trip does not see congestion
  change along the way (no time-dependent routing).
* The overload slope (2.0) is an assumption; BPR itself is a coarse model.
* Equal-cost ties may make A* and Dijkstra return different (equally optimal) paths.
* Haversine assumes a spherical Earth (sub-0.5 % error; irrelevant at city scale).
* No route caching (deferred by design).

## 11. Future milestones

* **M2** — passenger demand simulation
* **M3** — pod simulation
* Later — swarm formation, platooning / splitting on trunk corridors,
  predictive demand, AI-assisted control, dashboard, impact comparison.
