# backend/app/

The application package. Layered so that domain logic contains no I/O, all SQL
lives in one module, and all HTTP concerns live in another.

| Module | Responsibility |
|---|---|
| `main.py` | FastAPI routes and run lifecycle. No domain logic. |
| `schemas.py` | Pydantic models for the HTTP boundary, kept separate from the domain dataclasses. |
| `config.py` | Typed settings from the environment. |
| `db.py` | SQLite schema and repository functions. Every SQL statement in the project. |
| `loop.py` | `ClosedLoopRunner` — the closed loop. The only module that knows the loop's shape. |
| `comfort.py` | Fanger PMV and PPD. Pure functions. |
| `energy.py` | kWh, cost, carbon and the baseline-vs-agent savings report. Pure functions. |
| `mcp_server.py` | MCP wrapper over `agents/tools.py`. |
| `sim/` | Simulation layer. See its README. |
| `agents/` | Control and cognitive layer. See its README. |
| `utils/` | Two narrow helpers: log compaction and simulation-clock arithmetic. Not a catch-all. |

Dependency direction is one-way: `main` → `loop` → {`sim`, `agents`, `comfort`,
`energy`, `db`}. Nothing in `sim/` or `agents/` imports `main` or `db`.
