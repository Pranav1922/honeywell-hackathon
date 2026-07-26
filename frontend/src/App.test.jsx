// Dashboard shell tests.
//
// App is the only module that fetches, so these tests are about wiring: that it
// composes every panel, that starting a run posts the form and then follows the
// new run, and — the one with real consequences — that the baseline overlay and
// the savings comparison are requested only when they can actually succeed.
// /api/compare answers 409 until both runs have totals, so asking too early would
// put a spurious error on screen on every live run.

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from './App.jsx'
import * as api from './lib/api.js'
import { config, run, savings, scenario, trace } from './test/fixtures.js'

vi.mock('./lib/api.js', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    getConfig: vi.fn(),
    getScenarios: vi.fn(),
    listRuns: vi.fn(),
    getRun: vi.fn(),
    getTimeseries: vi.fn(),
    getDecisions: vi.fn(),
    getSummary: vi.fn(),
    startRun: vi.fn(),
    stopRun: vi.fn(),
    compare: vi.fn(),
  }
})

// The dashboard's own stream hook is exercised in useRunStream.test.jsx; here it
// is replaced so these tests are about App's wiring rather than about transports.
vi.mock('./hooks/useRunStream.js', () => ({
  useRunStream: vi.fn(),
}))

const { useRunStream } = await import('./hooks/useRunStream.js')

const streamState = (overrides = {}) => ({
  timeseries: [],
  decisions: [],
  status: null,
  error: null,
  transport: 'sse',
  isRunning: false,
  ...overrides,
})

