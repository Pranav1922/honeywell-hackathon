// Typed fetch wrappers for every backend endpoint in ARCHITECTURE.md section 6.
// Same-origin relative paths; the Vite dev proxy forwards /api to FastAPI.
//
// One function per endpoint and nothing else — no caching, no retry, no request
// deduplication. The dashboard is a single live view over one run; adding a data
// layer here would be inventing a problem to solve.

export const BASE = '/api'

/**
 * An HTTP failure carrying the backend's own explanation.
 *
 * FastAPI puts the useful part in `detail`, so surfacing that instead of the
 * status code is what lets the UI say "run 3 has not completed; nothing to
 * compare" rather than "409".
 */
export class ApiError extends Error {
  constructor(message, status, detail) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

/** Build a query string, omitting anything null, undefined or empty. */
export function query(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === '') continue
    search.append(key, String(value))
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

/** Issue a request and decode the response, or throw an `ApiError`. */
export async function request(path, options = {}) {
  const response = await fetch(`${BASE}${path}`, {
    headers: options.body ? { 'Content-Type': 'application/json' } : undefined,
    ...options,
  })

  if (!response.ok) {
    throw new ApiError(await errorMessage(response), response.status, await detailOf(response))
  }
  // 204 on DELETE, and /stop may answer with an empty body.
  if (response.status === 204) return null
  const text = await response.text()
  return text === '' ? null : JSON.parse(text)
}

async function detailOf(response) {
  try {
    const body = await response.clone().json()
    return typeof body?.detail === 'string' ? body.detail : null
  } catch {
    return null
  }
}

async function errorMessage(response) {
  const detail = await detailOf(response)
  return detail ?? `${response.status} ${response.statusText || 'request failed'}`
}

// -- endpoints (ARCHITECTURE.md section 6) ----------------------------------

/** GET /api/health — liveness, plus EnergyPlus and model availability. */
export const getHealth = () => request('/health')

/** GET /api/config — controllers, simulators, scenarios, comfort limits. */
export const getConfig = () => request('/config')

/** GET /api/scenarios — scenario definitions on disk. */
export const getScenarios = () => request('/scenarios')

/** POST /api/runs — start a run; returns immediately with the run record. */
export const startRun = (body) =>
  request('/runs', { method: 'POST', body: JSON.stringify(body) })

/** GET /api/runs — runs newest first, with summary metrics. */
export const listRuns = (limit) => request(`/runs${query({ limit })}`)

/** GET /api/runs/{id} — one run's full record. */
export const getRun = (runId) => request(`/runs/${runId}`)

/** DELETE /api/runs/{id} — delete a run and its telemetry. */
export const deleteRun = (runId) => request(`/runs/${runId}`, { method: 'DELETE' })

/** POST /api/runs/{id}/stop — request cooperative cancellation. */
export const stopRun = (runId) => request(`/runs/${runId}/stop`, { method: 'POST' })

/** GET /api/runs/{id}/timeseries — telemetry rows, incremental and downsampled. */
export const getTimeseries = (runId, { sinceStep, stride } = {}) =>
  request(`/runs/${runId}/timeseries${query({ since_step: sinceStep, stride })}`)

/** GET /api/runs/{id}/decisions — decisions with rationale, tools and latency. */
export const getDecisions = (runId, { sinceStep } = {}) =>
  request(`/runs/${runId}/decisions${query({ since_step: sinceStep })}`)

/** GET /api/runs/{id}/summary — aggregate KPIs and how far the run has got. */
export const getSummary = (runId) => request(`/runs/${runId}/summary`)

/** GET /api/compare — the savings report. The deliverable-3 number. */
export const compare = (baselineRunId, agentRunId) =>
  request(
    `/compare${query({ baseline_run_id: baselineRunId, agent_run_id: agentRunId })}`
  )

/** The SSE URL for a run. Handed to `EventSource`, not fetched. */
export const streamUrl = (runId) => `${BASE}/runs/${runId}/stream`

/** The CSV export URL for a run. Handed to a link, not fetched. */
export const exportUrl = (runId) => `${BASE}/runs/${runId}/export`
