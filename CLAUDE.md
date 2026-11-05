# Modular Swarm Network — working notes for Claude Code

Read `README.md` first: it documents the architecture, the congestion formula, the
A* admissibility proof and the synthetic-city assumptions.

## Status

Milestones 1, 1.1, 2, 3, 4, 4.1, 5, 6, 7 and 7.2 are complete and green: **997
Python cases** (739 test functions) and **14 frontend cases** (5 files), all passing;
`npm run build` succeeds. The `v0.7.2-demo` tag marks `12515ae`; the branch has moved
on since, so re-read the counts rather than trusting the tag.

**M5 completes the deterministic engine. M6 is the orchestration boundary above it.
M7/M7.2 is the Visual Command Center — an HTTP adapter and a React frontend — above
that.** The engine is still the source of truth: every number the UI shows is read
from M1-M6, and M6's `OrchestrationAPI` is the only way anything reaches the engine.

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
* **M7** — `app/api/`: a **stdlib-only** HTTP adapter (`http.server`, no FastAPI, no
  Flask, no requests) over the engine, and `frontend/`: a React + TypeScript + Vite
  Visual Command Center that talks to it over JSON. The UI reaches the orchestrator
  only through M6's `OrchestrationAPI`.
* **M7.2** — `app/api/demo.py` (a deterministic scripted Demo Mode) and
  `app/api/events.py` (an event feed derived from real state differences), plus the
  frontend panels that show them.

The UI **is built** — do not write it again, and do not describe it as absent. The
next milestone is **not started**; do not begin it without being asked. Before
changing anything here, read §"The M7 API adapter" and §"The M7 frontend" below:
they record which parts are load-bearing.

## Commands

Python engine, CLI and API (no third-party runtime dependency):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # pytest only; runtime is stdlib-only
pytest -q                                # 997 cases, ~2.5 min
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

The Visual Command Center (M7). The API server is stdlib-only; only the frontend
needs npm, and `frontend/node_modules/` and `frontend/dist/` are gitignored:

```bash
python -m app.api.server                 # serves http://localhost:8000 (--host, --port)

cd frontend
npm ci                                   # or npm install, the first time
npm test                                 # vitest run — 5 files, 14 cases
npm run build                            # tsc && vite build -> frontend/dist/
npm run dev                              # Vite on :5173, proxying /api to :8000
```

Two ways to run the UI. In development, `npm run dev` serves the frontend on 5173
and proxies `/api` to the Python server on 8000 (`frontend/vite.config.ts`). For a
single-port run, `npm run build` first: the API server serves `frontend/dist/` for
any non-`/api` path, and falls back to the health JSON when `dist/` is absent — so a
browser showing raw JSON at `/` means the frontend was never built, not that the
server is broken.

## Invariants — do not break these

1. **Stdlib only in Python at runtime, frontend deps stay in the frontend.** No
   Streamlit, FastAPI, Flask, NetworkX, requests, DB or external APIs anywhere under
   `app/` — **including `app/api/`, which is built on `http.server` and must stay
   that way**. `requirements.txt` is still pytest-only. Two bounded exceptions:
   * M6's optional `google-genai` in `requirements-gemini.txt`, imported inside
     `GeminiProvider.__init__`, never needed by the engine, the mock path or the
     test suite — a test asserts no module in `app/orchestration/` imports an SDK
     at module scope;
   * `frontend/`'s npm toolchain (React, Vite, Tailwind, vitest), which is a
     **separate build**, never imported by Python, and whose `node_modules/` and
     `dist/` are gitignored.
   So `pytest -q` and every CLI command must keep working with nothing installed
   but pytest, on a checkout where `frontend/` was never touched.
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
   swarm ← rebalancing ← orchestration ← {cli, api} ← frontend. Never import
   downward-to-upward: nothing below a layer may import `app.demand`, `app.fleet`,
   `app.swarm`, `app.rebalancing` or `app.orchestration` (tests walk the lower
   layers to enforce all five). `app/api/` and `app/cli/` are siblings at the top of
   the Python stack: neither imports the other, and nothing under `app/` imports
   `app.api`. The frontend sits above everything and reaches Python only over HTTP.
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
   there is no ground truth. Count process; take outcomes from the engine. This
   extends to the API: `/api/health` reports `gemini_mode` from key *presence*, and
   a UI badge saying LIVE GEMINI must never be shown for a mock run.
