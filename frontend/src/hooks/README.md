# frontend/src/hooks/

| Hook | Purpose |
|---|---|
| `useRunStream.js` | Subscribes to `/api/runs/{id}/stream` over Server-Sent Events and returns timeseries, decisions and run status. Falls back to incremental polling with `since_step` when SSE is unavailable, so the dashboard degrades rather than freezing. |
