# Modular Swarm Network — working notes for Claude Code

Read `README.md` first: it documents the architecture, the congestion formula, the
A* admissibility proof and the synthetic-city assumptions.

## Status

Milestone 1 (deterministic city + network foundation) is complete and green:
64 test functions / 105 parametrized cases, all passing. Next milestone is
**M2: passenger demand simulation** — not started. Do not implement pods,
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
```

## Invariants — do not break these

1. **Stdlib only at runtime.** No Gemini/OpenAI/Anthropic SDKs, no Streamlit,
   FastAPI, NetworkX, requests, DB or external APIs. pytest is test-only.
2. **Determinism.** Same seed → byte-identical scenario JSON; same input →
   identical `Route`. Tie-breaking is by node id; adjacency keeps insertion
   order. `scenarios/baseline.json` must stay byte-identical to
   `generate_synthetic_city_dict(42)` (a test enforces this) — regenerate it
   with `export-scenario` rather than hand-editing.
3. **A\* heuristic stays admissible.** It relies on two graph rules enforced in
   `NetworkGraph.add_edge`: road distance ≥ straight-line distance, and
   free-flow speed ≤ `NETWORK_MAX_SPEED_KMH`. Congestion multipliers are always
   ≥ 1. Changing any of these means redoing the proof in `app/routing/costs.py`.
4. **Assumptions live in `app/config.py`** and the scenario file, not inside
   algorithms. No magic numbers in routing or graph code.
5. **Layering:** models ← network ← routing ← simulation ← cli. Never import
   downward-to-upward.
6. **Typed errors only** (`app/errors.py`); the CLI turns them into one clean
   line on stderr with exit code 1 (no route) or 2 (invalid input).
7. **Synthetic data must stay labelled as synthetic.** No real-world
   performance claims.

## When starting M2

Build on top of `SimulationState` (`app/simulation/state.py`) — it owns the
clock, the graph and per-edge vehicle counts, and exposes snapshot/restore for
reproducibility. Passengers/demand should be a new package (`app/demand/`) that
consumes this foundation without modifying it, with its own seeded RNG and
tests.
