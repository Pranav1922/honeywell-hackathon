# Eco-Loop Building Agents

An autonomous closed-loop building control agent. A physics engine simulates a
building, an open-source LLM reasons over its live telemetry, and the resulting
set-points are injected back into the *running* simulation — proving measurable
energy savings while holding occupant comfort.

**Honeywell Hackathon · Question 1**

```
EnergyPlus ──► sensor telemetry ──► LLM agent ──► control policy
     ▲                                                  │
     └──────────── set-point injection ◄────────────────┘
                         (live, in-process)

              baseline run  vs  agent run  ──►  % kWh saved
```

## Status

Architecture is frozen and scaffolded; implementation proceeds by milestone.

| Milestone | Contents | State |
|---|---|---|
| **M1** | Toy simulator, baseline + guard controllers, PMV comfort, energy accounting, SQLite, FastAPI, CLI | Not started |
| **M2** | LLM supervisor, tool registry, prompts, log compaction | Not started |
| **M3** | React dashboard | Not started |
| **M4** | EnergyPlus integration, MCP server, baseline `.idf` | Not started |

M1–M3 have no dependency on EnergyPlus or Ollama being installed. Only M4 does.

## Design in one paragraph

The simulator sits behind a protocol with two real implementations — an RC
thermal-network model that runs today, and EnergyPlus driven through its runtime
Python API — so every other layer is written once. Control is two-tier: a
deterministic guard runs every timestep in microseconds and clamps set-points to
hard comfort limits, while the LLM runs on a cadence and chooses the policy the
guard enforces. That split is what makes an extended simulation horizon feasible
at all, and it means a hallucinated set-point can never reach the building.
Baseline and agent runs execute on the identical code path with identical
weather and occupancy, so the savings figure is a controlled experiment rather
than an anecdote.

Full detail: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Layout

```
docs/       ARCHITECTURE.md — the design document (deliverable 4)
backend/    simulation, agents, closed loop, persistence, API
frontend/   React savings dashboard (deliverable 3)
```

Every folder carries a README explaining its purpose.

## Setup

```bash
cp .env.example .env
pip install -r requirements.txt
```

### Backend

```bash
cd backend
python cli.py --scenario summer_week --controller baseline   # headless
uvicorn app.main:app --reload                                # API on :8000
python -m pytest                                             # tests
```

### Dashboard

```bash
cd frontend
npm install
npm run dev                                                  # :5173
```

### Open-source LLM (Milestone 2)

```bash
brew install ollama && ollama serve
ollama pull qwen2.5:7b-instruct
```

Ollama exposes an OpenAI-compatible endpoint, so any other self-hosted
OpenAI-compatible server works by changing `LLM_BASE_URL` in `.env` — no code
change.

### EnergyPlus (Milestone 4)

Install EnergyPlus from the NREL releases page and set `ENERGYPLUS_DIR` in
`.env`. Note that `pyenergyplus` is **not** a pip package — it ships inside the
EnergyPlus installation directory, which the code places on `sys.path`.
