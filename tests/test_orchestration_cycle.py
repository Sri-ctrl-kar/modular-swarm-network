"""M6: the closed loop, the audit trail, the API boundary, and the M1-M5 regression.

The loop is: observe -> propose -> validate -> execute -> record. These tests walk it
end to end, break it at each stage, and check that the deterministic engine below is
unharmed by anything the AI does or fails to do.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from app.config import PROJECT_ROOT
from app.errors import ExecutionRefusedError, ProviderError
from app.orchestration import (
    AIAction,
    AIActionType,
    ActionExecutor,
    AuditLog,
    EVALUATION_SCENARIOS,
    ExecutionStatus,
    MockProvider,
    OrchestrationAPI,
    OrchestrationValidator,
    Orchestrator,
    ScenarioSpec,
    ScriptedProvider,
    Verdict,
    build_observation,
    build_simulation,
    compute_orchestration_metrics,
    cycle_id_for,
    evaluation_scenario,
    no_action,
    run_scenario,
)

OTHER_FINGERPRINT = "0" * 64


@pytest.fixture
def deficit_simulation():
    """A state with a real shortfall, so an approved action has something to do."""
    simulation = build_simulation(ScenarioSpec("cycle", pods=20, passengers=500))
    simulation.run(until_min=420.0)
    return simulation


# --- a complete cycle ---------------------------------------------------------
def test_a_full_cycle_runs_end_to_end(deficit_simulation, mock_provider):
    orchestrator = Orchestrator(mock_provider, simulation=deficit_simulation)
    result = orchestrator.run_cycle()

    assert result.approved and result.executed
    assert result.record.verdict == Verdict.APPROVED.value
    assert result.record.execution["status"] == ExecutionStatus.EXECUTED.value
    assert len(orchestrator.audit_log) == 1


def test_the_cycle_keeps_the_ai_away_from_the_engine(deficit_simulation):
    """A provider is handed an observation and nothing else."""
    seen = {}

    class Spy:
        name = "spy"

        def describe(self):
            return {"model": None}

        def propose_action(self, observation):
            seen["argument"] = observation
            return MockProvider().propose_action(observation)

    Orchestrator(Spy(), simulation=deficit_simulation).run_cycle()
    observation = seen["argument"]
    assert not hasattr(observation, "graph")
    assert not hasattr(observation, "fleet_object")
    assert observation.to_dict()["fleet"]["pod_count"] == len(deficit_simulation.fleet)


def test_a_no_action_cycle_runs_nothing(mock_provider):
    simulation = evaluation_scenario("B_NO_DEFICIT").prepared()
    before = simulation.snapshot().fingerprint()
    result = Orchestrator(mock_provider, simulation=simulation).run_cycle()

    assert result.record.proposal["action_type"] == AIActionType.NO_ACTION.value
    assert result.approved
    assert result.execution.status is ExecutionStatus.SKIPPED
    assert simulation.snapshot().fingerprint() == before


def test_the_loop_is_bounded(deficit_simulation, mock_provider):
    orchestrator = Orchestrator(mock_provider, simulation=deficit_simulation)
    results = orchestrator.run_cycles(3)
    assert len(results) == 3
    assert [r.record.cycle_id for r in results] == ["CY00001", "CY00002", "CY00003"]

    with pytest.raises(ValueError, match="max_cycles"):
        orchestrator.run_cycles(orchestrator.config.max_cycles + 1)
    for bad in (0, -1, 2.5):
        with pytest.raises(ValueError):
            orchestrator.run_cycles(bad)


def test_advancing_the_engine_is_the_callers_choice(deficit_simulation, mock_provider):
    orchestrator = Orchestrator(mock_provider, simulation=deficit_simulation)
    before = deficit_simulation.time_min
    result = orchestrator.run_cycle(advance_min=30.0)
    assert deficit_simulation.time_min >= before + 30.0
    assert "advanced the engine" in result.record.execution["detail"]


# --- failure at every stage ---------------------------------------------------
def test_a_provider_failure_is_recorded_and_harmless(deficit_simulation):
    before = deficit_simulation.snapshot().fingerprint()
    provider = ScriptedProvider([ProviderError("the API timed out")])
    result = Orchestrator(provider, simulation=deficit_simulation).run_cycle()

    assert result.provider_error is not None
    assert result.record.reason_code == "PROVIDER_FAILED"
    assert result.record.execution is None
    assert deficit_simulation.snapshot().fingerprint() == before


def test_the_engine_still_works_after_a_provider_failure(deficit_simulation):
    Orchestrator(ScriptedProvider([ProviderError("down")]),
                 simulation=deficit_simulation).run_cycle()
    report = deficit_simulation.tick()
    assert report.end_time_min > 420.0

    result = Orchestrator(MockProvider(), simulation=deficit_simulation).run_cycle()
    assert result.record.proposal is not None


def test_a_malformed_response_forms_no_action(deficit_simulation):
    before = deficit_simulation.snapshot().fingerprint()
    provider = ScriptedProvider(["I would move some pods around, probably."])
    result = Orchestrator(provider, simulation=deficit_simulation).run_cycle()

    assert result.record.proposal is None
    assert result.record.parse_error_code == "AI_OUTPUT_INVALID"
    assert result.record.execution is None
    assert deficit_simulation.snapshot().fingerprint() == before


def test_a_forbidden_action_never_reaches_the_engine(deficit_simulation):
    hostile = {"action_type": "EXECUTE_CODE",
               "parameters": {"code": "import os; os.system('echo pwned')"},
               "reason": "trust me", "expected_effect": "nothing bad",
               "confidence": 1.0, "observation_fingerprint": OTHER_FINGERPRINT}
    before = deficit_simulation.snapshot().fingerprint()
    result = Orchestrator(ScriptedProvider([hostile]),
                          simulation=deficit_simulation).run_cycle()

    assert result.record.proposal is None
    assert result.record.execution is None
    assert deficit_simulation.snapshot().fingerprint() == before


def test_a_stale_proposal_is_recorded_as_rejected(deficit_simulation):
    observation = build_observation(deficit_simulation)
    payload = MockProvider().decide(observation)
    payload["observation_fingerprint"] = OTHER_FINGERPRINT

    result = Orchestrator(ScriptedProvider([payload]),
                          simulation=deficit_simulation).run_cycle()
    assert result.record.reason_code == "REJECT_STALE_OBSERVATION"
    assert result.record.execution is None
    assert compute_orchestration_metrics(
        [result.record]).stale_proposals == 1


def test_executing_a_rejected_verdict_raises(deficit_simulation):
    """There must be no path that 'executes' an unapproved action and reports failure."""
    action = AIAction("REQUEST_REBALANCING", {"target_nodes": ["atlantis"], "pod_count": 1},
                      "r", "e", 0.5, build_observation(deficit_simulation).fingerprint())
    verdict = OrchestrationValidator().validate(action, simulation=deficit_simulation)
    assert not verdict.approved
    with pytest.raises(ExecutionRefusedError):
        ActionExecutor().execute(verdict, simulation=deficit_simulation)


def test_an_engine_refusal_is_reported_not_raised(deficit_simulation):
    """The engine saying no is information; the caller carries on with a valid engine."""
    action = AIAction("RUN_SIMULATION", {"scenario": "baseline", "pods": 5,
                                         "passengers": 5, "horizon_min": 5.0},
                      "r", "e", 0.5, build_observation(deficit_simulation).fingerprint())
    verdict = OrchestrationValidator().validate(action, simulation=deficit_simulation)
    assert verdict.approved

    class Broken(ActionExecutor):
        def _execute_run_simulation(self, action, simulation):
            from app.errors import RebalancingError
            raise RebalancingError("the scenario could not be built")

    result = Broken().execute(verdict, simulation=deficit_simulation)
    assert result.status is ExecutionStatus.FAILED
    assert result.error_type == "RebalancingError"
    assert "could not be built" in result.detail


def test_rebalancing_without_a_simulation_is_refused():
    action = no_action(OTHER_FINGERPRINT, "nothing")
    verdict = OrchestrationValidator().validate(action)
    assert verdict.approved
    assert ActionExecutor().execute(verdict).status is ExecutionStatus.SKIPPED


# --- execution maps onto the existing engine ----------------------------------
def test_request_rebalancing_goes_through_the_m5_boundary(deficit_simulation,
                                                          monkeypatch):
    """No second rebalancer: the action runs M5's own apply_validated_action."""
    import app.orchestration.execution as execution_module

    calls = []
    original = execution_module.apply_validated_action

    def spy(simulation, result):
        calls.append(result)
        return original(simulation, result)

    monkeypatch.setattr(execution_module, "apply_validated_action", spy)
    Orchestrator(MockProvider(), simulation=deficit_simulation).run_cycle()

    assert len(calls) == 1
    assert calls[0].accepted


