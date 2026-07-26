# frontend/src/

| Path | Purpose |
|---|---|
| `main.jsx` | React entrypoint; mounts `<App />`. |
| `App.jsx` | Dashboard shell. Owns the selected run, composes the panels, passes the live stream down. |
| `components/` | One file per dashboard panel. See its README. |
| `hooks/` | `useRunStream.js` — the live-data subscription. |
| `lib/` | `api.js` (endpoint wrappers), `format.js` (display formatting) and `series.js` (chart series shaping). |
| `index.css` | The whole stylesheet. Custom properties carry the palette, which the charts read as `var(--…)`. |
| `test/` | jsdom setup and fixtures matching the backend's wire models. |

Data flows one way: `useRunStream` → `App` → panels. Panels are presentational
and fetch nothing themselves, which keeps a single run's data consistent across
every chart on screen.
