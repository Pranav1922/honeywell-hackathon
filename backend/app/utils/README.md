# backend/app/utils/

Two helpers with narrow, stated charters. This is deliberately **not** a
catch-all — code that does not fit one of the descriptions below belongs in a
domain module instead.

| Module | Purpose |
|---|---|
| `logsummary.py` | Compacts long EnergyPlus logs and telemetry traces into a bounded token budget: severity filtering, warning deduplication, statistical windowing. This is the project's answer to the "handling lengthy simulation logs" requirement. |
| `timeutil.py` | Simulation-clock arithmetic: step index to wall-clock, occupancy tests, cadence decisions. |
