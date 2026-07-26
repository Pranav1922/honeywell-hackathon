// PMV trace with the acceptable -0.5 to +0.5 band shaded; excursions marked so
// comfort violations are visible rather than merely counted.
//
// A violation count is a number someone has to trust. A marked excursion on a
// trace is something they can check — and it shows *when* comfort slipped, which
// is what distinguishes a controller that is momentarily recovering from one that
// is quietly running the building cold to save energy.

import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { pmv as formatPmv, simClock } from '../lib/format.js'
import { excursions } from '../lib/series.js'
import ChartFrame from './ChartFrame.jsx'

export const PMV_LOW = -0.5
export const PMV_HIGH = 0.5

/**
 * Shape rows for the PMV trace.
 *
 * PMV is only plotted where the zone is occupied. Off-hours PMV is a real number
 * but not a meaningful one — nobody is there to have an opinion — and plotting it
 * makes every overnight setback look like a comfort failure.
 */
export function buildComfortSeries(rows, low = PMV_LOW, high = PMV_HIGH) {
  return (rows ?? []).map((row) => ({
    step: row.step,
    sim_time: row.sim_time,
    pmv: row.occupancy > 0 ? row.pmv : null,
    excursion:
      row.occupancy > 0 && (row.pmv < low || row.pmv > high) ? row.pmv : null,
  }))
}

export default function ComfortChart({ rows = [], low = PMV_LOW, high = PMV_HIGH }) {
  const data = buildComfortSeries(rows, low, high)
  const violations = excursions(rows, low, high).length

  return (
    <ChartFrame
      title="Thermal comfort"
      subtitle={
        violations > 0
          ? `Occupied-hours PMV — ${violations} excursion${violations === 1 ? '' : 's'} outside the band`
          : 'Occupied-hours PMV against the ASHRAE-55 acceptable band'
      }
      empty={data.length === 0}
    >
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: -8 }}>
          <CartesianGrid strokeDasharray="3 3" className="grid" />
          <XAxis dataKey="sim_time" tickFormatter={simClock} minTickGap={48} />
          <YAxis domain={[-1.5, 1.5]} width={48} tickFormatter={(value) => formatPmv(value, 1)} />
          <Tooltip
            labelFormatter={simClock}
            formatter={(value, name) => [formatPmv(value), name]}
          />
          <ReferenceArea
            y1={low}
            y2={high}
            fill="var(--comfort-band)"
            fillOpacity={1}
            ifOverflow="extendDomain"
          />
          <ReferenceLine y={0} stroke="var(--axis)" strokeDasharray="2 2" />
          <Line
            name="PMV"
            dataKey="pmv"
            stroke="var(--comfort)"
            strokeWidth={2}
            dot={false}
            connectNulls={false}
            isAnimationActive={false}
          />
          <Scatter
            name="Excursion"
            dataKey="excursion"
            fill="var(--violation)"
            shape="circle"
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </ChartFrame>
  )
}
