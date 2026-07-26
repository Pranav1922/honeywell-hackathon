// Start and stop runs: scenario picker, controller picker (baseline | rule | llm),
// simulator picker (toy | energyplus), horizon, and the baseline run to compare
// against. Posts to /api/runs.
//
// Every option list comes from /api/config and /api/scenarios rather than from a
// literal in here. That is what makes the simulator picker gain `energyplus` at
// Milestone 4, and the controller picker gain any future strategy, with no change
// to this file.

import { count, kwh, label } from '../lib/format.js'

/** Runs a new run may be compared against: complete, and with a total to compare. */
export function comparableRuns(runs) {
  return (runs ?? []).filter(
    (run) => run.status === 'complete' && typeof run.total_kwh === 'number'
  )
}

/** Build the POST /api/runs body, omitting anything the backend should default. */
export function startRunBody(form) {
  const body = {
    scenario: form.scenario,
    controller: form.controller,
    simulator: form.simulator,
  }
  const horizon = Number.parseInt(form.horizonSteps, 10)
  if (Number.isFinite(horizon) && horizon > 0) body.horizon_steps = horizon
  if (form.label) body.label = form.label
  const baseline = Number.parseInt(form.baselineRunId, 10)
  if (Number.isFinite(baseline)) body.baseline_run_id = baseline
  return body
}

export default function RunControls({
  config,
  scenarios = [],
  runs = [],
  form,
  onChange,
  onStart,
  onStop,
  starting = false,
  activeRun = null,
  error = null,
}) {
  const controllers = config?.controllers ?? []
  const simulators = config?.simulators ?? []
  const baselines = comparableRuns(runs)
  const isRunning = activeRun?.status === 'running'
  const set = (field) => (event) => onChange(field, event.target.value)

  return (
    <section className="panel controls">
      <header className="panel-head">
        <h2>Run</h2>
        <p className="panel-sub">
          {config?.llm_model ? `Supervisor model: ${config.llm_model}` : 'Configure and start a simulation'}
        </p>
      </header>

      <div className="control-grid">
        <label>
          <span>Scenario</span>
          <select value={form.scenario} onChange={set('scenario')}>
            {scenarios.length === 0 ? <option value="">No scenarios found</option> : null}
            {scenarios.map((scenario) => (
              <option key={scenario.id} value={scenario.id}>
                {scenario.label} ({scenario.days}d)
              </option>
            ))}
          </select>
        </label>

        <label>
          <span>Controller</span>
          <select value={form.controller} onChange={set('controller')}>
            {controllers.map((controller) => (
              <option key={controller} value={controller}>
                {label(controller)}
              </option>
            ))}
          </select>
        </label>

        <label>
          <span>Simulator</span>
          <select value={form.simulator} onChange={set('simulator')}>
            {simulators.map((simulator) => (
              <option key={simulator} value={simulator}>
                {label(simulator)}
              </option>
            ))}
          </select>
        </label>

        <label>
          <span>Horizon (steps)</span>
          <input
            type="number"
            min="1"
            placeholder="scenario default"
            value={form.horizonSteps}
            onChange={set('horizonSteps')}
          />
        </label>

        <label>
          <span>Compare against</span>
          <select value={form.baselineRunId} onChange={set('baselineRunId')}>
            <option value="">No baseline</option>
            {baselines.map((run) => (
              <option key={run.id} value={run.id}>
                #{run.id} {run.controller} — {kwh(run.total_kwh)}
              </option>
            ))}
          </select>
        </label>

        <div className="control-actions">
          <button
            type="button"
            className="primary"
            onClick={onStart}
            disabled={starting || isRunning || !form.scenario}
          >
            {starting ? 'Starting…' : 'Start run'}
          </button>
          <button type="button" onClick={onStop} disabled={!isRunning}>
            Stop
          </button>
        </div>
      </div>

      {activeRun ? (
        <p className="run-status">
          Run #{activeRun.id} · {activeRun.status}
          {typeof activeRun.horizon_steps === 'number'
            ? ` · ${count(activeRun.horizon_steps)} steps`
            : ''}
          {activeRun.error ? ` · ${activeRun.error}` : ''}
        </p>
      ) : null}

      {error ? <p className="error">{error}</p> : null}
    </section>
  )
}
