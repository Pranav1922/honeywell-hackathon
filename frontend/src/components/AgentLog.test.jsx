// Agent log tests.
//
// This is the panel that carries the autonomy claim on the demonstration video,
// so what it must never do is let a guard step look like a reasoned decision, or
// bury a fallback. Newest-first ordering is asserted because on a live run the
// entry that just arrived is the only one anyone is looking at.

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { decision, supervisoryDecision } from '../test/fixtures.js'
import AgentLog, { isSupervisory, toolNames } from './AgentLog.jsx'

describe('toolNames', () => {
  it('lists tool names in call order', () => {
    expect(
      toolNames([{ name: 'get_comfort_limits' }, { name: 'set_control_policy' }])
    ).toEqual(['get_comfort_limits', 'set_control_policy'])
  })

  it('deduplicates a tool called more than once', () => {
    expect(
      toolNames([{ name: 'evaluate_policy' }, { name: 'evaluate_policy' }])
    ).toEqual(['evaluate_policy'])
  })

  it('tolerates the null the API sends for a guard decision', () => {
    expect(toolNames(null)).toEqual([])
    expect(toolNames(undefined)).toEqual([])
    expect(toolNames([{}, { name: '' }])).toEqual([])
  })
})

describe('isSupervisory', () => {
  it('is true only when a model call was actually made', () => {
    // latency_ms is the discriminator: the guard never has one.
    expect(isSupervisory(supervisoryDecision(4))).toBe(true)
    expect(isSupervisory(decision(5))).toBe(false)
    expect(isSupervisory(null)).toBe(false)
  })
})

describe('AgentLog', () => {
  it('shows an empty state before the agent has decided anything', () => {
    render(<AgentLog decisions={[]} />)

    expect(screen.getByText('No decisions yet.')).toBeInTheDocument()
    expect(screen.getByText(/rationales appear here/i)).toBeInTheDocument()
  })

  it('renders rationales newest first', () => {
    render(
      <AgentLog
        decisions={[
          supervisoryDecision(4, { rationale: 'oldest decision' }),
          supervisoryDecision(8, { rationale: 'newest decision' }),
        ]}
      />
    )

    const entries = screen.getAllByText(/decision$/)
    expect(entries[0]).toHaveTextContent('newest decision')
    expect(entries[1]).toHaveTextContent('oldest decision')
  })

  it('counts how many decisions came from the model', () => {
    render(
      <AgentLog decisions={[supervisoryDecision(4), decision(5), decision(6)]} />
    )

    expect(screen.getByText('1 supervisory decision of 3 logged')).toBeInTheDocument()
  })

  it('shows latency for a model decision and marks a guard step as such', () => {
    render(<AgentLog decisions={[supervisoryDecision(4), decision(5)]} />)

    expect(screen.getByText('820 ms')).toBeInTheDocument()
    expect(screen.getByText('guard')).toBeInTheDocument()
  })

  it('promotes a slow call to seconds', () => {
    render(<AgentLog decisions={[supervisoryDecision(4, { latency_ms: 2480 })]} />)

    expect(screen.getByText('2.48 s')).toBeInTheDocument()
  })

  it('lists the tools called, in order', () => {
    render(<AgentLog decisions={[supervisoryDecision(4)]} />)

    expect(
      screen.getByText('tools: get_comfort_limits → set_control_policy')
    ).toBeInTheDocument()
  })

  it('reports token usage when the model supplied it', () => {
    render(<AgentLog decisions={[supervisoryDecision(4)]} />)

    expect(screen.getByText('1900+64 tokens')).toBeInTheDocument()
  })

  it('marks clamped, fallback and repaired decisions distinctly', () => {
    render(
      <AgentLog
        decisions={[
          supervisoryDecision(4, { guard_clamped: true, retries: 2, fallback_used: true }),
        ]}
      />
    )

    expect(screen.getByText('clamped')).toBeInTheDocument()
    expect(screen.getByText('fallback')).toBeInTheDocument()
    expect(screen.getByText('2× repaired')).toBeInTheDocument()
  })

  it('renders the set-points and actuator levels for each entry', () => {
    render(<AgentLog decisions={[supervisoryDecision(4)]} />)

    expect(screen.getByText('21.0 °C / 25.5 °C')).toBeInTheDocument()
    expect(screen.getByText('light 60%')).toBeInTheDocument()
    expect(screen.getByText('vent 1.20 ACH')).toBeInTheDocument()
  })

  it('caps how many entries it renders on a long run', () => {
    // A week-long run logs hundreds of decisions; the DOM does not need them all.
    const many = Array.from({ length: 300 }, (_, index) => supervisoryDecision(index))
    render(<AgentLog decisions={many} limit={25} />)

    expect(screen.getAllByRole('listitem')).toHaveLength(25)
    // The count still reports the whole run, so nothing is silently hidden.
    expect(screen.getByText('300 supervisory decisions of 300 logged')).toBeInTheDocument()
  })

  it('shows the simulated time of each decision, not the real one', () => {
    render(<AgentLog decisions={[supervisoryDecision(4)]} />)

    expect(screen.getByText('Mon 12:00')).toBeInTheDocument()
  })
})
