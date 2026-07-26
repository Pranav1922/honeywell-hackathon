// Zone temperature against outdoor temperature, with the active heating and
// cooling set-point band shaded behind them.
//
// The band is the point of the panel. A zone temperature line on its own says
// nothing about whether the controller is doing its job; the same line sitting
// inside a dead-band that visibly widens overnight and narrows at 08:00 is the
// agent's strategy made legible.

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

import { celsius, simClock } from '../lib/format.js'
import { domain } from '../lib/series.js'
import ChartFrame from './ChartFrame.jsx'

/**
 * Shape rows for a stacked-area dead-band.
 *
 * Recharts shades between two series by stacking a transparent floor under a
 * visible band, so the band is carried as (heating set-point, height) rather
 * than as (lower, upper).
 */
export function buildTemperatureSeries(rows) {
  return (rows ?? []).map((row) => ({
    step: row.step,
    sim_time: row.sim_time,
    zone_temp_c: row.zone_temp_c,
    outdoor_temp_c: row.outdoor_temp_c,
    band_floor: row.heating_sp_c,
    band_height: Math.max(0, row.cooling_sp_c - row.heating_sp_c),
  }))
}

export default function TemperatureChart({ rows = [] }) {
  const data = buildTemperatureSeries(rows)

  return (
    <ChartFrame
      title="Zone temperature"
      subtitle="Zone and outdoor air against the active set-point band"
      empty={data.length === 0}
    >
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: -8 }}>
          <CartesianGrid strokeDasharray="3 3" className="grid" />
          <XAxis dataKey="sim_time" tickFormatter={simClock} minTickGap={48} />
          <YAxis
            domain={domain(data, 'outdoor_temp_c', 2)}
            tickFormatter={(value) => `${value}°`}
            width={48}
          />
          <Tooltip
            labelFormatter={simClock}
            formatter={(value, name) => [celsius(value), name]}
          />
          <Legend verticalAlign="top" height={28} />
          <Area
            dataKey="band_floor"
            stackId="band"
            stroke="none"
            fill="transparent"
            legendType="none"
            tooltipType="none"
            isAnimationActive={false}
          />
          <Area
            name="Set-point band"
            dataKey="band_height"
            stackId="band"
            stroke="none"
            fill="var(--band)"
            fillOpacity={1}
            isAnimationActive={false}
          />
          <Line
            name="Zone"
            dataKey="zone_temp_c"
            stroke="var(--zone)"
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
          <Line
            name="Outdoor"
            dataKey="outdoor_temp_c"
            stroke="var(--outdoor)"
            strokeWidth={1.5}
            strokeDasharray="4 3"
            dot={false}
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </ChartFrame>
  )
}
