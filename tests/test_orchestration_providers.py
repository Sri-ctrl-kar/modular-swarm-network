"""M6: providers — the deterministic mock, the scripted double, and Gemini's configuration.

No test here makes a network call, and none needs GEMINI_API_KEY. The live provider is
exercised through an injected stub client, so its request shape and response handling
are covered without contacting anything, and nothing asserts what a real model would say.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from app.errors import ProviderConfigurationError, ProviderError
from app.orchestration import (
    ACTION_WHITELIST,
    DEFAULT_ORCHESTRATION_CONFIG,
    GeminiProvider,
    MockProvider,
    OrchestrationConfig,
    ScriptedProvider,
    api_key_status,
    build_observation,
    evaluation_scenario,
    parse_action,
)
from app.orchestration.prompt import (
    RESPONSE_SCHEMA,
    SYSTEM_PROMPT,
    build_user_payload,
    render_user_prompt,
)
from app.orchestration.providers.base import AIProvider, ProviderResponse


# --- the mock provider --------------------------------------------------------
def test_the_mock_satisfies_the_provider_interface(mock_provider):
    assert isinstance(mock_provider, AIProvider)
    assert mock_provider.name == "mock"
    assert mock_provider.describe()["requires_api_key"] is False


def test_the_mock_is_deterministic(mock_provider, ai_observation):
    payloads = {json.dumps(mock_provider.decide(ai_observation), sort_keys=True)
                for _ in range(10)}
    assert len(payloads) == 1


def test_the_mock_replays_identically_in_another_process():
    script = (
        "import json;"
        "from app.orchestration import build_observation, evaluation_scenario, MockProvider;"
        "s=evaluation_scenario('B_NO_DEFICIT');"
        "o=build_observation(s.prepared());"
        "print(json.dumps(MockProvider().decide(o), sort_keys=True))"
    )
    outputs = set()
    for seed in ("0", "1", "2"):
        completed = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True,
            env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"},
            cwd=str(__import__("app.config", fromlist=["x"]).PROJECT_ROOT))
        assert completed.returncode == 0, completed.stderr
        outputs.add(completed.stdout.strip())
    assert len(outputs) == 1


def test_the_mock_only_ever_proposes_whitelisted_actions(mock_provider):
    for name in ("A_LARGE_DEFICIT", "B_NO_DEFICIT", "C_EXPENSIVE"):
        observation = build_observation(evaluation_scenario(name).prepared())
        assert mock_provider.decide(observation)["action_type"] in ACTION_WHITELIST


def test_the_mock_carries_the_fingerprint_it_was_shown(mock_provider, ai_observation):
    payload = mock_provider.decide(ai_observation)
    assert payload["observation_fingerprint"] == ai_observation.fingerprint()


def test_the_mock_response_parses_against_the_schema(mock_provider, ai_observation):
    response = mock_provider.propose_action(ai_observation)
    assert isinstance(response, ProviderResponse)
    assert parse_action(response.payload, raw_text=response.raw_text).is_valid


def test_the_mock_declines_when_there_is_no_deficit(mock_provider):
    observation = build_observation(evaluation_scenario("B_NO_DEFICIT").prepared())
    payload = mock_provider.decide(observation)
    assert payload["action_type"] == "NO_ACTION"
    assert "below" in payload["reason"]


def test_the_mock_declines_when_the_deadhead_cost_is_already_high(mock_provider):
    """A real shortfall is not on its own a reason to spend more empty kilometres."""
    observation = build_observation(evaluation_scenario("C_EXPENSIVE").prepared())
    assert observation.demand["total_deficit"] >= \
        DEFAULT_ORCHESTRATION_CONFIG.mock_min_deficit_to_propose
    payload = mock_provider.decide(observation)
    assert payload["action_type"] == "NO_ACTION"
    assert "already empty" in payload["reason"]


def test_the_mock_proposes_rebalancing_for_a_real_shortfall(mock_provider):
    observation = build_observation(evaluation_scenario("A_LARGE_DEFICIT").prepared())
    payload = mock_provider.decide(observation)
    assert payload["action_type"] == "REQUEST_REBALANCING"
    assert payload["parameters"]["target_nodes"]
    assert 1 <= payload["parameters"]["pod_count"] <= \
        DEFAULT_ORCHESTRATION_CONFIG.max_pod_count


def test_the_mock_never_asks_for_more_pods_than_exist(mock_provider):
    observation = build_observation(evaluation_scenario("A_LARGE_DEFICIT").prepared())
    payload = mock_provider.decide(observation)
    assert payload["parameters"]["pod_count"] <= observation.fleet["idle_eligible_pods"]


def test_the_mock_names_only_nodes_from_the_observation(mock_provider):
    observation = build_observation(evaluation_scenario("A_LARGE_DEFICIT").prepared())
    payload = mock_provider.decide(observation)
    assert set(payload["parameters"]["target_nodes"]) <= set(observation.deficit_node_ids())


def test_the_mock_thresholds_are_configurable_and_explicit():
    lenient = MockProvider(OrchestrationConfig(mock_max_deadhead_share_percent=99.0))
    observation = build_observation(evaluation_scenario("C_EXPENSIVE").prepared())
    assert lenient.decide(observation)["action_type"] == "REQUEST_REBALANCING"


# --- the scripted double ------------------------------------------------------
def test_the_scripted_provider_replays_in_order(ai_observation):
    provider = ScriptedProvider(["first", "second"])
    assert provider.propose_action(ai_observation).raw_text == "first"
    assert provider.propose_action(ai_observation).raw_text == "second"
    with pytest.raises(ProviderError):
        provider.propose_action(ai_observation)


def test_the_scripted_provider_can_fail_on_cue(ai_observation):
    provider = ScriptedProvider([ProviderError("the API timed out")])
    with pytest.raises(ProviderError, match="timed out"):
        provider.propose_action(ai_observation)


# --- the prompt ---------------------------------------------------------------
def test_the_system_prompt_states_the_division_of_labour():
    flat = " ".join(SYSTEM_PROMPT.split())
    assert "You are the orchestration layer for a deterministic transportation " \
           "simulation." in flat
    assert "Propose actions only." in flat
    assert "The deterministic validator decides whether an action is permitted" in flat
    assert "the simulator determines the actual result" in flat


def test_the_system_prompt_forbids_the_things_that_matter():
    flat = " ".join(SYSTEM_PROMPT.split())
    for forbidden in ("Claim an action was executed", "Invent simulation results",
                      "Attempt to modify the simulation", "outside the whitelist"):
        assert forbidden in flat


def test_the_system_prompt_lists_exactly_the_whitelist():
    for name in ACTION_WHITELIST:
        assert name in SYSTEM_PROMPT


def test_the_response_schema_matches_the_action_schema():
    assert RESPONSE_SCHEMA["properties"]["action_type"]["enum"] == list(ACTION_WHITELIST)
    assert set(RESPONSE_SCHEMA["required"]) == {
        "action_type", "reason", "expected_effect", "confidence", "observation_fingerprint"}


def test_the_prompt_payload_carries_the_observation_and_nothing_else(ai_observation):
    payload = build_user_payload(ai_observation)
    assert payload["observation"] == ai_observation.to_dict()
    assert payload["observation_fingerprint"] == ai_observation.fingerprint()
    assert set(payload) == {"task", "allowed_action_types", "observation_fingerprint",
                            "observation"}


def test_the_rendered_prompt_is_deterministic(ai_observation):
    assert len({render_user_prompt(ai_observation) for _ in range(5)}) == 1


def test_no_secret_reaches_the_prompt(monkeypatch, ai_observation):
    monkeypatch.setenv("GEMINI_API_KEY", "super-secret-value")
    rendered = render_user_prompt(ai_observation)
    assert "super-secret-value" not in rendered
    assert "super-secret-value" not in SYSTEM_PROMPT
    assert "GEMINI_API_KEY" not in rendered


# --- the Gemini provider ------------------------------------------------------
def test_importing_the_gemini_module_needs_no_sdk():
    """The SDK is imported inside the constructor, so the runtime stays stdlib-only."""
    import importlib

    module = importlib.import_module("app.orchestration.providers.gemini")
    source = __import__("pathlib").Path(module.__file__).read_text(encoding="utf-8")
    sdk_imports = [line for line in source.splitlines() if "import genai" in line]
    assert sdk_imports, "the SDK import should still be here, just not at module scope"
    for line in sdk_imports:
        assert line.startswith("    "), "the SDK must be imported inside a function body"


def test_live_mode_without_a_key_fails_clearly(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ProviderConfigurationError) as excinfo:
        GeminiProvider()
    message = str(excinfo.value)
    assert "GEMINI_API_KEY" in message
    assert "mock" in message


def test_a_blank_key_counts_as_missing(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "   ")
    with pytest.raises(ProviderConfigurationError):
        GeminiProvider()


def test_the_key_status_reports_presence_only(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "a-real-looking-key")
    status = api_key_status()
    assert status["api_key_present"] is True
    assert "a-real-looking-key" not in json.dumps(status)

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert api_key_status()["api_key_present"] is False


def test_the_model_can_be_set_by_environment(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test-model")
    assert api_key_status()["model"] == "gemini-test-model"


class _StubResponse:
    def __init__(self, text):
        self.text = text


class _StubModels:
    def __init__(self, text, recorder):
        self._text = text
        self._recorder = recorder

    def generate_content(self, **kwargs):
        self._recorder.append(kwargs)
        if isinstance(self._text, Exception):
            raise self._text
        return _StubResponse(self._text)


class _StubClient:
    def __init__(self, text, recorder):
        self.models = _StubModels(text, recorder)


def _stub_provider(text, recorder, config=DEFAULT_ORCHESTRATION_CONFIG):
    return GeminiProvider(config, model="gemini-test-model",
                          client=_StubClient(text, recorder))


def test_the_live_path_asks_for_schema_constrained_json(ai_observation):
    recorder = []
    provider = _stub_provider(json.dumps({"action_type": "NO_ACTION"}), recorder)
    provider.propose_action(ai_observation)

    request = recorder[0]
    assert request["model"] == "gemini-test-model"
    assert request["config"]["response_mime_type"] == "application/json"
    assert request["config"]["response_schema"] == RESPONSE_SCHEMA
    assert request["config"]["system_instruction"] == SYSTEM_PROMPT
    assert ai_observation.fingerprint() in request["contents"]


def test_a_json_response_becomes_a_payload(ai_observation, mock_provider):
    recorder = []
    payload = mock_provider.decide(ai_observation)
    provider = _stub_provider(json.dumps(payload), recorder)
    response = provider.propose_action(ai_observation)
    assert response.payload == payload
    assert parse_action(response.payload, raw_text=response.raw_text).is_valid


def test_prose_from_the_live_provider_is_left_for_the_parser_to_refuse(ai_observation):
    recorder = []
    provider = _stub_provider("I think you should reposition some pods.", recorder)
    response = provider.propose_action(ai_observation)
    assert response.payload is None
    parsed = parse_action(response.raw_text, raw_text=response.raw_text)
    assert not parsed.is_valid


def test_an_empty_response_is_a_provider_failure(ai_observation):
    provider = _stub_provider("   ", [])
    with pytest.raises(ProviderError, match="empty"):
        provider.propose_action(ai_observation)


def test_a_failing_call_raises_a_provider_error_not_an_sdk_error(ai_observation):
    provider = _stub_provider(RuntimeError("503 service unavailable"), [])
    with pytest.raises(ProviderError) as excinfo:
        provider.propose_action(ai_observation)
    assert "RuntimeError" in str(excinfo.value)


def test_there_is_no_automatic_retry_loop(ai_observation):
    recorder = []
    provider = _stub_provider(RuntimeError("boom"), recorder)
    with pytest.raises(ProviderError):
        provider.propose_action(ai_observation)
    assert len(recorder) == DEFAULT_ORCHESTRATION_CONFIG.provider_max_attempts == 1


def test_the_provider_never_exposes_a_credential(monkeypatch, ai_observation):
    monkeypatch.setenv("GEMINI_API_KEY", "leak-me-if-you-can")
    provider = _stub_provider("{}", [])
    described = json.dumps(provider.describe())
    assert "leak-me-if-you-can" not in described
    assert "leak-me-if-you-can" not in repr(provider)
    assert not any("leak-me-if-you-can" in str(value)
                   for value in vars(provider).values())


def test_the_live_provider_reports_that_it_is_not_deterministic():
    provider = _stub_provider("{}", [])
    assert provider.describe()["deterministic"] is False
    assert provider.describe()["requires_api_key"] is True
