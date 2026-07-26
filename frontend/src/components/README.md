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
| `ChartFrame.jsx` | Not a panel — the shared heading, plot height and empty state the four charts reuse. |

`AgentLog` and `KpiRow` are the two panels that carry the demonstration: one
proves the agent is reasoning, the other proves it is saving energy.

Both are written to be honest rather than flattering. `KpiRow` shows "No
baseline" instead of inventing a percentage, and renders a negative saving as a
loss. `AgentLog` marks a guard step distinctly from a reasoned one — `latency_ms`
is the discriminator — and never hides a fallback or a clamp.

Each chart exports the pure function that shapes its data (`buildEnergySeries`
and so on), which is where the chart tests assert; Recharts under jsdom has no
layout engine, so asserting on SVG geometry would test the stub, not the code.
