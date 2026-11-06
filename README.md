# Modular Swarm Network — Milestones 1 through 7.2

**A fully deterministic city, demand, fleet, platooning and adaptive-rebalancing
simulation (M1-M5); an AI orchestration layer that can only propose bounded actions
to it (M6); and a Visual Command Center — a standard-library HTTP adapter and a
React frontend — that watches and drives it (M7, M7.2).**

There is no machine learning and no LLM anywhere in the engine. M6 adds an optional
orchestrator *above* it — **Gemini is an orchestrator, not the simulation engine**
(§12) — and the engine runs exactly as before with no provider at all. M7 adds a
screen on top of that; it reads the engine and sends operator inputs, and computes
nothing of its own (§13).

Tagged `v0.7.2-demo` at commit `12515ae`.

> ⚠️ **All data in this project is SYNTHETIC.** The city, coordinates, distances,
> capacities and traffic counts are invented. Nothing here is calibrated against
> real traffic, real mobility data or any real city, and nothing here claims
> real-world transportation performance.

> ⚠️ **Without `$GEMINI_API_KEY` everything runs in MOCK GEMINI mode**, which is a
> fixed arithmetic rule standing in for a model — not a language model, and not a
> prediction of what Gemini would choose (§12.7, §13.5). The UI labels which mode is
> live, and no live Gemini call has been made from this repository's own runs.

## 1. What this project does

M1 answers one question reliably and reproducibly:

> *Given an origin and a destination, what is the current fastest route through the city network?*

M2 adds a second:

> *Given a city network, where are people trying to travel, when are they travelling, and how much demand exists between origins and destinations?*

M3 adds a third:

> *Given that demand, which pods serve which trips, how do they move through the network, and what does that cost in time and energy?*

M4 adds a fourth:

> *Which of those pods have compatible enough journeys to travel as a coordinated platoon, where do they split, and what does coordinating actually change?*

M5 adds a fifth, and answers the limitation M3 exposed:

> *Demand has moved. Which idle pods should drive empty toward where it is going, what does that repositioning cost, and is it worth it?*

M6 adds a sixth, and is the first layer that is not deterministic end to end:

> *Given a read-only snapshot of all of the above, which bounded action is worth proposing — and does a deterministic validator agree the engine may take it?*

M7 and M7.2 add a seventh, which is a presentation question rather than a modelling one:

> *Can an operator watch all of this happen, drive it, and see what the engine actually did — without the screen computing, inventing or smoothing anything?*

It provides validated node/edge models, a directed weighted graph with dynamic
(traffic-dependent) costs, a transparent congestion model, Dijkstra and A*
routing with a proven-admissible heuristic, a seeded synthetic city, a
human-readable scenario file, a minimal simulation state, and a CLI.

## 2. What this project deliberately does NOT do

No magnetic linking, no route caching, no real map data, no cloud services, no
database, no autonomous agents and no arbitrary code execution. **The engine
(M1-M5) contains no AI of any kind**: M5 defines the boundary an orchestrator sits
above (§11), and M6 sits above it (§12) without changing a line of it.

**All Python here is standard library.** That includes the M7 API server, which is
built on `http.server` — there is no FastAPI, Flask, Django or `requests` anywhere
under `app/`, and `requirements.txt` installs pytest and nothing else. Two bounded
exceptions sit outside that rule:

* the optional Google GenAI SDK (`requirements-gemini.txt`), imported inside
  `GeminiProvider.__init__` and never needed by the engine, the mock path or the
  test suite;
* the frontend's npm toolchain (React, Vite, Tailwind, vitest), which is a separate
  build that Python never imports.

So the engine, the CLI and `pytest -q` all work offline on a checkout where
`frontend/` was never touched and no API key exists.

## 3. Architecture

```
   ┌────────────────────────────────┐   ┌────────────────────────────┐
   │ frontend/  (M7)  React + Vite  │   │ app/cli/main.py (argparse) │
   │  App.tsx  map/  panels/        │   │  route · demand-demo ·     │
   │  api/client.ts  components/    │   │  fleet/swarm/rebalancing/  │
   │  holds NO simulation logic     │   │  ai-demo · export-scenario │
   └───────────────┬────────────────┘   └──────────────┬─────────────┘
                   │  JSON over HTTP, /api/*           │ stdout = results
                   ▼                                   │ stderr = logs/errors
   ┌───────────────────────────────────────────┐        │
   │ app/api/  (M7, M7.2)   stdlib http.server │        │
   │  server.py  routes + serialisation        │        │
   │  demo.py    deterministic Demo Mode       │        │
   │  events.py  events derived from state     │        │
   │  adapts only; computes no model of its own│        │
   └─────────────────────┬─────────────────────┘        │
                         └──────────┬───────────────────┘
                                    │
          ┌─────────────────────────▼─────────────────┐
          │ app/orchestration/  (M6)                  │  observe -> propose ->
          │  config.py  observation.py  actions.py    │  validate -> execute -> record
          │  validator.py  execution.py  scenarios.py │  the AI proposes only; it
          │  orchestrator.py  audit.py  metrics.py    │  never mutates the engine
          │  prompt.py  api.py  providers/            │  mock (default) | gemini
          └─────────────────────┬─────────────────────┘
                                │  reaches the engine ONLY through the boundary below
          ┌─────────────────────▼─────────────────────┐
          │ app/rebalancing/  (M5)                    │  forecast, demand map,
          │  config.py  forecast.py  demand_map.py    │  eligibility, matching,
          │  eligibility.py  planner.py  execution.py │  empty repositioning
          │  simulation.py  metrics.py                │
          │  observation.py  <- the M6 boundary       │  read-only + validator
          └─────────────────────┬─────────────────────┘
                                │
          ┌─────────────────────▼─────────────────────┐
          │ app/swarm/  (M4)                          │  compatibility, formation,
          │  config.py  models.py  compatibility.py   │  platoon movement, splitting
          │  formation.py  movement.py  simulation.py │  rule-based, no AI/LLM
          │  metrics.py  rebalancing.py (hook only)   │
          └─────────────────────┬─────────────────────┘
                                │
          ┌─────────────────────▼─────────────────────┐
          │ app/fleet/  (M3)                          │  pods, assignment, movement
          │  config.py  models.py  pod_fleet.py       │  discrete ticks, battery model
          │  generator.py  assignment.py  movement.py │  independent pods, no swarms
          │  simulation.py  metrics.py                │
          └─────────────────────┬─────────────────────┘
                                │
          ┌─────────────────────▼─────────────────────┐
          │ app/demand/  (M2)                         │  passengers, trips, OD matrix
          │  config.py  generator.py  models.py       │  seeded RNG, no vehicles
          │  trip_routing.py  metrics.py  profile_io  │
          └─────────────────────┬─────────────────────┘
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
   scenarios/baseline.json       ──(builder)──▶  NetworkGraph
   scenarios/demand_*.json       ──(profile_io)──▶  DemandProfile
```

Dependencies point downward only:
`models ← network ← routing ← simulation ← demand ← fleet ← swarm ← rebalancing ←
orchestration ← {cli, api} ← frontend`.
Each layer consumes the ones below and is imported by none of them; tests walk the
lower packages to enforce that none of `app.demand`, `app.fleet`, `app.swarm`,
`app.rebalancing` or `app.orchestration` is imported downward. `app/cli/` and
`app/api/` are siblings at the top of the Python stack — neither imports the other,
and nothing under `app/` imports `app.api`. The M6 orchestrator reaches the engine
only through §11's read-only observation and validator, and the frontend reaches
Python only over HTTP.

## 4. Data models

| Model | Key fields | Validation |
|---|---|---|
| `Node` (frozen) | `node_id`, `name`, `latitude`, `longitude`, `node_type` ∈ {intersection, station, terminal} | non-empty id/name, finite lat ∈ [-90, 90], lon ∈ [-180, 180], known type |
| `Edge` | `edge_id`, `source`, `destination`, `distance_km`, `base_travel_time_min`, `capacity_vehicles_per_hour`, `current_vehicle_count`, `road_type` ∈ {local, arterial, trunk} | positive finite distance/time, integer capacity > 0, integer count ≥ 0, no self-loops |
| `Route` (frozen) | `origin`, `destination`, `node_ids`, `edge_ids`, `total_distance_km`, `total_travel_time_min`, `total_cost`, `cost_metric`, `nodes_visited`, `algorithm` | path consistency, non-negative totals |
| `NetworkSnapshot` (frozen) | `time_min`, `vehicle_counts` | stable SHA-256 `fingerprint()` |
| `Pod` (M3) | `pod_id`, `capacity`, `current_node_id`, `status`, `battery_percent`, `assigned_trip_id`, route + progress, `current_edge_id` | capacity ≥ 1, battery ∈ [0, 100], occupancy ≤ capacity, only legal state transitions |
| `TripRecord` (M3) | `trip_id`, `party_size`, endpoints, `status`, `pod_id`, timings, measured distance/energy | party ≥ 1, valid status, never deleted |
| `FleetSnapshot` (M3, frozen) | `time_min`, per-pod state, trip status counts | stable SHA-256 `fingerprint()`, battery rounded |
| `SharedCorridor` (M4, frozen) | `edge_ids`, `origin_node_id`, `divergence_node_id`, `distance_km`, `travel_time_min` | non-empty, no repeated edge, non-negative totals |
| `Swarm` (M4) | `swarm_id`, sorted `pod_ids`, `leader_pod_id`, `corridor`, `status`, progress, `formation_time_min` | **≥ 2 pods**, leader is a member, progress never exceeds the corridor or goes backwards |
| `SwarmSnapshot` (M4, frozen) | `time_min`, per-swarm state, status counts, formation/split counts | stable SHA-256 `fingerprint()`, distance rounded |
| `DemandForecast` (M5, frozen) | per-node `NodeForecast` with both components, `upcoming_bucket`, `has_sufficient_history` | unique nodes, sorted, thin history refused |
| `SpatialDemandMap` (M5, frozen) | per-node `DemandMapRow`: current/forecast demand, available pods, ratio, balance, deficit, surplus | sorted by node, ratio `None` when no pods |
| `RepositionAssignment` (M5) | `reposition_id`, pod, origin/target, reason, priority, estimated **and actual** km/min/kWh | status machine, records never deleted |

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

## 8. Passenger demand model (M2)

> ⚠️ **This is a synthetic demand model for simulation and is not calibrated to
> real-world city mobility data.** Passengers, trips, party sizes, role weights
> and time-of-day shares are all invented. No real mobility survey, ticketing
> feed or census underlies any number below.

M2 represents *demand*, not vehicles: who wants to travel, from where to where,
and when. Nothing is dispatched, grouped or platooned — that is M3 and later.

### What a passenger and a trip are

| Model | Meaning | Key fields |
|---|---|---|
| `Passenger` (frozen) | one synthetic traveller | `passenger_id`, `home_node_id`, `purpose` ∈ {commute, other} |
| `TripRequest` (frozen) | one wish to travel | `trip_id`, `passenger_id`, `origin_node_id`, `destination_node_id`, `request_time_min`, `party_size`, `trip_purpose`, `time_bucket` |
| `DemandMatrix` (frozen) | aggregated origin-destination demand | sorted `ODCell`s of `(origin, destination, trips, passengers)` |
| `DemandSnapshot` (frozen) | reproducibility handle | counts + OD cells + bucket totals, with a stable SHA-256 `fingerprint()` |

`party_size` is how many people one request carries, so **passenger volume**
(party sizes summed) is always ≥ **trip count**. `TripRequest` rejects
`origin == destination` outright, and the generator cannot produce one: the
origin is removed from the destination candidate set, so distinctness holds by
construction rather than by retrying until it does.

### How synthetic passengers are generated

`app/demand/generator.py`, from a local `random.Random(seed)` — the global
`random` module is never touched. The RNG is consumed in a fixed order:

1. **Per passenger:** home node, then purpose (commuter with probability
   `commuter_share`).
2. **Per trip:** time bucket → request time → party size → origin and/or
   destination.

