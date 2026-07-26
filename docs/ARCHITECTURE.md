# Eco-Loop Building Agents — System Architecture

**Honeywell Hackathon · Question 1 · Autonomous Closed-Loop Building Control**

Status: architecture frozen. Milestones 1 and 2 are implemented; M3 and M4 pending.
Every module named here exists as a declared contract in the repository; bodies are
implemented milestone by milestone.

---

## 1. High-Level System Architecture

The system is a **closed-loop supervisory controller** wrapped around a building energy
simulation. A physics engine produces sensor telemetry; an open-source LLM reasons over
that telemetry against comfort, energy and carbon targets; the resulting set-points are
injected back into the *running* simulation instance; the loop repeats until the
simulation horizon is exhausted. Two runs of the same scenario — one under a fixed
baseline schedule, one under the agent — are then differenced to prove savings.

Four architectural commitments define the system:

**1. The simulator is an interface, not a dependency.** `Simulator` is a Protocol with two
real implementations: `ToySimulator` (an RC thermal-network model, pure NumPy) and
`EnergyPlusSimulator` (the EnergyPlus runtime Python API). Every other module —
orchestrator, agents, persistence, API, dashboard — is written against the Protocol and
cannot tell them apart. The toy model is not a mock; it is a functioning building model
used to develop and stress the loop before EnergyPlus is installed, and as a live fallback
if the demonstration machine cannot host EnergyPlus.

**2. Control is two-tier.** EnergyPlus executes an entire simulation inside a single
blocking call, and control callbacks fire *on the simulation thread*. Anything slow in
that callback stalls the simulation. A supervisory call — prompt assembly, a hosted
inference request, one or more tool round trips — costs hundreds of milliseconds at best
and a full timeout at worst; an annual run at 15-minute steps has 35,040 timesteps.
Calling the LLM every timestep is therefore not merely slow, it is architecturally
impossible. Instead:

| Tier | Runs | Latency budget | Responsibility |
|---|---|---|---|
| **Reactive guard** (`agents/rule.py`) | every timestep | microseconds | Enforce the active policy; clamp set-points to hard comfort and safety limits |
| **LLM supervisor** (`agents/llm.py`) | every *N* timesteps | seconds | Choose the policy: set-point targets, pre-cool/setback strategy, lighting, ventilation |

The LLM sets *policy*; the guard *enforces* it and may override it. This yields a loop that
survives a long horizon without stalling, a comfort floor the LLM cannot breach, and
graceful degradation when the model is slow or emits a malformed tool call.

**3. Both control arms share one execution path.** Baseline and agent runs are the same
`ClosedLoopRunner` given a different `Controller`. The savings figure is therefore a
controlled experiment — identical weather, identical occupancy, identical building,
identical accounting code — and not a comparison of two different programs.

**4. Everything the agent perceives and decides is persisted.** Telemetry, decisions,
rationales, tool calls and per-decision latency all land in SQLite. The dashboard is a
pure reader of that store, which means the demonstration can be replayed offline.

```
                    ┌──────────────────────────────────────────┐
                    │            Scenario + Weather            │
                    │   (occupancy schedule, EPW / synthetic)  │
                    └────────────────────┬─────────────────────┘
                                         │
        ┌────────────────────────────────▼─────────────────────────────────┐
        │                       SIMULATION LAYER                           │
        │   Simulator (Protocol)                                           │
        │     ├── ToySimulator          RC thermal network, NumPy          │
        │     └── EnergyPlusSimulator   pyenergyplus runtime API           │
        └───────────┬──────────────────────────────────▲───────────────────┘
                    │ BuildingState                    │ ControlAction
                    │ (sensors, every timestep)        │ (actuators)
        ┌───────────▼──────────────────────────────────┴───────────────────┐
        │                     ORCHESTRATION LAYER                          │
        │   ClosedLoopRunner  —  owns the loop, cadence, persistence       │
        └──────┬─────────────────────┬──────────────────────┬──────────────┘
               │                     │                      │
   ┌───────────▼──────────┐  ┌───────▼────────┐  ┌──────────▼────────────┐
   │    CONTROL LAYER     │  │ EVALUATION     │  │   PERSISTENCE         │
   │ Controller(Protocol) │  │ comfort.py PMV │  │   db.py  (SQLite)     │
   │  ├ BaselineScheduler │  │ energy.py kWh  │  │   runs / timesteps    │
   │  ├ ReactiveGuard     │  │                │  │   / decisions         │
   │  └ LLMSupervisor ────┼──┐               │  └──────────┬────────────┘
   └──────────────────────┘  │                              │
                             │                              │
              ┌──────────────▼───────────────┐   ┌──────────▼────────────┐
              │       COGNITIVE LAYER        │   │      API LAYER        │
              │  LLMClient (Groq SDK)        │   │   FastAPI  main.py    │
              │    → Groq / llama-3.3-70b    │   │   REST + SSE          │
              │  ToolRegistry ── MCP server  │   └──────────┬────────────┘
              │  prompts.py, logsummary.py   │              │
              └──────────────────────────────┘   ┌──────────▼────────────┐
                                                 │   React Dashboard     │
                                                 │   Vite + Recharts     │
                                                 └───────────────────────┘
```

---

## 2. Component Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            FRONTEND (Vite + React)                          │
│                                                                             │
│  App.jsx                                                                    │
│    ├── RunControls.jsx      start / stop runs, pick controller + scenario    │
│    ├── KpiRow.jsx           kWh, % saved, comfort violations, mean PPD       │
│    ├── TemperatureChart.jsx zone temp · outdoor temp · set-point band        │
│    ├── EnergyChart.jsx      baseline vs agent power, cumulative kWh          │
│    ├── ComfortChart.jsx     PMV trace with the −0.5 … +0.5 acceptable band   │
│    ├── OccupancyChart.jsx   occupancy fraction over time                     │
│    ├── ActionPanel.jsx      current set-points, lighting, ventilation        │
│    └── AgentLog.jsx         streamed LLM rationales + tool calls + latency   │
│                                                                             │
│  hooks/useRunStream.js   polls / subscribes to a live run                    │
│  lib/api.js              typed fetch wrappers for the backend                │
└────────────────────────────────────┬────────────────────────────────────────┘
                                     │ HTTP / SSE  (JSON)
