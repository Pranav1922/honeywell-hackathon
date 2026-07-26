// Subscribes to /api/runs/{id}/stream over SSE and falls back to incremental
// polling with since_step when SSE is unavailable. Returns timeseries,
// decisions, and run status.
//
// Three properties this hook has to guarantee, because everything on screen
// depends on them:
//
// 1. Rows are appended in step order and never duplicated. The server's stream
//    generator restarts from step 0 on a new connection, and EventSource
//    reconnects on its own, so a reconnect replays the whole run. A monotonic
//    step filter is what makes that harmless instead of doubling every chart.
//
// 2. Arrival rate is decoupled from render rate. Replaying a finished run
//    delivers one frame per row as fast as the transport allows; dispatching per
//    row would re-render the charts thousands of times. Rows are buffered and
//    flushed on a fixed interval, so render cost is bounded by wall-clock rather
//    than by horizon length.
//
// 3. It degrades rather than freezing. No EventSource — an old browser, or a
//    proxy that buffers — falls back to incremental polling on the same
//    since_step contract.

import { useEffect, useMemo, useReducer, useRef } from 'react'

import { getDecisions, getSummary, getTimeseries, streamUrl } from '../lib/api.js'

export const TERMINAL_STATUSES = ['complete', 'failed', 'stopped']
export const DEFAULT_POLL_INTERVAL_MS = 1000
export const DEFAULT_FLUSH_INTERVAL_MS = 120

const EMPTY = Object.freeze({
  timeseries: [],
  decisions: [],
  status: null,
  error: null,
  transport: null,
})

/** Whether a status means the run has stopped producing data. */
export function isTerminal(status) {
  return TERMINAL_STATUSES.includes(status)
}

/** The highest step already ingested, or -1 for an empty trace. */
export function lastStep(rows) {
  return rows.length > 0 ? rows[rows.length - 1].step : -1
}

/** Rows strictly newer than `after`, in order. Drops replays and duplicates. */
export function onlyNew(rows, after) {
  const fresh = []
  let cursor = after
  for (const row of rows) {
    if (typeof row?.step !== 'number' || row.step <= cursor) continue
    fresh.push(row)
    cursor = row.step
  }
  return fresh
}

function reducer(state, action) {
  switch (action.type) {
    case 'reset':
      return { ...EMPTY, transport: action.transport ?? null }

    case 'transport':
      return state.transport === action.transport
        ? state
        : { ...state, transport: action.transport }

    case 'ingest': {
      const timesteps = onlyNew(action.timeseries, lastStep(state.timeseries))
      const decisions = onlyNew(action.decisions, lastStep(state.decisions))
      const status = action.status ?? state.status
      if (timesteps.length === 0 && decisions.length === 0 && status === state.status) {
        // Nothing new: return the same object so React skips the re-render.
        return state
      }
      return {
        ...state,
        status,
        timeseries:
          timesteps.length > 0 ? [...state.timeseries, ...timesteps] : state.timeseries,
        decisions:
          decisions.length > 0 ? [...state.decisions, ...decisions] : state.decisions,
      }
    }

    case 'error':
      return state.error === action.error ? state : { ...state, error: action.error }

    default:
      return state
  }
}

/**
 * Subscribe to a run's live telemetry and decisions.
 *
 * @param runId The run to follow. Falsy means "nothing selected"; the hook idles.
 * @param options.transport Force `'sse'` or `'poll'`. Defaults to SSE when the
 *   environment provides `EventSource`, which is also what makes the fallback
 *   path reachable under test.
 * @param options.pollIntervalMs How often to poll in fallback mode.
 * @param options.flushIntervalMs How often buffered rows reach React state.
 * @returns `{ timeseries, decisions, status, error, transport, isRunning }`
 */
