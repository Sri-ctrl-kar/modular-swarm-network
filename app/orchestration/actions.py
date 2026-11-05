"""The structured action schema, and the only way AI text becomes an action.

*** AI output is untrusted input. It is parsed as data — never evaluated, never
    imported, never executed, never used to name a module, file or command. ***

THE WHITELIST IS CLOSED
-----------------------
An AI may express exactly four intentions:

``REQUEST_REBALANCING``  ask the deterministic rebalancer to run a cycle
``RUN_SIMULATION``       ask for a named deterministic scenario to be run
``COMPARE_SCENARIOS``    ask for named scenarios to be compared
``NO_ACTION``            decide that nothing should be done

Anything else — ``EXECUTE_CODE``, ``RUN_SHELL``, ``MODIFY_FILE``,
``CHANGE_CONGESTION_FORMULA``, ``DELETE_POD``, ``DIRECTLY_MUTATE_STATE`` — is not a
member of the enum, so it cannot be constructed at all. Rejection is structural, not
a blacklist that someone has to keep up to date.

WHAT AN ACTION CARRIES
----------------------
``action_type``             one of the four above
``parameters``              plain JSON values only, bounded by the validator
``reason``                  why the AI proposed it (text; stored, never executed)
``expected_effect``         what the AI expects (text; **not** a result — the engine
                            alone produces results, and the audit stores both so the
                            two can be compared)
``confidence``              a number in [0, 1] the model reported about itself. It is
                            **not** a probability of being right, carries no
                            guarantee, and is never used to relax a check.
``observation_fingerprint`` the state this was reasoned from (see ``validator.py``)

PARSING
-------
``parse_action`` accepts a structured mapping (what a schema-constrained response
gives) or a JSON document. It does **not** hunt for JSON inside prose, because
salvaging a fragment is exactly how partially-understood output gets executed. A
failure returns ``AI_OUTPUT_INVALID`` with the raw text kept for the audit record,
and no action at all.
"""

from __future__ import annotations

import json
import math
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from app.errors import ActionSchemaError
from app.orchestration.config import DEFAULT_ORCHESTRATION_CONFIG, OrchestrationConfig


class AIActionType(str, Enum):
    """The closed set of intentions an AI may ever express."""

    REQUEST_REBALANCING = "REQUEST_REBALANCING"
    RUN_SIMULATION = "RUN_SIMULATION"
    COMPARE_SCENARIOS = "COMPARE_SCENARIOS"
    NO_ACTION = "NO_ACTION"


#: The whitelist, as plain strings, for prompts and error messages.
ACTION_WHITELIST: tuple[str, ...] = tuple(action.value for action in AIActionType)

#: Parse failure codes (machine-readable).
AI_OUTPUT_INVALID = "AI_OUTPUT_INVALID"

#: Schema rejection codes, raised as ``ActionSchemaError`` and reported by the parser.
UNKNOWN_ACTION_TYPE = "unknown_action_type"
NOT_A_MAPPING = "not_a_mapping"
MISSING_FIELD = "missing_field"
BAD_FIELD_TYPE = "bad_field_type"
BAD_CONFIDENCE = "confidence_out_of_range"
BAD_FINGERPRINT = "malformed_observation_fingerprint"
UNSERIALISABLE_PARAMETER = "unserialisable_parameter"

_FINGERPRINT_LENGTH = 64
_HEX_DIGITS = set("0123456789abcdef")
#: How deep a parameter tree may nest. A bounded depth keeps a hostile payload from
#: turning validation or serialisation into a stack problem.
_MAX_PARAMETER_DEPTH = 4


def clean_text(value: str, limit: int) -> str:
    """Make untrusted text safe to store and print: no control codes, bounded length.

    Control characters (terminal escapes among them) are dropped rather than
    escaped, and the result is truncated with an explicit marker so a reader can
    see that truncation happened.
    """
    stripped = "".join(ch for ch in value
                       if ch in ("\n", "\t") or unicodedata.category(ch)[0] != "C")
    stripped = " ".join(stripped.split())
    if len(stripped) > limit:
        return stripped[:limit - 3] + "..."
    return stripped


def _plain(value: Any, label: str, depth: int = 0) -> Any:
    """Copy a JSON-plain value, rejecting anything else. No object survives this."""
    if depth > _MAX_PARAMETER_DEPTH:
        raise ActionSchemaError(f"{UNSERIALISABLE_PARAMETER}: {label} nests too deeply")
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ActionSchemaError(f"{UNSERIALISABLE_PARAMETER}: {label} is {value!r}")
        return value
    if isinstance(value, (list, tuple)):
        return [_plain(item, f"{label}[{i}]", depth + 1) for i, item in enumerate(value)]
    if isinstance(value, Mapping):
        out = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ActionSchemaError(
                    f"{UNSERIALISABLE_PARAMETER}: {label} has a non-string key {key!r}")
            out[key] = _plain(item, f"{label}.{key}", depth + 1)
        return dict(sorted(out.items()))
    raise ActionSchemaError(
        f"{UNSERIALISABLE_PARAMETER}: {label} is a {type(value).__name__}, which is not "
        f"a plain JSON value")