┌────────────────────────────────────▼────────────────────────────────────────┐
│                              BACKEND (FastAPI)                              │
│                                                                             │
│  app/main.py ─────────── HTTP surface, run lifecycle, SSE stream            │
│  app/schemas.py ──────── Pydantic request/response contracts                │
│  app/config.py ───────── Settings (env-driven: model, base_url, paths)      │
│         │                                                                   │
│         ▼                                                                   │
│  app/loop.py  ClosedLoopRunner ─────────────────────────────────────┐       │
│         │                                                            │       │
│    ┌────┴───────────────┬─────────────────┬──────────────────┐      │       │
│    ▼                    ▼                 ▼                  ▼      │       │
│  app/sim/          app/agents/        app/comfort.py    app/energy.py│       │
│   base.py           base.py            PMV / PPD         kWh, cost,  │       │
│   toy.py            rule.py                              CO₂, savings│       │
│   energyplus.py     llm.py ──► client.py ──► Groq API                │       │
│   weather.py        tools.py ◄── mcp_server.py                       │       │
│                     prompts.py                                       │       │
│                                                                      ▼       │
│  app/utils/logsummary.py   compacts long simulation logs for prompts │       │
│  app/utils/timeutil.py     simulation clock, cadence arithmetic      │       │
│                                                                      │       │
│  app/db.py  ◄────────────────────────────────────────────────────────┘       │
│      SQLite: runs · timesteps · decisions                                    │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
┌────────────────────────────────────▼────────────────────────────────────────┐
│                             EXTERNAL RUNTIMES                               │
│   EnergyPlus (pyenergyplus API)   ·   Groq (llama-3.3-70b, tool-calling)    │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Folder Structure

```
honeywell/
├── README.md                     project overview, setup, run instructions
├── requirements.txt              backend Python dependencies
├── .env.example                  environment template (copy to .env)
├── .gitignore
│
├── docs/
│   ├── README.md
│   └── ARCHITECTURE.md           this document (deliverable 4)
│
├── backend/
│   ├── README.md
│   ├── cli.py                    headless entrypoint: run a scenario, print savings
│   │
│   ├── app/
│   │   ├── README.md
│   │   ├── __init__.py
│   │   ├── main.py               FastAPI application and all HTTP routes
│   │   ├── config.py             Settings loaded from environment
│   │   ├── schemas.py            Pydantic API request/response models
│   │   ├── db.py                 SQLite schema, connection, repository functions
│   │   ├── loop.py               ClosedLoopRunner — the closed-loop orchestrator
│   │   ├── comfort.py            Fanger PMV / PPD, comfort-band evaluation
│   │   ├── energy.py             energy accounting, cost, carbon, savings compare
│   │   ├── mcp_server.py         MCP server exposing the same tools as the agent
│   │   │
│   │   ├── sim/                  SIMULATION LAYER
│   │   │   ├── README.md
│   │   │   ├── __init__.py
│   │   │   ├── base.py           BuildingState, ControlAction, Simulator Protocol
│   │   │   ├── toy.py            ToySimulator — RC thermal network
│   │   │   ├── energyplus.py     EnergyPlusSimulator — pyenergyplus runtime API
│   │   │   └── weather.py        weather providers (synthetic + EPW)
│   │   │
│   │   ├── agents/               CONTROL + COGNITIVE LAYER
│   │   │   ├── README.md
│   │   │   ├── __init__.py
│   │   │   ├── base.py           Controller Protocol, Decision, ControlPolicy
│   │   │   ├── rule.py           BaselineScheduler, ReactiveGuard
│   │   │   ├── llm.py            LLMSupervisor — two-tier LLM controller
│   │   │   ├── client.py         LLMClient — OpenAI-compatible transport
│   │   │   ├── tools.py          ToolRegistry — agent-callable tools
│   │   │   └── prompts.py        system prompt and prompt builders
│   │   │
│   │   └── utils/
│   │       ├── README.md
│   │       ├── __init__.py
│   │       ├── logsummary.py     long simulation-log compaction for prompts
│   │       └── timeutil.py       simulation clock and cadence helpers
│   │
│   ├── config/
│   │   ├── README.md
│   │   └── scenarios/            scenario definitions (JSON)
│   │       └── README.md
│   │
│   ├── models/                   EnergyPlus building models (deliverable 2)
│   │   ├── README.md
│   │   ├── baseline/             unmodified baseline .idf + .epw
│   │   └── generated/            agent-modified .idf variants produced at runtime
│   │
│   └── tests/
│       ├── README.md
│       ├── __init__.py
│       ├── test_comfort.py
│       ├── test_energy.py
│       ├── test_toy_sim.py
│       └── test_loop.py
│
└── frontend/
    ├── README.md
    ├── package.json
    ├── vite.config.js
    ├── index.html
    ├── public/
    └── src/
        ├── README.md
        ├── main.jsx
        ├── App.jsx
        ├── components/           README + one file per dashboard panel
        ├── hooks/                useRunStream.js
        └── lib/                  api.js, format.js
```

---

## 4. Module Responsibilities

### Simulation layer — `backend/app/sim/`

| Module | Responsibility |
|---|---|
| `base.py` | Defines the contract every simulator obeys: the `BuildingState` sensor record, the `ControlAction` actuator record, and the `Simulator` Protocol. Contains no physics. This is the seam that makes EnergyPlus swappable. |
| `toy.py` | A single-zone RC thermal-network building. Integrates conduction to outdoors, solar and internal gains, occupancy heat, and HVAC delivered power. Fast, deterministic, dependency-free. |
| `energyplus.py` | Drives a real EnergyPlus run through the runtime Python API. Registers timestep callbacks, reads sensors via `api.exchange`, writes set-points via actuators, and bridges EnergyPlus's blocking `run_energyplus()` into the `Simulator` step interface. |
| `weather.py` | Supplies outdoor conditions and occupancy for a scenario. A synthetic diurnal generator for the toy model; EPW file parsing for EnergyPlus. Guarantees baseline and agent runs see byte-identical conditions. |