21. **The API adapter adapts; it does not compute.** `app/api/` reads the engine and
   serialises it. It must not add a cost, congestion, battery, forecasting or
   scoring model, must not re-derive a metric the engine already publishes, and must
   reach the orchestrator only through M6's `OrchestrationAPI` — never by building
   an `AIAction` and executing it itself, and never past `ActionValidator`.
   **A concrete instance, and the lesson it carries.**
   `/api/comparison` used to synthesise road space as
   `max(0, baseline.pod_distance_km - swarm.pod_distance_km * 0.85)`. That 0.85
   appears nowhere in the engine; the expression differenced physical km against a
   scaled quantity, labelled the result `equiv_km`, and differenced across two runs
   that serve different trip sets. It displayed 44.029 equiv-km on the endpoint's own
   spec where M4's figure was 0.000. **It was a synthesized formula and was never
   valid.** The cause was that `ScenarioResult` carried no occupancy field, so there
   was nothing canonical to read: when a figure is missing, plumb the engine's metric
   through, never invent one.
   See invariant 11 for the units and §"Road space" below for the current rule.
22. **Events are differences between real states, never timers.** Every kind in
   `DERIVED_KINDS` is emitted only when two successive reads of real engine state
   differ, or when a real M6 audit record says so; if nothing relevant changed,
   `observe()` returns `[]`. Lifecycle and operator inputs
   (`SIMULATION_INIT`, `SCENARIO_LOADED`, `TRAFFIC_INJECTED`, `DEMO_INPUT`) are
   **separately named and must stay separately named**, because they are inputs, not
   findings. Never emit an event because time passed or because a demo phase wanted
   one on screen.
23. **Demo Mode is a script of inputs, not a recording.** It injects operator inputs
   into the real simulation and then *waits* for the matching derived event; a phase
   whose gate is not met inside its beat budget is reported `not_observed` and the
   machine moves on. Never fake an event, extend a budget, or tune a threshold to
   make a phase light up. There is no wall-clock anywhere in it, so
   `reset -> run demo` must stay reproducible.

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
| `scenarios.py` | `ScenarioSpec`, `run_scenario`, `build_simulation`, `EVALUATION_SCENARIOS` A/B/C; `ScenarioResult` also publishes M4's `road_occupancy_*` fields and `swarm_count` |
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

## The M7 API adapter

`app/api/` sits on top of `app/orchestration/` and is organised as:

| Module | Holds |
|---|---|
| `server.py` | `SimulationSession`, `VisualCommandCenterHandler`, `run_server` — routes, serialisation, static files |
| `demo.py` | `DemoRunner`, `PHASES`, `IMPACT_METRICS` and the demo's fixed constants |
| `events.py` | `EventDeriver`, the event kinds, `DERIVED_KINDS`, the congestion/surge thresholds |
| `__init__.py` | re-exports `run_server` only |

Everything is stdlib: `http.server.ThreadingHTTPServer`, `json`, `urllib.parse`,
`mimetypes`. `DualStackServer` binds `::` and falls back to IPv4.

Routes. GET: `/api` and `/api/health`, `/api/network`, `/api/scenarios`,
`/api/simulation/state`, `/api/simulation/events`, `/api/demo/state`,
`/api/demand-map`, `/api/orchestrator/observation`, `/api/orchestrator/audit`,
`/api/orchestrator/result`, `/api/comparison`; anything else under `/api/` is a 404,
and anything outside it is served from `frontend/dist/`. POST:
`/api/simulation/init`, `/api/simulation/step`, `/api/simulation/reset`,
`/api/simulation/set-traffic`, `/api/route-switch`, `/api/orchestrator/cycle`,
`/api/orchestrator/validate`, `/api/orchestrator/execute`, `/api/demo/start`,
`/api/demo/advance`, `/api/demo/pause`, `/api/demo/resume`.

Things to know before changing anything:

* **One process-wide session.** `_session` is a single `SimulationSession` guarded by
  `_session_lock` (creation) and `_op_lock`, an `RLock` held across each mutating
  request. The server is threaded, so a handler that touches the simulation must hold
  `_op_lock`; dropping it would let two requests tick the same engine at once and
  destroy determinism.
* **`/api/simulation/reset` restores the current scenario's exact initial state** by
  re-running `_init_simulation()`, and leaves Demo Mode. Keep that true: the demo's
  reproducibility claim rests on `reset -> run demo`.
