"""Control contracts shared by every controller implementation.

Defines what a controller is (`Controller`), what the LLM supervisor emits
(`ControlPolicy`), and what one control decision records (`Decision`). The
baseline scheduler, the reactive guard and the LLM supervisor are
interchangeable behind this protocol, which is what lets the savings comparison
be a controlled experiment on a single execution path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, Sequence, runtime_checkable

from app.sim.base import BuildingState, ControlAction

STRATEGY_HOLD = "hold"
STRATEGY_PRECOOL = "precool"
STRATEGY_PREHEAT = "preheat"
STRATEGY_SETBACK = "setback"
STRATEGY_PEAK_SHAVE = "peak_shave"


@dataclass(frozen=True)
class ControlPolicy:
    """A supervisory policy: what the agent wants, valid until `expires_at_step`.

    The guard enforces this every timestep and may clamp it; the supervisor
    revises it on its own cadence.
    """

    strategy: str             # one of the STRATEGY_* constants
    heating_sp_c: float
    cooling_sp_c: float
    lighting_level: float
    ventilation_ach: float
    expires_at_step: int


@dataclass(frozen=True)
class Decision:
    """One control decision, persisted verbatim for the dashboard and audit."""

    step: int
    sim_time: datetime
    action: ControlAction
    policy: ControlPolicy
    rationale: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    retries: int = 0
    fallback_used: bool = False
    guard_clamped: bool = False


@runtime_checkable
class Controller(Protocol):
    """The contract every control strategy implements."""

    name: str

    def decide(
        self,
        state: BuildingState,
        history: Sequence[BuildingState],
    ) -> Decision:
        """Return the control decision for the current timestep."""
        ...

    def reset(self) -> None:
        """Clear controller state between runs."""
        ...
