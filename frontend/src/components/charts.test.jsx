// Chart tests.
//
// The assertions that matter are on the exported data-shaping functions, not on
// rendered SVG: Recharts under jsdom has no real layout engine, so asserting on
// path geometry would test the stub rather than the code. What the render tests
// check is the part that can genuinely break independently — the heading, the
// empty state, and that the component does not throw on real-shaped data.

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { timestep, trace } from '../test/fixtures.js'
import ComfortChart, { buildComfortSeries } from './ComfortChart.jsx'
import EnergyChart, { buildEnergySeries } from './EnergyChart.jsx'
import OccupancyChart, { buildOccupancySeries } from './OccupancyChart.jsx'
import TemperatureChart, { buildTemperatureSeries } from './TemperatureChart.jsx'

describe('buildTemperatureSeries', () => {
  it('carries the dead-band as a floor and a height for stacked shading', () => {
    // Recharts shades between two series by stacking, so the band cannot be
    // expressed as (lower, upper).
    const [row] = buildTemperatureSeries([timestep(1, { heating_sp_c: 21, cooling_sp_c: 25.5 })])

    expect(row.band_floor).toBe(21)
    expect(row.band_height).toBe(4.5)
  })

  it('never produces a negative band height', () => {
    // An inverted pair would render as a band pointing the wrong way; the
    // simulator rejects it, but the chart must not depend on that.
    const [row] = buildTemperatureSeries([timestep(1, { heating_sp_c: 25, cooling_sp_c: 21 })])

    expect(row.band_height).toBe(0)
  })

  it('keeps both temperatures and the step key', () => {
    const [row] = buildTemperatureSeries([timestep(3)])

    expect(row).toMatchObject({ step: 3, zone_temp_c: 24.5, outdoor_temp_c: 30.0 })
    expect(row.sim_time).toBe('2024-07-15T12:00:00')
  })

  it('handles empty input', () => {
    expect(buildTemperatureSeries([])).toEqual([])
    expect(buildTemperatureSeries(null)).toEqual([])
  })
})

describe('buildEnergySeries', () => {
  const agent = [
    timestep(0, { power_kw: 2, energy_kwh: 0.5 }),
    timestep(1, { power_kw: 4, energy_kwh: 1.0 }),
  ]
  const baseline = [
    timestep(0, { power_kw: 6, energy_kwh: 1.5 }),
    timestep(1, { power_kw: 8, energy_kwh: 2.0 }),
  ]

  it('accumulates cumulative kWh for both runs', () => {
    const data = buildEnergySeries(agent, baseline)

    expect(data.map((row) => row.agent_kwh)).toEqual([0.5, 1.5])
    expect(data.map((row) => row.baseline_kwh)).toEqual([1.5, 3.5])
  })

  it('carries instantaneous power for both runs', () => {
    const data = buildEnergySeries(agent, baseline)

    expect(data.map((row) => row.agent_kw)).toEqual([2, 4])
    expect(data.map((row) => row.baseline_kw)).toEqual([6, 8])
  })

  it('yields null baseline values when there is no baseline run', () => {
    const data = buildEnergySeries(agent, [])

    expect(data.every((row) => row.baseline_kw === null)).toBe(true)
    expect(data.every((row) => row.baseline_kwh === null)).toBe(true)
  })

  it('keeps the cumulative total correct after downsampling', () => {
    // The failure this catches: sampling before accumulating, which understates
    // the total by the stride and makes the savings figure wrong on screen.
    const long = Array.from({ length: 400 }, (_, index) =>
      timestep(index, { energy_kwh: 1 })
    )
    const data = buildEnergySeries(long, [], 50)

    expect(data.length).toBeLessThanOrEqual(51)
    expect(data[data.length - 1].agent_kwh).toBe(400)
  })

  it('handles empty input on both sides', () => {
    expect(buildEnergySeries([], [])).toEqual([])
    expect(buildEnergySeries(null, null)).toEqual([])
  })
})

