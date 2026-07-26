// Live-data subscription tests.
//
// The three properties this hook has to guarantee, each with a test that fails if
// it stops holding:
//
//  - rows arrive in order and a replay does not duplicate them (EventSource
//    reconnects on its own, and the server's generator restarts from step 0);
//  - arrival rate is decoupled from render rate;
//  - no EventSource means incremental polling, not a frozen dashboard.

import { act, render, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getDecisions, getSummary, getTimeseries } from '../lib/api.js'
import { isTerminal, lastStep, onlyNew, useRunStream } from './useRunStream.js'

vi.mock('../lib/api.js', async (importOriginal) => ({
  ...(await importOriginal()),
  getTimeseries: vi.fn(),
  getDecisions: vi.fn(),
  getSummary: vi.fn(),
}))

/** A controllable EventSource stand-in. jsdom provides none, so this is the only
 *  way to exercise the SSE path at all. */
class FakeEventSource {
  static instances = []
  static CONNECTING = 0
  static OPEN = 1
  static CLOSED = 2

  constructor(url) {
    this.url = url
    this.readyState = FakeEventSource.OPEN
    this.listeners = {}
    this.closed = false
    FakeEventSource.instances.push(this)
  }

  addEventListener(type, handler) {
    ;(this.listeners[type] ??= []).push(handler)
  }

  close() {
    this.closed = true
    this.readyState = FakeEventSource.CLOSED
  }

  /** Deliver a named server frame. */
  emit(type, data) {
    const payload = { data: typeof data === 'string' ? data : JSON.stringify(data) }
    for (const handler of this.listeners[type] ?? []) handler(payload)
  }

  /** Deliver a transport failure, which carries no data. */
  fail({ closed = false } = {}) {
    if (closed) this.readyState = FakeEventSource.CLOSED
    for (const handler of this.listeners.error ?? []) handler({})
  }

  static latest() {
    return FakeEventSource.instances[FakeEventSource.instances.length - 1]
  }

  static reset() {
    FakeEventSource.instances = []
  }
}

const timestep = (step, extra = {}) => ({
  step,
  sim_time: `2024-07-15T00:00:00`,
  zone_temp_c: 24,
  power_kw: 3,
  occupancy: 1,
  pmv: 0.1,
  energy_kwh: 0.75,
  ...extra,
})

const decision = (step, extra = {}) => ({
  step,
  sim_time: `2024-07-15T00:00:00`,
  strategy: 'hold',
  rationale: 'holding',
  heating_sp_c: 21,
  cooling_sp_c: 25,
  lighting_level: 0.5,
  ventilation_ach: 1,
  retries: 0,
  fallback_used: false,
  guard_clamped: false,
  ...extra,
})

const FLUSH = 10
const options = { flushIntervalMs: FLUSH, pollIntervalMs: FLUSH }

/**
 * Emit frames, then let the hook's flush interval fire and React commit.
 *
 * The buffered flush means state lands one interval after a frame arrives, so
 * every assertion has to be made after that interval rather than immediately.
 * Waiting on a real timer inside `act` is what lets the resulting update be
 * committed before the assertion reads it.
 */
async function settle(emit) {
  await act(async () => {
    emit?.()
    await new Promise((resolve) => setTimeout(resolve, FLUSH * 3))
  })
}

