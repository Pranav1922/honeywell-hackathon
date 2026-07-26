// Occupancy fraction over time — the context that makes setback decisions
// legible to a viewer.
//
// CO2 rides on the same panel because the two belong together: occupancy is what
// the agent is responding to, and CO2 is the measurement that proves the
// ventilation it chose was actually sufficient. Reading them apart is what makes
// demand-controlled ventilation look like a guess.

import {
  Area,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { fraction, ppm, simClock } from '../lib/format.js'
import ChartFrame from './ChartFrame.jsx'

export const CO2_CEILING_PPM = 1000

export function buildOccupancySeries(rows) {
  return (rows ?? []).map((row) => ({
    step: row.step,
    sim_time: row.sim_time,
    occupancy: row.occupancy,
    co2_ppm: row.co2_ppm,
  }))
}

export default function OccupancyChart({ rows = [], co2Ceiling = CO2_CEILING_PPM }) {
  const data = buildOccupancySeries(rows)

  return (
    <ChartFrame
      title="Occupancy and air quality"
      subtitle="Occupied fraction, with measured CO₂ against its ceiling"
      empty={data.length === 0}
    >
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 8, right: 4, bottom: 4, left: -8 }}>
          <CartesianGrid strokeDasharray="3 3" className="grid" />
          <XAxis dataKey="sim_time" tickFormatter={simClock} minTickGap={48} />
          <YAxis
            yAxisId="occupancy"
            domain={[0, 1]}
            width={44}
            tickFormatter={(value) => fraction(value)}
          />
          <YAxis
            yAxisId="co2"
            orientation="right"
            width={52}
            domain={[400, 'auto']}
            tickFormatter={(value) => `${Math.round(value)}`}
          />
          <Tooltip
            labelFormatter={simClock}
            formatter={(value, name) => [
              name === 'CO₂' ? ppm(value) : fraction(value),
              name,
            ]}
          />
          <Legend verticalAlign="top" height={28} />
          <Area
            yAxisId="occupancy"
            name="Occupancy"
            dataKey="occupancy"
            stroke="var(--occupancy)"
            fill="var(--occupancy-fill)"
            strokeWidth={1.5}
            isAnimationActive={false}
          />
          <Line
            yAxisId="co2"
            name="CO₂"
            dataKey="co2_ppm"
            stroke="var(--co2)"
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
          />
          <ReferenceLine
            yAxisId="co2"
            y={co2Ceiling}
            stroke="var(--violation)"
            strokeDasharray="4 3"
            label={{ value: 'CO₂ ceiling', position: 'insideTopRight', fontSize: 11 }}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </ChartFrame>
  )
}