Every weighted draw walks a list sorted by key, so nothing depends on dict
iteration order or on the graph's node insertion order. Ids are positional and
therefore stable: `P000000…`, `T000000…`. A larger run keeps a smaller run's
passengers as a prefix.

### Why demand is not uniform: place roles

M1's `Node` only knows `intersection` / `station` / `terminal`, which cannot
express "residential" versus "employment". Rather than change the M1 model —
which would alter `scenarios/baseline.json` — the demand layer keeps its own
classification as **demand configuration**:

```
role(node) = node_roles[node_id]                    if listed explicitly
             default_roles_by_node_type[node_type]  otherwise
```

Roles are `residential`, `employment`, `hub`, `mixed`, `through`. In the
synthetic city, Residential North/South and Riverside are residential;
University, Tech Park and Industrial Zone are employment; the stations and
terminals are hubs; Market is mixed; plain junctions are `through` and barely
start or end a trip. **These are invented judgements about what each place is
for.**

Each time bucket then gives every role a *production* weight (how likely a trip
starts there) and an *attraction* weight (how likely one ends there):

```
origin weight      = production[role(origin)]
destination weight = attraction[role(dest)] / (1 + straight_line_km) ** gravity_distance_exponent
```

The distance term is a plain gravity-style decay, so nearby destinations are
likelier. **Straight-line km is used for weighting only — travel cost always
comes from the network's own road distances and times, exactly as in M1.**

Commuters additionally anchor on their home node: in the morning peak the home
node *is* the origin, in the evening peak it *is* the destination. That is what
makes the daily flow reverse.

### How demand varies by time

Four buckets in the `baseline` profile, each with window(s), a share of the
day's trips, and its own role weights:

| Bucket | Window | Trips | Character |
|---|---|---|---|
| `morning_peak` | 06:00–10:00 | 35 % | residential → employment / hub |
| `midday` | 10:00–16:00 | 22 % | moderate, distributed |
| `evening_peak` | 16:00–20:00 | 31 % | employment / hub → residential |
| `off_peak` | 20:00–24:00 and 00:00–06:00 | 12 % | low, distributed |

Windows are half-open `[start, end)` and a bucket may have several, which is how
a night that wraps past midnight is expressed without modular arithmetic. The
profile itself contains **no randomness**: windows, shares and weights are fixed
configuration. Only which bucket a given trip lands in is drawn.

`horizon_min` clips windows to `[0, horizon_min)` and renormalises the shares, so
a short simulation only sees the buckets it actually covers.

### How OD demand is aggregated

`DemandMatrix.from_trips` sums party sizes per ordered `(origin, destination)`
pair. The matrix is **sparse** — only pairs with demand get a cell, and the "0"
cells of a conceptual dense table simply have no entry:

```
                  Destination
                 University   Industrial Zone   ...
Residential North    39              14
Residential South    76              99
```

Cells are always stored sorted by `(origin, destination)`, so two matrices built
from the same trips in any order are equal and fingerprint identically.
`top_pairs()` breaks ties on origin then destination rather than on insertion
order. Self-pairs are rejected unless `allow_self_pairs=True`, and any unknown
node id — in a cell or in a `demand_for()` lookup — raises `NodeNotFoundError`.

### How routing integrates

`app/demand/trip_routing.py` calls M1's `find_route` and stores the `Route` it
returns. It does not reimplement, wrap or tune any routing logic, and **routing
never imports the demand layer** (a test walks the lower layers to enforce this).

Because cost is read from the graph's *current* vehicle counts on every call,
routing the same trip after traffic changes yields the new cost — the M1.1 route
switch is reproduced through a `TripRequest` in `test_demand_routing.py`. Each
`RoutedTrip` also records the route's free-flow time (its edges' base times), so
`congestion_delay_min` shows what current traffic costs that trip.

A genuinely unreachable destination is recorded as `unroutable_reason` rather
than raised, because a demand set may legitimately contain trips the network
cannot serve. An unknown node id or algorithm still raises — that is a bug, not
a demand fact.

Nothing is cached: 10,000 trips route in about 2.3 s, so M1's deliberate
"no route caching" decision stands.

### Demand scenarios

Two human-readable profiles, in the same export-not-hand-edit style as
`scenarios/baseline.json`:

* `scenarios/demand_baseline.json` — the whole-day profile above.
* `scenarios/demand_peak_hour.json` — the morning rush squeezed into 07:00–09:00,
  86 % of trips in that window, `commuter_share` 0.9 and sharper role weights.

Both are exports of the profiles in `app/demand/config.py`, and a test asserts
they stay byte-identical to them. Edit the config and re-export rather than
hand-editing the JSON.

### Metrics

`compute_metrics()` returns total passengers, trip requests and passenger
volume; unique origins and destinations; OD pairs used; average party size; top
OD pairs; demand per time bucket and per node; and — when routed trips are
supplied — routed / unroutable counts and average route distance, travel time
and free-flow time. Averages are rounded to a modest number of decimals, and an
average over zero samples is `None` rather than a misleading `0.0`.

### What is deterministic

Same seed, passenger count, profile and network ⇒ byte-identical passengers,
trips, OD matrix, metrics and `DemandSnapshot.fingerprint()`, in one process and
across separate processes. Verified for both profiles.

## 9. Pod fleet simulation (M3)

> ⚠️ **All fleet data is SYNTHETIC.** The pods, their capacity, the energy model
> and the charging behaviour are invented for prototyping. The battery figure is
> an explicit simulation approximation, **not** a physically calibrated vehicle,
> battery or charging model.

> **M3 models pods independently. Swarm/platoon formation is intentionally
> deferred to M4.** There is no grouping, no platooning and no magnetic linking
> here, and no pod status describes one — `PodStatus` has exactly five members and
> a test asserts that.

M3 is the physical fleet layer. The pipeline is now end to end:

```
demand generator → TripRequests → router → pod assignment → pod movement → trip completion
```

### What a pod represents

One autonomous electric passenger pod: a single vehicle with a stable id, a seat
count, a position on the network, a battery and at most one trip. Units are
explicit — `battery_percent` is 0–100, distances km, times minutes, energy kWh.

`Pod` follows M1's `Edge` convention: a frozen dataclass whose identity
(`pod_id`, `capacity`) can never change, while dynamic state moves only through
validated methods. A `Pod` validates the *shape* of a node id; `PodFleet` holds
the graph and so is the layer that checks the node actually exists.

**State machine** (any other transition raises `PodStateError`):

```
IDLE ──assign──▶ ASSIGNED ──start_travel──▶ TRAVELING ──arrive──▶ ARRIVED
 │  ▲                │                                               │
 │  └────release─────┘                                               │
 │  ▲────────────────────────release──────────────────────────────────┘
 │
 └──begin_charging──▶ CHARGING ──finish_charging──▶ IDLE
```

### Fleet initialisation

`generate_fleet(graph, fleet_size=…, seed=…)` places pods on nodes with weights
from `FleetConfig.depot_role_weights`, resolved through **M2's `PlaceRole`
classification** — the fleet reuses that role map rather than inventing a second
one. Hubs are weighted highest (4.0), then residential (3.0), employment and
mixed (2.0), plain junctions last (1.0), so pods start where travel starts.

Determinism works as in M1's city and M2's demand: a local `random.Random(seed)`
(the global `random` module is never touched), one draw per pod in index order,
over a node list sorted by node id. Ids are positional — `POD00000`, `POD00001`,
… — so a larger fleet keeps a smaller one as a prefix. **This deployment is
invented and does not represent any real fleet positioning.**

### Pod capacity

Capacity defaults to 4 seats and is configurable per fleet or per pod. A pod
tracks `capacity`, `occupied_seats` and `available_seats`; a trip whose party
exceeds the available seats is refused. Capacity here is strictly *individual* —
there is no combined swarm capacity, because there are no swarms.

### Trip assignment

`assign_trip` decides *which* pod takes a trip, asks M2's `route_trip` for the
path (no routing logic is reimplemented), and hands the result to the pod. A pod
is eligible when **all** of these hold:

1. it is `IDLE`;
2. it is standing at the trip's origin node;
3. its available seats cover the party size;
4. its battery covers the route's estimated energy plus the configured reserve.

Among eligible pods the **lowest `pod_id`** wins, which makes assignment
reproducible and independent of dict ordering. Assignment is deliberately local,
not globally optimised.

Condition 2 means **M3 does no repositioning**: a pod never drives empty to a
pickup, so a trip with no co-located pod is reported as unassigned rather than
quietly served. See *Known limitations* — this, not fleet size, is what bounds
the completion rate.

### Movement

A pod progresses edge by edge along its route, and `advance_pod` may finish
several edges inside one tick. Event times are exact rather than tick-quantised.

Movement reads its costs from M1 — `edge.current_travel_time_min` and
`edge.congestion_multiplier` — so **there is no second cost model and no second
congestion model.** When a cost is sampled matters:

* travel time, distance and energy are sampled when the pod **enters** an edge,
  and hold for that traversal;
* so congestion on an edge the pod has **not yet reached** does change its trip;
* congestion on the edge it is **already driving** does not retroactively change
  that traversal, because the pod is already on the road.

That is a deliberate, documented choice: it keeps a traversal's cost
well-defined without continuously re-integrating traffic. Both directions are
pinned by tests.

### Battery approximation

```
energy_kwh           = base_energy_kwh_per_km × distance_km × congestion_multiplier
battery_drop_percent = energy_kwh / battery_capacity_kwh × 100
```

`congestion_multiplier` is the edge's own multiplier from M1, so stop-start
traffic costs proportionally more energy — directionally right, numerically
invented. At the defaults (0.18 kWh/km, 40 kWh) one km costs 0.45 % of a full
battery.

The battery can never go negative: `discharge()` clamps at 0 and returns what it
actually removed, `charge()` clamps at 100. A pod below `low_battery_percent`
(20 %) is not eligible for assignment and enters `CHARGING` when idle, returning
to service at `target_charge_percent` (90 %) at `charge_percent_per_min`.

**Ignored on purpose:** gradients, mass, payload, regenerative braking,
temperature, battery ageing, non-linear charging curves, auxiliary loads, and the
energy cost of driving empty. No drafting or platooning benefit exists, because
pods are independent in M3.

### Simulation ticks

A discrete simulation, not a live control system: nothing sleeps and nothing
polls a wall clock. There is **no RNG in the tick loop at all** — fleet
initialisation is M3's only seeded step. Each tick covers `[t, t + tick_minutes)`
(default 1 minute) in a fixed order:

1. finish charging (battery at or above target → `IDLE`)
2. release `ARRIVED` pods → `IDLE`
3. send flat `IDLE` pods to `CHARGING`
4. charge `CHARGING` pods
5. depart pods assigned on an **earlier** tick
6. advance `TRAVELING` pods, completing edges and arrivals
7. assign `PENDING` trips whose `request_time_min` has arrived
8. give up on trips waiting past `max_trip_wait_min`
9. advance the clock

Steps 5 and 7 are in that order deliberately: a pod assigned during a tick
departs on the *next* one, so `ASSIGNED` is a real, observable boarding state
rather than an instant no snapshot could catch. Trips are processed in `trip_id`
order and pods in `pod_id` order.

Step 8 exists so a run terminates: without a dispatch timeout, a trip whose
origin never sees an idle pod would wait forever.

### Trip records

A `TripRecord` per trip, **never deleted**, moving through
`PENDING → ASSIGNED → IN_PROGRESS → COMPLETED`, or to `FAILED`. The two kinds of
failure stay distinguishable by reason prefix: `unroutable:` (no path exists at
all) and `no pod available within …` (the dispatch timeout). Completed records
carry measured figures — the energy the pod really consumed and the minutes it
really drove — not route estimates.

### Fleet metrics

`compute_fleet_metrics()` reports pod counts per status; completed, pending,
failed, unroutable, timed-out and unassigned trips; average pod utilisation
(share of the window each pod spent driving); average passenger occupancy and
occupancy rate; total distance, driving time and energy; average energy per km;
and average trip completion and wait times. Figures are rounded modestly, and an
average over zero samples is `None` rather than a misleading `0.0`.