### Control and cognitive layer — `backend/app/agents/`

| Module | Responsibility |
|---|---|
| `base.py` | `Controller` Protocol (`decide(state, history) -> Decision`), the `ControlPolicy` the supervisor emits, and the `Decision` record carrying action, rationale, tool calls and latency. |
| `rule.py` | Two deterministic controllers. `BaselineScheduler` reproduces conventional fixed-schedule BMS behaviour — the experimental control arm. `ReactiveGuard` is the fast tier: it applies the active policy and clamps every action to hard comfort and equipment limits. |
| `llm.py` | The supervisory agent. Assembles observations, invokes the model with tools, parses the returned policy, validates it, retries on malformed output, and falls back to `BaselineScheduler` when the model is unavailable. Owns the decision cadence. |
| `client.py` | The only code that talks to a model. One implementation over the official Groq SDK, covering every model in the Groq catalogue by name alone. Configuration is entirely environmental; handles timeouts, retries and token accounting. |
| `tools.py` | The tool registry: JSON-schema'd functions the LLM may call (query telemetry history, evaluate a candidate policy, read comfort limits, inspect simulation errors). Single source of truth, shared with `mcp_server.py`. |
| `prompts.py` | System prompt, observation rendering, and tool-result formatting. Isolated so prompt engineering is reviewable as a diff. |

### Core services — `backend/app/`

| Module | Responsibility |
|---|---|
| `loop.py` | `ClosedLoopRunner`: steps the simulator, decides at the configured cadence, applies actions, evaluates comfort and energy, and persists every record. The only module that knows the loop's shape. |
| `comfort.py` | Fanger PMV and PPD, plus band evaluation against ASHRAE-55 limits. Pure functions, no I/O. |
| `energy.py` | Converts power traces to kWh, applies a tariff and grid carbon intensity, and computes the baseline-vs-agent savings comparison. Pure functions, no I/O. |
| `db.py` | SQLite schema creation and repository functions. Every SQL statement in the project lives here. |
| `main.py` | FastAPI application: routes, run lifecycle, background execution, SSE streaming. Contains no domain logic — it delegates to `loop.py` and `db.py`. |
| `schemas.py` | Pydantic models for the HTTP boundary, kept distinct from internal dataclasses so the API can evolve independently. |
| `config.py` | Typed settings from environment: model name, LLM base URL, EnergyPlus install path, database path, decision cadence, comfort limits. |
| `mcp_server.py` | Wraps `tools.ToolRegistry` as an MCP server so an external MCP-capable client drives the same tools the in-process agent uses. |
| `utils/logsummary.py` | Compacts long EnergyPlus logs and telemetry histories into a bounded token budget: severity filtering, deduplication of repeated warnings, and statistical summarisation of long traces. |
| `utils/timeutil.py` | Simulation-clock arithmetic: step indices to wall-clock, occupied-hour tests, cadence decisions. |

---

## 5. Class Responsibilities

| Class | Module | Responsibility |
|---|---|---|
| `BuildingState` | `sim/base.py` | Immutable sensor snapshot at one timestep: sim time, step index, zone air temperature, outdoor temperature, occupancy fraction, HVAC mode, active set-points, lighting level, ventilation rate, instantaneous power, CO₂ concentration. |
| `ControlAction` | `sim/base.py` | Immutable actuator command: heating set-point, cooling set-point, lighting level, ventilation rate. What the loop injects into the simulator. |
| `Simulator` | `sim/base.py` | Protocol: `reset()`, `step(action) -> BuildingState`, `close()`, plus `horizon_steps` and `timestep_seconds`. The contract both simulators satisfy. |
| `ToySimulator` | `sim/toy.py` | RC-network single-zone building implementing `Simulator`. Owns thermal state and the energy model. |
| `EnergyPlusSimulator` | `sim/energyplus.py` | Implements `Simulator` over the EnergyPlus runtime API. Owns callback registration, actuator/sensor handle resolution, and the thread bridge that turns a blocking simulation into discrete `step()` calls. |
| `WeatherProvider` | `sim/weather.py` | Supplies `(outdoor_temp, solar, occupancy)` for a step. `SyntheticWeather` and `EpwWeather` implement it. |
| `Controller` | `agents/base.py` | Protocol: `decide(state, history) -> Decision`. Implemented by every control strategy. |
| `ControlPolicy` | `agents/base.py` | The supervisor's output: target set-points, strategy label, ventilation and lighting targets, and the step index until which the policy is valid. |
| `Decision` | `agents/base.py` | One control decision: the `ControlAction`, the originating policy, natural-language rationale, tool calls made, latency in milliseconds, model identifier, and whether fallback was used. |
| `BaselineScheduler` | `agents/rule.py` | Fixed occupancy-schedule controller. The control arm against which savings are measured. |
| `ReactiveGuard` | `agents/rule.py` | Fast tier. Applies the active policy each timestep and clamps it to hard comfort and equipment limits. |
| `LLMSupervisor` | `agents/llm.py` | Slow tier. Produces a `ControlPolicy` from telemetry via tool-calling, validates and retries, delegates every timestep to an internal `ReactiveGuard`. |
| `LLMClient` | `agents/client.py` | OpenAI-compatible chat/tool-call transport with timeout and retry. |
| `ToolRegistry` | `agents/tools.py` | Holds tool definitions and JSON schemas; dispatches a tool call by name; exports schemas for both the LLM and MCP. |
| `ClosedLoopRunner` | `loop.py` | Owns the closed loop: step, observe, decide on cadence, act, evaluate, persist. |
| `RunSummary` | `energy.py` | Aggregated result of a run: total kWh, peak demand, cost, CO₂, comfort violation count, mean PPD. |
| `SavingsReport` | `energy.py` | Baseline-vs-agent comparison: absolute and percentage kWh reduction, peak reduction, cost and carbon delta, comfort delta. The headline number. |
| `Settings` | `config.py` | Typed environment-driven configuration. |

