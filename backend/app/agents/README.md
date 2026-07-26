# backend/app/agents/

The control and cognitive layer. Every controller satisfies the same protocol,
which is what lets the savings comparison run on a single execution path.

| Module | Purpose | Milestone |
|---|---|---|
| `base.py` | `Controller` protocol, `ControlPolicy`, `Decision`. | 1 |
| `rule.py` | `BaselineScheduler` (the control arm) and `ReactiveGuard` (the fast tier). | 1 |
| `llm.py` | `LLMSupervisor` — the slow tier. | 2 |
| `client.py` | `LLMClient` — the only code that talks to a model. | 2 |
| `tools.py` | `ToolRegistry` — tool definitions, shared with the MCP server. | 2 |
| `prompts.py` | System prompt and observation rendering, isolated so prompt changes are reviewable. | 2 |

## The two-tier design

EnergyPlus callbacks execute on the simulation thread, and an annual run at
15-minute resolution has 35,040 timesteps. A 7B model taking seconds per call
cannot sit in that path. So:

- **`ReactiveGuard`** runs every timestep in microseconds. It applies the active
  policy and clamps it to hard comfort and equipment limits.
- **`LLMSupervisor`** runs on a cadence in seconds. It chooses the policy.

The LLM sets policy; the guard enforces it and may override it. Hard comfort
limits are enforced in code, never requested in a prompt — a hallucinated
set-point cannot reach the building.

## Failure is a designed path

Invalid model output is returned to the model as a tool result for bounded
self-repair. When retries or the timeout are exhausted, the run continues under
`BaselineScheduler` and the decision records `fallback_used`. A run never dies
because a model misbehaved, and the flag keeps the reported results honest.

## Model independence

Ollama serves an OpenAI-compatible endpoint, so one client covers local
open-source models and any self-hosted OpenAI-compatible server. Switching model
or endpoint is a change to `.env`, not to code.
