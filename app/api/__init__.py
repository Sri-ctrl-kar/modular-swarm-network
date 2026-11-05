"""Modular Swarm Network HTTP API Adapter (Milestone 7).

Stdlib-only HTTP API adapter exposing pure-Python M1-M6 engines to the Visual Command Center.
"""

from __future__ import annotations

from app.api.server import run_server

__all__ = ["run_server"]
