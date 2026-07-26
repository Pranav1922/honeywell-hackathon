// Headline metrics: total kWh, percent saved versus baseline, peak demand,
// comfort violations, mean PPD. Reads /api/compare.
//
// This panel is one half of the demonstration — the half that proves the agent
// saved energy. Two rules govern it, both about honesty:
//
// Without a baseline it says so. A percentage needs something to be a percentage
// *of*, and inventing one would be the easiest way to make this dashboard lie.
//
// A negative saving is shown as a loss, in the loss colour, and comfort is
// reported next to it. A controller that saved 12% by letting the building drift
// has not succeeded, and the panel should not let that read as a win.

import { count, kw, kwh, number, percent } from '../lib/format.js'

/**
 * Decide what the savings tile shows.
 *
 * Pulled out of the component because it is the one place a wrong branch would
 * misrepresent the result, and it is worth asserting on directly.
 */
export function savingsState({ savings, baselineRunId, runStatus }) {
  if (!baselineRunId) {
    return { kind: 'none', label: 'No baseline', hint: 'Pick a baseline run to compare' }
  }
  if (!savings) {
    return {
      kind: 'pending',
      label: '—',
      hint: runStatus === 'running' ? 'Available when the run completes' : 'Comparison unavailable',
    }
  }
  const saved = savings.kwh_saved_pct
  return {
    kind: saved >= 0 ? 'saving' : 'loss',
    label: percent(saved),
    hint: `${kwh(savings.kwh_saved)} against run ${savings.baseline_run_id}`,
  }
}

function Tile({ title, value, hint, tone = 'neutral' }) {
  return (
    <div className={`kpi kpi-${tone}`}>
      <span className="kpi-title">{title}</span>
      <strong className="kpi-value">{value}</strong>
      <span className="kpi-hint">{hint}</span>
    </div>
  )
}

export default function KpiRow({ run, savings, summary }) {
  const totalKwh = run?.total_kwh ?? summary?.total_kwh
  const peakKw = run?.peak_kw ?? summary?.peak_kw
  const violations = run?.comfort_violations ?? summary?.comfort_violations
  const meanPpd = run?.mean_ppd ?? summary?.mean_ppd

  const saving = savingsState({
    savings,
    baselineRunId: run?.baseline_run_id,
    runStatus: run?.status,
  })

  const comfortTone =
    typeof violations !== 'number' ? 'neutral' : violations === 0 ? 'good' : 'warn'

  return (
    <div className="kpi-row">
      <Tile
        title="Energy used"
        value={kwh(totalKwh)}
        hint={run?.cost != null ? `Cost ${number(run.cost)}` : 'Total for this run'}
      />
      <Tile
        title="Energy saved"
        value={saving.label}
        hint={saving.hint}
        tone={saving.kind === 'saving' ? 'good' : saving.kind === 'loss' ? 'bad' : 'neutral'}
      />
      <Tile
        title="Peak demand"
        value={kw(peakKw)}
        hint={
          savings
            ? `${percent(savings.peak_reduction_pct)} vs baseline`
            : 'Highest instantaneous draw'
        }
      />
      <Tile
        title="Comfort violations"
        value={count(violations)}
        hint={
          savings
            ? savings.comfort_maintained
              ? 'Comfort maintained vs baseline'
              : 'Comfort degraded vs baseline'
            : 'Occupied steps outside the PMV band'
        }
        tone={savings && !savings.comfort_maintained ? 'bad' : comfortTone}
      />
      <Tile
        title="Mean PPD"
        value={meanPpd != null ? `${number(meanPpd, 1)} %` : '—'}
        hint="Predicted percentage dissatisfied"
      />
      <Tile
        title="Carbon"
        value={run?.co2_kg != null ? `${number(run.co2_kg)} kg` : '—'}
        hint={savings ? `${number(savings.co2_saved_kg)} kg avoided` : 'CO₂ for this run'}
      />
    </div>
  )
}
