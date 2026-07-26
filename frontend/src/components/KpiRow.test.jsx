// KPI row tests.
//
// This panel is where the dashboard could most easily mislead, so the tests are
// mostly about honesty rather than layout: no baseline means no percentage, a
// negative saving reads as a loss, and comfort degradation is reported next to
// the saving that bought it.

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { run, savings } from '../test/fixtures.js'
import KpiRow, { savingsState } from './KpiRow.jsx'

describe('savingsState', () => {
  it('refuses to show a percentage with no baseline to compare against', () => {
    const state = savingsState({ savings: null, baselineRunId: null })

    expect(state.kind).toBe('none')
    expect(state.label).toBe('No baseline')
  })

  it('waits for the comparison while the run is still going', () => {
    const state = savingsState({ savings: null, baselineRunId: 1, runStatus: 'running' })

    expect(state.kind).toBe('pending')
    expect(state.hint).toMatch(/when the run completes/)
  })

  it('reports an unavailable comparison for a finished run', () => {
    const state = savingsState({ savings: null, baselineRunId: 1, runStatus: 'failed' })

    expect(state.kind).toBe('pending')
    expect(state.hint).toMatch(/unavailable/)
  })

  it('reports a saving', () => {
    const state = savingsState({ savings: savings(), baselineRunId: 1 })

    expect(state.kind).toBe('saving')
    expect(state.label).toBe('+17.3%')
    expect(state.hint).toMatch(/44.83 kWh/)
  })

  it('reports a loss as a loss', () => {
    // The failure mode this prevents: an agent that used more energy than the
    // baseline showing up as a neutral or positive number.
    const state = savingsState({
      savings: savings({ kwh_saved_pct: -8.4, kwh_saved: -21.8 }),
      baselineRunId: 1,
    })

    expect(state.kind).toBe('loss')
    expect(state.label).toBe('-8.4%')
  })

  it('treats a zero saving as neither a win nor a loss to hide', () => {
    const state = savingsState({ savings: savings({ kwh_saved_pct: 0 }), baselineRunId: 1 })

    expect(state.kind).toBe('saving')
    expect(state.label).toBe('0.0%')
  })
})

describe('KpiRow', () => {
  it('renders the headline metrics for a completed run', () => {
    render(<KpiRow run={run()} savings={savings()} />)

    expect(screen.getByText('214.50 kWh')).toBeInTheDocument()
    expect(screen.getByText('+17.3%')).toBeInTheDocument()
    expect(screen.getByText('8.10 kW')).toBeInTheDocument()
    expect(screen.getByText('6.2 %')).toBeInTheDocument()
    expect(screen.getByText('90.09 kg')).toBeInTheDocument()
  })

  it('shows the comfort verdict beside the saving', () => {
    render(<KpiRow run={run()} savings={savings()} />)

    expect(screen.getByText('Comfort maintained vs baseline')).toBeInTheDocument()
  })

  it('says comfort was degraded when the saving cost comfort', () => {
    render(
      <KpiRow
        run={run({ comfort_violations: 14 })}
        savings={savings({ comfort_maintained: false, agent_comfort_violations: 14 })}
      />
    )

    expect(screen.getByText('Comfort degraded vs baseline')).toBeInTheDocument()
    expect(screen.getByText('14')).toBeInTheDocument()
  })

  it('renders dashes rather than NaN for a run in progress', () => {
    render(
      <KpiRow
        run={run({
          status: 'running',
          total_kwh: null,
          peak_kw: null,
          cost: null,
          co2_kg: null,
          comfort_violations: null,
          mean_ppd: null,
        })}
        savings={null}
      />
    )

    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument()
    expect(screen.getAllByText('—').length).toBeGreaterThan(0)
  })

  it('renders with no run at all', () => {
    render(<KpiRow run={null} savings={null} />)

    expect(screen.getByText('Energy used')).toBeInTheDocument()
    expect(screen.getByText('No baseline')).toBeInTheDocument()
  })

  it('falls back to the live summary before the run row has aggregates', () => {
    render(<KpiRow run={run({ total_kwh: null })} summary={{ total_kwh: 12.5 }} />)

    expect(screen.getByText('12.50 kWh')).toBeInTheDocument()
  })
})