describe('buildComfortSeries', () => {
  it('plots PMV only while the zone is occupied', () => {
    // Off-hours PMV is real but meaningless — nobody is there — and plotting it
    // makes every overnight setback look like a comfort failure.
    const data = buildComfortSeries([
      timestep(0, { occupancy: 0, pmv: -1.4 }),
      timestep(1, { occupancy: 0.5, pmv: 0.2 }),
    ])

    expect(data[0].pmv).toBeNull()
    expect(data[1].pmv).toBe(0.2)
  })

  it('marks an occupied excursion above and below the band', () => {
    const data = buildComfortSeries([
      timestep(0, { occupancy: 1, pmv: 0.9 }),
      timestep(1, { occupancy: 1, pmv: -0.9 }),
      timestep(2, { occupancy: 1, pmv: 0.1 }),
    ])

    expect(data.map((row) => row.excursion)).toEqual([0.9, -0.9, null])
  })

  it('does not mark an unoccupied excursion', () => {
    const data = buildComfortSeries([timestep(0, { occupancy: 0, pmv: 2.5 })])

    expect(data[0].excursion).toBeNull()
  })

  it('honours a custom band', () => {
    const data = buildComfortSeries([timestep(0, { occupancy: 1, pmv: 0.4 })], -0.2, 0.2)

    expect(data[0].excursion).toBe(0.4)
  })

  it('handles empty input', () => {
    expect(buildComfortSeries([])).toEqual([])
    expect(buildComfortSeries(null)).toEqual([])
  })
})

describe('buildOccupancySeries', () => {
  it('carries occupancy and CO2 together', () => {
    const [row] = buildOccupancySeries([timestep(1)])

    expect(row).toEqual({
      step: 1,
      sim_time: '2024-07-15T12:00:00',
      occupancy: 0.75,
      co2_ppm: 760,
    })
  })

  it('handles empty input', () => {
    expect(buildOccupancySeries([])).toEqual([])
    expect(buildOccupancySeries(null)).toEqual([])
  })
})

describe('rendering', () => {
  const rows = trace(96)

  it('every chart shows its heading and renders real data without throwing', () => {
    render(
      <>
        <TemperatureChart rows={rows} />
        <EnergyChart rows={rows} baselineRows={rows} hasBaseline />
        <ComfortChart rows={rows} />
        <OccupancyChart rows={rows} />
      </>
    )

    expect(screen.getByText('Zone temperature')).toBeInTheDocument()
    expect(screen.getByText('Energy')).toBeInTheDocument()
    expect(screen.getByText('Thermal comfort')).toBeInTheDocument()
    expect(screen.getByText('Occupancy and air quality')).toBeInTheDocument()
    expect(screen.queryByText(/no telemetry yet/i)).not.toBeInTheDocument()
  })

  it('every chart shows an empty state rather than bare axes', () => {
    render(
      <>
        <TemperatureChart rows={[]} />
        <EnergyChart rows={[]} />
        <ComfortChart rows={[]} />
        <OccupancyChart rows={[]} />
      </>
    )

    expect(screen.getAllByText(/no telemetry yet/i)).toHaveLength(4)
  })

  it('every chart renders with no props at all', () => {
    render(
      <>
        <TemperatureChart />
        <EnergyChart />
        <ComfortChart />
        <OccupancyChart />
      </>
    )

    expect(screen.getAllByText(/no telemetry yet/i)).toHaveLength(4)
  })

  it('the energy chart says when there is no baseline to overlay', () => {
    render(<EnergyChart rows={rows} hasBaseline={false} />)

    expect(screen.getByText(/pick a baseline run to overlay it/i)).toBeInTheDocument()
  })

  it('the comfort chart counts excursions in its subtitle', () => {
    const uncomfortable = [
      timestep(0, { occupancy: 1, pmv: 0.9 }),
      timestep(1, { occupancy: 1, pmv: 1.2 }),
    ]
    render(<ComfortChart rows={uncomfortable} />)

    expect(screen.getByText(/2 excursions outside the band/)).toBeInTheDocument()
  })

  it('the comfort chart says nothing about excursions when there are none', () => {
    render(<ComfortChart rows={[timestep(0, { occupancy: 1, pmv: 0.1 })]} />)

    expect(screen.getByText(/ASHRAE-55 acceptable band/)).toBeInTheDocument()
  })

  it('the comfort chart pluralises a single excursion correctly', () => {
    render(<ComfortChart rows={[timestep(0, { occupancy: 1, pmv: 0.9 })]} />)

    expect(screen.getByText(/1 excursion outside the band/)).toBeInTheDocument()
  })
})
