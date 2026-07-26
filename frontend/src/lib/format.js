// Display formatting shared by the panels: kWh, percentages, temperatures,
// timestamps, and PMV values.
//
// Every formatter tolerates null and undefined, because a run in progress has
// null aggregates until it finishes and the panels render before that. Returning
// a dash rather than "NaN" is the difference between a dashboard that looks
// unfinished and one that looks broken.

export const EMPTY = '—'

const isNumber = (value) => typeof value === 'number' && Number.isFinite(value)

/** A number to `digits` decimal places, or an em dash. */
export function number(value, digits = 2) {
  return isNumber(value) ? value.toFixed(digits) : EMPTY
}

/** Energy in kWh. */
export function kwh(value, digits = 2) {
  return isNumber(value) ? `${value.toFixed(digits)} kWh` : EMPTY
}

/** Power in kW. */
export function kw(value, digits = 2) {
  return isNumber(value) ? `${value.toFixed(digits)} kW` : EMPTY
}

/** A temperature in degrees Celsius. */
export function celsius(value, digits = 1) {
  return isNumber(value) ? `${value.toFixed(digits)} °C` : EMPTY
}

/**
 * A percentage. Signed by default, because the savings figure is the headline
 * number and a negative one must read as a loss rather than quietly as a gain.
 */
export function percent(value, { digits = 1, signed = true } = {}) {
  if (!isNumber(value)) return EMPTY
  const sign = signed && value > 0 ? '+' : ''
  return `${sign}${value.toFixed(digits)}%`
}

/** A fraction 0..1 rendered as a percentage. */
export function fraction(value, digits = 0) {
  return isNumber(value) ? `${(value * 100).toFixed(digits)}%` : EMPTY
}

/**
 * PMV, always signed. The sign is the information: -0.4 is a cold building and
 * +0.4 is a hot one, and both are inside the acceptable band.
 */
export function pmv(value, digits = 2) {
  if (!isNumber(value)) return EMPTY
  return `${value > 0 ? '+' : ''}${value.toFixed(digits)}`
}

/** Air changes per hour. */
export function ach(value, digits = 2) {
  return isNumber(value) ? `${value.toFixed(digits)} ACH` : EMPTY
}

/** CO2 concentration. */
export function ppm(value) {
  return isNumber(value) ? `${Math.round(value)} ppm` : EMPTY
}

/** A model latency in milliseconds, promoted to seconds once it is long. */
export function latency(value) {
  if (!isNumber(value)) return EMPTY
  return value >= 1000 ? `${(value / 1000).toFixed(2)} s` : `${Math.round(value)} ms`
}

/** A whole number with thousands separators. */
export function count(value) {
  return isNumber(value) ? Math.round(value).toLocaleString('en-US') : EMPTY
}

/** Money, in whatever currency the tariff is denominated in. */
export function money(value, digits = 2) {
  return isNumber(value) ? value.toFixed(digits) : EMPTY
}

/** Carbon mass. */
export function co2(value, digits = 2) {
  return isNumber(value) ? `${value.toFixed(digits)} kg` : EMPTY
}

/**
 * Simulated wall-clock as `Mon 14:30`.
 *
 * Simulation timestamps are naive ISO strings with no zone — they are simulated
 * local time, not real instants — so they are parsed and rendered in UTC. Letting
 * the browser apply its own offset would shift the occupancy schedule on screen
 * away from the one the controller actually ran against.
 */
export function simTime(value) {
  const date = parseSimTime(value)
  if (!date) return EMPTY
  const day = date.toLocaleDateString('en-US', { weekday: 'short', timeZone: 'UTC' })
  return `${day} ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`
}

/** Simulated wall-clock as `07-15 14:30`, for chart axes. */
export function simClock(value) {
  const date = parseSimTime(value)
  if (!date) return EMPTY
  return `${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())} ${pad(
    date.getUTCHours()
  )}:${pad(date.getUTCMinutes())}`
}

/** Time of day only, `14:30`. */
export function clock(value) {
  const date = parseSimTime(value)
  if (!date) return EMPTY
  return `${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`
}

/** A real ISO instant rendered in the viewer's own zone. */
export function wallClock(value) {
  if (!value) return EMPTY
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? EMPTY : date.toLocaleString()
}

/** Parse a simulated timestamp, treating a zone-less string as UTC. */
export function parseSimTime(value) {
  if (typeof value !== 'string' || value === '') return null
  const hasZone = /(Z|[+-]\d{2}:?\d{2})$/.test(value)
  const date = new Date(hasZone ? value : `${value}Z`)
  return Number.isNaN(date.getTime()) ? null : date
}

/** A run's progress through its horizon, as a percentage clamped to 0..100. */
export function progress(completed, horizon) {
  if (!isNumber(completed) || !isNumber(horizon) || horizon <= 0) return 0
  return Math.min(100, Math.max(0, (completed / horizon) * 100))
}

/** Title-case a machine token: `peak_shave` becomes `Peak Shave`. */
export function label(value) {
  if (typeof value !== 'string' || value === '') return EMPTY
  return value
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}

function pad(value) {
  return String(value).padStart(2, '0')
}