---

## 6. API Endpoints

Base path `/api`. All payloads JSON.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Liveness, plus availability of EnergyPlus and the LLM endpoint. |
| `GET` | `/api/config` | Available controllers, simulators, models, scenarios and comfort limits. |
| `GET` | `/api/scenarios` | List scenario definitions from `backend/config/scenarios/`. |
| `POST` | `/api/runs` | Start a run. Body: scenario, controller, simulator, horizon, optional `baseline_run_id`. Returns the run record immediately; execution proceeds in the background. |
| `GET` | `/api/runs` | List runs, newest first, with summary metrics. |
| `GET` | `/api/runs/{run_id}` | One run's full record and summary. |
| `DELETE` | `/api/runs/{run_id}` | Delete a run and its telemetry. |
| `POST` | `/api/runs/{run_id}/stop` | Request cooperative cancellation of an in-flight run. |
| `GET` | `/api/runs/{run_id}/timeseries` | Telemetry rows. `?since_step=` for incremental polling, `?stride=` for downsampling long horizons. |
| `GET` | `/api/runs/{run_id}/decisions` | Agent decisions with rationale, tool calls and latency. `?since_step=` supported. |
| `GET` | `/api/runs/{run_id}/summary` | Aggregated KPIs for one run. |
| `GET` | `/api/runs/{run_id}/stream` | Server-Sent Events: live telemetry and decisions while a run executes. |
| `GET` | `/api/compare` | `?baseline_run_id=&agent_run_id=` — the `SavingsReport`. The deliverable-3 number. |
| `GET` | `/api/runs/{run_id}/export` | CSV export of the full run for offline analysis. |

---

## 7. Database Schema

SQLite, single file, accessed through `db.py`. Three tables: decisions are separate from
timesteps because the supervisor runs at a different cadence than the simulation — one
decision spans many timesteps, and forcing them into one table would either duplicate
rationales or leave most rows null.

```sql
CREATE TABLE runs (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    label                TEXT    NOT NULL,
    controller           TEXT    NOT NULL,   -- 'baseline' | 'rule' | 'llm'
    simulator            TEXT    NOT NULL,   -- 'toy' | 'energyplus'
    scenario             TEXT    NOT NULL,   -- scenario id
    model                TEXT,               -- LLM identifier, null for rule runs
    baseline_run_id      INTEGER REFERENCES runs(id) ON DELETE SET NULL,
    status               TEXT    NOT NULL,   -- 'running' | 'complete' | 'failed' | 'stopped'
    error                TEXT,
    horizon_steps        INTEGER NOT NULL,
    timestep_seconds     INTEGER NOT NULL,
    started_at           TEXT    NOT NULL,
    finished_at          TEXT,
    total_kwh            REAL,
    peak_kw              REAL,
    cost                 REAL,
    co2_kg               REAL,
    comfort_violations   INTEGER,
    mean_ppd             REAL
);

CREATE TABLE timesteps (
    run_id           INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step             INTEGER NOT NULL,
    sim_time         TEXT    NOT NULL,   -- ISO-8601 simulated wall-clock
    zone_temp_c      REAL    NOT NULL,
    outdoor_temp_c   REAL    NOT NULL,
    occupancy        REAL    NOT NULL,   -- 0.0 … 1.0
    hvac_mode        TEXT    NOT NULL,   -- 'off' | 'heating' | 'cooling'
    heating_sp_c     REAL    NOT NULL,
    cooling_sp_c     REAL    NOT NULL,
    lighting_level   REAL    NOT NULL,   -- 0.0 … 1.0
    ventilation_ach  REAL    NOT NULL,
    power_kw         REAL    NOT NULL,
    energy_kwh       REAL    NOT NULL,   -- this step only
    co2_ppm          REAL,
    pmv              REAL    NOT NULL,
    ppd              REAL    NOT NULL,
    comfort_ok       INTEGER NOT NULL,   -- 0 | 1
    PRIMARY KEY (run_id, step)
);

CREATE TABLE decisions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step             INTEGER NOT NULL,
    sim_time         TEXT    NOT NULL,
    strategy         TEXT    NOT NULL,   -- e.g. 'precool', 'setback', 'hold'
    heating_sp_c     REAL    NOT NULL,
    cooling_sp_c     REAL    NOT NULL,
    lighting_level   REAL    NOT NULL,
    ventilation_ach  REAL    NOT NULL,
    rationale        TEXT    NOT NULL,   -- natural-language explanation, shown in UI
    tool_calls       TEXT,               -- JSON array of {name, arguments, result}
    latency_ms       INTEGER,
    prompt_tokens    INTEGER,
    completion_tokens INTEGER,
    retries          INTEGER NOT NULL DEFAULT 0,
    fallback_used    INTEGER NOT NULL DEFAULT 0,
    guard_clamped    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_timesteps_run_step ON timesteps(run_id, step);
CREATE INDEX idx_decisions_run_step ON decisions(run_id, step);
```

An annual run at 15-minute resolution is ~35,000 rows per run — well inside SQLite's
comfortable range, and the entire evidence base is a single committable file.

---

## 8. Agent Workflow

