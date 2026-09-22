# Modular Swarm Network — working notes for Claude Code

Read `README.md` first: it documents the architecture, the congestion formula, the
A* admissibility proof and the synthetic-city assumptions.

## Status

Milestones 1, 1.1, 2, 3, 4, 4.1, 5 and 6 are complete and green: 723 test functions /
981 parametrized cases, all passing. **M5 completes the deterministic engine; M6 is
the orchestration layer above it.**

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
* **M4.1** — `tests/test_swarm_metric_semantics.py`: the comparison metrics were
  audited, found numerically correct, and renamed to carry their units; the
  formulas are now pinned by tests.
* **M5** — `app/rebalancing/`: deterministic demand forecasting, a spatial demand
  map, eligibility rules, surplus/deficit matching, **empty** pod repositioning
  with its full deadhead cost, and the read-only M6 boundary.
* **M6** — `app/orchestration/`: an AI orchestration layer that observes the engine
  through plain frozen values, proposes one of four whitelisted actions, has a
  deterministic validator rule on it against the live engine, and lets the existing
  M5/M3 entry points execute it. Mock provider by default; Gemini optional and
  lazily imported. **M1-M5 were not modified** — the engine is byte-for-byte
  identical to the M5 commit, and tests pin the layering and the boundary contract
  that keep it that way.

