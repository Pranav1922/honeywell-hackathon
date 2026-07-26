<div align="center">

# 🏢 Eco-Loop Building Agents

**An open-source LLM closes the control loop on a live EnergyPlus simulation — and proves the savings.**

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black)](https://react.dev/)
[![EnergyPlus](https://img.shields.io/badge/EnergyPlus-runtime%20API-F5A623)](https://energyplus.net/)
[![Groq](https://img.shields.io/badge/Groq-Llama%203.3%2070B-F55036)](https://console.groq.com/)
[![MCP](https://img.shields.io/badge/MCP-stdio%20server-6E56CF)](https://modelcontextprotocol.io/)
[![Tests](https://img.shields.io/badge/tests-392%20passing-4c1)](#-testing)

*Honeywell Hackathon · Question 1 — Autonomous Closed-Loop Building Control*

</div>

---

## Overview

Buildings consume roughly **40 % of global energy**. Most are still run by building
management systems that follow a fixed clock: the same set-points every weekday,
regardless of weather, occupancy or grid carbon intensity. They are *reactive* —
comfort is corrected only after the occupants complain.

**Eco-Loop closes the loop.** A physics engine simulates a real building, an
open-source LLM reasons over its live telemetry against comfort, energy, tariff
and carbon targets, and the resulting set-points are injected back into the
*running* simulation through EnergyPlus actuators — no file rewriting, no restart.

```
EnergyPlus ──► sensor telemetry ──► LLM agent ──► control policy
     ▲                                                  │
     └──────────── set-point injection ◄────────────────┘
                     (live, in-process)

          baseline run  vs  agent run  ──►  % kWh saved
```

### The key innovation: two-tier control

An LLM call costs hundreds of milliseconds. An annual simulation at 15-minute
steps has **35,040 timesteps**. Putting a model on that path is impossible, and
letting one write set-points directly is unsafe.

| Tier | Runs | Cost | Responsibility |
|---|---|---|---|
| **Reactive Guard** (`agents/rule.py`) | every timestep | microseconds | Enforces the active policy, clamps every action to hard comfort and equipment limits |
| **LLM Supervisor** (`agents/llm.py`) | every *N* timesteps | seconds | Chooses the policy: set-point targets, pre-cool/setback strategy, lighting, ventilation |

The LLM sets **policy**; the guard **enforces** it and may override it. A
hallucinated set-point therefore cannot reach the building, and a slow or failed
model never stalls the run.

> [!NOTE]
> **Baseline and agent runs execute the identical code path**, with identical
> weather and occupancy — only the `Controller` differs. The savings figure is a
> controlled experiment, not a comparison of two different programs.

---

## ✨ Features

| | Feature | What it means |
|---|---|---|
| **M1** | Toy simulator, baseline + guard controllers, PMV comfort, energy accounting, SQLite, FastAPI, CLI | Complete |
| **M2** | LLM supervisor, tool registry, prompts, log compaction | Complete |
| **M3** | React dashboard | Not started |
| **M4** | EnergyPlus integration, MCP server, baseline `.idf` | Not started |

M1 needs nothing external. M2 needs a Groq API key. Only M4 needs EnergyPlus.

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
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
</details>

<details>
<summary><b>Windows (PowerShell)</b></summary>

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
</details>

### 3. Environment file

```bash
cd backend
python cli.py --scenario summer_week --controller baseline   # fixed schedule
python cli.py --scenario summer_week --controller rule    --compare 1
python cli.py --scenario summer_week --controller llm     --compare 1   # the agent
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

The supervisor runs an open-source model — Llama 3.3 70B by default — served by
Groq. Get a key at [console.groq.com/keys](https://console.groq.com/keys) and put
it in `.env`:

```bash
GROQ_API_KEY=gsk_...
GROQ_MODEL=llama-3.3-70b-versatile
```

The key is read from the environment only and is never committed. Switching model
is a `.env` change, not a code change; `--controller=baseline` and
`--controller=rule` need no key at all.

### EnergyPlus (Milestone 4)

Install EnergyPlus from the NREL releases page and set `ENERGYPLUS_DIR` in
`.env`. Note that `pyenergyplus` is **not** a pip package — it ships inside the
EnergyPlus installation directory, which the code places on `sys.path`.