```
                 ┌─────────────────────────────────────────────┐
                 │ every timestep                              │
                 │   ReactiveGuard.decide(state, history)      │
                 │     ├─ apply active ControlPolicy           │
                 │     ├─ clamp to hard comfort limits         │
                 │     ├─ clamp to equipment limits            │
                 │     └─ return Decision (guard_clamped flag) │
                 └────────────────────┬────────────────────────┘
                                      │
                    is step % cadence == 0 ?
                                      │
              ┌──────── no ───────────┴──────── yes ────────┐
              ▼                                             ▼
      keep active policy                    ┌───────────────────────────────┐
                                            │ 1. Build observation          │
                                            │    current state              │
                                            │    + compacted recent history │
                                            │      (utils/logsummary.py)    │
                                            │    + targets: comfort band,   │
                                            │      peak threshold, tariff,  │
                                            │      grid carbon intensity    │
                                            └───────────────┬───────────────┘
                                                            ▼
                                            ┌───────────────────────────────┐
                                            │ 2. LLMClient.chat(            │
                                            │      prompts.SYSTEM,          │
                                            │      observation,             │
                                            │      tools=ToolRegistry)      │
                                            └───────────────┬───────────────┘
                                                            ▼
                                            ┌───────────────────────────────┐
                                            │ 3. Tool-calling loop          │
                                            │    get_recent_telemetry       │
                                            │    get_comfort_limits         │
                                            │    evaluate_policy            │
                                            │    get_energy_summary         │
                                            │    get_simulation_errors      │
                                            │    (bounded iteration count)  │
                                            └───────────────┬───────────────┘
                                                            ▼
                                            ┌───────────────────────────────┐
                                            │ 4. set_control_policy(...)    │
                                            │    parse + schema-validate    │
                                            └───────────────┬───────────────┘
                                                            ▼
                                      ┌──────── valid? ─────┴─── invalid ────┐
                                      ▼                                      ▼
                          ┌─────────────────────┐            ┌──────────────────────────┐
                          │ adopt ControlPolicy │            │ SELF-CORRECTION          │
                          │ persist Decision    │            │ return the validation    │
                          │ with rationale      │            │ error to the model and   │
                          └─────────────────────┘            │ retry (bounded)          │
                                                             │  exhausted → fall back   │
                                                             │  to BaselineScheduler,   │
                                                             │  flag fallback_used,     │
                                                             │  loop continues          │
                                                             └──────────────────────────┘
```

**Latency management.** The supervisor is invoked on a cadence (default: hourly in
simulated time), never per timestep. Its call is bounded by a timeout; on expiry the
previous policy remains active and the loop proceeds. The guard therefore always has a
valid policy, and no LLM behaviour can stall the simulation.

**Handling lengthy simulation logs.** Raw EnergyPlus output and full telemetry traces
exceed any practical context window. `utils/logsummary.py` reduces them before they reach
a prompt: severity filtering (errors and severe warnings only), deduplication of repeated
warnings into counted entries, and statistical compression of long numeric traces into
min/mean/max/trend per window. The model sees a bounded, information-dense observation.

---

## 9. EnergyPlus Workflow

EnergyPlus is integrated through the **runtime Python API**, not by editing and re-running
`.idf` files. The problem statement requires set-points to "feed directly back into the
*active* EnergyPlus instance"; only the runtime API satisfies that.

```
  ClosedLoopRunner                        EnergyPlusSimulator                EnergyPlus
        │                                          │                              │
        │  reset()                                 │                              │
        ├─────────────────────────────────────────►│                              │
        │                                          │ add EnergyPlus install dir   │
        │                                          │ to sys.path, import          │
        │                                          │ pyenergyplus.api             │
        │                                          │                              │
        │                                          │ register callbacks:          │
        │                                          │  begin_new_environment       │
        │                                          │  begin_system_timestep_      │
        │                                          │    before_predictor          │
        │                                          │  end_zone_timestep_after_    │
        │                                          │    zone_reporting            │
        │                                          ├─────────────────────────────►│
        │                                          │                              │
        │                                          │ run_energyplus() on a worker │
        │                                          │ thread (it blocks until the  │
        │                                          │ whole simulation completes)  │
        │                                          ├─────────────────────────────►│
        │                                          │                              │
        │  ┌───────────────────────────────────────┴──────────────────────────┐   │
        │  │ per-timestep callback, executing on the simulation thread        │   │
        │  │                                                                  │   │
        │  │  a. resolve handles once warmup is done                          │   │
        │  │       get_variable_handle / get_actuator_handle                  │   │
        │  │  b. READ sensors  api.exchange.get_variable_value(...)           │   │
        │  │       Zone Mean Air Temperature                                  │   │
        │  │       Site Outdoor Air Drybulb Temperature                       │   │
        │  │       Zone People Occupant Count                                 │   │
        │  │       Facility Total Electricity Demand Rate                     │   │
        │  │       Zone Air CO2 Concentration                                 │   │
        │  │  c. hand BuildingState to the runner, block for a ControlAction  │   │
        │  │  d. WRITE actuators  api.exchange.set_actuator_value(...)        │   │
        │  │       Schedule:Constant → Schedule Value  (heating set-point)    │   │
        │  │       Schedule:Constant → Schedule Value  (cooling set-point)    │   │
        │  │       Lights → Electricity Rate  /  lighting schedule            │   │
        │  │       Outdoor Air Controller → outdoor air mass flow rate        │   │
        │  └──────────────────────────────────────────────────────────────────┘   │
        │                                          │                              │
        │  step(action) → BuildingState            │                              │
        │◄─────────────────────────────────────────┤                              │
        │           (repeat until horizon)         │                              │
        │                                          │                              │
        │  close()                                 │ join worker thread,          │
        ├─────────────────────────────────────────►│ collect .err / .csv output   │
```

**Thread bridge.** `run_energyplus()` blocks for the whole simulation, but `Simulator.step()`
must return one state at a time. `EnergyPlusSimulator` runs EnergyPlus on a worker thread
and connects it to the caller with a pair of bounded queues: the callback pushes a
`BuildingState` and blocks awaiting a `ControlAction`. The runner therefore sees an
ordinary step-wise simulator, and control remains genuinely synchronous with the physics.

**Deliverable 2 (`.idf` files).** `models/baseline/` holds the unmodified reference model.
When the agent's policy implies a structural change rather than a runtime set-point, the
modified `.idf` is written to `models/generated/` and recorded against the run, giving the
required "baseline file along with the modified versions generated during runtime
evaluation".