* **The three manual actions are refused during a demo.** `step`,
  `orchestrator/cycle` and `orchestrator/execute` raise `_DemoBusy` -> HTTP 409 while
  a demo is `running` or `paused` (`_reject_if_demo`), so a demo run cannot be
  contaminated half way through. `validate` is allowed, because it executes nothing.
* **Provider choice is automatic and must stay honest.** `_init_simulation` builds a
  `GeminiProvider` when `api_key_status()` reports a key and falls back to
  `MockProvider` on any failure, logging the fallback; `provider_type` and
  `gemini_label` carry which one is live. Never label a mock run as live.
* `/api/scenarios` lists the three standard scenarios (`baseline`, `peak_hour`,
  `DEMO_MORNING_RUSH`) plus M6's three evaluation scenarios, each with its
  `expected_consideration`. Scenario definitions live in M6 and M2 — the list here is
  presentation, so add a scenario in its own layer first.
* `tests/test_api_server.py` drives `SimulationSession` directly rather than over a
  socket, so it is fast and needs no port. Keep new API tests that way.

### Road space

The rule, and the only acceptable source:

* **Read `road_occupancy_saved_equiv_km` and `road_occupancy_saving_percent` from
  M4**, via `ScenarioResult` (whose fields `run_scenario` copies out of
  `compute_swarm_metrics` unaltered) or via a live run's `compute_swarm_metrics`.
  `_road_space_entry` and `_road_space_summary` in `app/api/server.py` select and
  label those fields; the only computation in that path is
  `available = pod_distance_km > 0.0`. Keep it that way.
* **Units.** `saved_equiv_km = pod_distance_km - road_occupancy_equiv_km`, in
  single-pod-equivalent km. Both operands are equiv-km: `pod_distance_km` enters as
  *occupancy if nothing platooned*, at the 1.0 equiv-km-per-km identity that
  `tests/test_swarm_metric_semantics.py` asserts. The percentage divides that by the
  **same run's** pod-km, so it is dimensionless.
* **Never difference it across runs.** `cross_run_difference_equiv_km` is always
  `null` and `cross_run_comparable` always `false`, because the modes serve different
  trip sets (the formation delay shifts departures). Only the scale-free percentage
  compares between runs — invariant 11's rule, enforced here.
* **Zero is a real answer; undefined is a different one.** No formations means
  exactly `0.0` with a `zero_reason`; a run that drove no distance means
  `available: false` with an `unavailable_reason`, not a zero. Do not collapse the
  two, and do not substitute a plausible-looking number for either.

#### Measured results versus the planning target — keep these apart

Measured by this repository, on the comparison's 60-pod / 400-passenger spec:

