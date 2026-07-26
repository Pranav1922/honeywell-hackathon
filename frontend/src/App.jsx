// Dashboard shell. Owns the selected run, composes every panel, and passes the
// live stream from useRunStream down to the charts.
//
// Layout: RunControls across the top, KpiRow beneath it, a two-column grid of
// TemperatureChart / EnergyChart / ComfortChart / OccupancyChart, then
// ActionPanel and AgentLog side by side.
//
// Implemented in Milestone 3.
