"""The LLM supervisor — slow tier of the two-tier control design.

Runs on a cadence rather than every timestep, because EnergyPlus callbacks
execute on the simulation thread and an annual run has tens of thousands of
steps. Between supervisory calls, an internal `ReactiveGuard` enforces the
active policy.

Failure is a designed path, not an exception handler: invalid model output is
returned to the model as a tool result for bounded self-repair, and when retries
or the timeout are exhausted the run continues under `BaselineScheduler` with
`fallback_used` recorded on the decision.

Implemented in Milestone 2.
"""

from __future__ import annotations

from typing import Sequence

from app.agents.base import ControlPolicy, Decision
from app.agents.client import LLMClient
from app.agents.tools import ToolRegistry
from app.sim.base import BuildingState


class LLMSupervisor:
    """Tool-calling supervisory controller backed by a local open-source model."""

    name = "llm"

    def __init__(
        self,
        client: LLMClient,
        tools: ToolRegistry,
        guard,                      # ReactiveGuard, enforces every timestep
        fallback,                   # BaselineScheduler, used when the model fails
        cadence_steps: int = 4,     # supervise hourly at a 15-minute timestep
        max_tool_iterations: int = 5,
        max_retries: int = 2,
    ) -> None:
        raise NotImplementedError("Milestone 2")

    def decide(self, state: BuildingState, history: Sequence[BuildingState]) -> Decision:
        """Delegate to the guard; re-plan through the model when the cadence is due."""
        raise NotImplementedError("Milestone 2")

    def _plan(
        self,
        state: BuildingState,
        history: Sequence[BuildingState],
    ) -> tuple[ControlPolicy, str, list[dict]]:
        """Run the tool-calling loop and return (policy, rationale, tool_calls)."""
        raise NotImplementedError("Milestone 2")

    def _validate(self, payload: dict, state: BuildingState) -> ControlPolicy:
        """Schema- and range-check a proposed policy. Raises on violation so the
        error text can be fed back to the model for self-correction."""
        raise NotImplementedError("Milestone 2")

    def reset(self) -> None:
        raise NotImplementedError("Milestone 2")
