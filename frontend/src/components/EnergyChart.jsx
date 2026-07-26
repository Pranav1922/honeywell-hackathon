// Instantaneous power for baseline and agent overlaid, plus cumulative kWh —
// the visual form of the savings claim.
//
// Two y-axes on purpose. Instantaneous power shows *where* the agent behaves
// differently — the overnight setback, the pre-cool ahead of the peak — while
// the diverging cumulative curves show the size of the result. One axis alone
// tells half the story: power traces cross each other constantly, and only the
// cumulative pair shows which run is actually ahead.

import {
  Area,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { kw, kwh, simClock } from '../lib/format.js'
import { mergeByStep, sample, withCumulative } from '../lib/series.js'
import ChartFrame from './ChartFrame.jsx'

/**
 * Shape agent and baseline traces into one comparable series.
 *
 * Cumulative totals are accumulated over the full traces *before* downsampling,
 * because summing sampled rows would drop the energy used between them and
 * understate both totals.
 */
export function buildEnergySeries(agentRows, baselineRows, maxPoints) {
  const agent = withCumulative(agentRows ?? [])
  const baseline = withCumulative(baselineRows ?? [])
  const merged = mergeByStep(agent, baseline, {
    baseline_kw: 'power_kw',
    baseline_kwh: 'cumulative_kwh',
  })
  return sample(merged, maxPoints).map((row) => ({
    step: row.step,
    sim_time: row.sim_time,
    agent_kw: row.power_kw,
    agent_kwh: row.cumulative_kwh,
    baseline_kw: row.baseline_kw,
    baseline_kwh: row.baseline_kwh,
  }))
}

export default function EnergyChart({
  rows = [],
  baselineRows = [],
  hasBaseline = false,
}) {
  const data = buildEnergySeries(rows, baselineRows)

  return (
    <ChartFrame
      title="Energy"
      subtitle={
        hasBaseline
          ? 'Power and cumulative consumption against the baseline run'
          : 'Power and cumulative consumption — pick a baseline run to overlay it'
      }
      empty={data.length === 0}
    >
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 8, right: 4, bottom: 4, left: -8 }}>
          <CartesianGrid strokeDasharray="3 3" className="grid" />
          <XAxis dataKey="sim_time" tickFormatter={simClock} minTickGap={48} />
          <YAxis yAxisId="power" width={44} />
          <YAxis
            yAxisId="energy"
            orientation="right"
            width={52}
            tickFormatter={(value) => `${Math.round(value)}`}
          />
          <Tooltip
            labelFormatter={simClock}
            formatter={(value, name) => [
              String(name).includes('kWh') ? kwh(value) : kw(value),
              name,
            ]}
          />
          <Legend verticalAlign="top" height={28} />
          <Area
            yAxisId="energy"
            name="Agent kWh"
            dataKey="agent_kwh"
            stroke="var(--agent)"
            fill="var(--agent-fill)"
            strokeWidth={2}
            isAnimationActive={false}
          />
          {hasBaseline ? (
            <Line
              yAxisId="energy"
              name="Baseline kWh"
              dataKey="baseline_kwh"
              stroke="var(--baseline)"
              strokeWidth={2}
              strokeDasharray="5 4"
              dot={false}
              isAnimationActive={false}
            />
          ) : null}
          <Line
            yAxisId="power"
            name="Agent kW"
            dataKey="agent_kw"
            stroke="var(--zone)"
            strokeWidth={1.25}
            dot={false}
            isAnimationActive={false}
          />
          {hasBaseline ? (
            <Line
              yAxisId="power"
              name="Baseline kW"
              dataKey="baseline_kw"
              stroke="var(--outdoor)"
              strokeWidth={1}
              strokeDasharray="3 3"
              dot={false}
              isAnimationActive={false}
            />
          ) : null}
        </ComposedChart>
      </ResponsiveContainer>
    </ChartFrame>
  )
}
