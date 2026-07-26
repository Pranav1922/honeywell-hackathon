"""Prompt construction, isolated so prompt engineering is reviewable as a diff.

Observations are assembled here rather than in `llm.py` to keep the control flow
and the wording independently testable. Long histories are compacted by
`utils.logsummary` before they reach a prompt.

Implemented in Milestone 2.
"""

from __future__ import annotations

from typing import Any, Sequence

from app.sim.base import BuildingState

SYSTEM_PROMPT = """\
You are the supervisory control agent for a commercial building. Each time you
are consulted you set the operating policy for the next control window.

You are given: current zone and outdoor temperature, occupancy, recent
telemetry, indoor air quality, energy used so far, the tariff, grid carbon
intensity, and the occupant comfort band.

Your objective is to minimise electricity consumption and peak demand while
keeping predicted mean vote (PMV) inside the acceptable band whenever the
building is occupied.

Available strategies: hold, precool, preheat, setback, peak_shave. Use the tools
to inspect data before deciding. When you have decided, call set_control_policy
exactly once with your set-points and a one-sentence rationale explaining the
trade-off you made.

Hard limits are enforced downstream and will override unsafe set-points; aim to
stay inside them rather than relying on the override.
"""


def build_observation(
    state: BuildingState,
    history: Sequence[BuildingState],
    targets: dict[str, Any],
) -> str:
    """Render current state, compacted history and scenario targets as a prompt."""
    raise NotImplementedError("Milestone 2")


def build_tool_result(name: str, result: dict[str, Any]) -> dict[str, Any]:
    """Format a tool result as a conversation message."""
    raise NotImplementedError("Milestone 2")


def build_correction(error: str) -> dict[str, Any]:
    """Format a validation failure as feedback the model can repair from."""
    raise NotImplementedError("Milestone 2")