export function useRunStream(runId, options = {}) {
  const {
    transport: forced,
    pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
    flushIntervalMs = DEFAULT_FLUSH_INTERVAL_MS,
  } = options

  const [state, dispatch] = useReducer(reducer, EMPTY)
  const buffer = useRef({ timeseries: [], decisions: [], status: null, dirty: false })

  useEffect(() => {
    if (!runId) {
      dispatch({ type: 'reset', transport: null })
      return undefined
    }

    const useSse =
      forced === 'sse' || (forced !== 'poll' && typeof EventSource !== 'undefined')

    dispatch({ type: 'reset', transport: useSse ? 'sse' : 'poll' })
    buffer.current = { timeseries: [], decisions: [], status: null, dirty: false }

    let cancelled = false

    const push = (patch) => {
      if (cancelled) return
      const pending = buffer.current
      if (patch.timeseries) pending.timeseries.push(...patch.timeseries)
      if (patch.decisions) pending.decisions.push(...patch.decisions)
      if (patch.status) pending.status = patch.status
      pending.dirty = true
    }

    const flush = () => {
      const pending = buffer.current
      if (!pending.dirty) return
      buffer.current = { timeseries: [], decisions: [], status: null, dirty: false }
      dispatch({
        type: 'ingest',
        timeseries: pending.timeseries,
        decisions: pending.decisions,
        status: pending.status,
      })
    }

    const flushTimer = setInterval(flush, flushIntervalMs)
    const stopSource = useSse
      ? subscribe(runId, push, (error) => dispatch({ type: 'error', error }))
      : poll(runId, push, (error) => dispatch({ type: 'error', error }), pollIntervalMs, () => cancelled)

    return () => {
      cancelled = true
      clearInterval(flushTimer)
      stopSource()
    }
  }, [runId, forced, pollIntervalMs, flushIntervalMs])

  return useMemo(
    () => ({ ...state, isRunning: state.status === 'running' }),
    [state]
  )
}

// -- transports -------------------------------------------------------------

/** Open an SSE subscription. Returns a teardown function. */
function subscribe(runId, push, onError) {
  const source = new EventSource(streamUrl(runId))
  let closed = false

  const close = () => {
    if (closed) return
    closed = true
    source.close()
  }

  source.addEventListener('timestep', (event) => {
    const row = decode(event, onError)
    if (row) push({ timeseries: [row], status: 'running' })
  })

  source.addEventListener('decision', (event) => {
    const row = decode(event, onError)
    if (row) push({ decisions: [row] })
  })

  source.addEventListener('complete', (event) => {
    const payload = decode(event, onError)
    push({ status: payload?.status ?? 'complete' })
    // Close before the server's own hang-up reaches us, or EventSource will
    // reconnect and replay the entire run.
    close()
  })

  source.addEventListener('error', (event) => {
    // This one listener sees two different things: a server-sent `event: error`
    // frame, which carries `data`, and a transport failure, which does not.
    if (event?.data) {
      const payload = decode(event, onError)
      onError(payload?.detail ?? 'the run stream reported an error')
      close()
      return
    }
    // A transport drop while the run is live is recoverable — EventSource
    // reconnects and the monotonic step filter discards the replay — so it is
    // not surfaced as an error unless the connection is already closed for good.
    if (source.readyState === EventSource.CLOSED) {
      onError('lost connection to the run stream')
    }
  })

  return close
}

/** Poll incrementally on the `since_step` contract. Returns a teardown function. */
function poll(runId, push, onError, intervalMs, isCancelled) {
  let timer = null
  let sinceTimestep = 0
  let sinceDecision = 0

  const tick = async () => {
    try {
      const [timeseries, decisions, summary] = await Promise.all([
        getTimeseries(runId, { sinceStep: sinceTimestep }),
        getDecisions(runId, { sinceStep: sinceDecision }),
        getSummary(runId),
      ])
      if (isCancelled()) return

      if (timeseries.length > 0) {
        sinceTimestep = timeseries[timeseries.length - 1].step + 1
      }
      if (decisions.length > 0) {
        sinceDecision = decisions[decisions.length - 1].step + 1
      }
      push({ timeseries, decisions, status: summary.status })

      if (isTerminal(summary.status)) return
    } catch (error) {
      if (isCancelled()) return
      onError(error?.detail ?? error?.message ?? 'polling failed')
      return
    }
    // Chained rather than on an interval, so a slow response can never cause
    // overlapping requests to pile up against the same run.
    timer = setTimeout(tick, intervalMs)
  }

  tick()

  return () => {
    if (timer !== null) clearTimeout(timer)
  }
}

function decode(event, onError) {
  try {
    return JSON.parse(event.data)
  } catch {
    onError('received a malformed frame from the run stream')
    return null
  }
}
