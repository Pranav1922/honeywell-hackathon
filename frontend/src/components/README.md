# frontend/src/components/

One file per dashboard panel. All presentational — they receive data as props
and never fetch, so every chart on screen shows the same instant of the same run.

| Component | Shows |
|---|---|
| `RunControls.jsx` | Scenario, controller, simulator and horizon pickers; start and stop. |
| `KpiRow.jsx` | Total kWh, percent saved, peak demand, comfort violations, mean PPD. |
| `TemperatureChart.jsx` | Zone temperature vs. outdoor temperature, with the set-point band shaded. |
| `EnergyChart.jsx` | Baseline and agent power overlaid, plus cumulative kWh. |
| `ComfortChart.jsx` | PMV trace with the acceptable band shaded and excursions marked. |
| `OccupancyChart.jsx` | Occupancy fraction over time. |
| `ActionPanel.jsx` | The control action in force, and whether the guard clamped it. |
| `AgentLog.jsx` | Streamed rationales, tool calls, latency, retries, fallbacks. |

`AgentLog` and `KpiRow` are the two panels that carry the demonstration: one
proves the agent is reasoning, the other proves it is saving energy.