**Warmup.** EnergyPlus warmup timesteps must not be logged or controlled. The simulator
suppresses callbacks until `api.exchange.warmup_flag()` clears, so telemetry begins at the
first real timestep and the energy comparison is not polluted.

---

## 10. Sequence Diagram

A complete demonstration: baseline run, agent run, savings comparison.

```
User    Dashboard   FastAPI    ClosedLoopRunner   Controller    LLMClient   Simulator   SQLite
 │          │          │              │                │            │           │         │
 │ start    │          │              │                │            │           │         │
 ├─────────►│          │              │                │            │           │         │
 │          │ POST /api/runs {baseline}                │            │           │         │
 │          ├─────────►│              │                │            │           │         │
 │          │          │ INSERT run (status=running)   │            │           │         │
 │          │          ├───────────────────────────────────────────────────────────────►│
 │          │◄─────────┤ 202 {run_id: 1}               │            │           │         │
 │          │          │ spawn background task         │            │           │         │
 │          │          ├─────────────►│                │            │           │         │
 │          │          │              │ reset()        │            │           │         │
 │          │          │              ├───────────────────────────────────────►│         │
 │          │          │              │◄─────────────────────────── BuildingState        │
 │          │          │              │                │            │           │         │
 │          │          │       ╔══════╪════ loop over horizon ══════╪═══════════╪══════╗ │
 │          │          │       ║      │ decide(state, history)      │           │      ║ │
 │          │          │       ║      ├───────────────►│            │           │      ║ │
 │          │          │       ║      │◄─── Decision ──┤ (fixed schedule)       │      ║ │
 │          │          │       ║      │ step(action)   │            │           │      ║ │
 │          │          │       ║      ├───────────────────────────────────────►│      ║ │
 │          │          │       ║      │◄──────────────────── BuildingState ────┤      ║ │
 │          │          │       ║      │ pmv/ppd, kwh                │           │      ║ │
 │          │          │       ║      │ INSERT timestep + decision  │           │      ║ │
 │          │          │       ║      ├─────────────────────────────────────────────►│ ║ │
 │          │          │       ╚══════╪═════════════════════════════╪═══════════╪══════╝ │
 │          │          │              │ UPDATE run (complete, totals)           │         │
 │          │          │              ├─────────────────────────────────────────────────►│
 │          │          │              │                │            │           │         │
 │          │ POST /api/runs {llm, baseline_run_id: 1} │            │           │         │
 │          ├─────────►│              │                │            │           │         │
 │          │◄─────────┤ 202 {run_id: 2}               │            │           │         │
 │          │          ├─────────────►│                │            │           │         │
 │          │          │              │                │            │           │         │
 │          │          │       ╔══════╪════ loop over horizon ══════╪═══════════╪══════╗ │
 │          │          │       ║      │ decide(state, history)      │           │      ║ │
 │          │          │       ║      ├───────────────►│            │           │      ║ │
 │          │          │       ║      │                │ cadence hit?           │      ║ │
 │          │          │       ║      │                ├── yes ────►│           │      ║ │
 │          │          │       ║      │                │  observation + tools   │      ║ │
 │          │          │       ║      │                │◄─ tool_call ┤          │      ║ │
 │          │          │       ║      │                ├─ tool_result ─────────►│      ║ │
 │          │          │       ║      │                │◄─ ControlPolicy + rationale    ║ │
 │          │          │       ║      │                │ validate → guard clamp │      ║ │
 │          │          │       ║      │◄─── Decision ──┤            │           │      ║ │
 │          │          │       ║      │ step(action)   │            │           │      ║ │
 │          │          │       ║      ├───────────────────────────────────────►│      ║ │
 │          │          │       ║      │◄──────────────────── BuildingState ────┤      ║ │
 │          │          │       ║      │ INSERT timestep (+ decision if cadence) │      ║ │
 │          │          │       ║      ├─────────────────────────────────────────────►│ ║ │
 │          │          │       ╚══════╪═════════════════════════════╪═══════════╪══════╝ │
 │          │          │              │                │            │           │         │
 │          │ GET /api/runs/2/stream (SSE, throughout) │            │           │         │
 │          │◄═════════╪══ live telemetry + rationales ════════════════════════════════  │
 │          │          │              │                │            │           │         │
 │          │ GET /api/compare?baseline_run_id=1&agent_run_id=2     │           │         │
 │          ├─────────►│              │                │            │           │         │
 │          │          │ SELECT both summaries ────────────────────────────────────────►│
 │          │◄─────────┤ SavingsReport {kwh_saved_pct, comfort_delta, ...}      │         │
 │◄─────────┤ render   │              │                │            │           │         │
```

---

## 11. Data Flow Diagram

