"""Providers: the boundary between the deterministic engine and anything external.

``MockProvider`` and ``ScriptedProvider`` need no network and no credentials.
``GeminiProvider`` is imported here by name only — the Google GenAI SDK is imported
inside its constructor — so importing this package never requires it.
"""

from __future__ import annotations

from app.orchestration.providers.base import AIProvider, ProviderResponse
from app.orchestration.providers.gemini import (
    GeminiProvider,
    INSTALL_HINT,
    api_key_status,
)
from app.orchestration.providers.mock import MockProvider, ScriptedProvider

__all__ = ["AIProvider", "GeminiProvider", "INSTALL_HINT", "MockProvider",
           "ProviderResponse", "ScriptedProvider", "api_key_status"]
