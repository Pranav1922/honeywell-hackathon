"""MCP server exposing the agent's tools to external MCP clients.

Deliberately a thin wrapper over `agents.tools.ToolRegistry` rather than a second
implementation: the tools the in-process agent calls and the tools an external
MCP client calls are the same functions, so the two can never drift.

Run it over stdio:

    python -m app.mcp_server

The context it serves is a live `AgentContext`. Started standalone it holds the
most recent run read back out of SQLite, which is what lets an external client
inspect a completed run's telemetry, comfort limits and diagnostics through
exactly the tools the agent used while producing it.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

from app import db
from app.agents.tools import AgentContext, ToolRegistry
from app.config import Settings, load_scenario, load_settings
from app.loop import guard_limits
from app.sim.base import BuildingState

SERVER_NAME = "ecoloop"
OUTDOOR_CO2_PPM = 420.0


class EcoLoopMCPServer:
    """Serves the tool registry over the Model Context Protocol."""

    def __init__(self, registry: ToolRegistry, name: str = SERVER_NAME) -> None:
        """Wrap an existing registry. No tools are defined here."""
        self._registry = registry
        self._name = name

    @property
    def name(self) -> str:
        """The server name advertised to clients."""
        return self._name

    def list_tools(self) -> list[dict]:
        """MCP `tools/list` — delegates to `ToolRegistry.mcp_schemas()`."""
        return self._registry.mcp_schemas()

    def call_tool(self, name: str, arguments: dict) -> dict:
        """MCP `tools/call` — delegates to `ToolRegistry.dispatch()`.

        Errors come back as payloads rather than exceptions, exactly as they do
        for the in-process agent, so an external client receives the same
        self-correctable feedback.
        """
        return self._registry.dispatch(name, arguments or {})

    def serve_stdio(self) -> None:
        """Run the MCP server over stdio transport."""
        asyncio.run(self._serve_stdio())

    async def _serve_stdio(self) -> None:
        """Bind the registry to an MCP stdio session."""
        # Imported here rather than at module scope so the wrapper stays importable
        # and testable without the MCP SDK present. The tool layer is not
        # optional; this transport is.
        import mcp.types as types
        from mcp.server import Server
        from mcp.server.stdio import stdio_server

        server = Server(self._name)

        @server.list_tools()
        async def handle_list_tools() -> list[types.Tool]:
            return [
                types.Tool(
                    name=schema["name"],
                    description=schema["description"],
                    inputSchema=schema["inputSchema"],
                )
                for schema in self.list_tools()
            ]

        @server.call_tool()
        async def handle_call_tool(
            name: str, arguments: dict
        ) -> list[types.TextContent]:
            result = self.call_tool(name, arguments)
            return [
                types.TextContent(
                    type="text", text=json.dumps(result, default=str, indent=2)
                )
            ]

        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream, write_stream, server.create_initialization_options()
            )


def build_registry(settings: Settings | None = None) -> ToolRegistry:
    """Build a registry over the most recent run's telemetry.

    Reuses `loop.guard_limits` so the limits an external client is told about are
    the same ones the guard enforces, rather than a second copy that can drift.
    """
    settings = settings or load_settings()

    context = AgentContext(
        timestep_seconds=900,
        limits=guard_limits(settings),
        targets={
            "tariff_per_kwh": settings.tariff_per_kwh,
            "grid_carbon_kg_per_kwh": settings.grid_carbon_kg_per_kwh,
            "peak_demand_kw": 10.0,
        },
        occupied_hours=(8, 18),
        history_window_steps=settings.history_window_steps,
    )
    _load_latest_run(context, settings)
    return ToolRegistry(context)


def _load_latest_run(context: AgentContext, settings: Settings) -> None:
    """Populate the context from the newest run that has telemetry."""
    if not Path(settings.database_path).exists():
        return

    conn = db.connect(settings.database_path)
    try:
        for record in db.list_runs(conn, limit=10):
            rows = db.get_timeseries(conn, record["id"])
            if not rows:
                continue

            context.timestep_seconds = record["timestep_seconds"] or 900
            try:
                scenario = load_scenario(record["scenario"], settings.scenarios_dir)
                context.occupied_hours = scenario.occupied_hours
                context.targets["peak_demand_kw"] = scenario.targets.peak_demand_kw
            except (FileNotFoundError, ValueError):
                pass

            history = [_to_state(row) for row in rows]
            for index, state in enumerate(history):
                context.observe(state, history[: index + 1])
            return
    finally:
        conn.close()


def _to_state(row: dict) -> BuildingState:
    """Rebuild a `BuildingState` from a persisted telemetry row."""
    return BuildingState(
        step=row["step"],
        sim_time=datetime.fromisoformat(row["sim_time"]),
        zone_temp_c=row["zone_temp_c"],
        outdoor_temp_c=row["outdoor_temp_c"],
        occupancy=row["occupancy"],
        hvac_mode=row["hvac_mode"],
        heating_sp_c=row["heating_sp_c"],
        cooling_sp_c=row["cooling_sp_c"],
        lighting_level=row["lighting_level"],
        ventilation_ach=row["ventilation_ach"],
        power_kw=row["power_kw"],
        co2_ppm=row["co2_ppm"] if row["co2_ppm"] is not None else OUTDOOR_CO2_PPM,
        # Not persisted; the comfort model's defaults, matching the toy simulator.
        relative_humidity=0.5,
        air_speed_ms=0.1,
    )


def main() -> int:
    """Entry point for `python -m app.mcp_server`."""
    EcoLoopMCPServer(build_registry()).serve_stdio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
