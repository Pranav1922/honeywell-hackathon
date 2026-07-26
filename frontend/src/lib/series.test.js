// Series shaping tests.
//
// Two of these guard against a wrong number reaching the screen rather than a
// crash, which makes them the most valuable tests in the frontend: cumulative
// energy must be accumulated before downsampling, and a baseline overlay must be
// matched on step rather than on array index.

import { describe, expect, it } from 'vitest'

import {
  MAX_CHART_POINTS,
  domain,
  excursions,
  latest,
  mergeByStep,
  sample,
  withCumulative,
} from './series.js'

const rows = (count, extra = () => ({})) =>
  Array.from({ length: count }, (_, index) => ({
    step: index,
    sim_time: `2024-07-15T00:${String(index % 60).padStart(2, '0')}:00`,
    energy_kwh: 1,
    power_kw: 2,
    pmv: 0,
    occupancy: 1,
    ...extra(index),
  }))

describe('sample', () => {
  it('leaves a short trace untouched', () => {
    const short = rows(50)
    expect(sample(short, 100)).toBe(short)
  })

  it('caps a long trace at the requested budget', () => {
    // An annual run at 15-minute resolution. A browser will draw 35,040 points
    // and then stop responding.
    const sampled = sample(rows(35040), 1000)
    expect(sampled.length).toBeLessThanOrEqual(1001)
    expect(sampled.length).toBeGreaterThan(900)
  })

  it('keeps the first and last rows, so the live edge does not lag', () => {
    const full = rows(1000)
    const sampled = sample(full, 100)

    expect(sampled[0]).toBe(full[0])
    expect(sampled[sampled.length - 1]).toBe(full[full.length - 1])
  })

  it('preserves chronological order', () => {
    const steps = sample(rows(5000), 200).map((row) => row.step)
    expect(steps).toEqual([...steps].sort((a, b) => a - b))
  })

  it('plots only real measurements, never an average', () => {
    const full = rows(1000, (index) => ({ power_kw: index }))
    for (const row of sample(full, 100)) {
      expect(full).toContain(row)
    }
  })

  it('handles empty and degenerate input', () => {
    expect(sample([])).toEqual([])
    expect(sample(null)).toEqual([])
    expect(sample(undefined)).toEqual([])
    expect(sample(rows(10), 0)).toEqual([])
  })

  it('defaults to a sane budget', () => {
    expect(sample(rows(MAX_CHART_POINTS + 500)).length).toBeLessThanOrEqual(
      MAX_CHART_POINTS + 1
    )
  })
})

describe('withCumulative', () => {
  it('accumulates a running total', () => {
    const result = withCumulative(rows(4))
    expect(result.map((row) => row.cumulative_kwh)).toEqual([1, 2, 3, 4])
  })

  it('does not mutate the input rows', () => {
    const input = rows(3)
    withCumulative(input)
    expect(input[0].cumulative_kwh).toBeUndefined()
  })

  it('treats a missing value as zero rather than poisoning the total with NaN', () => {
    const input = [{ step: 0, energy_kwh: 1 }, { step: 1 }, { step: 2, energy_kwh: 2 }]
    expect(withCumulative(input).map((row) => row.cumulative_kwh)).toEqual([1, 1, 3])
  })

  it('accumulating before sampling is what keeps the total correct', () => {
    // The bug this prevents: sample first and the total is off by the stride.
    const full = rows(1000)
    const correct = sample(withCumulative(full), 100)
    const wrong = withCumulative(sample(full, 100))

    expect(correct[correct.length - 1].cumulative_kwh).toBe(1000)
    expect(wrong[wrong.length - 1].cumulative_kwh).toBeLessThan(1000)
  })

  it('accepts custom source and target fields', () => {
    const result = withCumulative(rows(3), 'power_kw', 'total_kw')
    expect(result[2].total_kw).toBe(6)
  })

  it('handles empty input', () => {
    expect(withCumulative([])).toEqual([])
    expect(withCumulative(null)).toEqual([])
  })
})

