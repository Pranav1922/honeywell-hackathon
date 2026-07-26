// Dashboard shell. Owns the selected run, composes every panel, and passes the
// live stream from useRunStream down to the charts.
//
// Layout: RunControls across the top, KpiRow beneath it, a two-column grid of
// TemperatureChart / EnergyChart / ComfortChart / OccupancyChart, then
// ActionPanel and AgentLog side by side.
//
// Everything fetched lives here and flows downward. The panels take props and
// fetch nothing, which is what guarantees every chart on screen is showing the
// same instant of the same run — the property that makes a screenshot of this
// page usable as evidence.
//
// Downsampling also happens here, once, so all four charts share an x-axis
// instead of each choosing its own stride.

import { useCallback, useEffect, useMemo, useState } from 'react'

import ActionPanel from './components/ActionPanel.jsx'
import AgentLog from './components/AgentLog.jsx'
import ComfortChart from './components/ComfortChart.jsx'
import EnergyChart from './components/EnergyChart.jsx'
import KpiRow from './components/KpiRow.jsx'
import OccupancyChart from './components/OccupancyChart.jsx'
import RunControls, { startRunBody } from './components/RunControls.jsx'
import TemperatureChart from './components/TemperatureChart.jsx'
import { useRunStream } from './hooks/useRunStream.js'
import * as api from './lib/api.js'
import { latest, sample } from './lib/series.js'

const DEFAULT_FORM = {
  scenario: '',
  controller: 'llm',
  simulator: 'toy',
  horizonSteps: '',
  baselineRunId: '',
  label: '',
}