@dataclass(frozen=True)
class AIAction:
    """One proposed action. Constructing one executes nothing.

    Construction enforces the *shape* of a proposal. Whether it is permitted in the
    current state is a separate question, answered by ``OrchestrationValidator``.
    """

    action_type: AIActionType
    parameters: Mapping[str, Any]
    reason: str
    expected_effect: str
    confidence: float
    observation_fingerprint: str

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "action_type", AIActionType(self.action_type))
        except ValueError as exc:
            raise ActionSchemaError(
                f"{UNKNOWN_ACTION_TYPE}: {self.action_type!r} is not one of "
                f"{', '.join(ACTION_WHITELIST)}") from exc

        if not isinstance(self.parameters, Mapping):
            raise ActionSchemaError(
                f"{BAD_FIELD_TYPE}: parameters must be a mapping, got "
                f"{type(self.parameters).__name__}")
        object.__setattr__(self, "parameters", _plain(dict(self.parameters), "parameters"))

        # The text caps belong to the schema itself, so they come from the default
        # config rather than a per-call one: an AIAction must mean the same thing
        # whoever built it.
        config = DEFAULT_ORCHESTRATION_CONFIG
        for field_name, limit in (("reason", config.max_reason_chars),
                                  ("expected_effect", config.max_expected_effect_chars)):
            raw = getattr(self, field_name)
            if not isinstance(raw, str):
                raise ActionSchemaError(
                    f"{BAD_FIELD_TYPE}: {field_name} must be a string, got "
                    f"{type(raw).__name__}")
            object.__setattr__(self, field_name, clean_text(raw, limit))

        confidence = self.confidence
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ActionSchemaError(
                f"{BAD_FIELD_TYPE}: confidence must be a number, got {confidence!r}")
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ActionSchemaError(f"{BAD_CONFIDENCE}: {confidence!r} is not in [0, 1]")
        object.__setattr__(self, "confidence", round(float(confidence), 4))

        fingerprint = self.observation_fingerprint
        if not isinstance(fingerprint, str):
            raise ActionSchemaError(
                f"{BAD_FIELD_TYPE}: observation_fingerprint must be a string, got "
                f"{type(fingerprint).__name__}")
        if len(fingerprint) != _FINGERPRINT_LENGTH or not set(fingerprint) <= _HEX_DIGITS:
            raise ActionSchemaError(
                f"{BAD_FINGERPRINT}: expected {_FINGERPRINT_LENGTH} lowercase hex "
                f"characters, got {len(fingerprint)}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type.value,
            "parameters": dict(self.parameters),
            "reason": self.reason,
            "expected_effect": self.expected_effect,
            "confidence": self.confidence,
            "observation_fingerprint": self.observation_fingerprint,
        }


@dataclass(frozen=True)
class ParsedAction:
    """The result of reading a provider's response. Either an action or a failure.

    ``raw_text`` is kept whichever way it went, because the audit record has to be
    able to answer "what did the model actually say?" even — especially — when the
    answer was unusable. It is stored as text and never interpreted.
    """

    action: AIAction | None
    raw_text: str
    error_code: str | None = None
    error_detail: str | None = None

    def __bool__(self) -> bool:
        return self.action is not None

    @property
    def is_valid(self) -> bool:
        return self.action is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.to_dict() if self.action else None,
            "error_code": self.error_code,
            "error_detail": self.error_detail,
        }


def parse_action(payload: Any, *, raw_text: str = "",
                 config: OrchestrationConfig = DEFAULT_ORCHESTRATION_CONFIG) -> ParsedAction:
    """Turn a provider payload into an ``AIAction``, or into a clean refusal.

    Accepts a mapping (a schema-constrained response) or a JSON document. A string
    is parsed **whole** with ``json.loads``: no fence stripping, no regex search for
    a JSON-looking substring, no repair. Either the provider returned the structure
    it was asked for, or the proposal is discarded.
    """
    text = clean_text(raw_text if raw_text else _safe_repr(payload),
                      config.max_raw_response_chars)

    data: Any = payload
    if isinstance(payload, (str, bytes)):
        try:
            decoded = payload.decode("utf-8") if isinstance(payload, bytes) else payload
            data = json.loads(decoded)
        except (UnicodeDecodeError, ValueError) as exc:
            return ParsedAction(None, text, AI_OUTPUT_INVALID,
                                f"response is not valid JSON: {exc}")

    if not isinstance(data, Mapping):
        return ParsedAction(None, text, AI_OUTPUT_INVALID,
                            f"{NOT_A_MAPPING}: got {type(data).__name__}")

    missing = [name for name in ("action_type", "reason", "expected_effect", "confidence",
                                 "observation_fingerprint") if name not in data]
    if missing:
        return ParsedAction(None, text, AI_OUTPUT_INVALID,
                            f"{MISSING_FIELD}: {', '.join(missing)}")

    try:
        action = AIAction(
            action_type=data["action_type"],
            parameters=data.get("parameters") or {},
            reason=data["reason"],
            expected_effect=data["expected_effect"],
            confidence=data["confidence"],
            observation_fingerprint=data["observation_fingerprint"],
        )
    except ActionSchemaError as exc:
        return ParsedAction(None, text, AI_OUTPUT_INVALID, str(exc))
    return ParsedAction(action, text)


def _safe_repr(payload: Any) -> str:
    """A bounded textual record of a payload, for the audit trail only."""
    if isinstance(payload, (str, bytes)):
        return payload if isinstance(payload, str) else payload.decode("utf-8", "replace")
    try:
        return json.dumps(payload, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return f"<unserialisable {type(payload).__name__}>"


def no_action(observation_fingerprint: str, reason: str,
              expected_effect: str = "Nothing changes; the engine continues unchanged.",
              confidence: float = 1.0) -> AIAction:
    """The safe default. Deciding to do nothing is itself a first-class action."""
    return AIAction(action_type=AIActionType.NO_ACTION, parameters={}, reason=reason,
                    expected_effect=expected_effect, confidence=confidence,
                    observation_fingerprint=observation_fingerprint)