### Deterministic guarantees

Same network, demand, fleet size, seed and configuration ⇒ identical fleet
initialisation, assignments, pod trajectories, trip completions, battery levels,
metrics, final fleet state and `FleetSnapshot.fingerprint()` — in one process,
across repeated runs, tick report by tick report, and across separate Python
processes. All four are covered by tests.

## 10. Swarm formation and platooning (M4)

> **M4 uses deterministic rule-based swarm formation. No AI/LLM is involved.**
> Every decision is an explicit numeric comparison against a threshold in
> `app/swarm/config.py`. There is no learned model, no scoring heuristic and no
> randomness anywhere in the swarm layer.

> **A swarm is a coordination layer over individual autonomous pods, not a
> fictional single vehicle.** Each pod keeps its own id, battery, passengers,
> route and odometer. Four pods in a platoon are four pods.

```
individual pods → compatible routes → swarm formation → platoon travel
                → route divergence → swarm split → individual pods
```

### What a swarm is

A `Swarm` records that two or more pods traverse one **shared corridor**
together: their ids (always sorted), a leader, the corridor, when it formed, and
how far along it is. It moves `FORMING → ACTIVE → SPLITTING → COMPLETED`, and a
swarm needs **at least 2 pods** — one pod travelling alone is not a swarm.

M4 added **no pod states**. `PodStatus` still has M3's five members and
`app/fleet/` needed no edit at all: swarm membership lives in the swarm layer,
which is what keeps the "coordination layer, not a vehicle" framing honest in the
code as well as the prose.

### The compatibility rules

All of these must hold, and each is a threshold comparison:

| # | Rule | Threshold |
|---|---|---|
| 1 | **Co-location** — candidates stand at the same node, ready for the same next edge | — (no teleporting into formation) |
| 2 | **Corridor length** — their routes' common prefix is long enough | `min_shared_edges` = 2, `min_shared_distance_km` = 3.0 |
| 3 | **Bounded divergence** — the corridor is a real share of *every* member's remaining journey | `min_shared_route_fraction` = 0.4 |
| 4 | **Size** | 2 ≤ n ≤ `max_swarm_size` = 4 |
| 5 | **State** — each holds a trip and a route and is in no other swarm | — |

Plus two timing thresholds:

* `max_formation_delay_min` (5.0) — how long an assigned pod waits at its origin
  for partners before departing alone. **This is the only behavioural difference
  from M3**, and by far the most influential threshold: on the seed-42 city with
  100 pods and 1,000 trips, raising it from 3 → 5 → 10 → 15 minutes takes the
  swarm count from 15 → 28 → 36 → 47, while the other thresholds barely move it.
  Longer waits platoon more pods but delay arrivals, and past ~15 minutes
  completions start to fall.
* `min_formation_stability_min` (2.0) — a swarm only forms if its corridor is
  worth at least this much travel time. **This is what prevents oscillation:** a
  group that would disband almost immediately is never formed in the first place.

### Common corridors

The corridor is the **common prefix** of the members' remaining routes — not any
shared edge later on. Given

```
Pod A:  A → B → C → D → E
Pod B:  A → B → C → D → F
```

the corridor is `A → B → C → D`, the divergence node is `D`, and each pod's
remaining individual route after `D` is its own. A swarm knows its corridor's
edge ids, distance, travel time and divergence node explicitly.

### Platoon movement

`advance_swarm` moves the members **together**, stepping to edge boundaries so
they stay on the same edge with the same elapsed time and stop *exactly* at the
divergence node rather than overrunning it. A test asserts the lockstep property
mid-corridor, and that at the divergence node the pods share a node but sit on
different onward edges with zero elapsed time.

Every metre still goes through M3's `advance_pod`, which reads M1's
`edge.current_travel_time_min`. **There is no second cost model and no second
congestion model.**

### Splitting and rejoining

When a corridor is exhausted the swarm goes `SPLITTING`, and the members standing
at the divergence node are offered to the formation planner again. A subgroup that
still shares a corridor continues as a **new** swarm; everyone else becomes
independent. Worked example from the fixture used in the tests:

```
SW00001  POD00000 POD00001 POD00002 POD00003   corridor C1 → C2 → C3  (12.0 km)
                                               divergence H3
   ├── SW00002  POD00000 POD00002 POD00003      corridor HE → ME  (8.0 km)  → E
   └── POD00001 continues alone                                              → F
```

Rejoining is the same mechanism: a later valid corridor forms a new swarm. Because
formation only happens at departure and at a divergence, and splitting only
happens when a corridor genuinely ends, there is no per-tick churn — and the
stability window blocks groups that would break up at once. A test asserts no
swarm is ever formed or split twice.

### Congestion changes compatibility

```
traffic → edge cost → route change → shared corridor change → compatibility change
```

A regression test pins this on a purpose-built fork. In free flow two pods share
`C1 → C2 → C3` and platoon. Congesting `C3` reroutes the E-bound pod onto a
bypass while the F-bound pod, which has no alternative, stays on the corridor:
they now share nothing and no swarm forms. Same fleet, same trips, same
thresholds — only the traffic differs. A second test covers the reverse, where
congestion reroutes both pods the same way and they platoon on the *new* corridor.

### What coordinating actually changes — and what it does not

**Platooning grants no discount on distance, travel time or energy.** A pod in a
formation drives the same edges at the same M1 costs and pays the same M3 battery
cost as it would alone — a test asserts a platooned pod and a solo pod covering
the same corridor consume exactly the same.

What coordination changes is modelled road **space**. Two different quantities are
involved, in **two different units**, and the field names keep them apart:

* **`km`** — physical kilometres actually driven on the tarmac.
* **`equiv-km`** — *single-pod-equivalent road space*, where one pod driving alone
  for one km is 1.0 by definition. These are **not distances**; never add them to
  or difference them against physical kilometres from elsewhere.

For each swarm *s*: `d` = corridor length actually travelled together, `n` =
members, `f` = `formation_occupancy_factor`.

| Metric | Unit | Exact formula | Meaning |
|---|---|---|---|
| `pod_distance_km` | km | `Σ pods (pod.total_distance_km)` | physically driven. **Platooning never reduces this.** |
| `total_shared_corridor_distance_km` | km | `Σ swarms (d)` | corridor length, counted **once per swarm** ("unique corridor distance") |
| `coordinated_pod_km` | km | `Σ swarms (d × n)` | pod-km driven *while in formation* |
| `unplatooned_pod_km` | km | `pod_distance_km − coordinated_pod_km` | pod-km driven outside any formation — **including the solo legs of pods that did platoon** |
| `road_occupancy_equiv_km` | equiv-km | `unplatooned_pod_km + Σ swarms (d × (1 + (n−1) × f))` | estimated road space used |
| `road_occupancy_saved_equiv_km` | equiv-km | `pod_distance_km − road_occupancy_equiv_km`, identically `Σ swarms (d × (n−1) × (1−f))` | road space freed, **within this run** |
| `road_occupancy_saving_percent` | % | `100 × saved / pod_distance_km` | scale-free, so this is the only occupancy figure comparable across two runs |

Four pods over a 5 km corridor is **5 corridor-km and 20 pod-km** — a platoon is
never one vehicle. With no swarms at all, `road_occupancy_equiv_km` reduces
**exactly** to `pod_distance_km`, because every pod then occupies 1.0 equiv-km per
km; that is why an independent run reports the same number twice, and a test
asserts the identity rather than leaving it to coincidence.

The `formation_occupancy_factor` (0.4) is a **project assumption about headway** —
a following pod needs 40 % of an independent pod's road space. It is **not** an
aerodynamic, fuel, energy or emissions saving, and none is claimed anywhere. At
`f = 1.0` the saving is exactly zero by construction, which a test pins.

`compute_swarm_metrics()` defaults to the simulation's **own** `swarm_config`, so
the occupancy figures cannot silently be scored under a factor the run never used.

Two participation fields are a **snapshot, not a total**:
`pods_currently_in_swarms` and `pods_currently_independent` describe the instant
the metrics were taken, so after a finished run they read 0 and *total_pods*. The
cumulative figure is `distinct_pods_ever_in_a_swarm`, which is what the CLI
reports.

### Independent vs swarm: the controlled comparison

`compare_modes()` runs the same scenario twice with only `enable_swarms` flipped —
same network, demand, fleet, seed, thresholds and horizon, each run built from
scratch so neither inherits the other's traffic or pod positions.
`enable_swarms=False` reproduces M3 exactly, fingerprint for fingerprint (a test
asserts that against `FleetSimulation` itself).

Read the comparison carefully: the two runs **do not serve the same trips**, so
their raw totals are not like-for-like and cannot be differenced to isolate the
coordination effect. Difference `road_occupancy_saving_percent` instead, which is
scale-free (independent mode is exactly 0 %). Fleet totals differ between modes,
and not because platooning discounts anything. Waiting up to `max_formation_delay_min`
shifts departures, so a slightly different set of trips gets served and distance
and energy totals move with the served set. Waiting also costs riders time, so
average wait and completion time **rise**. Both effects are reported rather than
hidden.

### Fleet rebalancing — hook only

> **M4 exposes the interface required for future fleet rebalancing. Actual
> adaptive rebalancing belongs to M5.**

`app/swarm/rebalancing.py` defines `RepositionRequest` (a proposed empty move) and
the `FleetRebalancer` protocol, plus `SurplusDeficitRebalancer` — a deliberately
naive reference planner that pairs nodes holding idle pods against nodes where
trips went unserved. `plan()` is **read-only**: it moves no pod, assigns no trip
and changes no traffic, and nothing in M4 executes what it returns. A test asserts
all of that.

## 11. Adaptive fleet rebalancing (M5)

> **M5 uses deterministic, explainable demand forecasting and fleet rebalancing.
> No machine learning or LLM is involved.**

> **Rebalancing is a heuristic and is not claimed to be globally optimal.**

M3 exposed the real bottleneck: pods drift to wherever demand last took them, so
trips go unserved for want of a pod *in the right place* rather than for want of a
pod. M5 closes that loop:

```
demand → demand imbalance → rebalancing decision → pod repositioning
       → better spatial availability → more trips served
```

### Demand windows

Four windows over one timeline, at simulated minute `t`:

| Window | Span | Role |
|---|---|---|
| **recent** | `[t − 60, t)` | the history the forecast learns a rate from |
| **current** | `[t − 15, t]` | "demand right now", reported for context |
| **near future** | `[t, t + 30)` | the window the forecast is *about* |
| **forecast** | — | the estimate for that window |

### The deterministic forecast — and why it is not clairvoyant

The simulation holds the whole trip list, so peeking at future demand would be
trivial and would make every number meaningless. It does not. `build_forecast` is
handed the trip records and immediately filters them to the **recent** window; it
reads nothing else about the future. Its two inputs are both available to an
operator at the moment it runs:

```
forecast(node) = horizon × [ w_recent  × recent_rate(node)
                           + w_profile × total_recent_rate × profile_share(node) ]

recent_rate(node)   = trips requested from node in the recent window / window length
profile_share(node) = production_weight(role(node), upcoming bucket)
                      / Σ over nodes of the same weight
```

`w_recent = w_profile = 0.5`, and they must sum to 1. The second term is the
"known scenario profile": M2's published time-of-day production weights, a declared
assumption rather than an observation. The **upcoming** bucket is the one covering
`t + horizon`, which is what lets pods move *before* a peak instead of after it.

**Thin history is refused, not extrapolated.** Until `min_history_min` (15) of
simulated time has passed, one trip in a two-minute window would imply an enormous
hourly rate, so the forecast returns zeros, reports `has_sufficient_history=False`,
and nothing is repositioned. A test asserts that adding 50 future trips leaves the
forecast byte-identical.

### The spatial demand map

Demand is counted in **trips**, supply in **pods**: one pod serves one trip at a
time whatever the party size, so the two are directly comparable.