describe('mergeByStep', () => {
  it('overlays the baseline onto matching steps', () => {
    const agent = [
      { step: 0, power_kw: 5 },
      { step: 1, power_kw: 6 },
    ]
    const baseline = [
      { step: 0, power_kw: 9 },
      { step: 1, power_kw: 8 },
    ]

    expect(mergeByStep(agent, baseline, { baseline_kw: 'power_kw' })).toEqual([
      { step: 0, power_kw: 5, baseline_kw: 9 },
      { step: 1, power_kw: 6, baseline_kw: 8 },
    ])
  })

  it('matches on step, not on index, so unequal runs do not misalign', () => {
    // The bug this prevents: a stopped baseline makes index N of one run line up
    // against a completely different hour of the other.
    const agent = [
      { step: 10, power_kw: 5 },
      { step: 11, power_kw: 6 },
    ]
    const baseline = [
      { step: 11, power_kw: 8 },
      { step: 12, power_kw: 7 },
    ]

    const merged = mergeByStep(agent, baseline, { baseline_kw: 'power_kw' })
    expect(merged[0].baseline_kw).toBeNull()
    expect(merged[1].baseline_kw).toBe(8)
  })

  it('yields nulls when there is no baseline, so the line simply does not draw', () => {
    const merged = mergeByStep([{ step: 0, power_kw: 5 }], [], { baseline_kw: 'power_kw' })
    expect(merged[0].baseline_kw).toBeNull()
  })

  it('maps several fields at once', () => {
    const merged = mergeByStep(
      [{ step: 0 }],
      [{ step: 0, power_kw: 3, cumulative_kwh: 12 }],
      { baseline_kw: 'power_kw', baseline_kwh: 'cumulative_kwh' }
    )
    expect(merged[0]).toEqual({ step: 0, baseline_kw: 3, baseline_kwh: 12 })
  })

  it('handles empty input', () => {
    expect(mergeByStep(null, null, { a: 'b' })).toEqual([])
  })
})

describe('excursions', () => {
  it('finds occupied steps outside the band', () => {
    const trace = [
      { step: 0, pmv: 0.2, occupancy: 1 },
      { step: 1, pmv: 0.9, occupancy: 1 },
      { step: 2, pmv: -0.8, occupancy: 1 },
    ]
    expect(excursions(trace).map((row) => row.step)).toEqual([1, 2])
  })

  it('ignores an empty building, which cannot be uncomfortable', () => {
    const trace = [
      { step: 0, pmv: 2.5, occupancy: 0 },
      { step: 1, pmv: 2.5, occupancy: 0.1 },
    ]
    expect(excursions(trace).map((row) => row.step)).toEqual([1])
  })

  it('treats the band edges as acceptable', () => {
    const trace = [
      { step: 0, pmv: -0.5, occupancy: 1 },
      { step: 1, pmv: 0.5, occupancy: 1 },
    ]
    expect(excursions(trace)).toEqual([])
  })

  it('honours a custom band', () => {
    const trace = [{ step: 0, pmv: 0.4, occupancy: 1 }]
    expect(excursions(trace, -0.2, 0.2)).toHaveLength(1)
  })

  it('skips rows with no PMV', () => {
    expect(excursions([{ step: 0, occupancy: 1 }])).toEqual([])
    expect(excursions(null)).toEqual([])
  })
})

describe('latest', () => {
  it('returns the most recent row, or null', () => {
    expect(latest([{ step: 0 }, { step: 1 }])).toEqual({ step: 1 })
    expect(latest([])).toBeNull()
    expect(latest(null)).toBeNull()
  })
})

describe('domain', () => {
  it('pads the observed range so a trace does not touch the axis', () => {
    expect(domain([{ t: 20.4 }, { t: 28.9 }], 't', 1)).toEqual([19, 30])
  })

  it('ignores non-numeric values', () => {
    expect(domain([{ t: 20 }, { t: null }, { t: 24 }], 't', 0)).toEqual([20, 24])
  })

  it('defers to Recharts when there is nothing to measure', () => {
    expect(domain([], 't')).toEqual(['auto', 'auto'])
    expect(domain([{ t: null }], 't')).toEqual(['auto', 'auto'])
  })
})