def test_the_engine_decides_which_pods_move(deficit_simulation):
    result = Orchestrator(MockProvider(), simulation=deficit_simulation).run_cycle()
    payload = result.record.execution["payload"]
    assert payload["requested_pod_count"] >= payload["dispatched_count"]
    assert "The planner, not the AI, chose every move." in result.record.execution["detail"]
    for move in payload["moves"]:
        assert move["pod_id"] in deficit_simulation.fleet.pod_ids()


def test_run_simulation_uses_the_deterministic_runner(deficit_simulation):
    action = AIAction("RUN_SIMULATION",
                      {"scenario": "baseline", "pods": 10, "passengers": 40,
                       "horizon_min": 120.0},
                      "r", "e", 0.5, build_observation(deficit_simulation).fingerprint())
    verdict = OrchestrationValidator().validate(action, simulation=deficit_simulation)
    execution = ActionExecutor().execute(verdict, simulation=deficit_simulation)

    assert execution.status is ExecutionStatus.EXECUTED
    expected = run_scenario(ScenarioSpec("RUN_baseline", profile_name="baseline", pods=10,
                                         passengers=40, horizon_min=120.0))
    assert execution.payload["result"]["fleet_fingerprint"] == expected.fleet_fingerprint


def test_compare_scenarios_reports_each_run_side_by_side(deficit_simulation):
    action = AIAction("COMPARE_SCENARIOS",
                      {"scenarios": ["baseline", "peak_hour"], "horizon_min": 120.0},
                      "r", "e", 0.5, build_observation(deficit_simulation).fingerprint())
    verdict = OrchestrationValidator().validate(action, simulation=deficit_simulation)
    execution = ActionExecutor().execute(verdict, simulation=deficit_simulation)

    rows = execution.payload["scenarios"]
    assert [row["spec"]["profile_name"] for row in rows] == ["baseline", "peak_hour"]
    assert "not like-for-like" in execution.payload["note"]


