// Run controls tests.
//
// The important assertion here is that no option list is hardcoded. The
// controller and simulator pickers are driven from /api/config, which is what
// makes `energyplus` appear at Milestone 4 without this file changing — and a
// test that pins the options to literals would defeat exactly that.

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { config, run, scenario } from '../test/fixtures.js'
import RunControls, { comparableRuns, startRunBody } from './RunControls.jsx'

const FORM = {
  scenario: 'summer_week',
  controller: 'llm',
  simulator: 'toy',
  horizonSteps: '',
  baselineRunId: '',
  label: '',
}

const setup = (props = {}) => {
  const handlers = { onChange: vi.fn(), onStart: vi.fn(), onStop: vi.fn() }
  render(
    <RunControls
      config={config()}
      scenarios={[scenario()]}
      runs={[]}
      form={FORM}
      {...handlers}
      {...props}
    />
  )
  return handlers
}

describe('startRunBody', () => {
  it('sends only the three required fields by default', () => {
    // Omitting the rest lets the backend apply the scenario's own defaults
    // rather than the dashboard second-guessing them.
    expect(startRunBody(FORM)).toEqual({
      scenario: 'summer_week',
      controller: 'llm',
      simulator: 'toy',
    })
  })

  it('includes a horizon override when one is given', () => {
    expect(startRunBody({ ...FORM, horizonSteps: '192' }).horizon_steps).toBe(192)
  })

  it('ignores an empty, zero or unparseable horizon', () => {
    for (const horizonSteps of ['', '0', '-5', 'abc']) {
      expect(startRunBody({ ...FORM, horizonSteps })).not.toHaveProperty('horizon_steps')
    }
  })

  it('includes the baseline run to compare against', () => {
    expect(startRunBody({ ...FORM, baselineRunId: '1' }).baseline_run_id).toBe(1)
  })

  it('omits the baseline when none is selected', () => {
    expect(startRunBody({ ...FORM, baselineRunId: '' })).not.toHaveProperty('baseline_run_id')
  })

  it('includes a label only when one was typed', () => {
    expect(startRunBody(FORM)).not.toHaveProperty('label')
    expect(startRunBody({ ...FORM, label: 'demo' }).label).toBe('demo')
  })
})

describe('comparableRuns', () => {
  it('offers only completed runs that have a total to compare', () => {
    // /api/compare answers 409 for anything else, so offering them would be
    // offering a button that cannot work.
    const runs = [
      run({ id: 1, status: 'complete', total_kwh: 259 }),
      run({ id: 2, status: 'running', total_kwh: null }),
      run({ id: 3, status: 'failed', total_kwh: null }),
      run({ id: 4, status: 'stopped', total_kwh: 40 }),
      run({ id: 5, status: 'complete', total_kwh: null }),
    ]

    expect(comparableRuns(runs).map((entry) => entry.id)).toEqual([1])
  })

  it('handles an empty list', () => {
    expect(comparableRuns([])).toEqual([])
    expect(comparableRuns(null)).toEqual([])
  })
})

describe('RunControls', () => {
  it('populates the controller picker from the API, not from a literal', () => {
    setup({ config: config({ controllers: ['baseline', 'rule', 'llm'] }) })

    expect(screen.getByRole('option', { name: 'Baseline' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Rule' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Llm' })).toBeInTheDocument()
  })

  it('gains a new simulator when the backend advertises one', () => {
    // This is the Milestone 4 path: no frontend change required.
    setup({ config: config({ simulators: ['toy', 'energyplus'] }) })

    expect(screen.getByRole('option', { name: 'Energyplus' })).toBeInTheDocument()
  })

  it('populates the scenario picker from the API', () => {
    setup({
      scenarios: [scenario(), scenario({ id: 'winter_week', label: 'Winter week', days: 7 })],
    })

    expect(screen.getByRole('option', { name: /Summer week, hot climate \(7d\)/ })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /Winter week \(7d\)/ })).toBeInTheDocument()
  })

  it('says so when there are no scenarios on disk', () => {
    setup({ scenarios: [] })

    expect(screen.getByRole('option', { name: 'No scenarios found' })).toBeInTheDocument()
  })

  it('shows the supervisor model in use', () => {
    setup()

    expect(screen.getByText(/llama-3.3-70b-versatile/)).toBeInTheDocument()
  })

  it('offers completed runs as a baseline', () => {
    setup({ runs: [run({ id: 1, controller: 'baseline', total_kwh: 259.33 })] })

    expect(screen.getByRole('option', { name: 'No baseline' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /#1 baseline — 259.33 kWh/ })).toBeInTheDocument()
  })

  it('reports a field change to the parent', async () => {
    const { onChange } = setup({ config: config({ controllers: ['baseline', 'llm'] }) })

    await userEvent.selectOptions(screen.getByLabelText('Controller'), 'baseline')

    expect(onChange).toHaveBeenCalledWith('controller', 'baseline')
  })

  it('starts a run', async () => {
    const { onStart } = setup()

    await userEvent.click(screen.getByRole('button', { name: 'Start run' }))

    expect(onStart).toHaveBeenCalled()
  })

  it('cannot start without a scenario', () => {
    setup({ form: { ...FORM, scenario: '' }, scenarios: [] })

    expect(screen.getByRole('button', { name: 'Start run' })).toBeDisabled()
  })

  it('cannot start twice while a start is in flight', () => {
    setup({ starting: true })

    expect(screen.getByRole('button', { name: 'Starting…' })).toBeDisabled()
  })

  it('cannot start another run while one is running', () => {
    setup({ activeRun: run({ status: 'running' }) })

    expect(screen.getByRole('button', { name: 'Start run' })).toBeDisabled()
  })

  it('can only stop a run that is actually running', () => {
    const { unmount } = render(
      <RunControls
        config={config()}
        scenarios={[scenario()]}
        form={FORM}
        onChange={vi.fn()}
        onStart={vi.fn()}
        onStop={vi.fn()}
        activeRun={run({ status: 'complete' })}
      />
    )
    expect(screen.getByRole('button', { name: 'Stop' })).toBeDisabled()
    unmount()

    setup({ activeRun: run({ status: 'running' }) })
    expect(screen.getByRole('button', { name: 'Stop' })).toBeEnabled()
  })

  it('stops a run', async () => {
    const { onStop } = setup({ activeRun: run({ status: 'running' }) })

    await userEvent.click(screen.getByRole('button', { name: 'Stop' }))

    expect(onStop).toHaveBeenCalled()
  })

  it('shows the active run and its horizon', () => {
    setup({ activeRun: run({ id: 9, status: 'running', horizon_steps: 672 }) })

    expect(screen.getByText(/Run #9 · running · 672 steps/)).toBeInTheDocument()
  })

  it("surfaces a failed run's error", () => {
    setup({ activeRun: run({ status: 'failed', error: 'LLMConfigError: GROQ_API_KEY is not set' }) })

    expect(screen.getByText(/GROQ_API_KEY is not set/)).toBeInTheDocument()
  })

  it('surfaces a request error', () => {
    setup({ error: 'could not reach the backend' })

    expect(screen.getByText('could not reach the backend')).toBeInTheDocument()
  })

  it('renders before /api/config has answered', () => {
    setup({ config: null, scenarios: [] })

    expect(screen.getByText('Configure and start a simulation')).toBeInTheDocument()
  })
})
