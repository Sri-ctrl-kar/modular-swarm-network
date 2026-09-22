"""M6: the action schema and the parser — the only door AI text comes through."""

from __future__ import annotations

import json

import pytest

from app.errors import ActionSchemaError
from app.orchestration import (
    ACTION_WHITELIST,
    AI_OUTPUT_INVALID,
    AIAction,
    AIActionType,
    no_action,
    parse_action,
)
from app.orchestration.actions import clean_text

FINGERPRINT = "a" * 64


def _payload(**overrides):
    data = {
        "action_type": "REQUEST_REBALANCING",
        "parameters": {"target_nodes": ["market"], "pod_count": 3},
        "reason": "Forecast deficit at market.",
        "expected_effect": "More pods near the forecast demand.",
        "confidence": 0.8,
        "observation_fingerprint": FINGERPRINT,
    }
    data.update(overrides)
    return data


# --- the whitelist ------------------------------------------------------------
def test_the_whitelist_has_exactly_four_members():
    assert set(ACTION_WHITELIST) == {"REQUEST_REBALANCING", "RUN_SIMULATION",
                                     "COMPARE_SCENARIOS", "NO_ACTION"}
    assert {a.value for a in AIActionType} == set(ACTION_WHITELIST)


@pytest.mark.parametrize("forbidden", [
    "EXECUTE_CODE", "MODIFY_FILE", "RUN_SHELL", "CHANGE_ROUTING_ALGORITHM",
    "CHANGE_CONGESTION_FORMULA", "CHANGE_BATTERY_RULE", "DELETE_POD",
    "DIRECTLY_MUTATE_STATE", "SET_PARAMETER", "", "request_rebalancing",
])
def test_anything_outside_the_whitelist_cannot_be_constructed(forbidden):
    with pytest.raises(ActionSchemaError):
        AIAction(forbidden, {}, "r", "e", 0.5, FINGERPRINT)


@pytest.mark.parametrize("forbidden", ["EXECUTE_CODE", "RUN_SHELL", "DELETE_POD"])
def test_a_forbidden_action_is_reported_not_raised_when_parsed(forbidden):
    parsed = parse_action(_payload(action_type=forbidden))
    assert not parsed.is_valid
    assert parsed.error_code == AI_OUTPUT_INVALID
    assert "unknown_action_type" in parsed.error_detail
    assert parsed.action is None


# --- the schema ---------------------------------------------------------------
def test_a_well_formed_proposal_parses():
    parsed = parse_action(_payload())
    assert parsed.is_valid
    action = parsed.action
    assert action.action_type is AIActionType.REQUEST_REBALANCING
    assert action.parameters == {"pod_count": 3, "target_nodes": ["market"]}
    assert action.confidence == 0.8
    assert action.observation_fingerprint == FINGERPRINT


@pytest.mark.parametrize("field", ["action_type", "reason", "expected_effect",
                                   "confidence", "observation_fingerprint"])
def test_a_missing_field_is_refused(field):
    payload = _payload()
    payload.pop(field)
    parsed = parse_action(payload)
    assert not parsed.is_valid
    assert "missing_field" in parsed.error_detail


@pytest.mark.parametrize("value", [-0.1, 1.1, "high", None, float("nan"), True])
def test_confidence_must_be_a_number_in_range(value):
    with pytest.raises(ActionSchemaError):
        AIAction("NO_ACTION", {}, "r", "e", value, FINGERPRINT)


@pytest.mark.parametrize("value", ["", "abc", "A" * 64, "a" * 63, "a" * 65, 12345])
def test_the_fingerprint_must_look_like_a_fingerprint(value):
    with pytest.raises(ActionSchemaError):
        AIAction("NO_ACTION", {}, "r", "e", 0.5, value)


def test_parameters_must_be_a_mapping():
    with pytest.raises(ActionSchemaError):
        AIAction("NO_ACTION", ["not", "a", "mapping"], "r", "e", 0.5, FINGERPRINT)


