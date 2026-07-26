// API client tests.
//
// These pin the request shape against ARCHITECTURE.md section 6 — path, method,
// and query-parameter names. The parameter names matter more than they look:
// `since_step` and `stride` are what make incremental polling and long-horizon
// downsampling work, and a typo in either fails silently by fetching the whole
// run every time instead of erroring.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ApiError,
  BASE,
  compare,
  deleteRun,
  exportUrl,
  getConfig,
  getDecisions,
  getHealth,
  getRun,
  getScenarios,
  getSummary,
  getTimeseries,
  listRuns,
  query,
  request,
  startRun,
  stopRun,
  streamUrl,
} from './api.js'

function jsonResponse(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: 'OK',
    text: () => Promise.resolve(JSON.stringify(body)),
    json: () => Promise.resolve(body),
    clone() {
      return this
    },
  }
}

function errorResponse(status, body) {
  return {
    ok: false,
    status,
    statusText: 'Error',
    text: () => Promise.resolve(JSON.stringify(body ?? {})),
    json: () => (body ? Promise.resolve(body) : Promise.reject(new Error('no body'))),
    clone() {
      return this
    },
  }
}

let fetchMock

beforeEach(() => {
  fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }))
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

const calledUrl = () => fetchMock.mock.calls[0][0]
const calledOptions = () => fetchMock.mock.calls[0][1]

describe('query', () => {
  it('omits null, undefined and empty values', () => {
    expect(query({ a: 1, b: null, c: undefined, d: '', e: 0 })).toBe('?a=1&e=0')
  })

  it('returns an empty string when nothing survives', () => {
    expect(query({})).toBe('')
    expect(query({ a: null })).toBe('')
    expect(query()).toBe('')
  })

  it('encodes values', () => {
    expect(query({ label: 'summer week' })).toBe('?label=summer+week')
  })
})

describe('endpoint paths', () => {
  it('reads the fixed collections', async () => {
    await getHealth()
    expect(calledUrl()).toBe(`${BASE}/health`)

    fetchMock.mockClear()
    await getConfig()
    expect(calledUrl()).toBe(`${BASE}/config`)

    fetchMock.mockClear()
    await getScenarios()
    expect(calledUrl()).toBe(`${BASE}/scenarios`)
  })

  it('lists runs, with an optional limit', async () => {
    await listRuns()
    expect(calledUrl()).toBe(`${BASE}/runs`)

    fetchMock.mockClear()
    await listRuns(10)
    expect(calledUrl()).toBe(`${BASE}/runs?limit=10`)
  })

  it('reads one run and its summary', async () => {
    await getRun(7)
    expect(calledUrl()).toBe(`${BASE}/runs/7`)

    fetchMock.mockClear()
    await getSummary(7)
    expect(calledUrl()).toBe(`${BASE}/runs/7/summary`)
  })

  it('requests telemetry incrementally and downsampled', async () => {
    await getTimeseries(3)
    expect(calledUrl()).toBe(`${BASE}/runs/3/timeseries`)

    fetchMock.mockClear()
    await getTimeseries(3, { sinceStep: 96 })
    expect(calledUrl()).toBe(`${BASE}/runs/3/timeseries?since_step=96`)

    fetchMock.mockClear()
    await getTimeseries(3, { sinceStep: 96, stride: 4 })
    expect(calledUrl()).toBe(`${BASE}/runs/3/timeseries?since_step=96&stride=4`)
  })

  it('requests decisions incrementally', async () => {
    await getDecisions(3, { sinceStep: 12 })
    expect(calledUrl()).toBe(`${BASE}/runs/3/decisions?since_step=12`)
  })

  it('compares two runs by id', async () => {
    await compare(1, 2)
    expect(calledUrl()).toBe(`${BASE}/compare?baseline_run_id=1&agent_run_id=2`)
  })

  it('builds stream and export URLs without fetching them', () => {
    expect(streamUrl(5)).toBe(`${BASE}/runs/5/stream`)
    expect(exportUrl(5)).toBe(`${BASE}/runs/5/export`)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('uses relative paths so the dev proxy handles the origin', async () => {
    await getHealth()
    expect(calledUrl().startsWith('/api')).toBe(true)
  })
})

describe('mutations', () => {
  it('posts a run with a JSON body and content type', async () => {
    const body = { scenario: 'summer_week', controller: 'llm', simulator: 'toy' }
    await startRun(body)

    expect(calledUrl()).toBe(`${BASE}/runs`)
    expect(calledOptions().method).toBe('POST')
    expect(JSON.parse(calledOptions().body)).toEqual(body)
    expect(calledOptions().headers['Content-Type']).toBe('application/json')
  })

  it('stops a run with a bodyless POST', async () => {
    await stopRun(4)

    expect(calledUrl()).toBe(`${BASE}/runs/4/stop`)
    expect(calledOptions().method).toBe('POST')
    expect(calledOptions().headers).toBeUndefined()
  })

  it('deletes a run', async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 204 })
    await expect(deleteRun(4)).resolves.toBeNull()
    expect(calledOptions().method).toBe('DELETE')
  })
})

describe('responses', () => {
  it('decodes a JSON body', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ status: 'ok', llm_model: 'llama' }))
    await expect(getHealth()).resolves.toEqual({ status: 'ok', llm_model: 'llama' })
  })

  it('returns null for an empty body rather than throwing on parse', async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 200, text: () => Promise.resolve('') })
    await expect(getHealth()).resolves.toBeNull()
  })

  it("surfaces the backend's own explanation, not the status code", async () => {
    // The value of this: the UI can say why the comparison is unavailable.
    fetchMock.mockResolvedValue(
      errorResponse(409, { detail: 'run 3 has not completed; nothing to compare' })
    )

    await expect(compare(1, 3)).rejects.toThrow('run 3 has not completed')
    fetchMock.mockClear()

    fetchMock.mockResolvedValue(
      errorResponse(409, { detail: 'run 3 has not completed; nothing to compare' })
    )
    const error = await compare(1, 3).catch((cause) => cause)
    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(409)
    expect(error.detail).toBe('run 3 has not completed; nothing to compare')
  })

  it('falls back to the status when there is no detail', async () => {
    fetchMock.mockResolvedValue(errorResponse(500, null))
    const error = await getHealth().catch((cause) => cause)

    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(500)
    expect(error.detail).toBeNull()
    expect(error.message).toContain('500')
  })

  it('propagates a network failure', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))
    await expect(getHealth()).rejects.toThrow('Failed to fetch')
  })

  it('passes arbitrary options through to fetch', async () => {
    await request('/runs', { method: 'POST', body: '{}' })
    expect(calledOptions().method).toBe('POST')
  })
})