```
 ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
 │ Scenario     │   │ Weather      │   │ Building     │
 │ JSON         │   │ EPW /        │   │ model        │
 │ (occupancy,  │   │ synthetic    │   │ .idf / RC    │
 │  targets,    │   │              │   │ parameters   │
 │  tariff)     │   │              │   │              │
 └──────┬───────┘   └──────┬───────┘   └──────┬───────┘
        │                  │                  │
        └──────────────────┼──────────────────┘
                           ▼
                  ┌─────────────────┐
                  │   Simulator     │
                  └────────┬────────┘
                           │ BuildingState  (temp, outdoor, occupancy,
                           │                 power, CO₂, set-points)
                           ▼
        ┌──────────────────┴───────────────────┐
        │                                      │
        ▼                                      ▼
┌───────────────┐                    ┌──────────────────┐
│ comfort.py    │                    │ history buffer   │
│ PMV / PPD     │                    │ (in-memory ring) │
└───────┬───────┘                    └────────┬─────────┘
        │                                     │
        │                                     ▼
        │                          ┌────────────────────────┐
        │                          │ utils/logsummary.py    │
        │                          │ compact to token budget│
        │                          └───────────┬────────────┘
        │                                      ▼
        │                          ┌────────────────────────┐
        │                          │ prompts.py → LLMClient │
        │                          │   ◄── tools.py ───►    │
        │                          └───────────┬────────────┘
        │                                      │ ControlPolicy + rationale
        │                                      ▼
        │                          ┌────────────────────────┐
        │                          │ ReactiveGuard          │
        │                          │ clamp to hard limits   │
        │                          └───────────┬────────────┘
        │                                      │ ControlAction
        │                                      ▼
        │                            ┌───────────────────┐
        │                            │    Simulator      │  ◄── closes the loop
        │                            │    (actuators)    │
        │                            └───────────────────┘
        │
        ▼
┌───────────────┐        ┌──────────────┐        ┌───────────────────┐
│ energy.py     │───────►│ db.py        │───────►│ FastAPI /api      │
│ kWh, cost,    │        │ SQLite       │        │ REST + SSE        │
│ CO₂, peak     │        │ runs         │        └─────────┬─────────┘
└───────────────┘        │ timesteps    │                  │
                         │ decisions    │                  ▼
                         └──────────────┘        ┌───────────────────┐
                                                 │ React Dashboard   │
                                                 │ charts, KPIs,     │
                                                 │ AI explanations   │
                                                 └───────────────────┘

 Baseline run ──┐
                ├──► energy.SavingsReport ──► "X% kWh saved, comfort maintained"
 Agent run ─────┘
```

---

## 12. Technology Choices

| Concern | Choice | Why this and not the alternative |
|---|---|---|
| Simulation engine | **EnergyPlus** via `pyenergyplus` runtime API | Mandated. The runtime API is the only route that injects set-points into a *live* instance; `eppy` edit-and-rerun is batch optimisation and does not satisfy the requirement. |
| EnergyPlus binding | Install-bundled `pyenergyplus` on `sys.path` | `pyenergyplus` is not a PyPI package — it ships inside the EnergyPlus installation. `config.py` carries the install path. |
| Development simulator | **Custom RC thermal network** (NumPy) | Lets the loop, agent, persistence and dashboard be built and stress-tested before EnergyPlus exists, and serves as a live demo fallback. Two real implementations justify the Protocol. |
| LLM | **Groq + `llama-3.3-70b-versatile`** | Genuinely open-source weights as required, with reliable native tool-calling. Hosted inference removes the 7B-on-a-laptop quality ceiling and the local-GPU dependency on demo day, and is fast enough that a supervisory call costs well under a second. |
| LLM transport | **Official Groq SDK** (`groq` package) | One client, one endpoint, model selected by name. Every model in the catalogue speaks the same chat-completions and tool-calling shape, so switching model is a `.env` change — model-agnostic by construction, no adapter hierarchy. |
| Web framework | **FastAPI** | Already installed; async background tasks and SSE are first-class; Pydantic gives the API contract for free. |
| Persistence | **SQLite via stdlib `sqlite3`** | ~35k rows per annual run — trivial for SQLite. No server, no ORM, no migrations, and the whole evidence base is one committable file. |
| Frontend | **Vite + React + Recharts** | Fastest path to a live-updating multi-panel dashboard; Recharts covers every chart needed without a bespoke D3 layer. |
| Live updates | **SSE, with polling fallback** | One-directional server→client streaming is exactly the shape of the problem; simpler than WebSockets. |
| Protocol layer | **MCP server wrapping `ToolRegistry`** | Satisfies the MCP criterion without a second tool implementation — the in-process agent and any external MCP client call identical code. |
| Comfort metric | **Fanger PMV / PPD** | Named explicitly in the problem statement. Self-contained, deterministic, unit-testable. |
| Config | **Environment via `.env`** | Model, endpoint, EnergyPlus path and cadence differ per machine; nothing machine-specific is committed. |

---

## 13. Design Decisions

**D1 — The simulator is behind a Protocol, and the toy model is real.**
Not a mock and not scaffolding: it is a functioning building model that lets every other
layer be finished and validated before EnergyPlus is installed, and it de-risks the live
demonstration. Two genuine implementations are what justify the abstraction.

**D2 — Two-tier control.** The LLM cannot be in the per-timestep path: EnergyPlus callbacks
run on the simulation thread, and a supervisory call takes hundreds of milliseconds to
seconds while an annual run has 35,040 timesteps. Splitting policy (slow, LLM) from
enforcement (fast, deterministic) is what makes an extended horizon feasible at all — the
criterion carrying the most weight.

**D3 — The guard can override the LLM.** Hard comfort and equipment limits are enforced in
code, not requested in a prompt. A hallucinated set-point cannot harm occupants or the
comfort score. This is the single most important safety property in the design.

**D4 — Baseline and agent share one execution path.** Same runner, same weather, same
occupancy, same accounting — only the `Controller` differs. The savings figure is a
controlled experiment rather than a comparison of two programs.

**D5 — Fallback is a first-class path, not error handling.** If the model is unreachable,
slow, or emits invalid output after bounded retries, the run continues under
`BaselineScheduler` with `fallback_used` recorded. The loop never dies mid-demonstration,
and the flag keeps the results honest.

**D6 — Self-correction returns the validation error to the model.** Schema violations are
fed back as a tool result so the model can repair its own output, bounded by a retry
count. This is the "self-correction loop" the rubric asks for, implemented rather than
claimed.

**D7 — Decisions are stored separately from timesteps.** The supervisor's cadence differs
from the simulation's. Merging them would either duplicate every rationale across dozens of
rows or leave most rows null.

**D8 — Long logs are compacted before they reach a prompt, never truncated blindly.**
Severity filtering, warning deduplication and statistical windowing preserve information
density within a bounded token budget.

**D9 — Domain logic contains no I/O.** `comfort.py` and `energy.py` are pure functions;
`db.py` owns every SQL statement; `main.py` owns every HTTP concern. This is what makes the
test suite fast and the modules individually reviewable.

**D10 — No ORM, no migrations, no message broker, no container.** A two-day build with one
writer and one reader does not need them, and each would add a failure mode to a live
demonstration. `sqlite3` and an in-process background task are sufficient and are chosen
deliberately.

