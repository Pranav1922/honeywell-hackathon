"""The agent's tool registry — the single source of truth for tool-calling.

Holds JSON-schema'd functions the model may call, dispatches a call by name, and
exports schemas in both OpenAI tool format and MCP format. `mcp_server.py` wraps
this same registry, so the in-process agent and any external MCP client invoke
identical code rather than parallel implementations.

`get_simulation_errors` is what lets the agent parse EnergyPlus runtime output
and react to problems without human code modification.

Implemented in Milestone 2.
"""

from __future__ import annotations

from typing import Any, Callable

TOOL_NAMES: tuple[str, ...] = (
    "get_recent_telemetry",     # windowed zone/outdoor temps, power, occupancy
    "get_comfort_limits",       # active comfort band and hard clamps
    "get_energy_summary",       # kWh so far, peak demand, tariff, carbon intensity
    "evaluate_policy",          # score a candidate policy before committing to it
    "get_simulation_errors",    # parsed EnergyPlus .err entries, severity-filtered
    "set_control_policy",       # terminal call: commit set-points and rationale
)


class ToolRegistry:
    """Registers, describes and dispatches the tools available to the agent."""

    def __init__(self, runner_context: Any) -> None:
        """`runner_context` gives tools read access to the live run: telemetry
        history, scenario targets and simulator diagnostics."""
        raise NotImplementedError("Milestone 2")

    def register(self, name: str, schema: dict[str, Any], fn: Callable) -> None:
        """Add a tool definition and its handler."""
        raise NotImplementedError("Milestone 2")

    def openai_schemas(self) -> list[dict[str, Any]]:
        """Tool definitions in OpenAI tool-calling format."""
        raise NotImplementedError("Milestone 2")

    def mcp_schemas(self) -> list[dict[str, Any]]:
        """The same definitions in MCP tool format."""
        raise NotImplementedError("Milestone 2")

    def dispatch(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke a tool by name. Unknown names and invalid arguments return an
        error payload rather than raising, so the model can self-correct."""
        raise NotImplementedError("Milestone 2")
