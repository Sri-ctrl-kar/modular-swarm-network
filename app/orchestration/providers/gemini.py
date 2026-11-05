"""The live Gemini provider. Optional, lazily imported, and never required.

*** The deterministic engine does not depend on this module. Importing it costs
    nothing: the Google GenAI SDK is imported inside the constructor, so the runtime
    stays standard-library-only unless live mode is explicitly asked for. ***

CREDENTIALS
-----------
The API key is read from an environment variable (``GEMINI_API_KEY`` by default) at
construction time, handed straight to the SDK client, and **never stored on the
instance, written to an audit record, printed, logged or placed in a prompt**.
``describe()`` reports whether a key was present, never what it was. Live mode
without a key fails immediately and says which variable to set.

Tests never reach this class's network path: they either assert the configuration
failure, or inject a stub client. Nothing in the suite needs a real key or a real
call, and no test asserts what a live model would say.

NO RETRY LOOP
-------------
``provider_max_attempts`` defaults to 1. A failed call fails; it does not quietly
turn into three calls against a paid API.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Mapping

from app.errors import ProviderConfigurationError, ProviderError
from app.orchestration.config import DEFAULT_ORCHESTRATION_CONFIG, OrchestrationConfig
from app.orchestration.observation import OrchestrationObservation
from app.orchestration.prompt import RESPONSE_SCHEMA, SYSTEM_PROMPT, render_user_prompt
from app.orchestration.providers.base import ProviderResponse

logger = logging.getLogger(__name__)

INSTALL_HINT = (
    "the Google GenAI SDK is not installed; live Gemini mode needs it "
    "(pip install -r requirements-gemini.txt). The deterministic engine and the mock "
    "provider do not."
)


def api_key_status(config: OrchestrationConfig = DEFAULT_ORCHESTRATION_CONFIG
                   ) -> dict[str, Any]:
    """Whether live mode is configured. Reports presence only — never the value."""
    raw = os.environ.get(config.api_key_env_var, "")
    return {
        "api_key_env_var": config.api_key_env_var,
        "api_key_present": bool(raw.strip()),
        "model_env_var": config.model_env_var,
        "model": os.environ.get(config.model_env_var, "").strip()
                 or config.default_gemini_model,
    }


class GeminiProvider:
    """Proposes an action by asking Gemini for one JSON object.

    The response is requested as schema-constrained JSON and is then re-parsed and
    re-validated locally. Whatever comes back is data: it is never executed, and a
    response that does not fit the schema produces no action at all.
    """

    name = "gemini"

    def __init__(self, config: OrchestrationConfig = DEFAULT_ORCHESTRATION_CONFIG, *,
                 model: str | None = None, client: Any | None = None) -> None:
        self._config = config
        self._model = (model or os.environ.get(config.model_env_var, "").strip()
                       or config.default_gemini_model)

        if client is not None:
            # Injected client: used by tests so the live path can be exercised without
            # a key, a network call or the SDK installed.
            self._client = client
            self._key_was_present = None
            return

        api_key = os.environ.get(config.api_key_env_var, "").strip()
        if not api_key:
            raise ProviderConfigurationError(
                f"live Gemini mode needs an API key in ${config.api_key_env_var}, which "
                f"is unset or empty. Use --provider mock to run without one.")
        try:
            from google import genai  # imported here so the runtime stays stdlib-only
        except ImportError as exc:
            raise ProviderConfigurationError(INSTALL_HINT) from exc
        try:
            self._client = genai.Client(api_key=api_key)
        except Exception as exc:  # SDK-specific failures are not ours to enumerate
            raise ProviderConfigurationError(
                f"the Google GenAI client could not be created: {type(exc).__name__}"
            ) from exc
        # The key is now the client's business. This object never keeps a copy.
        self._key_was_present = True

    @property
    def model(self) -> str:
        return self._model

    def describe(self) -> Mapping[str, Any]:
        """Safe to print, safe to log, safe to store: no credential appears here."""
        return {
            "provider": self.name,
            "model": self._model,
            "requires_api_key": True,
            "api_key_env_var": self._config.api_key_env_var,
            "api_key_present": bool(self._key_was_present),
            "deterministic": False,
            "note": "A language model's output varies between calls. Nothing downstream "
                    "trusts it: every proposal is validated against the live engine.",
        }

    def propose_action(self, observation: OrchestrationObservation) -> ProviderResponse:
        """One call, one response. No retry loop, no fallback to another model."""
        request = {
            "model": self._model,
            "contents": render_user_prompt(observation),
            "config": {
                "system_instruction": SYSTEM_PROMPT,
                "response_mime_type": "application/json",
                "response_schema": RESPONSE_SCHEMA,
            },
        }
        attempts = self._config.provider_max_attempts
        last_error: Exception | None = None
        started = time.perf_counter()
        for attempt in range(1, attempts + 1):
            try:
                response = self._client.models.generate_content(**request)
                break
            except Exception as exc:
                last_error = exc
                logger.warning("Gemini call failed (attempt %d of %d): %s",
                               attempt, attempts, type(exc).__name__)
        else:
            raise ProviderError(
                f"Gemini did not answer after {attempts} attempt(s): "
                f"{type(last_error).__name__}: {last_error}") from last_error

        latency_ms = round((time.perf_counter() - started) * 1000.0, 3)
        text = getattr(response, "text", None)
        if not isinstance(text, str) or not text.strip():
            raise ProviderError("Gemini returned an empty response")

        payload: Mapping[str, Any] | None
        try:
            decoded = json.loads(text)
            payload = decoded if isinstance(decoded, Mapping) else None
        except ValueError:
            # Left to ``parse_action``, which reports AI_OUTPUT_INVALID. Nothing here
            # tries to repair or extract JSON from prose.
            payload = None

        return ProviderResponse(provider=self.name, raw_text=text, payload=payload,
                                model=self._model, latency_ms=latency_ms,
                                metadata={"schema_constrained": True})