| Column | Definition |
|---|---|
| `current_demand` | trips requested from the node in the current window |
| `forecast_demand` | the forecast above, for the near-future window |
| `available_pods` | pods there that are **eligible to be repositioned** |
| `demand_supply_ratio` | `forecast / available`, or `None` when no pods — undefined, not infinity |
| `balance` | `available_pods − forecast_demand`; negative means short |
| `deficit` / `surplus` | `max(0, −balance)` / `max(0, balance)` |
| `actual_near_future_demand` | what really arrived — **evaluation only**, never a forecast input |

Mid-morning on the seed-42 city the map shows exactly the M3 imbalance:

```
node                 forecast  avail  balance  actual
Residential South        9.58      0    -9.58      11
Riverside                8.58      0    -8.58      11
Residential North        8.33      0    -8.33      13
```

29.19 pods short across 9 nodes, while University, Industrial Zone and Tech Park
sit on 17, 14 and 11 idle pods.

### Which pods may move

**A passenger's pod is never taken.** A pod is eligible only when it is `IDLE`
(so not in passenger service and not already repositioning), not `CHARGING`, not a
member of an active swarm — M4 owns that pod — and has battery for the move plus a
reserve. Every rejection carries a machine-readable reason (`not_idle`,
`is_charging`, `in_active_swarm`, `insufficient_battery`).

### The rebalancing decision

