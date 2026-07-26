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

Implemented in Milestone 3.