def test_parameters_must_be_plain_json_values():
    with pytest.raises(ActionSchemaError):
        AIAction("NO_ACTION", {"callable": len}, "r", "e", 0.5, FINGERPRINT)
    with pytest.raises(ActionSchemaError):
        AIAction("NO_ACTION", {"infinite": float("inf")}, "r", "e", 0.5, FINGERPRINT)


def test_deeply_nested_parameters_are_refused():
    nested = {"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}}
    with pytest.raises(ActionSchemaError):
        AIAction("NO_ACTION", nested, "r", "e", 0.5, FINGERPRINT)


def test_parameter_keys_are_sorted_so_the_record_is_stable():
    action = AIAction("RUN_SIMULATION", {"pods": 5, "scenario": "baseline", "horizon_min": 30},
                      "r", "e", 0.5, FINGERPRINT)
    assert list(action.parameters) == ["horizon_min", "pods", "scenario"]


def test_an_action_is_frozen():
    action = no_action(FINGERPRINT, "nothing to do")
    with pytest.raises(Exception):
        action.confidence = 0.1


def test_an_action_round_trips_through_json():
    data = parse_action(_payload()).action.to_dict()
    assert json.loads(json.dumps(data)) == data


# --- untrusted text -----------------------------------------------------------
def test_control_characters_are_stripped_from_ai_text():
    hostile = "reset \x1b[2J\x07 and \x00 done"
    action = AIAction("NO_ACTION", {}, hostile, "fine", 0.5, FINGERPRINT)
    assert "\x1b" not in action.reason
    assert "\x00" not in action.reason
    assert "\x07" not in action.reason
    assert "reset" in action.reason and "done" in action.reason


def test_ai_text_is_capped_in_length():
    action = AIAction("NO_ACTION", {}, "x" * 10_000, "y" * 10_000, 0.5, FINGERPRINT)
    assert len(action.reason) <= 600
    assert len(action.expected_effect) <= 600
    assert action.reason.endswith("...")


def test_clean_text_collapses_whitespace_without_losing_words():
    assert clean_text("  a\n\n  b   c ", 100) == "a b c"


# --- parsing ------------------------------------------------------------------
def test_a_json_document_parses():
    parsed = parse_action(json.dumps(_payload()))
    assert parsed.is_valid


def test_prose_is_refused_rather_than_mined_for_json():
    prose = ('Here is my answer!\n```json\n' + json.dumps(_payload()) + '\n```\nHope that helps.')
    parsed = parse_action(prose)
    assert not parsed.is_valid
    assert parsed.error_code == AI_OUTPUT_INVALID
    assert parsed.action is None


@pytest.mark.parametrize("junk", ["", "null", "[]", "42", '"a string"', "{not json}",
                                  "<?xml version='1.0'?><action/>"])
def test_malformed_responses_are_refused_cleanly(junk):
    parsed = parse_action(junk)
    assert not parsed.is_valid
    assert parsed.error_code == AI_OUTPUT_INVALID
    assert parsed.action is None


def test_the_raw_response_is_kept_for_the_audit_trail():
    parsed = parse_action("total nonsense", raw_text="total nonsense")
    assert parsed.raw_text == "total nonsense"
    assert parsed.to_dict()["action"] is None


def test_a_hostile_raw_response_is_sanitised_before_it_is_stored():
    parsed = parse_action("\x1b]0;pwned\x07 nonsense", raw_text="\x1b]0;pwned\x07 nonsense")
    assert "\x1b" not in parsed.raw_text
    assert "\x07" not in parsed.raw_text


def test_parse_action_never_evaluates_its_input():
    """A payload that would be dangerous if executed is simply refused as data."""
    parsed = parse_action("__import__('os').system('echo pwned')")
    assert not parsed.is_valid
    assert parsed.action is None


def test_no_action_helper_builds_a_valid_action():
    action = no_action(FINGERPRINT, "the deficit is negligible")
    assert action.action_type is AIActionType.NO_ACTION
    assert action.parameters == {}
    assert action.confidence == 1.0