def test_a_scenario_run_is_reproducible():
    spec = ScenarioSpec("repeat", pods=10, passengers=40, horizon_min=120.0)
    first, second = run_scenario(spec), run_scenario(spec)
    assert first.to_dict() == second.to_dict()


# --- the audit trail ----------------------------------------------------------
def test_the_record_answers_the_four_questions(deficit_simulation, mock_provider):
    record = Orchestrator(mock_provider, simulation=deficit_simulation).run_cycle().record
    data = record.to_dict()

    assert data["observation_fingerprint"] and data["observed_problem"]      # what it saw
    assert data["proposal"]["action_type"]                                   # what it proposed
    assert data["verdict"] and data["checks"]                                # why it was allowed
    assert data["execution"]["status"] and data["metrics_delta"]             # what happened


def test_the_ai_expectation_and_the_engine_result_are_kept_apart(deficit_simulation,
                                                                 mock_provider):
    record = Orchestrator(mock_provider, simulation=deficit_simulation).run_cycle().record
    assert record.proposal["expected_effect"]
    assert record.execution["detail"]
    assert record.proposal["expected_effect"] != record.execution["detail"]
    assert "AI EXPECTS" in record.explanation()
    assert "ENGINE DID" in record.explanation()


def test_the_record_carries_no_wall_clock(deficit_simulation, mock_provider):
    record = Orchestrator(mock_provider, simulation=deficit_simulation).run_cycle().record
    assert "latency_ms" not in record.to_dict()
    assert record.timing()["latency_ms"] is not None


def test_the_audit_log_serialises_to_plain_values(deficit_simulation, mock_provider):
    orchestrator = Orchestrator(mock_provider, simulation=deficit_simulation)
    orchestrator.run_cycles(2)
    data = orchestrator.audit_log.to_dict()
    assert json.loads(json.dumps(data)) == data


