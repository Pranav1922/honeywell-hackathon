// Fixtures matching the backend's wire models in `backend/app/schemas.py`.
//
// Field names are copied from the Pydantic models deliberately. If the API
// contract ever drifts, these fixtures are where it should be corrected — and
// every component test then fails at once rather than the dashboard silently
// rendering dashes in production.

export function timestep(step, overrides = {}) {
  return {
    step,
    sim_time: '2024-07-15T12:00:00',
    zone_temp_c: 24.5,
    outdoor_temp_c: 30.0,
    // Deliberately different from lighting_level, so a test asserting on one
    // percentage cannot accidentally match the other.
    occupancy: 0.75,
    hvac_mode: 'cooling',
    heating_sp_c: 21.0,
    cooling_sp_c: 25.5,
    lighting_level: 0.6,
    ventilation_ach: 1.2,
    power_kw: 6.4,
    energy_kwh: 1.6,
    co2_ppm: 760.0,
    pmv: 0.12,
    ppd: 5.3,
    comfort_ok: true,
    ...overrides,
  }
}

export function decision(step, overrides = {}) {
  return {
    step,
    sim_time: '2024-07-15T12:00:00',
    strategy: 'hold',
    heating_sp_c: 21.0,
    cooling_sp_c: 25.5,
    lighting_level: 0.6,
    ventilation_ach: 1.2,
    rationale: 'Widened the dead-band to the comfort-band edge to cut cooling.',
    tool_calls: null,
    latency_ms: null,
    prompt_tokens: null,
    completion_tokens: null,
    retries: 0,
    fallback_used: false,
    guard_clamped: false,
    ...overrides,
  }
}

/** A decision that came from the model rather than the guard alone. */
export function supervisoryDecision(step, overrides = {}) {
  return decision(step, {
    latency_ms: 820,
    prompt_tokens: 1900,
    completion_tokens: 64,
    tool_calls: [
      { name: 'get_comfort_limits', arguments: {}, result: {} },
      { name: 'set_control_policy', arguments: {}, result: {} },
    ],
    ...overrides,
  })
}

export function run(overrides = {}) {
  return {
    id: 2,
    label: 'summer_week / llm',
    controller: 'llm',
    simulator: 'toy',
    scenario: 'summer_week',
    model: 'llama-3.3-70b-versatile',
    status: 'complete',
    error: null,
    horizon_steps: 672,
    timestep_seconds: 900,
    started_at: '2024-07-15T00:00:00+00:00',
    finished_at: '2024-07-15T00:04:00+00:00',
    total_kwh: 214.5,
    peak_kw: 8.1,
    cost: 38.61,
    co2_kg: 90.09,
    comfort_violations: 0,
    mean_ppd: 6.2,
    baseline_run_id: 1,
    ...overrides,
  }
}

export function savings(overrides = {}) {
  return {
    baseline_run_id: 1,
    agent_run_id: 2,
    baseline_kwh: 259.33,
    agent_kwh: 214.5,
    kwh_saved: 44.83,
    kwh_saved_pct: 17.29,
    peak_reduction_kw: 1.33,
    peak_reduction_pct: 14.1,
    cost_saved: 8.07,
    co2_saved_kg: 18.83,
    baseline_comfort_violations: 0,
    agent_comfort_violations: 0,
    comfort_maintained: true,
    ...overrides,
  }
}

export function config(overrides = {}) {
  return {
    controllers: ['baseline', 'rule', 'llm'],
    simulators: ['toy'],
    scenarios: ['summer_week', 'winter_week'],
    llm_model: 'llama-3.3-70b-versatile',
    comfort: {
      pmv_low: -0.5,
      pmv_high: 0.5,
      min_zone_temp_c: 19.0,
      max_zone_temp_c: 26.0,
    },
    tariff_per_kwh: 0.18,
    grid_carbon_kg_per_kwh: 0.42,
    ...overrides,
  }
}

export function scenario(overrides = {}) {
  return {
    id: 'summer_week',
    label: 'Summer week, hot climate',
    start: '2024-07-15T00:00:00',
    days: 7,
    timestep_seconds: 900,
    horizon_steps: 672,
    occupied_hours: [8, 18],
    ...overrides,
  }
}

/** A trace long enough to exercise downsampling and a full diurnal shape. */
export function trace(count = 96) {
  return Array.from({ length: count }, (_, index) => {
    const hour = (index * 0.25) % 24
    const occupied = hour >= 8 && hour < 18
    return timestep(index, {
      sim_time: `2024-07-15T${String(Math.floor(hour)).padStart(2, '0')}:${String(
        (index * 15) % 60
      ).padStart(2, '0')}:00`,
      zone_temp_c: 22 + Math.sin((index / count) * Math.PI * 2) * 3,
      outdoor_temp_c: 26 + Math.sin((index / count) * Math.PI * 2) * 5,
      occupancy: occupied ? 0.7 : 0,
      power_kw: occupied ? 6.4 : 1.2,
      energy_kwh: occupied ? 1.6 : 0.3,
      co2_ppm: occupied ? 780 : 430,
      pmv: occupied ? 0.2 : -0.9,
    })
  })
}
