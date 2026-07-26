// Subscribes to /api/runs/{id}/stream over SSE and falls back to incremental
// polling with since_step when SSE is unavailable. Returns timeseries,
// decisions, and run status.
// Implemented in Milestone 3.
