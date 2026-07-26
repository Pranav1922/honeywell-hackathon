# backend/

Python backend: simulation, agents, closed-loop orchestration, persistence and
the HTTP API.

| Path | Purpose |
|---|---|
| `app/` | The application package. See `app/README.md`. |
| `cli.py` | Headless entrypoint — run a scenario and print the savings without the API or dashboard. |
| `config/` | Scenario definitions. Machine-specific settings come from `.env`, not from here. |
| `models/` | EnergyPlus building models: the baseline `.idf` and agent-generated variants (deliverable 2). |
| `tests/` | Unit tests for the domain modules. |

## Running

```bash
pip install -r ../requirements.txt
python cli.py --scenario summer_week --controller baseline    # headless
uvicorn app.main:app --reload                                  # API on :8000
```

Run commands from `backend/` so that `app` resolves as a package.