beforeEach(() => {
  FakeEventSource.reset()
  vi.stubGlobal('EventSource', FakeEventSource)
  vi.mocked(getTimeseries).mockResolvedValue([])
  vi.mocked(getDecisions).mockResolvedValue([])
  vi.mocked(getSummary).mockResolvedValue({ status: 'running' })
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

// -- pure helpers -----------------------------------------------------------

describe('helpers', () => {
  it('recognises terminal statuses', () => {
    expect(isTerminal('complete')).toBe(true)
    expect(isTerminal('failed')).toBe(true)
    expect(isTerminal('stopped')).toBe(true)
    expect(isTerminal('running')).toBe(false)
    expect(isTerminal(null)).toBe(false)
  })

  it('reports the highest ingested step', () => {
    expect(lastStep([])).toBe(-1)
    expect(lastStep([{ step: 0 }, { step: 7 }])).toBe(7)
  })

  it('keeps only strictly newer rows', () => {
    expect(onlyNew([{ step: 1 }, { step: 2 }, { step: 3 }], 1).map((r) => r.step)).toEqual([2, 3])
  })

  it('drops a full replay', () => {
    const replay = [{ step: 0 }, { step: 1 }, { step: 2 }]
    expect(onlyNew(replay, 2)).toEqual([])
  })

  it('drops duplicates inside one batch', () => {
    expect(onlyNew([{ step: 5 }, { step: 5 }, { step: 6 }], 4).map((r) => r.step)).toEqual([5, 6])
  })

  it('ignores rows with no usable step', () => {
    expect(onlyNew([{ step: null }, {}, { step: 3 }], -1).map((r) => r.step)).toEqual([3])
  })
})

// -- SSE transport ----------------------------------------------------------

describe('SSE transport', () => {
  it('subscribes to the run stream and reports the transport', async () => {
    const { result } = renderHook(() => useRunStream(7, options))

    expect(FakeEventSource.latest().url).toBe('/api/runs/7/stream')
    await waitFor(() => expect(result.current.transport).toBe('sse'))
  })

  it('idles with no run selected', () => {
    const { result } = renderHook(() => useRunStream(null, options))

    expect(FakeEventSource.instances).toHaveLength(0)
    expect(result.current.timeseries).toEqual([])
    expect(result.current.transport).toBeNull()
  })

  it('accumulates telemetry and decisions in order', async () => {
    const { result } = renderHook(() => useRunStream(1, options))
    const source = FakeEventSource.latest()

    await settle(() => {
      source.emit('timestep', timestep(1))
      source.emit('timestep', timestep(2))
      source.emit('decision', decision(1))
    })

    expect(result.current.timeseries.map((row) => row.step)).toEqual([1, 2])
    expect(result.current.decisions.map((row) => row.step)).toEqual([1])
    expect(result.current.status).toBe('running')
    expect(result.current.isRunning).toBe(true)
  })

  it('discards a replayed run rather than doubling every chart', async () => {
    // EventSource reconnects on its own and the server generator restarts at
    // step 0, so this is the normal consequence of one dropped connection.
    const { result } = renderHook(() => useRunStream(1, options))
    const source = FakeEventSource.latest()

    await settle(() => {
      source.emit('timestep', timestep(1))
      source.emit('timestep', timestep(2))
    })
    expect(result.current.timeseries).toHaveLength(2)

    await settle(() => {
      source.emit('timestep', timestep(1))
      source.emit('timestep', timestep(2))
      source.emit('timestep', timestep(3))
    })

    expect(result.current.timeseries.map((row) => row.step)).toEqual([1, 2, 3])
  })

  it('closes on completion, so the server hang-up cannot trigger a replay', async () => {
    const { result } = renderHook(() => useRunStream(1, options))
    const source = FakeEventSource.latest()

    await settle(() => {
      source.emit('timestep', timestep(1))
      source.emit('complete', { run_id: 1, status: 'complete' })
    })

    expect(result.current.status).toBe('complete')
    expect(source.closed).toBe(true)
    expect(result.current.isRunning).toBe(false)
  })

  it('carries a non-complete terminal status through', async () => {
    const { result } = renderHook(() => useRunStream(1, options))

    await settle(() => {
      FakeEventSource.latest().emit('complete', { run_id: 1, status: 'stopped' })
    })

    expect(result.current.status).toBe('stopped')
  })

  it('surfaces a server-sent error frame', async () => {
    const { result } = renderHook(() => useRunStream(1, options))
    const source = FakeEventSource.latest()

    await settle(() => {
      source.emit('error', { detail: 'run produced no data; stream timed out' })
    })

    expect(result.current.error).toBe('run produced no data; stream timed out')
    expect(source.closed).toBe(true)
  })

  it('ignores a recoverable transport drop while the run is live', async () => {
    // The step filter makes the ensuing replay harmless, so a blip must not put
    // a scary message on screen.
    const { result } = renderHook(() => useRunStream(1, options))

    await act(async () => {
      FakeEventSource.latest().fail()
      await Promise.resolve()
    })

    expect(result.current.error).toBeNull()
  })

  it('reports a connection that has closed for good', async () => {
    const { result } = renderHook(() => useRunStream(1, options))

    await settle(() => {
      FakeEventSource.latest().fail({ closed: true })
    })

    expect(result.current.error).toMatch(/lost connection/)
  })

  it('reports a malformed frame instead of throwing', async () => {
    const { result } = renderHook(() => useRunStream(1, options))

    await settle(() => {
      FakeEventSource.latest().emit('timestep', 'not json{')
    })

    expect(result.current.error).toMatch(/malformed/)
    expect(result.current.timeseries).toEqual([])
  })

  it('closes the subscription on unmount', async () => {
    const { unmount } = renderHook(() => useRunStream(1, options))
    const source = FakeEventSource.latest()

    unmount()

    expect(source.closed).toBe(true)
  })

  it('resets and resubscribes when the run changes', async () => {
    const { result, rerender } = renderHook(({ id }) => useRunStream(id, options), {
      initialProps: { id: 1 },
    })

    await settle(() => {
      FakeEventSource.latest().emit('timestep', timestep(5))
    })
    expect(result.current.timeseries).toHaveLength(1)

    rerender({ id: 2 })

    expect(FakeEventSource.instances[0].closed).toBe(true)
    expect(FakeEventSource.latest().url).toBe('/api/runs/2/stream')
    await waitFor(() => expect(result.current.timeseries).toEqual([]))
  })

  it('decouples arrival rate from render rate', async () => {
    // Replaying a finished run delivers one frame per row as fast as the
    // transport allows. One render per row would be thousands of renders.
    let renders = 0
    function Probe() {
      renders += 1
      useRunStream(1, options)
      return null
    }
    render(<Probe />)
    const source = FakeEventSource.latest()
    const before = renders

    await act(async () => {
      for (let step = 1; step <= 400; step += 1) source.emit('timestep', timestep(step))
      await new Promise((resolve) => setTimeout(resolve, FLUSH * 3))
    })

    // 400 rows must not cost 400 renders; buffering bounds it by wall-clock.
    expect(renders - before).toBeLessThan(20)
  })
})

// -- polling fallback -------------------------------------------------------

describe('polling fallback', () => {
  it('polls when EventSource is unavailable', async () => {
    // Not a hypothetical: an old browser, or a proxy that buffers the stream.
    vi.stubGlobal('EventSource', undefined)
    vi.mocked(getTimeseries).mockResolvedValue([timestep(1), timestep(2)])
    vi.mocked(getDecisions).mockResolvedValue([decision(1)])
    vi.mocked(getSummary).mockResolvedValue({ status: 'running' })

    const { result } = renderHook(() => useRunStream(3, options))

    await waitFor(() => expect(result.current.timeseries).toHaveLength(2))
    expect(result.current.transport).toBe('poll')
    expect(getTimeseries).toHaveBeenCalledWith(3, { sinceStep: 0 })
    expect(getDecisions).toHaveBeenCalledWith(3, { sinceStep: 0 })
  })

  it('can be forced on even where EventSource exists', async () => {
    vi.mocked(getTimeseries).mockResolvedValue([timestep(1)])
    const { result } = renderHook(() => useRunStream(3, { ...options, transport: 'poll' }))

    await waitFor(() => expect(result.current.transport).toBe('poll'))
    expect(FakeEventSource.instances).toHaveLength(0)
  })

  it('advances since_step so rows are not refetched or re-appended', async () => {
    vi.mocked(getTimeseries)
      .mockResolvedValueOnce([timestep(1), timestep(2)])
      .mockResolvedValueOnce([timestep(3)])
      .mockResolvedValue([])
    vi.mocked(getDecisions).mockResolvedValue([])

    const { result } = renderHook(() => useRunStream(3, { ...options, transport: 'poll' }))

    await waitFor(() => expect(result.current.timeseries).toHaveLength(3))
    expect(getTimeseries).toHaveBeenNthCalledWith(2, 3, { sinceStep: 3 })
    expect(result.current.timeseries.map((row) => row.step)).toEqual([1, 2, 3])
  })

  it('stops polling once the run reaches a terminal status', async () => {
    vi.mocked(getSummary).mockResolvedValue({ status: 'complete' })
    const { result } = renderHook(() => useRunStream(3, { ...options, transport: 'poll' }))

    await waitFor(() => expect(result.current.status).toBe('complete'))
    const calls = vi.mocked(getSummary).mock.calls.length

    await new Promise((resolve) => setTimeout(resolve, FLUSH * 5))
    expect(vi.mocked(getSummary).mock.calls.length).toBe(calls)
  })

  it('surfaces a polling failure and stops', async () => {
    vi.mocked(getTimeseries).mockRejectedValue(
      Object.assign(new Error('boom'), { detail: 'run 3 not found' })
    )
    const { result } = renderHook(() => useRunStream(3, { ...options, transport: 'poll' }))

    await waitFor(() => expect(result.current.error).toBe('run 3 not found'))
  })

  it('stops polling on unmount', async () => {
    const { unmount } = renderHook(() => useRunStream(3, { ...options, transport: 'poll' }))
    await waitFor(() => expect(getSummary).toHaveBeenCalled())

    unmount()
    const calls = vi.mocked(getSummary).mock.calls.length

    await new Promise((resolve) => setTimeout(resolve, FLUSH * 5))
    expect(vi.mocked(getSummary).mock.calls.length).toBe(calls)
  })
})
