"""The closed-loop orchestrator — the only module that knows the loop's shape.

Steps the simulator, asks the controller to decide, injects the action back into
the simulator, evaluates comfort and energy, and persists every record. Baseline
and agent runs use this identical path with a different `Controller`, which is
what makes the savings figure a controlled experiment.

Implemented in Milestone 1.
"""

from __future__ import annotations

from typing import Callable, Sequence

from app.agents.base import Controller
from app.config import Settings
from app.energy import RunSummary
from app.sim.base import BuildingState, Simulator


class ClosedLoopRunner:
    """Runs one scenario under one controller to completion."""

    def __init__(
        self,
        run_id: int,
        simulator: Simulator,
        controller: Controller,
        settings: Settings,
        on_progress: Callable[[BuildingState], None] | None = None,
    ) -> None:
        """`on_progress` feeds the SSE stream; persistence happens regardless."""
        raise NotImplementedError("Milestone 1")

    def run(self) -> RunSummary:
        """Execute the full horizon and return the run's aggregate metrics.

        A simulator or controller failure marks the run `failed` and closes the
        simulator; it never leaves an orphaned EnergyPlus process.
        """
        raise NotImplementedError("Milestone 1")

    def stop(self) -> None:
        """Request cooperative cancellation. The loop exits at the next step."""
        raise NotImplementedError("Milestone 1")

    @property
    def history(self) -> Sequence[BuildingState]:
        """Recent telemetry, bounded by `Settings.history_window_steps`. This is
        the window the agent's tools read from."""
        raise NotImplementedError("Milestone 1")