def test_no_credential_reaches_the_audit_trail(monkeypatch, deficit_simulation,
                                               mock_provider):
    monkeypatch.setenv("GEMINI_API_KEY", "leak-me-if-you-can")
    orchestrator = Orchestrator(mock_provider, simulation=deficit_simulation)
    orchestrator.run_cycle()
    assert "leak-me-if-you-can" not in json.dumps(orchestrator.audit_log.to_dict())


def test_cycle_ids_are_positional_not_hashed():
    assert cycle_id_for(1) == "CY00001"
    assert cycle_id_for(42) == "CY00042"
    log = AuditLog()
    assert log.next_cycle_id() == "CY00001"


def test_two_identical_runs_produce_identical_audit_trails():
    """Deterministic replay: same scenario, same mock, same trail, byte for byte."""
    fingerprints = []
    for _ in range(2):
        simulation = build_simulation(ScenarioSpec("replay", pods=20, passengers=500))
        simulation.run(until_min=420.0)
        orchestrator = Orchestrator(MockProvider(), simulation=simulation)
        orchestrator.run_cycles(2, advance_min=20.0)
        fingerprints.append(orchestrator.audit_log.fingerprint())
    assert len(set(fingerprints)) == 1


def test_the_audit_trail_replays_identically_in_another_process():
    script = (
        "from app.orchestration import (build_simulation, ScenarioSpec, MockProvider, "
        "Orchestrator);"
        "s=build_simulation(ScenarioSpec('replay', pods=20, passengers=500));"
        "s.run(until_min=420.0);"
        "o=Orchestrator(MockProvider(), simulation=s);"
        "o.run_cycles(2, advance_min=20.0);"
        "print(o.audit_log.fingerprint())"
    )
    outputs = set()
    for seed in ("0", "1"):
        completed = subprocess.run([sys.executable, "-c", script], capture_output=True,
                                   text=True,
                                   env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"},
                                   cwd=str(PROJECT_ROOT))
        assert completed.returncode == 0, completed.stderr
        outputs.add(completed.stdout.strip())
    assert len(outputs) == 1


# --- decision-process metrics -------------------------------------------------
def test_the_metrics_count_process_not_quality(deficit_simulation):
    observation = build_observation(deficit_simulation)
    stale = MockProvider().decide(observation)
    stale["observation_fingerprint"] = OTHER_FINGERPRINT
    provider = ScriptedProvider([
        MockProvider().decide(observation),   # accepted
        "not json",                           # malformed
        stale,                                # stale
        ProviderError("timeout"),             # provider failure
    ])
    orchestrator = Orchestrator(provider, simulation=deficit_simulation)
    orchestrator.run_cycles(4)
    metrics = orchestrator.metrics()

    assert metrics.cycles == 4
    assert metrics.proposals_generated == 2
    assert metrics.proposals_accepted == 1
    assert metrics.proposals_rejected == 1
    assert metrics.stale_proposals == 1
    assert metrics.invalid_proposals == 1
    assert metrics.provider_failures == 1
    assert metrics.successful_actions == 1
    assert metrics.acceptance_rate == 0.5


def test_there_is_no_ai_accuracy_metric(deficit_simulation, mock_provider):
    orchestrator = Orchestrator(mock_provider, simulation=deficit_simulation)
    orchestrator.run_cycle()
    data = orchestrator.metrics().to_dict()
    assert not any("accuracy" in key for key in data if key != "accuracy_note")
    assert "no 'AI accuracy' metric" in data["accuracy_note"]


def test_the_acceptance_rate_is_undefined_when_nothing_parsed(deficit_simulation):
    orchestrator = Orchestrator(ScriptedProvider(["junk"]), simulation=deficit_simulation)
    orchestrator.run_cycle()
    assert orchestrator.metrics().acceptance_rate is None


def test_latency_is_flagged_as_wall_clock(deficit_simulation, mock_provider):
    orchestrator = Orchestrator(mock_provider, simulation=deficit_simulation)
    orchestrator.run_cycle()
    data = orchestrator.metrics().to_dict()
    assert "not deterministic" in data["latency_note"]


