# Modular Swarm Network — Milestones 1 + 2

**Deterministic city + network foundation, and a deterministic passenger-demand model.**

> ⚠️ **All data in this milestone is SYNTHETIC.** The city, coordinates, distances,
> capacities and traffic counts are invented. Nothing here claims real-world
> transportation performance.

## 1. What M1 and M2 do

M1 answers one question reliably and reproducibly:

> *Given an origin and a destination, what is the current fastest route through the city network?*

M2 adds a second:

> *Given a city network, where are people trying to travel, when are they travelling, and how much demand exists between origins and destinations?*

It provides validated node/edge models, a directed weighted graph with dynamic
(traffic-dependent) costs, a transparent congestion model, Dijkstra and A*
routing with a proven-admissible heuristic, a seeded synthetic city, a
human-readable scenario file, a minimal simulation state, and a CLI.

## 2. What M1 + M2 deliberately do NOT do

No pods, platooning or splitting (M3+), no swarm
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
          │ app/demand/  (M2)                         │  passengers, trips, OD matrix
          │  config.py  generator.py  models.py       │  seeded RNG, no vehicles yet
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

Dependencies point downward only: `models ← network ← routing ← simulation ← demand ← cli`.
M2's demand layer consumes the M1 foundation and is imported by none of it (a test
enforces this). Future layers (pods, swarm controller) sit above demand.

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

## 9. How to run

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

Exit codes: `0` success, `1` no route, `2` invalid input or scenario,
`3` A*/Dijkstra cost mismatch (should never happen).

## 10. How to run tests

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest -q
```

184 test functions (275 cases with parametrization) across models, network,
routing, determinism, edge cases, route-switch regression and the M2 demand
layer. Highlights: A* and Dijkstra costs match for **all 462 ordered node
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

## 11. Known limitations

* Synthetic data only; no calibration against real traffic.
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
* No vehicles, capacity limits, pods, grouping or platooning — a `TripRequest`
  is a wish to travel, not a booking.

## 12. Future milestones

* **M1** — deterministic city + network foundation ✅
* **M1.1** — route-switch regression validation ✅
* **M2** — deterministic passenger demand model ✅
* **M3** — pod simulation (next; not started)
* Later — swarm formation, platooning / splitting on trunk corridors,
  predictive demand, AI-assisted control, dashboard, impact comparison.

The eventual pipeline is `passenger demand → trip requests → routing → pod
grouping → swarm formation`. M2 implements the first two stages plus the routing
integration; everything downstream is deliberately absent.
