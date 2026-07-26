// Shared frame for the four chart panels: heading, fixed plot height, and the
// empty state.
//
// Not a dashboard panel itself — a presentational primitive the panels reuse.
// It exists because a chart with no data must say so rather than render empty
// axes, and repeating that judgement in four files is how the four of them end
// up disagreeing about it.

export default function ChartFrame({
  title,
  subtitle,
  empty = false,
  emptyMessage = 'No telemetry yet. Start a run to see it here.',
  height = 240,
  children,
}) {
  return (
    <section className="panel chart-panel">
      <header className="panel-head">
        <h2>{title}</h2>
        {subtitle ? <p className="panel-sub">{subtitle}</p> : null}
      </header>
      <div className="chart-body" style={{ height }}>
        {empty ? <p className="empty">{emptyMessage}</p> : children}
      </div>
    </section>
  )
}