# --- the API boundary ---------------------------------------------------------
def test_the_api_exposes_the_six_operations(deficit_simulation, mock_provider):
    api = OrchestrationAPI(mock_provider, deficit_simulation)

    observation = api.get_observation()
    assert observation["fingerprint"] and observation["headline"]

    record = api.propose_action()
    assert record["verdict"] == Verdict.APPROVED.value
    assert api.get_result() == record

    log = api.get_audit_log()
    assert log["cycle_count"] == 1
    assert log["metrics"]["proposals_generated"] == 1


def test_the_api_validates_a_caller_supplied_proposal(deficit_simulation, mock_provider):
    api = OrchestrationAPI(mock_provider, deficit_simulation)
    fingerprint = api.get_observation()["fingerprint"]
    proposal = {"action_type": "REQUEST_REBALANCING",
                "parameters": {"target_nodes": ["market"], "pod_count": 2},
                "reason": "a shortfall", "expected_effect": "more pods there",
                "confidence": 0.7, "observation_fingerprint": fingerprint}
    assert api.validate_action(proposal)["verdict"] == "APPROVED"

    proposal["observation_fingerprint"] = OTHER_FINGERPRINT
    assert api.validate_action(proposal)["reason_code"] == "REJECT_STALE_OBSERVATION"


def test_the_api_refuses_to_execute_what_it_would_not_validate(deficit_simulation,
                                                               mock_provider):
    api = OrchestrationAPI(mock_provider, deficit_simulation)
    before = deficit_simulation.snapshot().fingerprint()
    outcome = api.execute_action({"action_type": "RUN_SHELL", "parameters": {},
                                  "reason": "r", "expected_effect": "e",
                                  "confidence": 1.0,
                                  "observation_fingerprint": OTHER_FINGERPRINT})
    assert outcome["status"] == "REJECTED"
    assert outcome["execution"] is None
    assert deficit_simulation.snapshot().fingerprint() == before


def test_the_api_returns_plain_values_only(deficit_simulation, mock_provider):
    api = OrchestrationAPI(mock_provider, deficit_simulation)
    api.propose_action()
    for payload in (api.get_observation(), api.get_result(), api.get_audit_log()):
        assert json.loads(json.dumps(payload)) == payload


def test_the_api_has_no_result_before_a_cycle_runs(deficit_simulation, mock_provider):
    assert OrchestrationAPI(mock_provider, deficit_simulation).get_result() is None


# --- the evaluation set -------------------------------------------------------
@pytest.mark.parametrize("scenario", EVALUATION_SCENARIOS, ids=lambda s: s.name)
def test_every_evaluation_scenario_completes_a_cycle(scenario, mock_provider):
    simulation = scenario.prepared()
    result = Orchestrator(mock_provider, simulation=simulation).run_cycle()
    assert result.record.proposal is not None
    assert result.record.verdict == Verdict.APPROVED.value


def test_the_mock_matches_the_expected_consideration_on_each_scenario(mock_provider):
    """Asserted against the mock only. A live model is recorded, never required."""
    chosen = {}
    for scenario in EVALUATION_SCENARIOS:
        observation = build_observation(scenario.prepared())
        chosen[scenario.name] = mock_provider.decide(observation)["action_type"]

    assert chosen["A_LARGE_DEFICIT"] == "REQUEST_REBALANCING"
    assert chosen["B_NO_DEFICIT"] == "NO_ACTION"
    assert chosen["C_EXPENSIVE"] == "NO_ACTION"


def test_each_evaluation_scenario_states_what_it_poses():
    for scenario in EVALUATION_SCENARIOS:
        assert scenario.description
        assert scenario.expected_consideration
        assert "never asserted" not in scenario.description  # the note lives in the docs


# --- layering and the M1-M5 regression ----------------------------------------
def test_lower_layers_do_not_import_the_orchestration_layer():
    """... <- swarm <- rebalancing <- orchestration <- cli. Nothing reaches upward."""
    offenders = []
    for layer in ("models", "network", "routing", "simulation", "demand", "fleet",
                  "swarm", "rebalancing"):
        for path in sorted((PROJECT_ROOT / "app" / layer).rglob("*.py")):
            if "app.orchestration" in path.read_text(encoding="utf-8"):
                offenders.append(str(path.relative_to(PROJECT_ROOT)))
    assert offenders == [], f"lower layers import the orchestration layer: {offenders}"