1. Build the demand map.
2. Walk the **deficit** nodes, biggest shortfall first, ties on `node_id`.
3. Fill each one pod at a time. A candidate must stand at a node that still has
   `≥ min_surplus_to_release` spare *after everything already claimed this cycle* —
   **a node is never stripped below its own forecast to feed another** — have a
   route (M1's router, current traffic), be within `max_reposition_distance_km`,
   and pass the battery rule. The **nearest** survivor wins, ties on `pod_id`.
4. If cycle capacity remains, drain badly clumped nodes (more than
   `max_surplus_before_drain` above their own forecast) toward the busiest node.
5. Stop at `max_repositions_per_cycle`, or when `max_concurrent_repositions` pods
   are already moving.

Planning mutates nothing, and a cycle runs every `rebalance_interval_min`.

**Why a pod was moved** — the whole priority formula:

```
score = deficit × priority_deficit_weight − distance_km × priority_distance_weight
```

With the defaults (1.0 and 0.05) a node short of 10 pods 8 km away scores
10 − 0.4 = 9.6. A reader can reproduce any score by hand from the request's target
deficit and distance.

**Machine-readable reasons**, one per request, in precedence order:

| Reason | Meaning |
|---|---|
| `DEMAND_DEFICIT` | the target is short **right now** |
| `PEAK_PREPOSITIONING` | no present shortfall, only a forecast one — the pod moves before the peak |
| `SUPPLY_SURPLUS` | the target is not short; the origin is clumped and is being drained |

### Repositioning movement

A repositioning pod drives **empty**, through M3's movement machinery unchanged:
`pod.assign(..., kind=TripKind.REPOSITIONING)` with **zero** occupied seats, then
M3's own departure, `advance_pod` and release. **There is no second movement model,
no second cost model and no second congestion model.**

`PASSENGER_TRIP` and `REPOSITIONING_TRIP` are kept apart at every level, so an
empty move can never flatter a passenger figure:

* the pod counts it under `completed_repositioning_count`, never `completed_trip_count`;
* no `TripRecord` is created, so M3's trip metrics never see it;
* occupied seats stay 0;
* its kilometres are reported as **deadhead** and never netted off anything.

An empty pod also never joins a swarm — it has no passengers to coordinate — and
while moving it is not `IDLE`, so passenger assignment cannot take it. On arrival
it returns to `IDLE` and is immediately available for service again.

### Congestion and battery

Repositioning routes come from M1's router under **current** traffic, so congestion
changes the route and therefore the time: a test congests `E011` and watches a
move reroute from `E009 → E011` to `E013 → E003` with a different travel time.
Energy uses M3's battery model, so congestion raises what a move costs too.

A move is refused unless `battery ≥ energy_for_the_move + 15 %` reserve, so a pod
arrives able to work rather than stranded. The reserve is stricter than M3's 5 %
passenger reserve precisely because an empty move earns nothing.

### The cost, never hidden

| Metric | Meaning |
|---|---|
| `reposition_distance_km` | km driven **empty** (deadhead) |
| `reposition_energy_kwh` | kWh spent doing it |
| `passenger_distance_km` | pod km **minus** deadhead — distance with someone aboard |
| `pod_distance_km` | every km driven, deadhead included; rebalancing makes this go **up** |
| `deadhead_share_percent` | deadhead as a share of all driving |

The efficiency figure has one exact definition and needs **both** runs:

```
rebalancing_trips_per_deadhead_km
    = (trips completed WITH − trips completed WITHOUT) / empty km driven WITH
```

It is a rate of additional trips per empty kilometre. It can be zero or negative,
it is `None` when nothing was repositioned, and its reciprocal
`deadhead_km_per_additional_trip` is reported alongside because it is easier to
reason about. **It is not a return on investment**: it puts trips over kilometres
and says nothing about money, emissions or welfare.

### What it actually achieved, costs included

100 pods, 1,000 trips, identical network, demand, fleet, seed and horizon:

| metric | no rebalancing | adaptive |
|---|---|---|
| trips served | 499 | **904** |
| unserved trips | 501 | **96** |
| average wait (min) | 4.63 | 5.24 |
| average completion (min) | 28.81 | 31.35 |
| pod distance, all (km) | 8 045 | 25 052 |
| — with passengers | 8 045 | 16 073 |
| — **empty (deadhead)** | 0 | **8 979** |
| energy, all (kWh) | 1 453 | 4 525 |
| average deficit across cycles | 12.38 | **8.10** |
| swarms formed | 28 | 95 |

+405 trips served, at **22.17 km driven empty per additional trip** (0.0451 trips
per deadhead km). Deadhead is 35.8 % of all driving. Note the two figures that got
**worse**: average wait rose 4.63 → 5.24 min and average completion 28.81 → 31.35
min, because repositioning competes for pods and adds driving. Both are reported
rather than buried.

Both modes measure the imbalance on the same cadence — the baseline observes
without acting — so `average deficit` is a like-for-like comparison.

### The M6 boundary — defined here, not implemented here

`app/rebalancing/observation.py` defines where an orchestrator attaches — M5 ships
the boundary and no orchestrator; M6 (§12) is what attaches to it:

```
DATA → DETERMINISTIC SIMULATION → METRICS → (M6 ORCHESTRATOR)
     → STRUCTURED PROPOSAL → VALIDATOR → SIMULATION
```

Three things make that boundary real rather than decorative:

1. **`observe()` is read-only.** It returns a frozen, JSON-serialisable snapshot of
   plain values covering network, demand, fleet, swarm, forecast, rebalancing and
   metrics state. It hands out no graph, fleet, pod or simulation, so a caller
   holding an observation *cannot* mutate anything through it.
2. **A `ProposedAction` is inert data** — an action type from a closed enum
   (`REQUEST_REBALANCING`, `SET_DEMAND_SCENARIO`, `SET_PARAMETER`,
   `COMPARE_SCENARIOS`) plus parameters. Building one executes nothing.
3. **`ActionValidator` is deterministic and bounded.** Every settable parameter has
   an explicit numeric range; unknown actions, unknown parameters and out-of-range
   values are rejected with machine-readable reasons, and there is no trusted path
   that skips it.

`apply_validated_action` refuses anything unvalidated or rejected and handles
exactly one action, `REQUEST_REBALANCING`. The others validate but are not applied,
because deciding *when* to switch scenario or run a comparison is orchestration —
**M6's job, and deliberately absent here. There is no Gemini, no LLM and no agent
in this milestone.**

## 12. AI orchestration (M6)

> ⚠️ **Gemini is an orchestrator, not the simulation engine.** Every number this
> section reports is produced by the deterministic M1–M5 engine. The AI chooses
> *which question to ask*; it never computes an answer, and it never changes
> simulation state.

M1–M5 are the deterministic source of truth and were **not modified by M6**: the
whole engine — `app/models/`, `app/network/`, `app/routing/`, `app/simulation/`,
`app/demand/`, `app/fleet/`, `app/swarm/`, `app/rebalancing/`, `app/config.py` and
every scenario file — is byte-for-byte identical to the M5 commit (`git diff` against
it is empty). Tests pin what keeps it that way: no module below M6 mentions
`app.orchestration`, M6 uses M5's published boundary exactly as it stands without
widening its action vocabulary, and M6 introduces no second cost, congestion,
battery or forecasting model. M6 is a new package, `app/orchestration/`, above
everything.

### 12.1 The architecture, and why it is shaped this way

```
                        GEMINI  (or the deterministic mock)
                          │  sees a frozen tree of plain values
                          ▼
                     PROPOSAL          action_type, parameters, reason,
                          │            expected_effect, confidence,
                          │            observation_fingerprint
                          ▼
                     VALIDATOR         deterministic; re-derives every fact
                          │            from the live engine; APPROVED / REJECTED
                          ▼
              DETERMINISTIC SIMULATION  M1-M5, unchanged
                          │
                          ▼
                       RESULT           the engine's numbers, never the AI's
                          │
                          ▼
                     AUDIT LOG          what was seen, proposed, decided, done
```

The shape is the point. A language model is a useful reader of a messy situation and
an unreliable executor of anything. So it is given a *read-only* picture and a
*closed* vocabulary, and everything it says is treated as untrusted input: parsed as
data, checked against the engine, and only then acted on — by the engine, using code
that already existed and was already tested.

| | Gemini | The deterministic engine |
|---|---|---|
| Interprets the situation | ✅ | |
| Prioritises what matters | ✅ | |
| Reasons about trade-offs | ✅ | |
| Proposes a bounded action | ✅ | |
| Explains a decision in prose | ✅ | |
| Routing, congestion, demand | | ✅ |
| Pod movement, swarms, battery | | ✅ |
| Rebalancing feasibility | | ✅ |
| Validation and execution | | ✅ |
| Every metric | | ✅ |

### 12.2 The observation — the AI's only input

`app/orchestration/observation.py` composes M5's `observe()` and adds read-only
derived detail. What comes out is a frozen tree of plain values: no graph, no fleet,
no pod, no swarm, no simulation, no callable, no secret. A caller holding one
**cannot** mutate anything through it, which is what makes the boundary structural
rather than a matter of politeness.

| Section | Holds |
|---|---|
| `network` | node/edge counts, overloaded edges, average load, the most congested edges |
| `demand` | trips requested and their statuses, total deficit and surplus, the worst deficit and surplus nodes |
| `fleet` | pod count, status counts, capacity, battery distribution, pods per node, how many pods are eligible to move and why the rest are not |
| `swarm` | active swarms, sizes, shared corridors, formations and splits so far |
| `forecast` | horizon, upcoming bucket, whether there is enough history, per-node forecasts |
| `rebalancing` | cycles run, moves in flight, reposition statuses |
| `metrics` | M3 fleet, M4 swarm (including the `*_equiv_km` road-space figures) and M5 rebalancing metrics |
| `engine_fingerprints` | the fleet and swarm snapshot fingerprints |

Determinism is enforced, not hoped for: every collection is built by walking a sorted
sequence, every float is rounded to a fixed number of decimals, the only clock is the
simulation's own `time_min`, and no object identity, `repr` or Python `hash()`
appears anywhere. `fingerprint()` is the SHA-256 of the canonical JSON form and is
identical across processes and `PYTHONHASHSEED` values.

```
minute 420.0: forecast deficit 26.0408 pods across 12 node(s), worst at residential_south
fingerprint: 4d69876696c3a20ab199ef3799714cd203fa6aa4f6b95b42b529c0d4923e60a7
```

### 12.3 The action schema and the whitelist

```json
{
  "action_type": "REQUEST_REBALANCING",
  "parameters": {"target_nodes": ["residential_south", "riverside"], "pod_count": 11},
  "reason": "Forecast deficit of 26.0408 pods across 12 node(s), worst at residential_south.",
  "expected_effect": "Ask the deterministic rebalancer to run a cycle toward those nodes.",
  "confidence": 0.95,
  "observation_fingerprint": "4d69876696c3a20a..."
}
```

Exactly four actions exist — `REQUEST_REBALANCING`, `RUN_SIMULATION`,
`COMPARE_SCENARIOS`, `NO_ACTION` — and they are the members of a Python enum, so
`EXECUTE_CODE`, `RUN_SHELL`, `MODIFY_FILE`, `CHANGE_CONGESTION_FORMULA`, `DELETE_POD`
and `DIRECTLY_MUTATE_STATE` are rejected *structurally*, not by a blacklist someone
has to remember to update:

```
unknown_action_type: 'EXECUTE_CODE' is not one of REQUEST_REBALANCING,
RUN_SIMULATION, COMPARE_SCENARIOS, NO_ACTION
```

Two details worth stating plainly. `expected_effect` is **the model's words, not a
result** — it is stored beside the engine's actual outcome and never merged with it.
And `confidence` is a number the model reports about itself; it is not a probability
of being right, and **no check consults it**.

Parsing is strict. A structured (schema-constrained) response is used as-is; a text
response is parsed **whole** with `json.loads`. There is no fence-stripping, no regex
hunt for a JSON-looking substring and no repair, because salvaging a fragment is
precisely how partially-understood output gets executed. Prose produces
`AI_OUTPUT_INVALID`, no action, and an audit record holding the raw text:

```
AI_OUTPUT_INVALID | response is not valid JSON: Expecting value: line 1 column 1 (char 0)
```

### 12.4 Validation, including stale-state protection

The validator re-derives every fact from the live engine — it never takes the AI's
word for anything, including what the AI claims to have seen. For
`REQUEST_REBALANCING` it runs fifteen named checks in order, and the list is stored
in the audit record so a rejection explains exactly which gate closed (`ai-demo`
prints one per line; two columns here for space, read down the left first):

```
VALIDATOR: APPROVED
  15/15 check(s) passed:
    [x] action_type_whitelisted                     [x] pod_count_present
    [x] parameter_keys_allowed                      [x] pod_count_is_a_whole_number
    [x] observation_is_current                      [x] pod_count_within_bounds
    [x] target_nodes_present                        [x] rebalancing_is_enabled
    [x] target_nodes_is_a_list_of_strings           [x] enough_eligible_pods
    [x] target_node_count_within_bound              [x] some_eligible_pod_clears_the_battery_reserve
    [x] target_nodes_are_distinct                   [x] below_concurrent_reposition_limit
    [x] target_nodes_exist
```

`enough_eligible_pods` applies **M5's eligibility rule unchanged**: idle, not
charging, not in an active swarm. A passenger's pod is never the AI's to move.

**Stale observation protection.** Every action carries the fingerprint of the
observation it was reasoned from. Before approving, the validator builds a fresh
observation of the simulation *as it is now* and compares:

```
REJECT_STALE_OBSERVATION | action was reasoned from 000000000000...,
                           the simulation is now 4d69876696c3...
```

The fingerprint moves the instant anything material changes, so a decision made about
one city state can never be applied to another. `NO_ACTION` is the one exemption, and
only `NO_ACTION`: doing nothing is safe in every state.

### 12.5 Execution — no second implementation of anything

| Action | Runs |
|---|---|
| `REQUEST_REBALANCING` | M5's own boundary: a `ProposedAction` through M5's `ActionValidator` and `apply_validated_action`, which runs one deterministic rebalancing cycle |
| `RUN_SIMULATION` | `run_scenario`, a composition of `build_synthetic_city`, `generate_fleet`, `generate_demand` and `RebalancingSimulation` |
| `COMPARE_SCENARIOS` | the same runner, once per named scenario, tabulated side by side |
| `NO_ACTION` | nothing. Reported as `SKIPPED`, not as a failure |

Note carefully what `REQUEST_REBALANCING` does **not** do. The AI names target nodes
and a pod count; M5's planner then decides which pods actually move, using its own
forecast, surplus rule, distance limit and battery reserve. The AI's numbers are
recorded as its *request*, and the result reports what the engine actually did
alongside them:

```
The deterministic rebalancer ran one cycle: 1 move(s) dispatched, 6 refused by the
engine's own rules. The AI asked for 11 pod(s) toward 5 node(s); 1 dispatched move(s)
target a node it named. The planner, not the AI, chose every move.
```

**One dispatched out of eleven requested.** That gap is the architecture working, and
it is reported as it falls rather than smoothed over. An AI that asks for eleven pods
has not moved eleven pods; it has asked a planner that moved one.

### 12.6 A worked cycle, honestly reported

Evaluation scenario A (20 pods, 800 passengers, minute 420), one cycle with the
caller then advancing the engine 60 minutes:

```
The deterministic rebalancer ran one cycle: 1 move(s) dispatched, 6 refused.
The caller then advanced the engine 60 minutes.
```

| Metric | before | after | delta |
|---|---:|---:|---:|
| trips served | 55 | 75 | **+20** |
| trips not yet completed | 745 | 725 | −20 |
| completed repositions | 18 | 21 | +3 |
| deadhead distance (km) | 215.97 | 259.66 | **+43.69** |
| deadhead energy (kWh) | 38.944 | 46.830 | +7.886 |
| pod distance, all (km) | 1 067.154 | 1 522.304 | +455.15 |
| forecast deficit | 26.0408 | 34.0962 | **+8.0554** |
| average wait (min) | 9.39 | 11.45 | **+2.06** |
| average completion (min) | 30.01 | 33.30 | **+3.29** |

Three things must be said about this table rather than left for a careful reader to
notice.

**It is not attribution.** The delta covers a 60-minute window during which the
engine also ran its *own* rebalancing cycles on its normal cadence, served whatever
trips arrived, and charged whatever pods needed it. One AI-requested cycle dispatched
one move. Almost none of the +20 trips or the +43.69 deadhead km belongs to it. The
record reports the window the caller asked for; it does not claim the AI caused it,
and §12.10 explains why no metric here pretends to.

**"Not yet completed" is not "refused".** This run holds all 800 trip records from the
start, with request times out to minute 1 439, so at minute 420 most of the 745 simply
have not been asked for yet. The figure is M3's, reported with its own meaning intact.

**Three numbers got worse.** The forecast deficit rose, and so did both wait and
completion time. Demand in this scenario arrives faster than 20 pods can serve it, so
the queue grows whatever anyone proposes. The figures are printed unnetted in both
directions, because a layer that only showed its wins would be worth nothing.

### 12.7 Providers

| Provider | Needs a key | Deterministic | Notes |
|---|---|---|---|
| `MockProvider` | no | **yes** | fixed arithmetic rules; the default, and what every test uses |
| `ScriptedProvider` | no | yes | a test double that replays prepared responses or fails on cue |
| `GeminiProvider` | yes | no | the Google GenAI SDK, imported *inside* the constructor |

The mock is a **fixed rule, not a model**, and it is not a prediction of what Gemini
would choose. Its rules, in order: a deficit below the threshold → `NO_ACTION`; no
eligible pod → `NO_ACTION`; deadhead already above 40 % of all driving → `NO_ACTION`;
otherwise `REQUEST_REBALANCING` toward the worst deficit nodes. The third rule is the
one worth reading twice — a real shortfall is *not* on its own a reason to spend more
empty kilometres.

**Credentials.** The API key is read from `$GEMINI_API_KEY` at construction time,
handed straight to the SDK client, and never stored on the instance, written to an
audit record, printed, logged or placed in a prompt. `describe()` reports whether a
key was present, never what it was. Live mode without one fails immediately:

```
error: live Gemini mode needs an API key in $GEMINI_API_KEY, which is unset or
empty. Use --provider mock to run without one.
```

`provider_max_attempts` is 1: a failed call fails, rather than quietly becoming three
calls against a paid API.

### 12.8 Failure handling

Every stage can end a cycle, and each ends it with a complete audit record and an
untouched engine:

| Failure | What happens |
|---|---|
| missing API key | `ProviderConfigurationError` naming the variable; mock mode still works |
| SDK not installed | `ProviderConfigurationError` pointing at `requirements-gemini.txt` |
| API timeout or provider error | recorded as `PROVIDER_FAILED`; nothing is validated or run |
| malformed or prose response | `AI_OUTPUT_INVALID`; no action is formed |
| action outside the whitelist | cannot be constructed; recorded as unusable output |
| stale observation | `REJECT_STALE_OBSERVATION` |
| invented node, bad pod count, unknown scenario | rejected with a named reason code |
| not enough eligible pods, or too little battery | rejected using M5's own rules |
| the engine refusing an approved action | recorded as `FAILED` with the error type; the engine stays valid |

A test asserts that after a provider failure the simulation still ticks and a later
cycle still works. **If Gemini is missing, slow, broken or wrong, the simulation is
unaffected.**

### 12.9 The audit trail

One record per cycle, appended in order, never rewritten. Between them the records
answer the four questions that make an AI-in-the-loop system reviewable:

```
CY00001  provider=mock
  OBSERVED    minute 420.0: forecast deficit 26.0408 pods across 12 node(s),
              worst at residential_south
  PROPOSAL    REQUEST_REBALANCING (confidence 0.95)
  REASONING   Forecast deficit of 26.0408 pods across 12 node(s), worst at
              residential_south, against 11 eligible pod(s).
  AI EXPECTS  Ask the deterministic rebalancer to run a cycle toward those nodes.
              The engine decides which pods, if any, actually move.
  VALIDATOR   APPROVED
  ENGINE DID  EXECUTED: 1 move(s) dispatched, 6 refused by the engine's own rules.
```

`AI EXPECTS` and `ENGINE DID` are stored separately and never merged — collapsing
them is exactly how a system starts reporting an AI's intentions as results. The
record holds no wall-clock time (latency lives on `timing()`, outside the
deterministic record), so two identical runs produce byte-identical trails; a test
checks that across separate processes.

### 12.10 Decision-quality metrics

Measured: proposals generated, accepted, rejected, stale, malformed; provider
failures; executed and failed actions; which action types were chosen; and average
provider latency, flagged everywhere as wall-clock and excluded from every
fingerprint.

**There is deliberately no "AI accuracy" metric.** Accuracy needs a ground truth, and
there is none: the rebalancing heuristic the AI is proposing to invoke is itself
explicitly not claimed to be optimal (§11), so there is no right answer for a
proposal to match. Inventing a percentage anyway would be the one genuinely dishonest
number this project could produce. What is measured is *process*; what is compared,
when decisions are compared, is the deterministic engine's *outcome* on the same
scenario, with the methodology stated.

### 12.11 Evaluation scenarios

| Scenario | The situation | Consideration | Mock chose |
|---|---|---|---|
| `A_LARGE_DEFICIT` | 20 pods, 800 passengers, minute 420: ~26 pods short across 12 nodes, 20 % of driving empty so far | rebalancing | `REQUEST_REBALANCING` |
| `B_NO_DEFICIT` | 120 pods, 40 passengers, minute 240: forecast demand met everywhere | `NO_ACTION` | `NO_ACTION` |
| `C_EXPENSIVE` | 110 pods, 250 passengers, minute 560: a real ~4-pod deficit, but 48.6 % of all driving is already empty | `NO_ACTION` or something smaller | `NO_ACTION` |

The expected consideration is asserted **only against the mock**, whose rules are
fixed and reviewable. A live model's output is recorded for reading and never
asserted: a test suite that demands a particular sentence from a language model is
testing the weather.

### 12.12 The API boundary the UI sits on

`app/orchestration/api.py` is pure Python — no HTTP, no framework, no server — and
exposes exactly what a front end needs: `get_observation`, `propose_action`,
`validate_action`, `execute_action`, `get_result`, `get_audit_log`. Everything in and
out is JSON-serialisable plain values.

**M7 is that adapter** (§13): `app/api/server.py` holds a `SimulationSession` whose
`api` attribute is one of these objects, and every orchestrator route is a thin
translation onto these six operations — `/api/orchestrator/cycle` calls
`propose_action`, `validate` and `execute` call their namesakes, and observation,
result and audit are straight pass-throughs. The cycle route adds a few convenience
fields (a passed-checks count, the execution status and detail, a flattened move
list), each re-presenting data already in the audit record rather than computing
anything new. So the frontend reaches the orchestrator through exactly the same
façade a Python caller would.

### 12.13 Security

Never: expose, log or store an API key; execute AI text as code; run a shell command
from AI output; modify a file from AI output; import a module an AI named; let an AI
change a validation rule. All external AI output is untrusted input, and the tests
include hostile payloads — terminal escape sequences in a reason string, an
`EXECUTE_CODE` action, `__import__('os').system(...)` as a response body — each of
which is refused as data without ever being interpreted.


## 13. The Visual Command Center (M7, M7.2)

> ⚠️ **The screen computes nothing.** Every figure it shows was produced by the
> M1-M6 engines and is read over HTTP. The API adapter serialises and labels; it
> holds no cost, congestion, battery, forecasting or scoring model of its own, and
> it does not difference a metric across two runs. Road space in particular is
> reported per run from M4's published figure — see §16.

```
     React frontend  ──────────── JSON over /api/* ───────────▶  app/api/server.py
     (no simulation logic)                                            │
                                                                      ▼
                                              app/orchestration/api.py (M6 façade)
                                                                      │
                                              M1-M5 deterministic engine
```

### 13.1 The API adapter

`app/api/` is standard library only: `http.server.ThreadingHTTPServer`, `json`,
`urllib.parse`, `mimetypes`. `DualStackServer` binds `::` and falls back to IPv4.
`python -m app.api.server` serves on port 8000 by default (`--host`, `--port`).

| Module | Holds |
|---|---|
| `server.py` | `SimulationSession`, `VisualCommandCenterHandler`, `run_server` |
| `demo.py` | `DemoRunner`, the phases, the impact metrics and the demo's constants |
| `events.py` | `EventDeriver`, the event kinds and their thresholds |

**GET** `/api` and `/api/health`, `/api/network`, `/api/scenarios`,
`/api/simulation/state`, `/api/simulation/events`, `/api/demo/state`,
`/api/demand-map`, `/api/orchestrator/observation`, `/api/orchestrator/audit`,
`/api/orchestrator/result`, `/api/comparison`. Anything else under `/api/` is a 404;
anything outside it is served from `frontend/dist/`.

**POST** `/api/simulation/init`, `/api/simulation/step`, `/api/simulation/reset`,
`/api/simulation/set-traffic`, `/api/route-switch`, `/api/orchestrator/cycle`,
`/api/orchestrator/validate`, `/api/orchestrator/execute`, `/api/demo/start`,
`/api/demo/advance`, `/api/demo/pause`, `/api/demo/resume`.

There is **one process-wide session**, guarded by a creation lock and an `RLock`
held across each mutating request, so two concurrent requests cannot tick the same
engine and break determinism. `/api/simulation/reset` re-initialises the current
scenario to its exact starting state and leaves Demo Mode.

`/api/scenarios` lists `baseline`, `peak_hour` and `DEMO_MORNING_RUSH`, plus M6's
three evaluation scenarios (`A_LARGE_DEFICIT`, `B_NO_DEFICIT`, `C_EXPENSIVE`) with
the consideration each one is expected to provoke.

### 13.2 Deterministic Demo Mode

Demo Mode is **a script of inputs, not a recording and not a timer.** It starts from
a fully specified state — `DEMO_MORNING_RUSH`, 40 pods, 600 passengers, from minute
360.0, the quiet minutes before the morning ramp — then injects operator inputs into
the real simulation (one traffic injection at 150 % of an edge's capacity, one
orchestrator cycle) and *waits* for the matching derived event.

Progress is counted in beats, not seconds: 2 ticks per beat, at most 45 beats per
phase, a 2-beat quiet baseline and a 10-beat settling window at the end. The eight
phases are `BASELINE`, `DEMAND_SURGE`, `CONGESTION`, `ROUTING`, `SWARM`,
`REBALANCING`, `GEMINI`, `IMPACT`.

Each phase names the events that gate it. **A phase is marked `complete` only once
those events have actually been derived from real state; if its beat budget runs out
first it is marked `not_observed` and the machine moves on.** Nothing is faked to
make a phase light up. Because no wall clock is involved, `reset` → `run demo`
produces the same phases, events and final metrics every time, and pausing between
beats cannot change the outcome.

While a demo is running or paused, the three manual actions that would contaminate
it — stepping, running an orchestrator cycle, executing an action — are refused with
HTTP 409. Validating an action is still allowed, because validation executes
nothing.

The impact summary at the end carries a polarity per metric (lower is better, higher
is better, or neutral), **so it can and does report a figure getting worse.**

### 13.3 The event feed

Every derived event is a difference between two real engine states, or a field of a
real M6 audit record. If nothing relevant changed between two reads, no event is
emitted. Ten kinds are derived — `DEMAND_SURGE`, `CONGESTION_DETECTED`,
`ROUTE_ADAPTATION`, `SWARM_FORMED`, `SWARM_SPLIT`, `DEMAND_IMBALANCE`,
`FLEET_REBALANCING`, `GEMINI_PROPOSAL`, `VALIDATION`, `EXECUTION` — and four are
**inputs rather than findings**, named separately so they cannot be mistaken for
them: `SIMULATION_INIT`, `SCENARIO_LOADED`, `TRAFFIC_INJECTED`, `DEMO_INPUT`.

The thresholds are documented constants, not dials tuned to make a demo look busy:
an edge is congested at 80 % utilization and must fall back below 70 % before it can
alert again; a demand surge needs the forecast to rise by at least 2 trips **and**
by at least 50 % of the previous forecast; a node counts as imbalanced at 1 pod
short. Event ids are `EVT-00001` upward in emission order — positional, never a
Python `hash()`. The deriver is read-only with respect to the simulation.

### 13.4 The frontend

React 19 + TypeScript + Vite 6 + Tailwind 3, tested with vitest and
`@testing-library/react` under jsdom. It holds no simulation logic: it fetches,
renders, and sends operator inputs.

| Path | Holds |
|---|---|
| `src/App.tsx` | the shell: session state, the playback loop, wiring between panels |
| `src/api/client.ts` | the single `api` object — every `fetch` in the app lives here |
| `src/types/api.ts` | the TypeScript mirror of the API's JSON shapes |
| `src/map/CityMap.tsx` | the city, pods, edges and swarms |
| `src/panels/` | `TopBar`, `TopMetricBar`, `SimulationControls`, `GeminiOrchestratorPanel`, `EventTimeline`, `UrbanImpactPanel`, `ScenarioComparisonModal` |
| `src/components/` | `DemoController`, `StartupSequence` |

"Play" is a loop that calls `/api/simulation/step`, so the clock on screen is the
engine's tick, not an animation running alongside it. At the fastest speed setting
the loop is capped at one request every 80 ms.

`UrbanImpactPanel` reads the engine's own `road_occupancy_saved_equiv_km` and shows
it in **equiv-km** — single-pod-equivalent road space, which §10 keeps textually
distinct from physical kilometres driven. The panel's own footnote states that
targets such as "up to 40 % urban road space reclaimed" are **prospective planning
models** resting on a theoretical headway-compression assumption, and **not measured
claims of this synthetic run**. Nothing in this repository has achieved, measured or
demonstrated such a reduction.

### 13.5 MOCK GEMINI versus LIVE GEMINI

On start-up the session reads the environment. With no `$GEMINI_API_KEY` it builds
M6's `MockProvider`; with a key it builds `GeminiProvider`, and if that construction
fails for any reason it logs the failure and falls back to the mock. Which one is
live is carried in every state payload and shown in the UI, and `/api/health`
reports the mode from key *presence* only — it never reports a live call that did
not happen.

**MOCK GEMINI is a fixed arithmetic rule, not a language model** (§12.7): a deficit
below threshold means `NO_ACTION`, no eligible pod means `NO_ACTION`, deadhead
already above 40 % of all driving means `NO_ACTION`, and otherwise it requests
rebalancing toward the worst deficit nodes. It is deterministic and reviewable, and
it says nothing about what Gemini would choose. **No live Gemini call has been made
from this repository's own verification runs.**

Whichever provider is active, the path is unchanged: the proposal is parsed against
the closed four-action schema, validated against the live engine (including the
stale-observation check), and executed — if at all — by the existing M5/M3 entry
points. The UI cannot bypass any of that.


## 14. How to run

Python 3.11+. **No installation is needed for the engine, the CLI or the API
server** — they are standard library only. Only the frontend needs npm.

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

# M2 — passenger demand
python -m app.cli.main demand-demo
python -m app.cli.main demand-demo --profile peak_hour --passengers 2000
python -m app.cli.main demand-demo --profile scenarios/demand_baseline.json --passengers 500
python -m app.cli.main demand-demo --passengers 500 --horizon-min 600 --no-routing
python -m app.cli.main demand-demo --passengers 1000 --demand-seed 7 --algorithm dijkstra --top 10
```

`demand-demo` prints the scenario, passenger and trip counts, total demand, the
top OD pairs, demand per time bucket, a routing summary and the demand
fingerprint. `--demand-seed` is independent of the city `--seed`, so the same
city can carry different demand and vice versa.

```bash
# M3 — pod fleet
python -m app.cli.main fleet-demo
python -m app.cli.main fleet-demo --pods 100 --passengers 1000
python -m app.cli.main fleet-demo --pods 40 --capacity 6 --profile peak_hour
python -m app.cli.main fleet-demo --pods 20 --passengers 200 --tick-minutes 5
python -m app.cli.main fleet-demo --pods 50 --fleet-seed 7 --demand-seed 7
```

`fleet-demo` prints the fleet size and capacity, trip counts, assignments and
completions, average occupancy and utilisation, distance, energy, the final pod
states and the fleet fingerprint. `--fleet-seed`, `--demand-seed` and the city
`--seed` are all independent.

```bash
# M4 — swarm formation and platooning
python -m app.cli.main swarm-demo
python -m app.cli.main swarm-demo --pods 100 --passengers 1000
python -m app.cli.main swarm-demo --pods 40 --formation-delay 10 --max-swarm-size 6
python -m app.cli.main swarm-demo --pods 30 --profile peak_hour --examples 5
```

`swarm-demo` prints the thresholds in force, the swarms formed with their
corridors, split counts, participation, the **independent-vs-swarm comparison**,
the road-space estimate with its assumption spelled out, a preview of planned
(never executed) reposition requests, and both fingerprints.

```bash
# M5 — adaptive fleet rebalancing
python -m app.cli.main rebalancing-demo
python -m app.cli.main rebalancing-demo --pods 100 --passengers 1000
python -m app.cli.main rebalancing-demo --interval 20 --horizon 45 --max-per-cycle 5
python -m app.cli.main rebalancing-demo --pods 40 --no-swarms --snapshot-min 400
```

`rebalancing-demo` prints the forecast settings, the imbalance **before** (snapshotted
mid-run, where it actually bites, with the forecast next to what really arrived), the
moves it made with their reasons and priorities, the state **after**, and a
no-rebalancing vs adaptive comparison that includes every deadhead kilometre and
both wait figures.

```bash
# M6 — AI orchestration (mock mode needs no API key and no third-party package)
python -m app.cli.main ai-demo --provider mock
python -m app.cli.main ai-demo --provider mock --scenario B_NO_DEFICIT
python -m app.cli.main ai-demo --provider mock --scenario C_EXPENSIVE
python -m app.cli.main ai-demo --provider mock --cycles 3 --advance-min 30
python -m app.cli.main ai-demo --provider mock --scenario custom --pods 40 --passengers 300 --at-min 300
python -m app.cli.main ai-demo --provider mock --show-observation      # the full AI input, as JSON

# live mode — needs the optional SDK and a key, and is never needed for tests
pip install -r requirements-gemini.txt
export GEMINI_API_KEY=...          # never committed, never logged, never in a prompt
python -m app.cli.main ai-demo --provider gemini
```

`ai-demo` prints the simulation state, the observation and its fingerprint, the AI's
proposal (clearly labelled as the AI's words), every validator check with its
verdict, what the **engine** actually did, the metrics before and after, the audit
trail and the decision-process metrics. Without `$GEMINI_API_KEY` it says so plainly
and mock mode carries on; `--provider gemini` without a key exits `2` with one clean
line naming the variable.

Exit codes: `0` success, `1` no route, `2` invalid input or scenario,
`3` A*/Dijkstra cost mismatch (should never happen).

### The Visual Command Center (M7)

```bash
# the API server — standard library only, no pip install needed
python -m app.api.server                 # http://localhost:8000
python -m app.api.server --port 9000 --host 127.0.0.1

# the frontend — needs npm; node_modules/ and dist/ are gitignored
cd frontend
npm ci                                   # or npm install, the first time
npm test                                 # vitest run
npm run build                            # tsc && vite build -> frontend/dist/
npm run dev                              # Vite on :5173, proxying /api to :8000
```

Two ways to run it:

* **Development** — `python -m app.api.server` in one shell and `npm run dev` in
  another. Vite serves the UI on 5173 and proxies `/api` to 8000.
* **Single port** — `npm run build` first, then `python -m app.api.server` alone. The
  server serves `frontend/dist/` for every non-`/api` path.

If `frontend/dist/` does not exist, opening `/` returns the health JSON rather than
the UI. That means the frontend was never built, not that the server is broken.

Mock mode needs no key and no network: with `$GEMINI_API_KEY` unset the session uses
M6's deterministic `MockProvider` and the UI labels it **MOCK GEMINI** (§13.5).

## 15. How to run tests

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # pytest, and nothing else
pytest -q                                # 997 cases, roughly 2 minutes

cd frontend && npm ci && npm test        # vitest: 5 files, 14 cases
cd frontend && npm run build             # tsc && vite build
```

739 test functions (997 cases with parametrization) across models, network,
routing, determinism, edge cases, route-switch regression, and the M2 demand,
M3 fleet, M4 swarm, M5 rebalancing, M6 orchestration and M7 API layers, plus 14
frontend component cases in 5 files. **No test needs an API key, a network
connection or the Gemini SDK**, and the Python suite needs nothing from `frontend/`.
Highlights: A* and Dijkstra costs match for **all 462 ordered node
pairs**, and again under random congestion; the heuristic is checked for
admissibility against true costs from every node to every goal.

M2 contributes 109 test functions (159 cases) covering passenger and trip
determinism (including a **separate-process** check), stable ids, valid and
distinct OD pairs, party-size validation, OD aggregation and ordering, demand
totals reconciled across every view, time-of-day behaviour (peaks busier than
midday, and the residential flow reversing between morning and evening),
baseline versus peak_hour concentration, invalid node and profile handling,
routing integration and determinism, congestion changing a routed trip's cost,
snapshot fingerprints, scenario loading, empty/one-passenger/two-node edge
cases, and a 10,000-passenger performance check. One test walks the lower
layers to prove none of them imports `app.demand`.

M3 contributes 143 test functions (205 cases) covering pod validation and every
illegal state transition, pod-id and fleet-generation determinism (including a
**separate-process** check), fleet size and initial placement, capacity
validation and occupancy, assignment and its rejection on capacity, battery or
location, deterministic assignment ordering, route attachment matching M1's
router exactly, movement across single- and multi-edge routes, edge completion
and arrival, trip completion and pod release, battery consumption and clamping at
both bounds, charging and unavailability, fleet metrics reconciled against the
fleet and records, tick-by-tick and end-state determinism, congestion changing a
pod's travel time **and** its energy, empty-fleet and zero-trip edge cases, and a
100-pod / 1,000-trip benchmark. Further tests assert that a fleet run leaves M1's
traffic and M2's demand untouched, that A*/Dijkstra still agree afterwards, and
that no lower layer imports `app.fleet`.

M4 contributes 101 test functions (134 cases) covering swarm validation and the
minimum size of two, deterministic swarm ids, sorted pod ordering, each of the
five compatibility rules in isolation, shared-prefix and corridor-metric
computation, divergence detection, the formation algorithm's determinism and
independence from input order, `max_swarm_size`, leader selection, lockstep
platoon movement, the four-pod → three-plus-one split, partial continuation,
independent-pod preservation, rejoining, the stability window and the absence of
oscillation, congestion changing compatibility in **both** directions, the swarm
metrics and their reconciliation, the independent-vs-swarm comparison, empty
fleet / zero trip / single pod / no-compatible-pod cases, tick-by-tick and
separate-process determinism, and the rebalancing hook's read-only contract.
Further tests assert that platooning grants no distance or energy discount, that
`enable_swarms=False` reproduces M3 fingerprint-for-fingerprint, that
`PodStatus` still has no swarm states, and that no lower layer imports
`app.swarm`.

`tests/test_swarm_metric_semantics.py` (M4.1) pins the *meaning* of the
comparison metrics rather than any behaviour: each formula above, the
`road_occupancy_equiv_km == pod_distance_km` identity when nothing platoons, the
closed form of the saving, that `d × n` is exact because no member can leave
mid-corridor, that the occupancy factor scales the saving linearly and gives
exactly zero at `f = 1.0`, that the `*_currently_*` fields are a snapshot while
`distinct_pods_ever_in_a_swarm` is the total, that physical-km and equiv-km field
names stay textually distinct, and that raw cross-mode totals are **not**
like-for-like. It deliberately asserts nothing about swarm mode being better:
one test exists specifically to confirm the comparison is free to show swarm mode
driving further or finishing later.

M5 contributes 107 test functions (129 cases) covering the demand windows, forecast
determinism and its **refusal to read future trips**, the thin-history guard,
profile shares, every demand-map column definition, deficit and surplus, every
eligibility rule including the protection of pods in service, charging and in
swarms, deterministic matching and its independence from fleet insertion order, the
priority formula, all three reasons, cycle and concurrency caps, battery refusal,
unreachable and too-far targets, congestion changing a repositioning route and its
energy, empty repositioning movement and arrival, a pod's return to service, the
separation of passenger from repositioning trips, the demand-shift experiment
(pods reach area B **before** its peak), the controlled before/after comparison, the
efficiency formula, metrics reconciliation, tick-by-tick and separate-process
determinism, empty-fleet / zero-trip / no-deficit / no-surplus edge cases, and the
whole M6 boundary — read-only observation, inert proposals, and a validator that
rejects out-of-range and unknown parameters. Further tests assert that
`enable_rebalancing=False` reproduces M4 fingerprint-for-fingerprint, that
`PodStatus` still has five members, that `TripKind` defaults to passenger, and that
no lower layer imports `app.rebalancing`.

M6 contributes 161 test functions (209 cases) across five files. The observation is
checked for determinism, for a stable fingerprint, for surviving a JSON round trip,
for holding **plain values only** (a walker asserts no graph, fleet, pod, swarm,
simulation or callable is reachable), for carrying no wall clock, for changing
nothing in the engine, for stable ordering, and for agreeing across separate
processes under three `PYTHONHASHSEED` values. The schema tests cover the four-member
whitelist and ten forbidden action names, every malformed field, control-character
stripping and length capping of untrusted text, and the refusal to mine JSON out of
prose or out of a fenced code block. The validator tests walk every gate — stale
observation (and the `NO_ACTION` exemption), invented, repeated and over-many nodes,
pod-count type and bounds, insufficient eligible pods, insufficient battery,
rebalancing disabled, the concurrency limit, unknown and duplicated scenarios, an
oversized comparison, out-of-range horizons and run sizes — and assert that
validating changes nothing. The provider tests cover the mock's determinism
(including a separate-process replay), each of its three decision rules, the scripted
double's failure path, the system prompt's contents, and the Gemini provider's
configuration: a missing or blank key, the lazy SDK import, a schema-constrained
request shape through an injected stub client, prose and empty responses, the absence
of a retry loop, and that **no credential appears in `describe()`, `repr()`, the
instance, the prompt or the audit log**. The cycle tests run the loop end to end,
break it at every stage (provider failure, malformed response, forbidden action,
stale proposal, engine refusal), assert that executing a rejected verdict *raises*,
that `REQUEST_REBALANCING` goes through M5's own `apply_validated_action` rather than
a second rebalancer, that two identical runs produce byte-identical audit trails in
the same process and in another one, that there is no accuracy metric, and that the
`ai-demo` CLI works with no key and exits cleanly without one in live mode. Further
tests assert that no lower layer imports `app.orchestration`, that no module in M6
imports an SDK at module scope, that `app/rebalancing/` was not modified, and that M6
introduces no second cost or congestion model.

M7 contributes 16 test functions in `tests/test_api_server.py`. Nine cover session
initialisation and the state payload, stepping, the orchestration cycle through the
session's `OrchestrationAPI`, traffic injection and the demand map. The other seven
pin `/api/comparison`'s road-space reporting (§16): that `ScenarioResult` matches
`compute_swarm_metrics` field for field, that a run with swarms disabled gives
exactly `0.0` under M4's occupancy identity, that a run which really platoons equals
the closed form `Σ d(n−1)(1−f)`, that a run which drove no distance is reported
unavailable rather than zero, that no cross-run difference is reported, that the old
flat `road_space_saved_equiv_km` scalar cannot reappear, and that the response still
round-trips through JSON. All sixteen drive `SimulationSession` directly rather than
over a socket, so no port is bound and no HTTP client is needed.

The frontend's 14 cases in 5 files (`frontend/src/__tests__/`) cover `CityMap`,
`GeminiOrchestratorPanel`, `SimulationControls`, `TopMetricBar` and
`UrbanImpactPanel` with `@testing-library/react` under jsdom. They render components
against fixture props; they do not start the API server, and `npm run build` runs
`tsc` first, so a type error fails the build rather than only the lint.

`tests/test_route_switch_regression.py` (M1.1) pins the end-to-end behaviour that
congestion can change the chosen route. On the seed-42 baseline,
North Station -> East Hub is normally `E009 -> E011` via Northgate Junction
(13.93 min); driving `E011` to utilization 3.0 (4500 veh/h against a capacity of
1500, multiplier x5.15) makes the router switch to the fully edge-disjoint
`E013 -> E003` via Central Station (17.13 min, versus 45.16 min for the original
path under the same traffic). The tests assert the switch happens, that the new
route is genuinely cheaper under the modified costs, that Dijkstra and A* still
agree before/after/restored, that repeated executions are bit-identical, and that
restoring the original traffic restores the original route and cost exactly.

## 16. Known limitations

* Synthetic data only; **no calibration against real traffic, real mobility data or
  any real city**, and no real-world performance is claimed anywhere.
* Vehicle counts are static inputs — there is no traffic propagation, queueing,
  signal timing, turn penalties or time-of-day variation.
* Travel time is computed at query time; a long trip does not see congestion
  change along the way (no time-dependent routing).
* The overload slope (2.0) is an assumption; BPR itself is a coarse model.
* Equal-cost ties may make A* and Dijkstra return different (equally optimal) paths.
* Haversine assumes a spherical Earth (sub-0.5 % error; irrelevant at city scale).
* No route caching (deferred by design).

M2 demand:

* **Synthetic demand, not calibrated to any real mobility data.** The place
  roles, production/attraction weights, time-of-day shares, party-size
  distribution, commuter share and gravity exponent are all invented.
* Demand is static: trips are generated up front and do not react to congestion,
  travel time or each other. There is no feedback loop and no mode choice.
* One passenger's trips are independent draws; there is no tour or
  return-journey logic beyond the commuter home anchoring.
* The gravity decay uses straight-line distance, not network distance, and is
  applied for candidate weighting only.
* Trips are routed at the moment you ask, all against the same network state; a
  trip requested at 08:00 and one at 19:00 see identical traffic unless you
  change it yourself. There is no time-dependent routing (same M1 limitation).
* No grouping or platooning — a `TripRequest` is a wish to travel, not a booking.

M3 fleet:

* **Synthetic fleet, and the battery model is an approximation, not physics.**
  Energy scales linearly with distance and the congestion multiplier; charging is
  linear. Gradients, mass, payload, regenerative braking, temperature, battery
  ageing, charging curves and auxiliary loads are all ignored.
* **No repositioning, and this is the dominant limitation.** A pod is only
  eligible for a trip starting at the node it is already parked on; it never
  drives empty to a pickup. With 100 pods and 1,000 trips this — not fleet
  capacity — is what bounds the completion rate: pod utilisation sits near 6 %,
  so the fleet is far from busy, yet roughly half the trips find no co-located
  pod before the dispatch timeout. Repositioning and rebalancing are
  fleet-optimisation concerns deferred with M4.
* Assignment is local and greedy (lowest eligible `pod_id`), never globally
  optimised, so it can strand a nearby trip by committing a pod to an earlier one.
* One pod serves one trip at a time. No ride pooling, so separate parties never
  share a pod even when the seats would fit.
* An edge's cost is fixed once the pod is on it, so congestion appearing
  mid-traversal does not affect that edge (documented under *Movement*).
* Charging happens wherever the pod stands — there are no charging stations,
  queues or connector limits, and a pod occupies no space while charging.
* A pod that somehow reaches 0 % mid-route is clamped at 0 and keeps moving
  rather than stranding; the battery reserve check at assignment is what makes
  that essentially unreachable, not a physical guarantee.
* The dispatch timeout (`max_trip_wait_min`, 60 min) is a policy assumption, and
  it is what makes a run terminate rather than wait forever.

M4 swarms:

* **Formation is rule-based and greedy, not optimal.** The planner admits pods in
  `pod_id` order, so taking a pod early can prevent a larger group later. It is a
  reproducible baseline; no optimality is claimed.
* **The road-occupancy figure rests on one invented constant.** The
  `formation_occupancy_factor` of 0.4 is an assumption about headway, not a
  measurement, and the "benefit" it produces is road space only — never fuel,
  energy, emissions or time.
* **Co-location is inherited from M3 and dominates the results.** Pods only
  platoon if they are already at the same node, so on the seed-42 city with 100
  pods and 1,000 trips just 28 swarms form, almost all of size 2 (43 % of pods
  platoon at least once). With repositioning — M5's job — far more pods would be
  co-located and the numbers would look very different.
* **Platooning costs riders time here.** Waiting up to `max_formation_delay_min`
  raises average wait and completion time. Coordination is not free.
* Formation only happens at a departure or a divergence node, never mid-edge, so a
  pod that would have been a good partner two minutes into its trip is missed.
* The corridor must be a common *prefix*; pods whose paths merge later, cross, or
  run the same road in opposite directions never platoon.
* Nothing physical is modelled about being in a platoon — no inter-pod spacing,
  no coupling or decoupling time, no leader-follower dynamics, no magnetic
  linking, and no limit on how much of a road a formation may occupy.
* Swarm capacity is reported as the sum of member capacities but grants **no
  pooling**: a passenger is never moved between pods, so a full pod and an empty
  one in the same swarm cannot share.
* Rebalancing is implemented in M5; M4 itself ships only the interface.

M5 rebalancing:

* **Rebalancing is a heuristic, not an optimum.** The planner is greedy: it fills
  the biggest deficit first with the nearest spare pod. Filling a smaller deficit
  first, or moving a slightly further pod, could serve more trips. No optimality is
  claimed or attempted.
* **It is expensive.** On the headline run 8 979 km — 35.8 % of all driving — were
  driven empty, at 22.17 km per additional trip served. Whether that trade is worth
  making is a judgement the model does not make for you.
* **It makes two things worse.** Average wait rose 4.63 → 5.24 min and average
  completion 28.81 → 31.35 min: repositioning competes for the same pods and adds
  traffic. Reported, not hidden.
* **The forecast is crude and no accuracy is claimed.** It is a weighted blend of a
  recent rate and a published profile share. It has no trend, no seasonality, no
  day-of-week effect and no uncertainty estimate, and it systematically
  under-forecasts a rising peak (9.58 against 11 actual in the example above).
* It assumes the published demand profile is correct. Feed it a scenario whose
  profile does not match the demand and the profile half of the blend becomes
  actively misleading.
* Repositioning targets a node's forecast, not individual trips: a pod may arrive
  just as the demand it was sent for goes elsewhere.
* One pod per move, one move at a time. No swarm repositioning, no chaining, and no
  reconsideration once a pod is under way — a dispatched move always completes even
  if the reason for it has evaporated.
* `min_history_min` means nothing is repositioned in the first 15 simulated minutes,
  so a run that starts at a peak is caught flat-footed.
* The deadhead energy is M3's approximated battery model, and a repositioning pod
  earns nothing for the charge it spends.

M6 orchestration:

* **The AI's `pod_count` and `target_nodes` are a request, not a command.** An
  approved `REQUEST_REBALANCING` authorises one deterministic rebalancing cycle;
  M5's planner then decides every move on its own rules. In the worked example
  eleven pods were asked for and one was dispatched. That is the design, but it does
  mean the AI has less leverage than the schema suggests, and the gap is reported on
  every cycle rather than smoothed over.
* **The mock provider is a fixed rule, not a model.** It exists so the whole path is
  testable without a key. Its choices say nothing about what Gemini would choose, and
  no test asserts anything about a live model.
* **No live Gemini run is included here.** `$GEMINI_API_KEY` was not available in the
  environment this milestone was built in, so every claim in §12 was verified through
  the mock and scripted providers and an injected stub client. The live path's request
  shape, response handling and failure modes are covered; an actual API call is not.
* **Nothing measures whether a proposal was good.** Process is counted; outcome comes
  from the engine. There is no accuracy figure, by choice (§12.10).
* A cycle sees one instant. There is no memory between cycles beyond the audit log,
  so the orchestrator cannot notice that its last three proposals achieved nothing.
* `expected_effect` and `confidence` are stored and printed but never acted on. A
  confident wrong proposal is treated exactly like a hesitant one.
* The observation is a summary. A model cannot ask a follow-up question, inspect an
  individual pod, or request a different slice — it sees the fixed sections in §12.2
  and nothing else.
* Only `REQUEST_REBALANCING` touches the live simulation. `RUN_SIMULATION` and
  `COMPARE_SCENARIOS` start fresh deterministic runs and leave the current one alone.

M7 / M7.2 Visual Command Center:

* **`/api/comparison` reports road space per run, and never differences it across
  runs.** `impact_summary.road_space.per_run.{baseline,swarm,adaptive}` carries M4's
  own `road_occupancy_saved_equiv_km` and `road_occupancy_saving_percent` for that
  one run, passed through unaltered; `cross_run_difference_equiv_km` is always
  `null` and `cross_run_comparable` is always `false`. That is deliberate: the three
  modes serve different trip sets, because the formation delay shifts departures, so
  subtracting one mode's equiv-km total from another's would not measure
  coordination (§10 states the rule, and only the scale-free percentage compares
  across runs). A run that drove no distance is reported `available: false` with a
  reason, not as a zero.
* **On the comparison's own fixed spec the honest answer is zero.** With 60 pods,
  400 passengers and a 180-minute horizon, **no swarm forms at all** in any of the
  three modes, so every run's saving is exactly `0.0` equiv-km and the response says
  why in `zero_reason`. On this pod and passenger count the **first formation occurs
  at simulated minute 372**, in both the swarm and the adaptive mode — the baseline
  demand profile's morning peak does not begin until minute 360, so a 180-minute
  horizon ends long before any two pods are ever assigned at the same node.
  Extending the horizon to a full day gives **1.3 %** of pod-km in the swarm mode
  and **2.84 %** in the adaptive mode. An earlier version of this endpoint derived
  the figure from a hardcoded 0.85 factor instead of the engine's metric and
  consequently displayed about 44 equiv-km of road space "freed" where the engine's
  own figure was 0.000. That formula has been removed, a
  regression test asserts the flat scalar cannot reappear, and the figures above are
  what the engine actually produces.
* **No road-space reduction has been achieved, measured or demonstrated here.** The
  "up to 40 %" figure the UI mentions is a prospective planning assumption resting on
  a theoretical headway-compression factor, and the panel says so on screen. §10's
  occupancy saving is an estimate derived from `formation_occupancy_factor` and is
  explicitly not an aerodynamic, fuel, energy or emissions saving. The largest
  road-space saving this repository has actually measured on the comparison's pod and
  passenger count is **4.71 %** (adaptive mode, 600-minute horizon) — an order of
  magnitude below the prospective target, and reported as measured rather than
  aspired to.
* **No live Gemini call has been made from this repository's own verification runs.**
  `$GEMINI_API_KEY` was unset throughout, so the live provider's network path is
  exercised only through an injected stub client; everything reported was produced by
  MOCK GEMINI.
* **One process-wide session, and no authentication.** The server holds a single
  simulation guarded by a lock, so it serves one operator at a time; a second browser
  shares and perturbs the same state. There is no login, no CSRF protection, no rate
  limiting and no TLS. It binds all interfaces by default. **Run it on localhost
  only; it is not hardened for exposure to a network.**
* The UI has no persistence. Reloading the page re-reads the server's current state,
  and restarting the server loses the session, the audit log and the event feed.
* **Not every read takes the operation lock.** `/api/simulation/state`,
  `/api/simulation/events` and `/api/demo/state` hold it, and every POST does, but
  `/api/network`, `/api/demand-map`, `/api/comparison` and the three orchestrator
  reads do not. The server is threaded, so a read issued while a POST is mutating
  the simulation can observe it part-way through. In practice the UI polls from one
  browser between its own writes, so this has not been seen to bite — but it is a
  real gap, not a guarantee.
* `/api/comparison` runs three 180-minute simulations synchronously inside the
  request. For its fixed 60-pod / 400-passenger spec that measured about 0.2 s here,
  so it is not slow today; it would be if the spec grew.
* The event feed is in-memory only. The deriver accumulates events for the life of
  the session and `/api/simulation/events` replays them, but nothing is written to
  disk: a server restart or `/api/simulation/reset` loses the feed. Events are also
  only derived when something calls the deriver (a step, a cycle, a demo beat), so
  the feed reflects what was driven, not a continuous watch.
* Demo Mode is one fixed script over one fixed start state. A phase whose gate is not
  met inside its beat budget is reported `not_observed` — honest, but it means the
  demo does not guarantee that every phase will be demonstrated on every run.
* The frontend's tests render components against fixture props. There is no
  end-to-end test that starts the API server and drives the real UI, so the contract
  between `src/types/api.ts` and the server's JSON is maintained by hand.

## 17. Future milestones

* **M1** — deterministic city + network foundation ✅
* **M1.1** — route-switch regression validation ✅
* **M2** — deterministic passenger demand model ✅
* **M3** — deterministic pod fleet simulation ✅
* **M4** — deterministic swarm formation and platooning ✅
* **M4.1** — swarm metric semantics audit ✅
* **M5** — adaptive fleet rebalancing and the M6 boundary ✅
* **M6** — Gemini orchestration and validated AI control ✅
* **M7** — stdlib HTTP adapter and the React Visual Command Center ✅
* **M7.2** — deterministic Demo Mode and the derived event feed ✅
* Not started — nothing beyond M7.2. The nearest known work is an end-to-end test
  across the server and the real UI, so the contract between `src/types/api.ts` and
  the server's JSON stops being maintained by hand (§16).

**M5 completes the deterministic engine. M6 is the only thing that proposes, and M7
is the only thing that displays.** Everything through M5 is reproducible from a seed,
uses only the Python standard library and runs offline — and all of that is still
true with M6 and the M7 API server in place, since both are standard library too.
The orchestrator observes through a read-only snapshot and proposes bounded actions
that a deterministic validator checks before the engine acts. It cannot mutate
simulation state, cannot widen its own bounds, and cannot execute anything. The
frontend cannot either: it reaches Python only over HTTP, and every endpoint it can
call goes through the same validator. **Gemini is an orchestrator, not the simulation
engine.**

The pipeline is `passenger demand → trip requests → routing → pod grouping → swarm
formation`, with rebalancing closing the loop back to demand. M1–M5 implement all of
it deterministically; M6 proposes against it and M7 shows it.