beforeEach(() => {
  vi.mocked(api.getConfig).mockResolvedValue(config())
  vi.mocked(api.getScenarios).mockResolvedValue([scenario()])
  vi.mocked(api.listRuns).mockResolvedValue([run()])
  vi.mocked(api.getRun).mockResolvedValue(run())
  vi.mocked(api.getTimeseries).mockResolvedValue([])
  vi.mocked(api.getDecisions).mockResolvedValue([])
  vi.mocked(api.getSummary).mockResolvedValue({ status: 'complete' })
  vi.mocked(api.compare).mockResolvedValue(savings())
  vi.mocked(useRunStream).mockReturnValue(streamState())
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('initial load', () => {
  it('composes every dashboard panel', async () => {
    render(<App />)

    await waitFor(() => expect(api.getConfig).toHaveBeenCalled())

    expect(screen.getByText('Eco-Loop Building Agents')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Run' })).toBeInTheDocument()
    expect(screen.getByText('Energy used')).toBeInTheDocument()
    expect(screen.getByText('Zone temperature')).toBeInTheDocument()
    expect(screen.getByText('Energy')).toBeInTheDocument()
    expect(screen.getByText('Thermal comfort')).toBeInTheDocument()
    expect(screen.getByText('Occupancy and air quality')).toBeInTheDocument()
    expect(screen.getByText('Control action')).toBeInTheDocument()
    expect(screen.getByText('Agent reasoning')).toBeInTheDocument()
  })

  it('loads config, scenarios and runs exactly once', async () => {
    render(<App />)

    await waitFor(() => expect(api.listRuns).toHaveBeenCalledTimes(1))
    expect(api.getConfig).toHaveBeenCalledTimes(1)
    expect(api.getScenarios).toHaveBeenCalledTimes(1)
  })

  it('selects the most recent run, so a reload lands on data', async () => {
    render(<App />)

    await waitFor(() => expect(api.getRun).toHaveBeenCalledWith(2))
    expect(useRunStream).toHaveBeenCalledWith(2)
  })

  it('selects nothing when there are no recorded runs', async () => {
    vi.mocked(api.listRuns).mockResolvedValue([])
    render(<App />)

    await waitFor(() => expect(api.listRuns).toHaveBeenCalled())
    expect(api.getRun).not.toHaveBeenCalled()
    expect(useRunStream).toHaveBeenCalledWith(null)
  })

  it('reports a backend that cannot be reached', async () => {
    vi.mocked(api.getConfig).mockRejectedValue(
      Object.assign(new Error('fail'), { detail: 'could not reach the backend' })
    )
    render(<App />)

    expect(await screen.findByText('could not reach the backend')).toBeInTheDocument()
  })

  it('defaults the controller to one the backend actually offers', async () => {
    vi.mocked(api.getConfig).mockResolvedValue(config({ controllers: ['baseline', 'rule'] }))
    render(<App />)

    await waitFor(() => expect(api.getConfig).toHaveBeenCalled())
    // 'llm' is the initial preference, but it is not on offer here.
    expect(screen.getByLabelText('Controller')).toHaveValue('baseline')
  })
})

describe('starting and stopping', () => {
  it('posts the form and follows the new run', async () => {
    vi.mocked(api.startRun).mockResolvedValue(run({ id: 7, status: 'running' }))
    render(<App />)
    await waitFor(() => expect(api.getConfig).toHaveBeenCalled())

    await userEvent.click(screen.getByRole('button', { name: 'Start run' }))

    await waitFor(() =>
      expect(api.startRun).toHaveBeenCalledWith({
        scenario: 'summer_week',
        controller: 'llm',
        simulator: 'toy',
      })
    )
    await waitFor(() => expect(useRunStream).toHaveBeenCalledWith(7))
  })

  it('surfaces a refused start', async () => {
    vi.mocked(api.startRun).mockRejectedValue(
      Object.assign(new Error('fail'), { detail: "unknown controller 'llm'" })
    )
    render(<App />)
    await waitFor(() => expect(api.getConfig).toHaveBeenCalled())

    await userEvent.click(screen.getByRole('button', { name: 'Start run' }))

    expect(await screen.findByText("unknown controller 'llm'")).toBeInTheDocument()
  })

  it('stops the selected run and re-reads it', async () => {
    vi.mocked(useRunStream).mockReturnValue(streamState({ status: 'running', isRunning: true }))
    vi.mocked(api.getRun).mockResolvedValue(run({ status: 'running' }))
    render(<App />)
    await waitFor(() => expect(api.getRun).toHaveBeenCalled())

    await userEvent.click(screen.getByRole('button', { name: 'Stop' }))

    await waitFor(() => expect(api.stopRun).toHaveBeenCalledWith(2))
  })

  it('switches runs from the picker', async () => {
    vi.mocked(api.listRuns).mockResolvedValue([run({ id: 2 }), run({ id: 1, controller: 'baseline' })])
    render(<App />)
    await waitFor(() => expect(api.getRun).toHaveBeenCalledWith(2))

    await userEvent.click(screen.getByRole('button', { name: /#1 baseline/ }))

    await waitFor(() => expect(useRunStream).toHaveBeenCalledWith(1))
  })
})

describe('baseline overlay and savings', () => {
  it('fetches the baseline trace and the comparison for a completed run', async () => {
    render(<App />)

    await waitFor(() => expect(api.getTimeseries).toHaveBeenCalledWith(1))
    await waitFor(() => expect(api.compare).toHaveBeenCalledWith(1, 2))
    expect(await screen.findByText('+17.3%')).toBeInTheDocument()
  })

  it('asks for neither when the run has no baseline', async () => {
    vi.mocked(api.getRun).mockResolvedValue(run({ baseline_run_id: null }))
    render(<App />)

    await waitFor(() => expect(api.getRun).toHaveBeenCalled())
    expect(api.getTimeseries).not.toHaveBeenCalled()
    expect(api.compare).not.toHaveBeenCalled()
    // The tile's own hint, rather than the label it shares with the picker's
    // "No baseline" option.
    expect(
      await screen.findByText('Pick a baseline run to compare')
    ).toBeInTheDocument()
  })

  it('does not compare a run that has not finished', async () => {
    // /api/compare answers 409 until both runs have totals; asking early would
    // put a spurious error on screen for the whole of every live run.
    vi.mocked(api.getRun).mockResolvedValue(run({ status: 'running', total_kwh: null }))
    vi.mocked(useRunStream).mockReturnValue(streamState({ status: 'running', isRunning: true }))
    render(<App />)

    await waitFor(() => expect(api.getTimeseries).toHaveBeenCalledWith(1))
    expect(api.compare).not.toHaveBeenCalled()
  })

  it('survives a comparison that fails anyway', async () => {
    vi.mocked(api.compare).mockRejectedValue(new Error('409'))
    render(<App />)

    await waitFor(() => expect(api.compare).toHaveBeenCalled())
    expect(screen.getByText('Energy saved')).toBeInTheDocument()
  })

  it('re-reads the run once the stream reports it finished', async () => {
    // The aggregates are only written when the run completes, so the row has to
    // be re-read or the KPI tiles stay empty.
    vi.mocked(useRunStream).mockReturnValue(streamState({ status: 'complete' }))
    render(<App />)

    await waitFor(() => expect(api.getRun.mock.calls.length).toBeGreaterThan(1))
  })
})

describe('live data', () => {
  it('passes streamed telemetry into the panels', async () => {
    const rows = trace(96)
    vi.mocked(useRunStream).mockReturnValue(
      streamState({ timeseries: rows, status: 'running', isRunning: true })
    )
    render(<App />)

    await waitFor(() => expect(api.getConfig).toHaveBeenCalled())
    expect(screen.queryByText(/no telemetry yet/i)).not.toBeInTheDocument()
    expect(screen.getByText('running')).toBeInTheDocument()
  })

  it('shows which transport is carrying the run', async () => {
    vi.mocked(useRunStream).mockReturnValue(streamState({ transport: 'poll' }))
    render(<App />)

    await waitFor(() => expect(api.getConfig).toHaveBeenCalled())
    expect(screen.getByText('polling')).toBeInTheDocument()
  })

  it('surfaces a stream error', async () => {
    vi.mocked(useRunStream).mockReturnValue(
      streamState({ error: 'lost connection to the run stream' })
    )
    render(<App />)

    expect(
      await screen.findByText('lost connection to the run stream')
    ).toBeInTheDocument()
  })

  it('offers a CSV export for the selected run', async () => {
    render(<App />)

    await waitFor(() => expect(api.getRun).toHaveBeenCalled())
    expect(screen.getByRole('link', { name: 'Export CSV' })).toHaveAttribute(
      'href',
      '/api/runs/2/export'
    )
  })
})
