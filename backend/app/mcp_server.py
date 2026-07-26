"""MCP server exposing the agent's tools to external MCP clients.

Deliberately a thin wrapper over `agents.tools.ToolRegistry` rather than a second
implementation: the tools the in-process agent calls and the tools an external
MCP client calls are the same functions, so the two can never drift.

Implemented in Milestone 4.
"""

from __future__ import annotations

from app.agents.tools import ToolRegistry


class EcoLoopMCPServer:
    """Serves the tool registry over the Model Context Protocol."""

    def __init__(self, registry: ToolRegistry, name: str = "ecoloop") -> None:
        raise NotImplementedError("Milestone 4")

    def list_tools(self) -> list[dict]:
        """MCP `tools/list` — delegates to `ToolRegistry.mcp_schemas()`."""
        raise NotImplementedError("Milestone 4")

    def call_tool(self, name: str, arguments: dict) -> dict:
        """MCP `tools/call` — delegates to `ToolRegistry.dispatch()`."""
        raise NotImplementedError("Milestone 4")

    def serve_stdio(self) -> None:
        """Run the MCP server over stdio transport."""
        raise NotImplementedError("Milestone 4")