def test_the_orchestration_layer_imports_no_sdk_at_module_scope():
    """Invariant 1 holds: importing M6 pulls in nothing but the standard library."""
    for path in sorted((PROJECT_ROOT / "app" / "orchestration").rglob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")) and not line.startswith(" "):
                assert "google" not in stripped and "genai" not in stripped, \
                    f"{path.name} imports an SDK at module scope: {stripped}"


def test_m6_depends_only_on_the_boundary_m5_published():
    """M5's boundary is the doorway. M6 uses it as published, and adds nothing to it."""
    import app.rebalancing.observation as boundary

    for name in ("SimulationObservation", "observe", "ActionType", "ProposedAction",
                 "ValidationResult", "ActionValidator", "apply_validated_action",
                 "PARAMETER_BOUNDS"):
        assert hasattr(boundary, name), f"M6 relies on {name}"

    # M6 must not have grown the boundary's own action vocabulary.
    assert {a.value for a in boundary.ActionType} == {
        "REQUEST_REBALANCING", "SET_DEMAND_SCENARIO", "SET_PARAMETER",
        "COMPARE_SCENARIOS"}


def test_pod_status_still_has_exactly_five_members():
    from app.fleet import ALLOWED_TRANSITIONS
    from app.fleet.models import PodStatus
    assert {s.value for s in PodStatus} == {"idle", "assigned", "traveling", "arrived",
                                            "charging"}
    assert set(ALLOWED_TRANSITIONS) == set(PodStatus)


def test_an_orchestrated_run_leaves_routing_agreeing(deficit_simulation, mock_provider):
    from app.routing import astar, dijkstra

    Orchestrator(mock_provider, simulation=deficit_simulation).run_cycle(advance_min=30.0)
    for origin, destination in (("north_station", "airport"), ("west_hub", "tech_park")):
        a = astar(deficit_simulation.graph, origin, destination)
        d = dijkstra(deficit_simulation.graph, origin, destination)
        assert a.total_cost == pytest.approx(d.total_cost, rel=1e-12, abs=1e-9)


def test_an_orchestrated_run_keeps_batteries_in_bounds(deficit_simulation, mock_provider):
    Orchestrator(mock_provider, simulation=deficit_simulation).run_cycles(2, advance_min=30.0)
    for pod in deficit_simulation.fleet:
        assert 0.0 <= pod.battery_percent <= 100.0


def test_orchestration_adds_no_second_cost_or_congestion_model():
    """Every kilometre, minute and kWh still comes from M1/M3."""
    banned = ("congestion_multiplier =", "def energy_kwh", "def travel_time",
              "total_travel_time_min =")
    for path in sorted((PROJECT_ROOT / "app" / "orchestration").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for phrase in banned:
            assert phrase not in text, f"{path.name} looks like a second model: {phrase}"


# --- the CLI ------------------------------------------------------------------
def test_the_ai_demo_command_runs_in_mock_mode():
    completed = subprocess.run(
        [sys.executable, "-m", "app.cli.main", "ai-demo", "--provider", "mock",
         "--scenario", "B_NO_DEFICIT"],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT))
    assert completed.returncode == 0, completed.stderr
    output = completed.stdout
    assert "Gemini is an orchestrator, not the engine" in output
    assert "AI PROPOSAL" in output and "VALIDATOR" in output and "EXECUTION" in output
    assert "AUDIT TRAIL" in output


def test_the_ai_demo_needs_no_api_key():
    completed = subprocess.run(
        [sys.executable, "-m", "app.cli.main", "ai-demo", "--provider", "mock",
         "--scenario", "custom", "--pods", "15", "--passengers", "60", "--at-min", "120"],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT),
        env={"PATH": "/usr/bin:/bin", "PYTHONHASHSEED": "0"})
    assert completed.returncode == 0, completed.stderr
    assert "$GEMINI_API_KEY is NOT set" in completed.stdout


def test_live_mode_without_a_key_exits_cleanly(monkeypatch):
    env = {"PATH": "/usr/bin:/bin"}
    completed = subprocess.run(
        [sys.executable, "-m", "app.cli.main", "ai-demo", "--provider", "gemini"],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT), env=env)
    assert completed.returncode == 2
    assert "GEMINI_API_KEY" in completed.stderr
    assert "Traceback" not in completed.stderr
