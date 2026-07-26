// Streamed agent decisions: rationale, tools called, latency, retries, and any
// fallback. This is the panel that proves autonomy on the demonstration video.
//
// Newest first, because on a live run the interesting entry is the one that just
// arrived and a viewer should not have to scroll to find it.
//
// Only supervisory decisions carry a latency, so `latency_ms` is what separates a
// reasoned policy from a guard step. Both are shown — filtering the guard out
// would hide how often the fast tier is the one actually deciding — but they are
// marked differently so the distinction is never ambiguous.

import { ach, celsius, fraction, label, latency, simTime } from '../lib/format.js'

/** Tool names in call order, deduplicated, for the one-line summary. */
export function toolNames(toolCalls) {
  if (!Array.isArray(toolCalls)) return []
  const seen = []
  for (const call of toolCalls) {
    const name = call?.name
    if (typeof name === 'string' && name !== '' && !seen.includes(name)) seen.push(name)
  }
  return seen
}

/** Whether the model, rather than the guard alone, produced this decision. */
export function isSupervisory(decision) {
  return typeof decision?.latency_ms === 'number'
}

function Entry({ decision }) {
  const tools = toolNames(decision.tool_calls)
  const supervisory = isSupervisory(decision)

  return (
    <li className={`log-entry ${supervisory ? 'log-supervisory' : 'log-guard'}`}>
      <div className="log-head">
        <span className="log-time">{simTime(decision.sim_time)}</span>
        <span className="log-strategy">{label(decision.strategy)}</span>
        <span className="log-setpoints">
          {celsius(decision.heating_sp_c)} / {celsius(decision.cooling_sp_c)}
        </span>
        {supervisory ? (
          <span className="badge badge-info">{latency(decision.latency_ms)}</span>
        ) : (
          <span className="badge badge-quiet" title="Fast-tier guard step, no model call">
            guard
          </span>
        )}
        {decision.guard_clamped ? <span className="badge badge-warn">clamped</span> : null}
        {decision.fallback_used ? <span className="badge badge-bad">fallback</span> : null}
        {decision.retries > 0 ? (
          <span className="badge badge-info">{decision.retries}× repaired</span>
        ) : null}
      </div>

      <p className="log-rationale">{decision.rationale}</p>

      <div className="log-meta">
        <span>light {fraction(decision.lighting_level)}</span>
        <span>vent {ach(decision.ventilation_ach)}</span>
        {tools.length > 0 ? <span>tools: {tools.join(' → ')}</span> : null}
        {decision.prompt_tokens != null ? (
          <span>
            {decision.prompt_tokens}+{decision.completion_tokens ?? 0} tokens
          </span>
        ) : null}
      </div>
    </li>
  )
}

export default function AgentLog({ decisions = [], limit = 60 }) {
  const newestFirst = decisions.slice(-limit).reverse()
  const supervised = decisions.filter(isSupervisory).length

  return (
    <section className="panel log-panel">
      <header className="panel-head">
        <h2>Agent reasoning</h2>
        <p className="panel-sub">
          {decisions.length === 0
            ? 'Rationales appear here as the agent decides'
            : `${supervised} supervisory decision${supervised === 1 ? '' : 's'} of ${decisions.length} logged`}
        </p>
      </header>

      {newestFirst.length === 0 ? (
        <p className="empty">No decisions yet.</p>
      ) : (
        <ul className="log-list">
          {newestFirst.map((decision) => (
            <Entry key={decision.step} decision={decision} />
          ))}
        </ul>
      )}
    </section>
  )
}
