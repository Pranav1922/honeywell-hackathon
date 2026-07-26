# backend/config/scenarios/

One JSON file per scenario. A scenario fixes everything a run needs *except* the
controller — which is precisely what makes a baseline run and an agent run
comparable.

```json
{
  "id": "summer_week",
  "label": "Summer week, hot climate",
  "start": "2024-07-15T00:00:00",
  "days": 7,
  "timestep_seconds": 900,
  "weather": {
    "provider": "synthetic",
    "mean_temp_c": 28.0,
    "daily_swing_c": 9.0,
    "peak_solar_w_m2": 850.0
  },
  "occupied_hours": [8, 18],
  "targets": {
    "comfort_pmv_low": -0.5,
    "comfort_pmv_high": 0.5,
    "peak_demand_kw": 10.0,
    "tariff_per_kwh": 0.18,
    "grid_carbon_kg_per_kwh": 0.42
  },
  "energyplus": {
    "idf": "baseline/small_office.idf",
    "epw": "baseline/weather.epw"
  }
}
```

The `energyplus` block is ignored when the run uses the toy simulator, and the
`weather` block is ignored when it uses EnergyPlus. Everything else applies to
both, so one scenario file drives either backend.

Scenario files are added in Milestone 1.
