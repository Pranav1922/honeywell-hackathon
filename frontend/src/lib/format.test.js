// Formatter tests. Two things are being protected here: that a run in progress
// renders a dash rather than "NaN", and that a signed number keeps its sign —
// because the sign is the whole meaning of both PMV and the savings percentage.

import { describe, expect, it } from 'vitest'

import {
  EMPTY,
  ach,
  celsius,
  clock,
  co2,
  count,
  fraction,
  kw,
  kwh,
  label,
  latency,
  money,
  number,
  parseSimTime,
  percent,
  pmv,
  ppm,
  progress,
  simClock,
  simTime,
  wallClock,
} from './format.js'

const FORMATTERS = { number, kwh, kw, celsius, percent, fraction, pmv, ach, ppm, latency, count, money, co2 }

describe('missing values', () => {
  it.each(Object.entries(FORMATTERS))('%s renders an em dash for null', (_name, fn) => {
    expect(fn(null)).toBe(EMPTY)
    expect(fn(undefined)).toBe(EMPTY)
  })

  it.each(Object.entries(FORMATTERS))('%s rejects non-finite numbers', (_name, fn) => {
    expect(fn(Number.NaN)).toBe(EMPTY)
    expect(fn(Number.POSITIVE_INFINITY)).toBe(EMPTY)
  })

  it.each(Object.entries(FORMATTERS))('%s rejects a numeric string', (_name, fn) => {
    // A string that looks like a number is a contract mismatch, not a value to
    // coerce; showing a dash surfaces it instead of hiding it.
    expect(fn('12.5')).toBe(EMPTY)
  })

  it('formats zero as a value, not as missing', () => {
    expect(kwh(0)).toBe('0.00 kWh')
    expect(count(0)).toBe('0')
    expect(fraction(0)).toBe('0%')
    expect(pmv(0)).toBe('0.00')
  })
})

describe('units', () => {
  it('formats energy, power and temperature', () => {
    expect(kwh(259.334)).toBe('259.33 kWh')
    expect(kwh(259.334, 1)).toBe('259.3 kWh')
    expect(kw(9.4271)).toBe('9.43 kW')
    expect(celsius(24.46)).toBe('24.5 °C')
    expect(ach(1.234)).toBe('1.23 ACH')
    expect(ppm(759.6)).toBe('760 ppm')
    expect(co2(108.921)).toBe('108.92 kg')
    expect(money(46.678)).toBe('46.68')
  })

  it('formats a fraction as a percentage', () => {
    expect(fraction(0.65)).toBe('65%')
    expect(fraction(0.655, 1)).toBe('65.5%')
    expect(fraction(1)).toBe('100%')
  })

  it('separates thousands in counts', () => {
    expect(count(35040)).toBe('35,040')
  })
})

describe('signed values', () => {
  it('marks a positive saving with a plus and a loss with a minus', () => {
    expect(percent(12.34)).toBe('+12.3%')
    expect(percent(-4.5)).toBe('-4.5%')
    expect(percent(0)).toBe('0.0%')
  })

  it('can render an unsigned percentage for non-comparative figures', () => {
    expect(percent(12.34, { signed: false })).toBe('12.3%')
  })

  it('keeps the sign on PMV, because it distinguishes cold from hot', () => {
    expect(pmv(0.31)).toBe('+0.31')
    expect(pmv(-0.31)).toBe('-0.31')
  })
})

describe('latency', () => {
  it('reports milliseconds below a second and seconds above', () => {
    expect(latency(420)).toBe('420 ms')
    expect(latency(999)).toBe('999 ms')
    expect(latency(1000)).toBe('1.00 s')
    expect(latency(2480)).toBe('2.48 s')
  })
})

describe('simulated timestamps', () => {
  // The backend sends naive ISO strings — simulated local time, not instants. If
  // these were parsed in the browser's zone, the occupancy schedule on screen
  // would shift away from the one the controller actually ran against.
  it('treats a zone-less timestamp as UTC', () => {
    expect(clock('2024-07-15T14:30:00')).toBe('14:30')
    expect(simClock('2024-07-15T14:30:00')).toBe('07-15 14:30')
    expect(simTime('2024-07-15T14:30:00')).toBe('Mon 14:30')
  })

  it('honours an explicit zone when one is present', () => {
    expect(clock('2024-07-15T14:30:00Z')).toBe('14:30')
    expect(clock('2024-07-15T14:30:00+00:00')).toBe('14:30')
  })

  it('pads hours and minutes', () => {
    expect(clock('2024-07-15T04:05:00')).toBe('04:05')
    expect(simClock('2024-01-02T00:00:00')).toBe('01-02 00:00')
  })

  it('returns a dash for anything unparseable', () => {
    for (const value of [null, undefined, '', 'not a date', 42]) {
      expect(clock(value)).toBe(EMPTY)
      expect(simTime(value)).toBe(EMPTY)
      expect(simClock(value)).toBe(EMPTY)
      expect(parseSimTime(value)).toBeNull()
    }
  })

  it('renders a real instant in the local zone', () => {
    expect(wallClock('2024-07-15T14:30:00Z')).not.toBe(EMPTY)
    expect(wallClock('rubbish')).toBe(EMPTY)
  })
})

describe('progress', () => {
  it('is a clamped percentage of the horizon', () => {
    expect(progress(48, 96)).toBe(50)
    expect(progress(0, 96)).toBe(0)
    expect(progress(96, 96)).toBe(100)
    expect(progress(200, 96)).toBe(100)
    expect(progress(-5, 96)).toBe(0)
  })

  it('is zero rather than infinite when there is no horizon', () => {
    expect(progress(10, 0)).toBe(0)
    expect(progress(10, null)).toBe(0)
    expect(progress(null, 96)).toBe(0)
  })
})

describe('labels', () => {
  it('title-cases machine tokens', () => {
    expect(label('peak_shave')).toBe('Peak Shave')
    expect(label('setback')).toBe('Setback')
    expect(label('llm')).toBe('Llm')
  })

  it('returns a dash for an absent token', () => {
    expect(label('')).toBe(EMPTY)
    expect(label(null)).toBe(EMPTY)
  })
})