**D11 — MCP wraps the existing registry rather than duplicating it.** The tools the agent
calls in-process and the tools an external MCP client calls are the same functions.

---

## 14. Requirement Coverage

### Technical core requirements

| Requirement (from the problem statement) | How it is satisfied |
|---|---|
| Utilise EnergyPlus for high-fidelity simulation | `sim/energyplus.py` drives a real `.idf` through the EnergyPlus runtime API; baseline model in `models/baseline/`. |
| Use functional libraries (eppy / PyEnergyPlus / EMS / BCVTB) to bridge Python and the `.idf` | `pyenergyplus` runtime API — sensor reads via `api.exchange.get_variable_value`, actuator writes via `set_actuator_value`. |
| Deploy a modern open-source LLM, locally or self-hosted | `llama-3.3-70b-versatile` — open weights — served by Groq through `agents/client.py`. The model is named in `.env`, so any other open-source model in the catalogue is a configuration change. |
| Implement an MCP server or custom agentic tools | `agents/tools.py` is the custom tool registry; `mcp_server.py` exposes the identical registry over MCP. |
| The LLM must use tools to parse files, extract runtime errors, and execute tasks without human code modification | Tools include telemetry queries, comfort-limit lookup, policy evaluation, and `get_simulation_errors`, which surfaces parsed EnergyPlus `.err` output so the agent can react to runtime problems autonomously. |
| **Feedback** — stream continuous performance metrics (zone temperatures, IAQ, energy, PMV) | `BuildingState` carries zone temperature, outdoor temperature, occupancy, CO₂ (IAQ), and power every timestep; `comfort.py` derives PMV/PPD; all persisted and streamed over SSE. |
| **Reasoning** — evaluate data against comfort targets, peak-demand thresholds, grid carbon intensity | The observation built by `prompts.py` includes the comfort band, the peak-demand threshold and grid carbon intensity from the scenario; `energy.py` scores the trade-off. |
| **Control Actions** — compute optimal ECMs and update dynamic set-points | `ControlPolicy` carries set-points, pre-cool/setback strategy, lighting level and ventilation rate — the ECMs the agent selects. |
| **Forward Injection** — computed set-points feed automatically into the *active* EnergyPlus instance | Actuator writes occur inside the EnergyPlus timestep callback, in-process, with no restart and no file rewriting. |

### Deliverables

| Deliverable | Where it comes from |
|---|---|
| 1. Fully functional source code (E+ wrapper, agent orchestration, communication bus) | `sim/energyplus.py` (wrapper), `agents/` + `loop.py` (orchestration), `main.py` + `db.py` (bus). |
| 2. Building models — baseline `.idf` plus modified versions generated at runtime | `models/baseline/` and `models/generated/`, the latter written and recorded per run. |
| 3. Quantitative savings dashboard proving % kWh reduction while holding comfort | `/api/compare` → `SavingsReport`; the React dashboard renders kWh, % saved, PMV band and violation count side by side. |
| 4. System architecture document (tool-calling, prompt engineering, latency management, long logs) | This document — §8 agent workflow and latency, §13 D2/D6/D8, §12 technology choices. |
| 5. PoC demonstration video showing live data transfer and automatic control updates | The SSE-driven dashboard shows telemetry and rationales updating live during a run — recorded directly. |
| 6. Presentation | Diagrams and figures in this document feed the template. |

### Evaluation criteria

| Criterion | Weight | How the architecture targets it |
|---|---|---|
| System Integration — robust closed-loop execution over an extended horizon without crashing | 30% | Two-tier control keeps the LLM off the per-timestep path (D2); bounded timeouts and retries; a first-class fallback path so no model failure aborts a run (D5); the thread bridge keeps control synchronous with the physics. |
| Energy Efficiency Realized — net reduction vs. baseline scheduling | 25% | Paired runs on one execution path (D4) with identical conditions; `SavingsReport` produces the headline percentage; the agent's ECMs are pre-cooling, setback, lighting dimming and ventilation modulation. |
| Thermal Comfort & Constraints | 20% | PMV/PPD computed every timestep against ASHRAE-55 bands; the guard clamps every action to hard limits so comfort is enforced in code, not requested in a prompt (D3); violations reported alongside savings. |
| Agentic Autonomy & Code Elegance — OSS tool-calling, MCP, self-correction | 15% | Local OSS model with real tool-calling; MCP server over the same registry (D11); validation errors returned to the model for bounded self-repair (D6); layered modules with no I/O in domain logic (D9). |
| Presentation & Documentation | 10% | This document; a multi-panel live dashboard; CSV export; per-decision rationales that make the agent's reasoning legible on video. |

---

## 15. Implementation Milestones

| Milestone | Contents | Demonstrable outcome |
|---|---|---|
| **M1** | `sim/base.py`, `sim/toy.py`, `sim/weather.py`, `agents/base.py`, `agents/rule.py`, `comfort.py`, `energy.py`, `db.py`, `loop.py`, `main.py`, `cli.py`, tests | `python backend/cli.py` runs baseline and rule controllers over one week and prints kWh saved and comfort violations. A working closed loop with quantified savings. |
| **M2** | `agents/client.py`, `agents/tools.py`, `agents/prompts.py`, `agents/llm.py`, `utils/logsummary.py` | Same command with `--controller=llm`: an open-source model drives the building, with readable rationales and measured savings. |
| **M3** | `frontend/` — all components, hooks, API client | Live dashboard: temperature, energy, occupancy, comfort, current action, cumulative savings, streaming AI explanations. |
| **M4** | `sim/energyplus.py`, `mcp_server.py`, `models/baseline/`, EnergyPlus install | The identical loop, agent and dashboard running against real EnergyPlus, plus MCP exposure. |

Milestone M1 has no external dependency at all. M2 and M3 need only a `GROQ_API_KEY`;
M4 is the only milestone that requires an EnergyPlus installation.