| run | horizon | saving |
|---|---|---|
| any of the three modes | 180 min (the endpoint's own) | **0.0 %** — no swarm forms |
| swarm mode | full day | **1.3 %** |
| adaptive mode | full day | **2.84 %** |
| adaptive mode | 600 min | **4.71 %** — the largest measured |

The 180-minute zero is correct, not a bug: the baseline profile's morning peak starts
at minute 360 and the first formation occurs at **minute 372**, so the horizon ends
before two pods are ever assigned at the same node.

The **"up to 40 % urban road space reclaimed"** figure in `UrbanImpactPanel` is a
**prospective planning assumption**, resting on a theoretical headway-compression
factor, and the panel says so on screen. It is not a result, it has not been measured
here, and it must never be presented as achieved. Nor is any of this validated
against the real world: the city, demand and `formation_occupancy_factor = 0.4` are
invented (invariants 7 and 11), the saving is a road-space *estimate* and not a fuel,
energy, emissions or time saving, and no real-world performance may be claimed.

## Deterministic Demo Mode and the event feed (M7.2)

Demo Mode is a fixed sequence of **inputs** over the real simulation, with **phase
gates** on real derived events. The start state is fully specified
(`DEMO_SCENARIO_ID = "DEMO_MORNING_RUSH"`, 40 pods, 600 passengers, from
`DEMO_START_MIN = 360.0` — the quiet minutes before the morning ramp), and the beats
are counted, not timed: `TICKS_PER_BEAT = 2`, `MAX_BEATS_PER_PHASE = 45`,
`BASELINE_BEATS = 2`, `IMPACT_BEATS = 10`, with one scripted traffic injection at
`INJECT_UTILIZATION = 1.5`.

The eight phases are `BASELINE`, `DEMAND_SURGE`, `CONGESTION`, `ROUTING`, `SWARM`,
`REBALANCING`, `GEMINI`, `IMPACT`. Each names the derived events that gate it; a
phase is `complete` only once those events have actually been produced, and
`not_observed` if its budget runs out. `IMPACT_METRICS` carries a polarity per metric
(`-1` lower is better, `+1` higher, `0` neutral) so the impact panel can say a figure
got **worse** — which it must be allowed to do.

The feed is derived in `events.py`. Ten kinds are state differences
(`DEMAND_SURGE`, `CONGESTION_DETECTED`, `ROUTE_ADAPTATION`, `SWARM_FORMED`,
`SWARM_SPLIT`, `DEMAND_IMBALANCE`, `FLEET_REBALANCING`, `GEMINI_PROPOSAL`,
`VALIDATION`, `EXECUTION`); four are inputs (`SIMULATION_INIT`, `SCENARIO_LOADED`,
`TRAFFIC_INJECTED`, `DEMO_INPUT`). Ids are `EVT-00001` upward in emission order —
positional, never a Python `hash()`.

The thresholds are documented constants, not dials: `CONGESTION_THRESHOLD = 0.80`
with hysteresis at `CONGESTION_CLEAR = 0.70` (an edge must fall below it before it
can alert again), `SURGE_MIN_ABS_TRIPS = 2.0` **and** `SURGE_MIN_REL = 0.50` together,
and `IMBALANCE_MIN_DEFICIT = 1.0`. `EventDeriver` is read-only with respect to the
simulation: it keeps only what it needs to spot a transition (previous swarm
statuses, previous reposition ids, which edges are currently over the threshold).
Changing any threshold changes which events a given run produces, so treat them as
part of the contract.

## The M7 frontend

`frontend/` is React 19 + TypeScript + Vite 6 + Tailwind 3, tested with vitest and
`@testing-library/react` under jsdom. It holds no simulation logic: it fetches,
renders and sends operator inputs.

| Path | Holds |
|---|---|
| `src/App.tsx` | the shell: polling, session state, wiring between panels |
| `src/api/client.ts` | the single `api` object — every `fetch` lives here, under `/api` |
| `src/types/api.ts` | the TypeScript mirror of the API's JSON shapes |
| `src/map/CityMap.tsx` | the city, pods, edges and swarms |
| `src/panels/` | `TopBar`, `TopMetricBar`, `SimulationControls`, `GeminiOrchestratorPanel`, `EventTimeline`, `UrbanImpactPanel`, `ScenarioComparisonModal` |
| `src/components/` | `DemoController`, `StartupSequence` |
| `src/__tests__/` | five component suites, 14 cases |

Things to know before changing anything:

* **All network access goes through `src/api/client.ts`.** Add an endpoint there, not
  a bare `fetch` in a component, and mirror its shape in `src/types/api.ts`.
* **`npm run build` runs `tsc` first**, so a type error fails the build, not just the
  lint. Run `npm test && npm run build` before claiming the frontend works.
* `npm run dev` proxies `/api` to `127.0.0.1:8000`, so the Python server must be
  running separately in development.
* The UI must not restate an engine number it did not fetch, and must not present the
  AI's `expected_effect` as a result — M6 keeps those apart, and so must the panels.
* **Road space, in both panels, comes from M4's metric.** `UrbanImpactPanel` reads a
  live run's `road_occupancy_saved_equiv_km`; `ScenarioComparisonModal` reads
  `impact_summary.road_space.per_run.swarm` and renders `n/a` with the server's
  reason when it is unavailable. Neither may compute a saving, and the modal must
  keep showing the server's `cross_run_note` and `semantics` strings rather than
  implying a cross-mode difference. The panel's own footnote about the prospective
  40 % target must stay labelled as a planning assumption — see §"Road space" above.

## When starting the next milestone

Nothing beyond M7.2 is started, and nothing here should be started unasked. Whatever
comes next, the rules above still hold: the engine stays the source of truth, the
orchestrator reaches it only through `app/orchestration/api.py`, `app/api/` stays
stdlib-only and free of models of its own, events stay differences between real
states, and the demo stays a script of inputs that is allowed to report a phase as
`not_observed` and a metric as worse. Do not modify `app/rebalancing/`, and keep
`pytest -q` green with pytest as the only installed package.
