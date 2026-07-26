// The control action currently in force: strategy label, heating and cooling
// set-points, lighting level, ventilation rate, and whether the guard clamped
// the agent's request.
//
// The clamp badge is the visible face of the safety property. When the guard
// overrides the supervisor, that override appears here rather than being folded
// away — an override is evidence about the agent's judgement, and hiding it would
// make the two-tier design impossible to demonstrate.

import { ach, celsius, fraction, label, ppm, simTime } from '../lib/format.js'

function Row({ name, value }) {
  return (
    <div className="action-row">
      <span className="action-name">{name}</span>
      <span className="action-value">{value}</span>
    </div>
  )
}

export default function ActionPanel({ decision, state }) {
  if (!decision && !state) {
    return (
      <section className="panel">
        <header className="panel-head">
          <h2>Control action</h2>
        </header>
        <p className="empty">No action yet. Start a run to see the live set-points.</p>
      </section>
    )
  }

  // Prefer the decision for what was *commanded* and the telemetry row for what
  // the building is actually doing; they agree except on the step a policy
  // changes, and the commanded value is the one being explained.
  const heating = decision?.heating_sp_c ?? state?.heating_sp_c
  const cooling = decision?.cooling_sp_c ?? state?.cooling_sp_c
  const lighting = decision?.lighting_level ?? state?.lighting_level
  const ventilation = decision?.ventilation_ach ?? state?.ventilation_ach
  const deadband =
    typeof heating === 'number' && typeof cooling === 'number' ? cooling - heating : null

  return (
    <section className="panel">
      <header className="panel-head">
        <h2>Control action</h2>
        <p className="panel-sub">
          {decision ? `As of ${simTime(decision.sim_time)}` : 'From live telemetry'}
        </p>
      </header>

      <div className="strategy">
        <span className="strategy-label">{label(decision?.strategy ?? 'unknown')}</span>
        <span className="badges">
          {decision?.guard_clamped ? (
            <span className="badge badge-warn" title="The reactive guard overrode the requested set-points">
              Guard clamped
            </span>
          ) : null}
          {decision?.fallback_used ? (
            <span className="badge badge-bad" title="The supervisor was unavailable; the fixed schedule is driving">
              Fallback
            </span>
          ) : null}
          {decision?.retries > 0 ? (
            <span className="badge badge-info" title="Self-correction rounds before the policy validated">
              {decision.retries} retr{decision.retries === 1 ? 'y' : 'ies'}
            </span>
          ) : null}
        </span>
      </div>

      <div className="action-grid">
        <Row name="Heating set-point" value={celsius(heating)} />
        <Row name="Cooling set-point" value={celsius(cooling)} />
        <Row name="Dead-band" value={deadband != null ? `${deadband.toFixed(1)} K` : '—'} />
        <Row name="Lighting" value={fraction(lighting)} />
        <Row name="Ventilation" value={ach(ventilation)} />
        <Row name="HVAC mode" value={label(state?.hvac_mode ?? 'off')} />
        <Row name="Zone temperature" value={celsius(state?.zone_temp_c)} />
        <Row name="Outdoor temperature" value={celsius(state?.outdoor_temp_c)} />
        <Row name="Occupancy" value={fraction(state?.occupancy)} />
        <Row name="CO₂" value={ppm(state?.co2_ppm)} />
      </div>
    </section>
  )
}
