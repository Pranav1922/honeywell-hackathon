# frontend/

The savings dashboard — hackathon deliverable 3, and the surface recorded for
the demonstration video.

Vite + React + Recharts. No routing, no state library, no component framework: a
single live view does not need them.

| Path | Purpose |
|---|---|
| `index.html`, `vite.config.js`, `package.json` | Build setup. The dev server proxies `/api` to FastAPI on :8000, so the API client uses same-origin paths and CORS never arises. |
| `src/` | Application source. See its README. |
| `public/` | Static assets served verbatim. |

## Running

```bash
npm install
npm run dev          # http://localhost:5173
```

The backend must be running on :8000 first:

```bash
cd ../backend && uvicorn app.main:app --reload
```

## Tests

```bash
npm test             # vitest, jsdom
npm run test:watch
npm run build        # production bundle
```

Tests are co-located as `*.test.js(x)` beside the modules they cover;
`src/test/` holds the jsdom setup and fixtures mirroring the backend's wire
models. Nothing reaches the network — `fetch` and `EventSource` are stubbed,
which is what makes the polling fallback and the stream-error paths testable.
