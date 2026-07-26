# backend/models/baseline/

The unmodified baseline building model and weather file.

Expected contents (added in Milestone 4):

- `small_office.idf` — a single-zone or small-office reference model with
  `Schedule:Constant` objects named `HEATING_SETPOINT_SCH`,
  `COOLING_SETPOINT_SCH`, `LIGHTING_SCH` and `VENTILATION_SCH`. Those schedules
  are what the agent actuates at runtime; the model must expose them or the
  actuator handles will not resolve.
- `weather.epw` — the matching weather file.

This directory is read-only at runtime. Nothing here is modified by a run.