Next is the **visual dashboard / UI**, which is not started. Do not build HTTP,
FastAPI, Streamlit, React or Antigravity UI code. A UI would sit on
`app/orchestration/api.py`'s six pure-Python operations.

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
python -m app.cli.main rebalancing-demo --pods 100 --passengers 1000
python -m app.cli.main ai-demo --provider mock
python -m app.cli.main ai-demo --provider mock --scenario C_EXPENSIVE --cycles 2
pip install -r requirements-gemini.txt   # OPTIONAL, live Gemini only
python -m app.cli.main ai-demo --provider gemini   # needs $GEMINI_API_KEY
```

## Invariants — do not break these

1. **Stdlib only at runtime.** No Streamlit, FastAPI, NetworkX, requests, DB or
   external APIs. pytest is test-only. The **one** permitted exception is M6's
   optional `google-genai`, which lives in `requirements-gemini.txt`, is imported
   inside `GeminiProvider.__init__` and is never needed by the engine, the mock
   path or the test suite — a test asserts no module in `app/orchestration/`
   imports an SDK at module scope.
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
   swarm ← rebalancing ← orchestration ← cli. Never import downward-to-upward:
   nothing below a layer may import `app.demand`, `app.fleet`, `app.swarm`,
   `app.rebalancing` or `app.orchestration` (tests walk the lower layers to enforce
   all five).
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
   **Units are part of the contract:** `*_km` fields are physical kilometres
   driven, `*_equiv_km` fields are single-pod-equivalent road space. Never add or
   difference across the two, and keep the names textually distinct — a test
   enforces the split. `compute_swarm_metrics` must keep defaulting to the
   simulation's own `swarm_config`, or occupancy gets scored under a factor the
   run never used. Cross-mode raw totals are not like-for-like (the modes serve
   different trips); only `road_occupancy_saving_percent` compares directly.
12. **`app/swarm/rebalancing.py` stays a hook.** It plans and returns
   `RepositionRequest`s and must stay read-only. M5's `AdaptiveRebalancer`
   implements that protocol and is what actually executes.
13. **The forecast must never read the future.** `build_forecast` sees only trips
   already requested (the recent window) plus M2's published profile. The
   simulation holds the whole trip list, so peeking is one line away and would
   make every number meaningless — a test asserts that adding future trips leaves
   the forecast byte-identical. `actual_near_future_demand` is evaluation-only and
   must never become an input.
14. **Passenger trips and repositioning trips stay separate.** An empty move is
   `TripKind.REPOSITIONING` with `party_size=0`, creates no `TripRecord`, counts
   under `completed_repositioning_count`, and its kilometres are reported as
   deadhead — never netted off a passenger figure. A passenger's pod is never
   taken: only `IDLE`, non-charging, non-swarmed, sufficiently charged pods move.
15. **The cost of rebalancing is never hidden.** Deadhead km, minutes and kWh are
   reported on their own, and the efficiency figure is trips per empty km — not a
   return on investment. If rebalancing makes wait or completion time worse, say
   so; two figures currently do get worse and the README states both.
16. **The M6 boundary stays a boundary.** `observation.py` hands out frozen plain
   values only — no graph, fleet, pod or simulation. Proposals are inert data,
   every settable parameter has explicit numeric bounds, and
   `apply_validated_action` accepts nothing but an already-validated action. The
   AI must never mutate simulation state directly. M6 honours this by **composing**
   `observe()` rather than editing it: the whole M1-M5 engine is byte-for-byte
   unchanged (verified by `git diff` against the M5 commit), and tests pin the
   layering that keeps it so.
17. **AI output is untrusted input, always.** It is parsed as data against a closed
   schema and never executed, `eval`'d, imported, shelled out, written to a file or
   used to name a module. `parse_action` uses `json.loads` on the whole response —
   no fence stripping, no regex hunt for a JSON substring, no repair. Free-form AI
   text is stripped of control characters and length-capped before it is stored or
   printed. The four-member `AIActionType` enum is the whitelist, so a forbidden
   action is rejected *structurally* rather than by a list someone must maintain.
18. **The validator re-derives everything from the live engine.** It never trusts a
   proposal's own account of the world, and it never mutates. Staleness is the
   heart of it: an action carries the fingerprint of the observation it was reasoned
   from, and `REJECT_STALE_OBSERVATION` stops a decision about one city state being
   applied to another. `NO_ACTION` is the only exemption. Do not add a bypass, a
   "trusted" provider, or a check that consults `confidence`.
19. **M6 implements nothing the engine already does.** `REQUEST_REBALANCING` runs
   M5's `apply_validated_action`; `RUN_SIMULATION` and `COMPARE_SCENARIOS` compose
   existing constructors. The AI's `pod_count`/`target_nodes` are a **request**: M5's
   planner decides every move, and the gap between asked and dispatched is reported
   on every cycle, never smoothed. There is no second cost, congestion, battery or
   forecasting model, and a test greps for one.
20. **No secret, and no fake accuracy.** The API key is read from the environment,
   handed to the SDK client and never stored, logged, printed, fingerprinted or put
   in a prompt; `describe()` reports presence only. And there is deliberately **no
   "AI accuracy" metric** — the heuristic it invokes is not claimed optimal, so
   there is no ground truth. Count process; take outcomes from the engine.

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

## The M5 rebalancing layer

`app/rebalancing/` sits on top of `app/swarm/` and is organised as:

| Module | Holds |
|---|---|
| `config.py` | every threshold: windows, forecast weights, `min_history_min`, cycle/concurrency caps, battery reserve, priority weights |
| `eligibility.py` | the pod-protection rules and the battery predicate |
| `forecast.py` | `DemandWindow`, `build_forecast`, `profile_shares` |
| `demand_map.py` | `SpatialDemandMap` — forecast against eligible supply, per node |
| `planner.py` | `AdaptiveRebalancer` — matching, priority, the three reasons |
| `execution.py` | `RepositionAssignment`, `dispatch`, `complete`, `fail` |
| `simulation.py` | `RebalancingSimulation`, a `SwarmSimulation` subclass |
| `metrics.py` | `RebalancingMetrics`, `compare_rebalancing_modes` |
| `observation.py` | the M6 boundary: `observe`, `ProposedAction`, `ActionValidator` |

`RebalancingSimulation` extends `SwarmSimulation` and overrides one step —
`_depart_assigned_pods`, to send empty pods off without formation — then appends a
rebalancing cycle after M4's tick. `enable_rebalancing=False` reproduces M4
exactly and a test pins that; keep it true, because the before/after experiment
depends on it. With rebalancing off the engine still *observes* the deficit on the
same cadence so the two modes compare like for like.

The one edit M5 needed below itself was additive: `TripKind` on `Pod`, so a pod can
be dispatched empty (`party_size=0`) and its arrivals counted apart from passenger
arrivals. `PodStatus` is unchanged.

## The M6 orchestration layer

`app/orchestration/` sits on top of `app/rebalancing/` and is organised as:

| Module | Holds |
|---|---|
| `config.py` | every bound an approved proposal is held to, plus the mock's rules and `NO_ACCURACY_NOTE` |
| `observation.py` | `OrchestrationObservation`, `build_observation`, `canonical_json`, `fingerprint_of` |
| `actions.py` | `AIActionType` (the whitelist), `AIAction`, `ParsedAction`, `parse_action`, `clean_text` |
| `validator.py` | `OrchestrationValidator`, `ValidationVerdict`, `Verdict`, every `REJECT_*` code |
| `execution.py` | `ActionExecutor`, `ExecutionResult`, `ExecutionStatus` — mapping actions onto M5/M3 |
| `scenarios.py` | `ScenarioSpec`, `run_scenario`, `build_simulation`, `EVALUATION_SCENARIOS` A/B/C |
| `providers/` | `base.py` (the protocol), `mock.py` (`MockProvider`, `ScriptedProvider`), `gemini.py` |
| `prompt.py` | `SYSTEM_PROMPT`, `RESPONSE_SCHEMA`, `build_user_payload` |
| `orchestrator.py` | `Orchestrator.run_cycle` / `run_cycles` — the bounded loop |
| `audit.py` | `OrchestrationRecord`, `AuditLog`, `cycle_id_for` |
| `metrics.py` | `OrchestrationMetrics`, `compute_orchestration_metrics` |
| `api.py` | the six pure-Python operations a future UI sits on |

Things to know before changing anything:

* **The loop is caller-driven.** `run_cycle` runs one cycle and returns; `run_cycles`
  is capped by `max_cycles`. There is no background thread, no autonomous loop and no
  automatic retry (`provider_max_attempts` is 1 — a failed call must not quietly
  become three calls against a paid API).
* **`advance_min` is the caller's, not the AI's, and its delta is a window rather
  than an attribution.** A dispatched move changes nothing that instant, so measuring
  an effect needs the engine advanced — but during those minutes the engine also runs
  its own rebalancing cycles and ordinary service, so most of the delta is not the
  AI's doing. The detail string says so; never let a report imply otherwise.
* **Latency is the one non-deterministic number.** It lives on `record.timing()`,
  outside `to_dict()`, so two identical runs produce byte-identical audit trails —
  pinned by a test, including across processes.
* **`expected_effect` and the engine's result are stored side by side, never merged.**
  Collapsing them is how a system starts reporting an AI's intentions as results.
* The mock provider is a **fixed rule, not a model**, and its choices say nothing
  about what Gemini would do. Assert expectations against it only.
* Evaluation scenarios A/B/C are tuned to genuinely pose their situations (a large
  deficit; no deficit; a real deficit that is already too expensive to chase). If you
  change fleet sizes or warm-up minutes, re-check that each still poses what it claims.

## When starting the UI

The dashboard is the next milestone and is **not started**. It must sit on
`app/orchestration/api.py`'s six operations — `get_observation`, `propose_action`,
`validate_action`, `execute_action`, `get_result`, `get_audit_log` — which already
return JSON-serialisable plain values, so an HTTP adapter over them should carry no
logic of its own. Do not reach past that façade into the engine, do not add a second
validator, and do not let a UI control widen `PARAMETER_BOUNDS` or the orchestration
bounds. Build a new package; do not modify `app/rebalancing/` or `app/orchestration/`
beyond what the UI genuinely needs, and keep every invariant above.