export default function App() {
  const [config, setConfig] = useState(null)
  const [scenarios, setScenarios] = useState([])
  const [runs, setRuns] = useState([])
  const [form, setForm] = useState(DEFAULT_FORM)
  const [runId, setRunId] = useState(null)
  const [run, setRun] = useState(null)
  const [savings, setSavings] = useState(null)
  const [baselineRows, setBaselineRows] = useState([])
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState(null)

  const stream = useRunStream(runId)

  // -- initial load ---------------------------------------------------------

  useEffect(() => {
    let cancelled = false
    Promise.all([api.getConfig(), api.getScenarios(), api.listRuns()])
      .then(([nextConfig, nextScenarios, nextRuns]) => {
        if (cancelled) return
        setConfig(nextConfig)
        setScenarios(nextScenarios)
        setRuns(nextRuns)
        setForm((current) => ({
          ...current,
          scenario: current.scenario || nextScenarios[0]?.id || '',
          controller: nextConfig.controllers?.includes(current.controller)
            ? current.controller
            : (nextConfig.controllers?.[0] ?? ''),
          simulator: nextConfig.simulators?.[0] ?? 'toy',
        }))
        // Resume whatever ran last, so a reload lands on data rather than a
        // blank page and a finished run can be replayed from the store.
        if (nextRuns.length > 0) setRunId((current) => current ?? nextRuns[0].id)
      })
      .catch((cause) => {
        if (!cancelled) setError(describe(cause, 'could not reach the backend'))
      })
    return () => {
      cancelled = true
    }
  }, [])

  // -- selected run record, refreshed while it is live ----------------------

  const refreshRun = useCallback(async () => {
    if (!runId) return
    try {
      const record = await api.getRun(runId)
      setRun(record)
      setRuns((current) =>
        current.some((entry) => entry.id === record.id)
          ? current.map((entry) => (entry.id === record.id ? record : entry))
          : [record, ...current]
      )
    } catch (cause) {
      setError(describe(cause, `could not load run ${runId}`))
    }
  }, [runId])

  useEffect(() => {
    setRun(null)
    setSavings(null)
    setBaselineRows([])
    refreshRun()
  }, [runId, refreshRun])

  // The run row carries the aggregates, and they are only written when the run
  // finishes — so it is re-read once the stream reports a terminal status.
  useEffect(() => {
    if (stream.status && stream.status !== 'running') refreshRun()
  }, [stream.status, refreshRun])

  // -- baseline overlay and savings -----------------------------------------

  useEffect(() => {
    const baselineId = run?.baseline_run_id
    if (!baselineId) {
      setBaselineRows([])
      setSavings(null)
      return undefined
    }
    let cancelled = false
    api
      .getTimeseries(baselineId)
      .then((rows) => {
        if (!cancelled) setBaselineRows(rows)
      })
      .catch(() => {
        if (!cancelled) setBaselineRows([])
      })
    return () => {
      cancelled = true
    }
  }, [run?.baseline_run_id])

  useEffect(() => {
    const baselineId = run?.baseline_run_id
    // /api/compare answers 409 until both runs have totals, so it is only asked
    // once this run has finished.
    if (!baselineId || run?.total_kwh == null) return undefined
    let cancelled = false
    api
      .compare(baselineId, run.id)
      .then((report) => {
        if (!cancelled) setSavings(report)
      })
      .catch(() => {
        if (!cancelled) setSavings(null)
      })
    return () => {
      cancelled = true
    }
  }, [run?.id, run?.baseline_run_id, run?.total_kwh])

  // -- actions --------------------------------------------------------------

  const onChange = useCallback((field, value) => {
    setForm((current) => ({ ...current, [field]: value }))
  }, [])

  const onStart = useCallback(async () => {
    setStarting(true)
    setError(null)
    try {
      const created = await api.startRun(startRunBody(form))
      setRuns((current) => [created, ...current])
      setRunId(created.id)
    } catch (cause) {
      setError(describe(cause, 'could not start the run'))
    } finally {
      setStarting(false)
    }
  }, [form])

  const onStop = useCallback(async () => {
    if (!runId) return
    try {
      await api.stopRun(runId)
      refreshRun()
    } catch (cause) {
      setError(describe(cause, 'could not stop the run'))
    }
  }, [runId, refreshRun])

  // -- derived --------------------------------------------------------------

  const rows = useMemo(() => sample(stream.timeseries), [stream.timeseries])
  const sampledBaseline = useMemo(() => sample(baselineRows), [baselineRows])
  const currentState = useMemo(() => latest(stream.timeseries), [stream.timeseries])
  const currentDecision = useMemo(() => latest(stream.decisions), [stream.decisions])
  const hasBaseline = Boolean(run?.baseline_run_id) && sampledBaseline.length > 0
  const comfortBand = config?.comfort

  return (
    <div className="app">
      <header className="app-head">
        <div>
          <h1>Eco-Loop Building Agents</h1>
          <p className="tagline">
            Autonomous closed-loop building control — live telemetry, agent
            reasoning and measured savings
          </p>
        </div>
        <div className="app-status">
          {stream.transport ? (
            <span className="badge badge-quiet">
              {stream.transport === 'sse' ? 'live stream' : 'polling'}
            </span>
          ) : null}
          {stream.isRunning ? <span className="badge badge-info">running</span> : null}
          {runId ? (
            <a className="export" href={api.exportUrl(runId)}>
              Export CSV
            </a>
          ) : null}
        </div>
      </header>

      <RunControls
        config={config}
        scenarios={scenarios}
        runs={runs}
        form={form}
        onChange={onChange}
        onStart={onStart}
        onStop={onStop}
        starting={starting}
        activeRun={run ? { ...run, status: stream.status ?? run.status } : null}
        error={error ?? stream.error}
      />

      <RunPicker runs={runs} selected={runId} onSelect={setRunId} />

      <KpiRow run={run} savings={savings} />

      <div className="chart-grid">
        <TemperatureChart rows={rows} />
        <EnergyChart
          rows={stream.timeseries}
          baselineRows={baselineRows}
          hasBaseline={hasBaseline}
        />
        <ComfortChart
          rows={rows}
          low={comfortBand?.pmv_low ?? -0.5}
          high={comfortBand?.pmv_high ?? 0.5}
        />
        <OccupancyChart rows={rows} />
      </div>

      <div className="bottom-grid">
        <ActionPanel decision={currentDecision} state={currentState} />
        <AgentLog decisions={stream.decisions} />
      </div>
    </div>
  )
}

/** Compact selector over recorded runs, so a finished run can be replayed. */
function RunPicker({ runs, selected, onSelect }) {
  if (runs.length === 0) return null
  return (
    <nav className="run-picker" aria-label="Recorded runs">
      {runs.slice(0, 12).map((entry) => (
        <button
          type="button"
          key={entry.id}
          className={entry.id === selected ? 'chip chip-active' : 'chip'}
          onClick={() => onSelect(entry.id)}
        >
          #{entry.id} {entry.controller}
          <span className={`dot dot-${entry.status}`} />
        </button>
      ))}
    </nav>
  )
}

function describe(cause, fallback) {
  return cause?.detail ?? cause?.message ?? fallback
}
