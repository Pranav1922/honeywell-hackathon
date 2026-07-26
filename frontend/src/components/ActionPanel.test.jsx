// Action panel tests.
//
// The clamp and fallback badges are the visible face of the two-tier safety
// property, so their presence *and* their absence are both asserted. A badge that
// never disappears is as misleading as one that never appears.

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { decision, supervisoryDecision, timestep } from '../test/fixtures.js'
import ActionPanel from './ActionPanel.jsx'

describe('ActionPanel', () => {
  it('shows an empty state before anything has been decided', () => {
    render(<ActionPanel decision={null} state={null} />)

    expect(screen.getByText(/no action yet/i)).toBeInTheDocument()
  })

  it('renders the commanded set-points and the measured state', () => {
    render(<ActionPanel decision={decision(96)} state={timestep(96)} />)

    expect(screen.getByText('Hold')).toBeInTheDocument()
    expect(screen.getByText('21.0 °C')).toBeInTheDocument()
    expect(screen.getByText('25.5 °C')).toBeInTheDocument()
    expect(screen.getByText('4.5 K')).toBeInTheDocument()
    expect(screen.getByText('60%')).toBeInTheDocument()   // lighting
    expect(screen.getByText('75%')).toBeInTheDocument()   // occupancy
    expect(screen.getByText('1.20 ACH')).toBeInTheDocument()
    expect(screen.getByText('Cooling')).toBeInTheDocument()
    expect(screen.getByText('24.5 °C')).toBeInTheDocument()
    expect(screen.getByText('30.0 °C')).toBeInTheDocument()
    expect(screen.getByText('760 ppm')).toBeInTheDocument()
  })

  it('titles the strategy from the machine token', () => {
    render(<ActionPanel decision={decision(96, { strategy: 'peak_shave' })} state={timestep(96)} />)

    expect(screen.getByText('Peak Shave')).toBeInTheDocument()
  })

  it('shows no badges when the agent was left alone', () => {
    render(<ActionPanel decision={supervisoryDecision(96)} state={timestep(96)} />)

    expect(screen.queryByText('Guard clamped')).not.toBeInTheDocument()
    expect(screen.queryByText('Fallback')).not.toBeInTheDocument()
  })

  it('flags a guard override, because an override is evidence', () => {
    render(
      <ActionPanel
        decision={supervisoryDecision(96, { guard_clamped: true })}
        state={timestep(96)}
      />
    )

    expect(screen.getByText('Guard clamped')).toBeInTheDocument()
  })

  it('flags a fallback decision', () => {
    render(
      <ActionPanel
        decision={decision(96, { fallback_used: true })}
        state={timestep(96)}
      />
    )

    expect(screen.getByText('Fallback')).toBeInTheDocument()
  })

  it('reports self-correction rounds, singular and plural', () => {
    const { unmount } = render(
      <ActionPanel decision={supervisoryDecision(96, { retries: 1 })} state={timestep(96)} />
    )
    expect(screen.getByText('1 retry')).toBeInTheDocument()
    unmount()

    render(
      <ActionPanel decision={supervisoryDecision(96, { retries: 2 })} state={timestep(96)} />
    )
    expect(screen.getByText('2 retries')).toBeInTheDocument()
  })

  it('renders from telemetry alone when no decision has been logged yet', () => {
    render(<ActionPanel decision={null} state={timestep(4)} />)

    expect(screen.getByText(/from live telemetry/i)).toBeInTheDocument()
    expect(screen.getByText('21.0 °C')).toBeInTheDocument()
  })

  it('prefers the commanded set-point over the measured one when they differ', () => {
    // They disagree on the step a policy changes, and the commanded value is the
    // one the rationale is explaining.
    render(
      <ActionPanel
        decision={decision(96, { heating_sp_c: 19.5 })}
        state={timestep(96, { heating_sp_c: 21.0 })}
      />
    )

    expect(screen.getByText('19.5 °C')).toBeInTheDocument()
  })
})
