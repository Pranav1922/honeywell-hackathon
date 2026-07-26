# backend/config/

Scenario definitions — the experiment setups a run can be launched against.

Machine-specific configuration (model endpoint, EnergyPlus install path,
database location) is **not** here. It comes from environment variables loaded
by `app/config.py`; see `.env.example` at the repository root.

| Path | Purpose |
|---|---|
| `scenarios/` | One JSON file per scenario. See its README for the schema. |
