# Modular Swarm Network — working notes for Claude Code

Read `README.md` first: it documents the architecture, the congestion formula, the
A* admissibility proof and the synthetic-city assumptions.

## Status

Milestones 1, 1.1 and 2 are complete and green: 184 test functions /
275 parametrized cases, all passing.

* **M1** — deterministic city + network foundation.
* **M1.1** — `tests/test_route_switch_regression.py`: congestion can change the
  optimal route, and restoring traffic restores it.
* **M2** — `app/demand/`: deterministic synthetic passenger demand — passengers,
  trip requests, an OD matrix, time-of-day profiles and routing integration.

Next milestone is **M3: pod simulation** — not started. Do not implement pods,
platooning, swarm control, LLM integration or a dashboard yet.

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
   Demand generation uses a local `random.Random(seed)` and never the global
   `random` module; every weighted draw walks a key-sorted list so results never
   depend on dict or graph insertion order.
3. **A\* heuristic stays admissible.** It relies on two graph rules enforced in
   `NetworkGraph.add_edge`: road distance ≥ straight-line distance, and
   free-flow speed ≤ `NETWORK_MAX_SPEED_KMH`. Congestion multipliers are always
   ≥ 1. Changing any of these means redoing the proof in `app/routing/costs.py`.
4. **Assumptions live in `app/config.py`** and the scenario file, not inside
   algorithms. No magic numbers in routing or graph code.
5. **Layering:** models ← network ← routing ← simulation ← demand ← cli. Never
   import downward-to-upward; in particular routing must never import
   `app.demand` (a test walks the lower layers to enforce this).
6. **Typed errors only** (`app/errors.py`); the CLI turns them into one clean
   line on stderr with exit code 1 (no route) or 2 (invalid input).
7. **Synthetic data must stay labelled as synthetic.** No real-world
   performance claims. Demand carries the stronger wording it needs: it is a
   synthetic model for simulation and is **not calibrated to real-world city
   mobility data**.
8. **Demand does not reach down.** `app/demand/` consumes `SimulationState`,
   `NetworkGraph` and `find_route` as they are. It must not reimplement routing,
   cache routes, or change the congestion model or M1's `Node`/`Edge`. The
   demand layer's `PlaceRole` classification exists precisely so that adding
   land-use information does not require touching M1.

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

## When starting M3

Pods consume `TripRequest`s (and their routes) without modifying `app/demand/`,
the same way demand consumes M1. Put them in a new package with their own seeded
RNG and tests. The pipeline is `demand → trips → routing → pod grouping → swarm
formation`; M2 ends at routing integration.
