// Series shaping shared by the charts: downsampling, cumulative accumulation,
// and merging a baseline run against an agent run by step.
//
// Separate from `format.js`, which turns one number into one string. This module
// turns one array into another array, and it is where the chart panels' only
// non-presentational logic lives — pulled out of the components so it can be
// asserted on directly rather than through rendered SVG.

/** Above this many points a chart is redrawing pixels nobody can distinguish. */
export const MAX_CHART_POINTS = 1500

/**
 * Downsample to at most `maxPoints` rows, keeping the first and the last.
 *
 * An annual run is 35,040 rows; a browser will draw that and then stop
 * responding. Fixed-stride selection keeps the shape of the trace and, unlike
 * averaging into buckets, keeps every plotted point a real measurement — which
 * matters because these charts are evidence.
 *
 * Retaining the final row is what stops the "now" end of a live chart lagging
 * behind the KPI row above it by up to a stride.
 */
export function sample(rows, maxPoints = MAX_CHART_POINTS) {
  if (!Array.isArray(rows) || rows.length === 0) return []
  if (maxPoints < 1) return []
  if (rows.length <= maxPoints) return rows

  const step = Math.ceil(rows.length / maxPoints)
  const sampled = []
  for (let index = 0; index < rows.length; index += step) sampled.push(rows[index])

  const last = rows[rows.length - 1]
  if (sampled[sampled.length - 1] !== last) sampled.push(last)
  return sampled
}

/**
 * Add a running total of `source` to every row under `target`.
 *
 * Accumulated over the *full* trace, never over a sampled one: summing sampled
 * rows would silently drop the energy used in between and understate the total
 * by whatever the stride happened to be. Downsample after this, not before.
 */
export function withCumulative(rows, source = 'energy_kwh', target = 'cumulative_kwh') {
  let total = 0
  return (rows ?? []).map((row) => {
    const value = typeof row?.[source] === 'number' ? row[source] : 0
    total += value
    return { ...row, [target]: total }
  })
}

/**
 * Overlay a baseline run onto an agent run, matched on step.
 *
 * Matched on step rather than on array index because the two runs can differ in
 * length — a stopped run, or a different horizon — and index alignment would
 * quietly compare hour 40 of one against hour 3 of the other.
 */
export function mergeByStep(primary, secondary, fields) {
  const lookup = new Map((secondary ?? []).map((row) => [row.step, row]))
  return (primary ?? []).map((row) => {
    const other = lookup.get(row.step)
    const merged = { ...row }
    for (const [target, source] of Object.entries(fields)) {
      merged[target] = other ? other[source] : null
    }
    return merged
  })
}

/**
 * Rows whose PMV falls outside the acceptable band while the zone is occupied.
 *
 * Occupancy is part of the test on purpose: an empty building cannot be
 * uncomfortable, and marking its overnight drift as a violation would bury the
 * excursions that actually count.
 */
export function excursions(rows, low = -0.5, high = 0.5) {
  return (rows ?? []).filter(
    (row) =>
      typeof row?.pmv === 'number' &&
      row.occupancy > 0 &&
      (row.pmv < low || row.pmv > high)
  )
}

/** The most recent row of a trace, or null. */
export function latest(rows) {
  return Array.isArray(rows) && rows.length > 0 ? rows[rows.length - 1] : null
}

/** The min and max of one numeric field, padded, for a stable chart domain. */
export function domain(rows, field, padding = 1) {
  const values = (rows ?? [])
    .map((row) => row?.[field])
    .filter((value) => typeof value === 'number' && Number.isFinite(value))
  if (values.length === 0) return ['auto', 'auto']
  return [
    Math.floor(Math.min(...values) - padding),
    Math.ceil(Math.max(...values) + padding),
  ]
}
